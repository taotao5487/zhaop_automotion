#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from wechat_service.utils import rss_store


@pytest.fixture()
def temp_rss_db(tmp_path, monkeypatch):
    db_path = tmp_path / "rss.db"
    monkeypatch.setattr(rss_store, "DB_PATH", db_path)
    rss_store.init_db()
    return db_path


def _seed_blank_recruitment_article(fakeid: str, link: str) -> None:
    rss_store.add_subscription(
        fakeid=fakeid,
        nickname="测试公众号",
        alias="test-account",
        head_img="",
    )
    rss_store.save_articles(
        fakeid,
        [
            {
                "aid": "aid-1",
                "title": "【招聘公告】测试医院公开招聘工作人员公告",
                "link": link,
                "digest": "",
                "cover": "",
                "author": "",
                "publish_time": 1710000000,
                "content": "",
                "plain_content": "",
                "images_json": "[]",
                "is_recruitment": 0,
                "review_status": "",
                "matched_keywords": "[]",
                "filter_stage": "",
            }
        ],
    )


def test_get_recruitment_articles_backfills_blank_cached_title_match(temp_rss_db):
    fakeid = "test-fakeid-1"
    link = "https://mp.weixin.qq.com/s/test-backfill-list"
    _seed_blank_recruitment_article(fakeid, link)

    rows = rss_store.get_recruitment_articles(status="confirmed", limit=10)

    assert len(rows) == 1
    assert rows[0]["link"] == link
    assert rows[0]["review_status"] == "confirmed"
    assert rows[0]["filter_stage"] == "title_confirmed"
    assert rows[0]["is_recruitment"] == 1


def test_get_recruitment_article_by_link_backfills_blank_cached_title_match(temp_rss_db):
    fakeid = "test-fakeid-2"
    link = "https://mp.weixin.qq.com/s/test-backfill-link"
    _seed_blank_recruitment_article(fakeid, link)

    row = rss_store.get_recruitment_article_by_link(link)

    assert row is not None
    assert row["link"] == link
    assert row["review_status"] == "confirmed"
    assert row["filter_stage"] == "title_confirmed"
    assert row["is_recruitment"] == 1
