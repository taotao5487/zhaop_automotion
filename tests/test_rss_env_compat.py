from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _reload_rss_poller(monkeypatch, **env: str):
    for key in (
        "RSS_SCHEDULER_TICK",
        "RSS_POLL_INTERVAL",
        "RSS_POLL_BATCH_SIZE",
        "ARTICLES_PER_POLL",
        "RSS_FETCH_FULL_CONTENT",
    ):
        monkeypatch.delenv(key, raising=False)

    for key, value in env.items():
        monkeypatch.setenv(key, value)

    sys.modules.pop("wechat_service.utils.rss_poller", None)
    import wechat_service.utils.rss_poller as rss_poller_module

    return importlib.reload(rss_poller_module)


def test_rss_poller_supports_legacy_poll_interval_env(monkeypatch):
    module = _reload_rss_poller(monkeypatch, RSS_POLL_INTERVAL="21600")

    assert module.SCHEDULER_TICK == 21600
    assert module.POLL_INTERVAL == 21600


def test_rss_poller_prefers_new_scheduler_tick_env(monkeypatch):
    module = _reload_rss_poller(
        monkeypatch,
        RSS_POLL_INTERVAL="21600",
        RSS_SCHEDULER_TICK="120",
    )

    assert module.SCHEDULER_TICK == 120
    assert module.POLL_INTERVAL == 120


@pytest.mark.asyncio
async def test_rss_poller_fetches_full_content_for_title_confirmed_articles(monkeypatch):
    module = _reload_rss_poller(monkeypatch, RSS_FETCH_FULL_CONTENT="true")
    seen_links: list[str] = []

    async def fake_enrich(self, articles):
        seen_links.extend(article["link"] for article in articles)
        return [
            {
                **article,
                "content": "<p>正文</p>",
                "plain_content": "正文",
                "images_json": "[]",
            }
            for article in articles
        ]

    monkeypatch.setattr(module.RSSPoller, "_enrich_articles_content", fake_enrich)

    poller = module.RSSPoller()
    articles = [
        {
            "title": "标题已确认招聘",
            "link": "https://example.com/title-confirmed",
            "filter_stage": "title_confirmed",
        },
        {
            "title": "粗筛命中招聘",
            "link": "https://example.com/coarse-matched",
            "filter_stage": "coarse_matched",
        },
        {
            "title": "已排除文章",
            "link": "https://example.com/rejected",
            "filter_stage": "coarse_excluded",
        },
    ]

    result = await poller._fetch_candidate_content(articles)

    assert seen_links == [
        "https://example.com/title-confirmed",
        "https://example.com/coarse-matched",
    ]
    assert result[0]["content"] == "<p>正文</p>"
    assert result[1]["content"] == "<p>正文</p>"
    assert result[2].get("content", "") == ""
