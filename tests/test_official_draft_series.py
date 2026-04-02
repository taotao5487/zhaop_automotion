#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from wechat_service.utils import rss_store
from wechat_service.utils.official_wechat_draft import (
    push_article_row_to_draft,
    push_article_row_to_shared_draft_series,
    push_recruitment_article_to_shared_draft_series,
)
from scripts.push_wechat_review_to_official_draft import main as crawler_push_main


def _make_row(source_url: str, title: str = "测试文章") -> dict:
    return {
        "title": title,
        "link": source_url,
        "cover": "https://example.com/cover.jpg",
        "content": "<p>正文</p>",
        "plain_content": "正文",
        "digest": "摘要",
        "author": "测试作者",
        "account_name": "测试来源",
    }


@pytest.fixture()
def temp_rss_db(tmp_path, monkeypatch):
    db_path = tmp_path / "rss.db"
    monkeypatch.setattr(rss_store, "DB_PATH", db_path)
    rss_store.init_db()
    return db_path


def test_init_db_creates_shared_series_schema(temp_rss_db):
    import sqlite3

    conn = sqlite3.connect(str(temp_rss_db))
    try:
        draft_sync_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(official_draft_sync)").fetchall()
        }
        series_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(official_draft_series)").fetchall()
        }
    finally:
        conn.close()

    assert "source_type" in draft_sync_columns
    assert "series_batch_index" in draft_sync_columns
    assert {"series_key", "current_media_id", "current_article_count", "batch_index"}.issubset(
        series_columns
    )


def test_build_article_row_from_review_package_drops_unresolved_external_images(tmp_path):
    from PIL import Image

    from wechat_service.utils.crawler_review_package_draft import (
        build_article_row_from_review_package,
    )

    review_dir = tmp_path / "wechat_review" / "item_external_images"
    inline_dir = review_dir / "assets" / "inline"
    inline_dir.mkdir(parents=True, exist_ok=True)

    local_image = inline_dir / "cover.png"
    Image.new("RGB", (320, 180), "white").save(local_image)

    package = {
        "title": "测试外链图片过滤",
        "source_url": "https://example.com/review-package",
        "hospital": "测试医院",
        "plain_text": "这里是正文摘要",
        "content_html": (
            '<article><p>正文</p>'
            '<img src="https://example.com/blocked-banner.png" />'
            '<img src="assets/inline/cover.png" /></article>'
        ),
        "images": [
            {
                "src": "https://example.com/blocked-banner.png",
                "category": "content_image",
                "keep": True,
            },
            {
                "src": "assets/inline/cover.png",
                "category": "content_image",
                "keep": True,
            },
        ],
        "attachments": [],
    }
    (review_dir / "package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    row = build_article_row_from_review_package(review_dir)

    assert str(local_image.resolve()) in row["content"]
    assert "https://example.com/blocked-banner.png" not in row["content"]


def test_build_article_row_from_review_package_allows_other_attachments_without_backfill(tmp_path):
    from wechat_service.utils.crawler_review_package_draft import (
        build_article_row_from_review_package,
    )

    review_dir = tmp_path / "wechat_review" / "item_other_attachment"
    attachments_dir = review_dir / "assets" / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)

    local_attachment = attachments_dir / "attachment_notice.html"
    local_attachment.write_text("<html><body>附件页</body></html>", encoding="utf-8")

    package = {
        "title": "测试普通附件链接",
        "source_url": "https://example.com/review-package",
        "hospital": "测试医院",
        "plain_text": "这里是正文摘要",
        "content_html": (
            '<article><p>正文</p><section class="wechat-review-section"><h2>附件说明</h2>'
            '<ul class="wechat-review-attachments"><li>'
            '<a href="https://example.com/download.html">下载专区</a>'
            "<span>（补充附件）</span></li></ul></section></article>"
        ),
        "images": [],
        "attachments": [
            {
                "title": "下载专区",
                "source_url": "https://example.com/download.html",
                "local_path": "assets/attachments/attachment_notice.html",
                "render_status": "link_only",
                "rendered_images": [],
                "category": "other",
                "description": "补充附件",
            }
        ],
    }
    (review_dir / "package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    row = build_article_row_from_review_package(review_dir)

    assert row["title"] == "测试普通附件链接"
    assert 'href="https://example.com/download.html"' in row["content"]


def test_series_lease_can_be_acquired_released_and_reclaimed(temp_rss_db):
    assert rss_store.acquire_official_draft_series_lease(
        series_key="default",
        lease_owner="worker-a",
        lease_ttl_seconds=60,
        now=100,
    )
    assert not rss_store.acquire_official_draft_series_lease(
        series_key="default",
        lease_owner="worker-b",
        lease_ttl_seconds=60,
        now=120,
    )
    assert rss_store.release_official_draft_series_lease("default", "worker-a")
    assert rss_store.acquire_official_draft_series_lease(
        series_key="default",
        lease_owner="worker-b",
        lease_ttl_seconds=60,
        now=121,
    )
    assert rss_store.acquire_official_draft_series_lease(
        series_key="default",
        lease_owner="worker-c",
        lease_ttl_seconds=60,
        now=999,
    )


def test_official_account_card_can_render_local_qr_without_link(tmp_path, monkeypatch):
    import utils.official_wechat_draft as official_draft_module

    qr_path = tmp_path / "official_qr.jpg"
    qr_path.write_bytes(b"fake-image")

    monkeypatch.setenv("OFFICIAL_WX_CARD_ENABLED", "true")
    monkeypatch.setenv("OFFICIAL_WX_CARD_TITLE", "白衣驿站")
    monkeypatch.setenv("OFFICIAL_WX_CARD_SUBTITLE", "关注公众号，获取更多招聘与医院资讯")
    monkeypatch.setenv("OFFICIAL_WX_CARD_NOTE", "长按识别下方二维码，关注白衣驿站")
    monkeypatch.setenv("OFFICIAL_WX_CARD_LINK", "")
    monkeypatch.setenv("OFFICIAL_WX_CARD_QR_URL", str(qr_path))

    html = official_draft_module._append_official_account_card("<p>正文</p>")

    assert "<p>正文</p>" in html
    assert str(qr_path) in html
    assert "width:96px" in html
    assert "白衣驿站" not in html
    assert "关注公众号" not in html
    assert "长按识别下方二维码" not in html
    assert "进入公众号主页" not in html


def test_official_account_card_reads_values_from_dotenv(tmp_path, monkeypatch):
    import utils.official_wechat_draft as official_draft_module

    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "OFFICIAL_WX_CARD_ENABLED=true",
                "OFFICIAL_WX_CARD_TITLE=测试公众号",
                "OFFICIAL_WX_CARD_SUBTITLE=测试副标题",
                "OFFICIAL_WX_CARD_NOTE=通过 dotenv 载入二维码名片",
                "OFFICIAL_WX_CARD_LINK=",
                "OFFICIAL_WX_CARD_QR_URL=/tmp/test-official-qr.jpg",
            ]
        ),
        encoding="utf-8",
    )

    for key in (
        "OFFICIAL_WX_CARD_ENABLED",
        "OFFICIAL_WX_CARD_TITLE",
        "OFFICIAL_WX_CARD_SUBTITLE",
        "OFFICIAL_WX_CARD_NOTE",
        "OFFICIAL_WX_CARD_LINK",
        "OFFICIAL_WX_CARD_QR_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(official_draft_module, "_OFFICIAL_DOTENV_PATH", env_path)
    monkeypatch.setattr(official_draft_module, "_OFFICIAL_ENV_LOADED", False)

    html = official_draft_module._build_official_account_card_html()

    assert "/tmp/test-official-qr.jpg" in html
    assert "width:96px" in html
    assert "测试公众号" not in html
    assert "测试副标题" not in html
    assert "通过 dotenv 载入二维码名片" not in html


def test_sanitize_title_trims_overlong_wechat_titles():
    import utils.official_wechat_draft as official_draft_module

    raw = (
        "广元市第一人民医院 2026年度非在编工作人员自主招聘公告（第三批）- "
        "广元市第一人民医院【www.gys072.cn】欢迎您！广元市第一人民医院"
    )

    sanitized = official_draft_module._sanitize_title(raw)

    assert sanitized.startswith("广元市第一人民医院 2026年度非在编工作人员自主招聘公告")
    assert len(sanitized) <= 64


@pytest.mark.asyncio
async def test_build_payload_rewrites_relative_qr_image_from_project_root(tmp_path, monkeypatch):
    import utils.official_wechat_draft as official_draft_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OFFICIAL_WX_CARD_ENABLED", "true")
    monkeypatch.setenv("OFFICIAL_WX_CARD_QR_URL", "static/official_wx_card_qr.jpg")

    uploaded_sources = []
    uploaded_url = "https://mmbiz.qpic.cn/tenant/uploaded-qr.jpg"

    async def fake_upload_content_image(access_token, image_url):
        uploaded_sources.append(image_url)
        return uploaded_url

    async def fake_upload_cover_material(access_token, cover_url):
        return "thumb-1"

    monkeypatch.setattr(
        "utils.official_wechat_draft._upload_content_image",
        fake_upload_content_image,
    )
    monkeypatch.setattr(
        "utils.official_wechat_draft._upload_cover_material",
        fake_upload_cover_material,
    )

    payload, thumb_media_id, replacements = await official_draft_module._build_recruitment_article_payload(
        "token",
        _make_row("https://example.com/qr-relative"),
    )

    assert thumb_media_id == "thumb-1"
    assert "<p>正文</p>" in payload["content"]
    assert uploaded_url in payload["content"]
    assert uploaded_sources == ["static/official_wx_card_qr.jpg"]
    assert replacements == [
        {
            "source": "static/official_wx_card_qr.jpg",
            "uploaded": uploaded_url,
        }
    ]


@pytest.mark.asyncio
async def test_build_payload_rejects_empty_content_even_if_card_exists(monkeypatch):
    import utils.official_wechat_draft as official_draft_module

    monkeypatch.setenv("OFFICIAL_WX_CARD_ENABLED", "true")
    monkeypatch.setenv("OFFICIAL_WX_CARD_QR_URL", "static/official_wx_card_qr.jpg")

    async def fake_upload_cover_material(access_token, cover_url):
        return "thumb-1"

    async def fake_rewrite_content_images(access_token, content_html):
        return (
            '<section class="official-account-card"><img src="https://weixin.example/qr.jpg" /></section>',
            [{"source": "static/official_wx_card_qr.jpg", "uploaded": "https://weixin.example/qr.jpg"}],
        )

    monkeypatch.setattr(
        "utils.official_wechat_draft._upload_cover_material",
        fake_upload_cover_material,
    )
    monkeypatch.setattr(
        "utils.official_wechat_draft._rewrite_content_images",
        fake_rewrite_content_images,
    )

    row = _make_row("https://example.com/empty-content")
    row["content"] = ""
    row["plain_content"] = ""

    with pytest.raises(official_draft_module.OfficialWechatDraftError, match="正文"):
        await official_draft_module._build_recruitment_article_payload("token", row)


@pytest.mark.asyncio
async def test_build_payload_opens_comments_by_default(monkeypatch):
    import utils.official_wechat_draft as official_draft_module

    async def fake_upload_cover_material(access_token, cover_url):
        return "thumb-1"

    async def fake_rewrite_content_images(access_token, content_html):
        return content_html, []

    monkeypatch.delenv("OFFICIAL_WX_OPEN_COMMENT", raising=False)
    monkeypatch.delenv("OFFICIAL_WX_ONLY_FANS_CAN_COMMENT", raising=False)
    monkeypatch.setenv("OFFICIAL_WX_CARD_ENABLED", "false")
    monkeypatch.setattr(
        "utils.official_wechat_draft._upload_cover_material",
        fake_upload_cover_material,
    )
    monkeypatch.setattr(
        "utils.official_wechat_draft._rewrite_content_images",
        fake_rewrite_content_images,
    )

    payload, thumb_media_id, replacements = await official_draft_module._build_recruitment_article_payload(
        "token",
        _make_row("https://example.com/comments-default"),
    )

    assert thumb_media_id == "thumb-1"
    assert replacements == []
    assert payload["need_open_comment"] == 1
    assert payload["only_fans_can_comment"] == 0


@pytest.mark.asyncio
async def test_build_payload_allows_fans_only_comments_by_env(monkeypatch):
    import utils.official_wechat_draft as official_draft_module

    async def fake_upload_cover_material(access_token, cover_url):
        return "thumb-1"

    async def fake_rewrite_content_images(access_token, content_html):
        return content_html, []

    monkeypatch.setenv("OFFICIAL_WX_OPEN_COMMENT", "true")
    monkeypatch.setenv("OFFICIAL_WX_ONLY_FANS_CAN_COMMENT", "true")
    monkeypatch.setenv("OFFICIAL_WX_CARD_ENABLED", "false")
    monkeypatch.setattr(
        "utils.official_wechat_draft._upload_cover_material",
        fake_upload_cover_material,
    )
    monkeypatch.setattr(
        "utils.official_wechat_draft._rewrite_content_images",
        fake_rewrite_content_images,
    )

    payload, _, _ = await official_draft_module._build_recruitment_article_payload(
        "token",
        _make_row("https://example.com/comments-fans-only"),
    )

    assert payload["need_open_comment"] == 1
    assert payload["only_fans_can_comment"] == 1


def test_preview_payload_opens_comments_by_default(monkeypatch):
    import utils.official_wx_preview as preview_module

    monkeypatch.delenv("OFFICIAL_WX_OPEN_COMMENT", raising=False)
    monkeypatch.delenv("OFFICIAL_WX_ONLY_FANS_CAN_COMMENT", raising=False)

    preview = preview_module._build_preview_article(_make_row("https://example.com/preview-default"))
    article = preview["official_draft_payload"]["articles"][0]

    assert article["need_open_comment"] == 1
    assert article["only_fans_can_comment"] == 0


def test_preview_payload_respects_comment_env(monkeypatch):
    import utils.official_wx_preview as preview_module

    monkeypatch.setenv("OFFICIAL_WX_OPEN_COMMENT", "false")
    monkeypatch.setenv("OFFICIAL_WX_ONLY_FANS_CAN_COMMENT", "true")

    preview = preview_module._build_preview_article(_make_row("https://example.com/preview-env"))
    article = preview["official_draft_payload"]["articles"][0]

    assert article["need_open_comment"] == 0
    assert article["only_fans_can_comment"] == 0


@pytest.mark.asyncio
async def test_push_article_row_to_draft_hydrates_missing_content_before_payload(monkeypatch):
    import utils.official_wechat_draft as official_draft_module

    async def fake_get_token():
        return "token"

    async def fake_ensure_article_content_ready(row):
        hydrated = dict(row)
        hydrated["content"] = "<p>补全后的正文</p>"
        hydrated["plain_content"] = "补全后的正文"
        return hydrated

    async def fake_build_payload(access_token, row):
        assert row["content"] == "<p>补全后的正文</p>"
        assert row["plain_content"] == "补全后的正文"
        return ({"title": row["title"], "content": row["content"]}, "thumb-1", [])

    async def fake_add_draft(access_token, articles):
        assert articles == [{"title": "测试文章", "content": "<p>补全后的正文</p>"}]
        return "draft-1"

    monkeypatch.setattr("utils.official_wechat_draft._get_stable_access_token", fake_get_token)
    monkeypatch.setattr(
        "utils.official_wechat_draft._ensure_article_content_ready",
        fake_ensure_article_content_ready,
    )
    monkeypatch.setattr(
        "utils.official_wechat_draft._build_recruitment_article_payload",
        fake_build_payload,
    )
    monkeypatch.setattr("utils.official_wechat_draft._add_draft", fake_add_draft)

    row = _make_row("https://example.com/hydrate")
    row["content"] = ""
    row["plain_content"] = ""

    result = await push_article_row_to_draft(row, save_record=False)

    assert result["draft_media_id"] == "draft-1"


@pytest.mark.asyncio
async def test_shared_series_first_push_creates_batch_and_second_push_appends(
    temp_rss_db,
    monkeypatch,
):
    article_counts: dict[str, int] = {}
    media_ids = iter(["draft-1", "draft-2"])

    async def fake_get_token():
        return "token"

    async def fake_build_payload(access_token, row):
        return ({"title": row["title"], "content": row["content"]}, "thumb-1", [])

    async def fake_add_draft(access_token, articles):
        media_id = next(media_ids)
        article_counts[media_id] = len(articles)
        if len(articles) == 2:
            assert articles[0]["thumb_media_id"] == "thumb-existing-1"
        return media_id

    async def fake_get_draft(access_token, media_id):
        count = article_counts[media_id]
        return {
            "news_item": [
                {
                    "title": f"item-{index}",
                    "content": "<p>existing</p>",
                    "content_source_url": f"https://example.com/existing-{index}",
                    "thumb_media_id": "",
                    "thumb_url": f"https://example.com/existing-{index}.jpg",
                }
                for index in range(count)
            ]
        }

    async def fake_update_draft_article(access_token, media_id, index, article):
        raise official_draft_module.OfficialWechatDraftError("invalid index value")

    async def fake_upload_cover_material(access_token, cover_url):
        return "thumb-existing-1"

    import utils.official_wechat_draft as official_draft_module

    monkeypatch.setattr("utils.official_wechat_draft._get_stable_access_token", fake_get_token)
    monkeypatch.setattr(
        "utils.official_wechat_draft._build_recruitment_article_payload",
        fake_build_payload,
    )
    monkeypatch.setattr("utils.official_wechat_draft._add_draft", fake_add_draft)
    monkeypatch.setattr("utils.official_wechat_draft._get_draft", fake_get_draft)
    monkeypatch.setattr(
        "utils.official_wechat_draft._update_draft_article",
        fake_update_draft_article,
    )
    monkeypatch.setattr(
        "utils.official_wechat_draft._upload_cover_material",
        fake_upload_cover_material,
    )

    first = await push_article_row_to_shared_draft_series(
        _make_row("https://example.com/a"),
        source_type="recruitment",
    )
    second = await push_article_row_to_shared_draft_series(
        _make_row("https://example.com/b"),
        source_type="crawler_review",
    )

    series = rss_store.get_official_draft_series("default")
    first_record = rss_store.get_official_draft_record("https://example.com/a")
    second_record = rss_store.get_official_draft_record("https://example.com/b")

    assert first["append_mode"] == "draft/add"
    assert first["batch_index"] == 1
    assert second["append_mode"] == "draft/add_rebuild"
    assert second["draft_media_id"] == "draft-2"
    assert second["article_count_after"] == 2
    assert series["current_media_id"] == "draft-2"
    assert series["current_article_count"] == 2
    assert series["batch_index"] == 1
    assert first_record["source_type"] == "recruitment"
    assert first_record["series_batch_index"] == 1
    assert second_record["source_type"] == "crawler_review"
    assert second_record["series_batch_index"] == 1


@pytest.mark.asyncio
async def test_shared_series_rotates_when_current_batch_is_full(temp_rss_db, monkeypatch):
    rss_store.update_official_draft_series(
        series_key="default",
        current_media_id="draft-full",
        current_article_count=8,
        batch_index=1,
    )

    async def fake_get_token():
        return "token"

    async def fake_build_payload(access_token, row):
        return ({"title": row["title"], "content": row["content"]}, "thumb-1", [])

    async def fake_get_draft(access_token, media_id):
        assert media_id == "draft-full"
        return {"news_item": [{} for _ in range(8)]}

    async def fake_add_draft(access_token, articles):
        assert len(articles) == 1
        return "draft-2"

    monkeypatch.setattr("utils.official_wechat_draft._get_stable_access_token", fake_get_token)
    monkeypatch.setattr(
        "utils.official_wechat_draft._build_recruitment_article_payload",
        fake_build_payload,
    )
    monkeypatch.setattr("utils.official_wechat_draft._get_draft", fake_get_draft)
    monkeypatch.setattr("utils.official_wechat_draft._add_draft", fake_add_draft)

    result = await push_article_row_to_shared_draft_series(
        _make_row("https://example.com/rotate"),
        source_type="crawler_review",
    )

    series = rss_store.get_official_draft_series("default")
    assert result["append_mode"] == "rotated_new_batch"
    assert result["draft_media_id"] == "draft-2"
    assert result["batch_index"] == 2
    assert series["current_media_id"] == "draft-2"
    assert series["current_article_count"] == 1
    assert series["batch_index"] == 2


@pytest.mark.asyncio
async def test_shared_series_skips_duplicate_by_default_and_allows_force(
    temp_rss_db,
    monkeypatch,
):
    draft_ids = iter(["draft-1", "draft-2"])

    async def fake_get_token():
        return "token"

    async def fake_build_payload(access_token, row):
        return ({"title": row["title"], "content": row["content"]}, "thumb-1", [])

    async def fake_add_draft(access_token, articles):
        return next(draft_ids)

    monkeypatch.setattr("utils.official_wechat_draft._get_stable_access_token", fake_get_token)
    monkeypatch.setattr(
        "utils.official_wechat_draft._build_recruitment_article_payload",
        fake_build_payload,
    )
    monkeypatch.setattr("utils.official_wechat_draft._add_draft", fake_add_draft)

    first = await push_article_row_to_shared_draft_series(
        _make_row("https://example.com/dup"),
        source_type="recruitment",
    )
    skipped = await push_article_row_to_shared_draft_series(
        _make_row("https://example.com/dup", title="重复文章"),
        source_type="crawler_review",
    )
    forced = await push_article_row_to_shared_draft_series(
        _make_row("https://example.com/dup", title="重复文章"),
        source_type="crawler_review",
        force=True,
    )

    assert first["draft_media_id"] == "draft-1"
    assert skipped["status"] == "skipped_duplicate"
    assert skipped["skipped"] is True
    assert forced["draft_media_id"] == "draft-2"


@pytest.mark.asyncio
async def test_shared_series_rebuild_refreshes_current_media_id(temp_rss_db, monkeypatch):
    rss_store.update_official_draft_series(
        series_key="default",
        current_media_id="draft-1",
        current_article_count=1,
        batch_index=1,
    )

    async def fake_get_token():
        return "token"

    async def fake_build_payload(access_token, row):
        return ({"title": row["title"], "content": row["content"]}, "thumb-1", [])

    async def fake_get_draft(access_token, media_id):
        return {
            "news_item": [
                {
                    "title": "existing",
                    "content": "<p>existing</p>",
                    "content_source_url": "https://example.com/existing",
                    "thumb_media_id": "",
                    "thumb_url": "https://example.com/existing.jpg",
                }
            ]
        }

    async def fake_update_draft_article(access_token, media_id, index, article):
        raise official_draft_module.OfficialWechatDraftError("draft/update failed")

    async def fake_add_draft(access_token, articles):
        assert len(articles) == 2
        return "draft-2"

    async def fake_delete_draft(access_token, media_id):
        return None

    async def fake_upload_cover_material(access_token, cover_url):
        return "thumb-existing-1"

    import utils.official_wechat_draft as official_draft_module

    monkeypatch.setattr("utils.official_wechat_draft._get_stable_access_token", fake_get_token)
    monkeypatch.setattr(
        "utils.official_wechat_draft._build_recruitment_article_payload",
        fake_build_payload,
    )
    monkeypatch.setattr("utils.official_wechat_draft._get_draft", fake_get_draft)
    monkeypatch.setattr(
        "utils.official_wechat_draft._update_draft_article",
        fake_update_draft_article,
    )
    monkeypatch.setattr("utils.official_wechat_draft._add_draft", fake_add_draft)
    monkeypatch.setattr("utils.official_wechat_draft._delete_draft", fake_delete_draft)
    monkeypatch.setattr(
        "utils.official_wechat_draft._upload_cover_material",
        fake_upload_cover_material,
    )

    result = await push_article_row_to_shared_draft_series(
        _make_row("https://example.com/rebuild"),
        source_type="crawler_review",
    )

    series = rss_store.get_official_draft_series("default")
    assert result["append_mode"] == "draft/add_rebuild"
    assert result["draft_media_id"] == "draft-2"
    assert result["previous_draft_media_id"] == "draft-1"
    assert series["current_media_id"] == "draft-2"
    assert series["current_article_count"] == 2


@pytest.mark.asyncio
async def test_recruitment_shared_series_all_pushes_all_unpushed_rows(monkeypatch):
    rows = [
        _make_row(f"https://example.com/batch-{index}", title=f"文章{index}")
        for index in range(1, 11)
    ]
    list_calls = []
    pushed_urls = []

    def fake_get_recruitment_articles(status="confirmed", limit=100, push_status="all"):
        list_calls.append((status, limit, push_status))
        return rows

    async def fake_push_article_row_to_shared_draft_series(row, *, source_type, force, series_key):
        pushed_urls.append(row["link"])
        idx = len(pushed_urls)
        if idx == 1:
            return {
                "success": True,
                "status": "pushed",
                "append_mode": "draft/add",
                "draft_media_id": "draft-1",
                "article_count_before": 0,
                "article_count_after": 1,
                "batch_index": 1,
                "source_url": row["link"],
                "title": row["title"],
            }
        if idx <= 8:
            return {
                "success": True,
                "status": "appended",
                "append_mode": "draft/update",
                "draft_media_id": "draft-1",
                "article_count_before": idx - 1,
                "article_count_after": idx,
                "batch_index": 1,
                "source_url": row["link"],
                "title": row["title"],
            }
        if idx == 9:
            return {
                "success": True,
                "status": "rotated_new_batch",
                "append_mode": "rotated_new_batch",
                "draft_media_id": "draft-2",
                "previous_draft_media_id": "draft-1",
                "article_count_before": 8,
                "article_count_after": 1,
                "batch_index": 2,
                "source_url": row["link"],
                "title": row["title"],
            }
        return {
            "success": True,
            "status": "appended",
            "append_mode": "draft/update",
            "draft_media_id": "draft-2",
            "article_count_before": 1,
            "article_count_after": 2,
            "batch_index": 2,
            "source_url": row["link"],
            "title": row["title"],
        }

    monkeypatch.setattr(
        "utils.official_wechat_draft.rss_store.get_recruitment_articles",
        fake_get_recruitment_articles,
    )
    monkeypatch.setattr(
        "utils.official_wechat_draft.push_article_row_to_shared_draft_series",
        fake_push_article_row_to_shared_draft_series,
    )

    result = await push_recruitment_article_to_shared_draft_series(
        force=False,
        push_all=True,
        limit=25,
    )

    assert list_calls == [("confirmed", 25, "unpushed")]
    assert pushed_urls == [row["link"] for row in rows]
    assert result["mode"] == "batch"
    assert result["total_candidates"] == 10
    assert result["processed_count"] == 10
    assert result["created_draft_count"] == 2
    assert result["draft_media_ids"] == ["draft-1", "draft-2"]
    assert result["status_counts"] == {
        "pushed": 1,
        "appended": 8,
        "rotated_new_batch": 1,
    }
    assert len(result["results"]) == 10


@pytest.mark.asyncio
async def test_recruitment_shared_series_all_rejects_source_url(monkeypatch):
    import utils.official_wechat_draft as official_draft_module

    with pytest.raises(official_draft_module.OfficialWechatDraftError, match="source_url"):
        await push_recruitment_article_to_shared_draft_series(
            source_url="https://example.com/one",
            push_all=True,
        )


def test_crawler_summary_path_pushes_ready_items_and_continues_on_error(tmp_path, monkeypatch, capsys):
    review_a = tmp_path / "review_a"
    review_b = tmp_path / "review_b"
    review_c = tmp_path / "review_c"
    for path in (review_a, review_b, review_c):
        path.mkdir()

    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "results": [
                    {"review_dir": str(review_a)},
                    {"review_dir": str(review_b)},
                    {"review_dir": ""},
                    {"review_dir": str(review_c)},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    calls = []

    async def fake_push_review_package_to_shared_draft_series(review_dir, force=False):
        calls.append((Path(review_dir).name, force))
        if Path(review_dir).name == "review_b":
            raise RuntimeError("boom")
        return {
            "status": "pushed" if Path(review_dir).name == "review_a" else "rotated_new_batch",
            "draft_media_id": "draft-1",
            "batch_index": 1,
        }

    monkeypatch.setattr(
        "push_wechat_review_to_official_draft.push_review_package_to_shared_draft_series",
        fake_push_review_package_to_shared_draft_series,
    )

    exit_code = crawler_push_main(["--summary-path", str(summary_path), "--force"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert calls == [("review_a", True), ("review_b", True), ("review_c", True)]
    assert "pushed" in output
    assert "rotated_new_batch" in output
    assert "error" in output


def test_crawler_explicit_draft_media_id_uses_legacy_append(tmp_path, monkeypatch):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    called = {}

    async def fake_append_review_package_to_draft(review_dir, draft_media_id):
        called["review_dir"] = str(review_dir)
        called["draft_media_id"] = draft_media_id
        return {"draft_media_id": draft_media_id, "append_mode": "draft/update"}

    monkeypatch.setattr(
        "push_wechat_review_to_official_draft.append_review_package_to_draft",
        fake_append_review_package_to_draft,
    )

    exit_code = crawler_push_main(
        ["--review-dir", str(review_dir), "--draft-media-id", "manual-draft"]
    )

    assert exit_code == 0
    assert called == {
        "review_dir": str(review_dir),
        "draft_media_id": "manual-draft",
    }


def test_crawler_summary_path_warns_when_explicit_path_is_not_latest(tmp_path, monkeypatch, capsys):
    older = tmp_path / "details" / "20260327_120000"
    newer = tmp_path / "details" / "20260328_120000"
    review_dir = tmp_path / "wechat_review" / "review"
    review_dir.mkdir(parents=True)
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    older_summary = older / "summary.json"
    newer_summary = newer / "summary.json"
    older_summary.write_text(
        json.dumps({"results": [{"review_dir": str(review_dir)}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    newer_summary.write_text(json.dumps({"results": []}, ensure_ascii=False), encoding="utf-8")

    async def fake_push_review_package_to_shared_draft_series(review_dir, force=False):
        return {"status": "pushed", "draft_media_id": "draft-1", "batch_index": 1}

    monkeypatch.setattr(
        "push_wechat_review_to_official_draft.push_review_package_to_shared_draft_series",
        fake_push_review_package_to_shared_draft_series,
    )
    monkeypatch.setattr(
        "push_wechat_review_to_official_draft.find_latest_summary_path",
        lambda root=None: newer_summary,
    )

    exit_code = crawler_push_main(["--summary-path", str(older_summary)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "warning | summary_path_is_not_latest" in output
    assert str(older_summary) in output
    assert str(newer_summary) in output


def test_publish_latest_batch_auto_picks_latest_summary(tmp_path):
    older = tmp_path / "details" / "20260327_120000"
    newer = tmp_path / "details" / "20260328_120000"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / "summary.json").write_text("{}", encoding="utf-8")
    (newer / "summary.json").write_text("{}", encoding="utf-8")

    from publish_latest_wechat_batch import find_latest_summary_path

    assert find_latest_summary_path(older.parent) == newer / "summary.json"


def test_publish_latest_batch_processes_attachments_before_push(tmp_path, monkeypatch, capsys):
    summary_dir = tmp_path / "details" / "20260328_120000"
    review_a = tmp_path / "wechat_review" / "batch_1" / "review_a"
    review_b = tmp_path / "wechat_review" / "batch_1" / "review_b"
    review_a.mkdir(parents=True)
    review_b.mkdir(parents=True)

    summary_path = summary_dir / "summary.json"
    summary_dir.mkdir(parents=True)
    summary_path.write_text(
        json.dumps(
            {
                "results": [
                    {"review_dir": str(review_a)},
                    {"review_dir": str(review_b)},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    import publish_latest_wechat_batch as publish_module

    upload_calls = []
    push_calls = []

    monkeypatch.setattr(
        publish_module,
        "find_latest_summary_path",
        lambda root=None: summary_path,
    )
    monkeypatch.setattr(
        publish_module,
        "build_local_attachments_from_review_dir",
        lambda review_dir: ["attachment"] if Path(review_dir).name == "review_a" else [],
    )

    def fake_process_review_dir_attachments(review_dir, **kwargs):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        upload_calls.append(Path(review_dir).name)
        return {"failed_count": 0, "success_count": 1}

    async def fake_push_review_package_to_shared_draft_series(review_dir, force=False):
        push_calls.append((Path(review_dir).name, force))
        return {"status": "pushed", "draft_media_id": "draft-1", "batch_index": 1}

    monkeypatch.setattr(publish_module, "process_review_dir_attachments", fake_process_review_dir_attachments)
    monkeypatch.setattr(
        publish_module,
        "push_review_package_to_shared_draft_series",
        fake_push_review_package_to_shared_draft_series,
    )

    exit_code = publish_module.main([])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert upload_calls == ["review_a"]
    assert push_calls == [("review_a", False), ("review_b", False)]
    assert "using summary" in output


def test_publish_latest_batch_warns_when_explicit_summary_is_not_latest(tmp_path, monkeypatch, capsys):
    older = tmp_path / "details" / "20260327_120000"
    newer = tmp_path / "details" / "20260328_120000"
    review_dir = tmp_path / "wechat_review" / "batch_1" / "review_a"
    review_dir.mkdir(parents=True)
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    older_summary = older / "summary.json"
    newer_summary = newer / "summary.json"
    older_summary.write_text(
        json.dumps({"results": [{"review_dir": str(review_dir)}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    newer_summary.write_text(json.dumps({"results": []}, ensure_ascii=False), encoding="utf-8")

    import publish_latest_wechat_batch as publish_module

    async def fake_push_review_package_to_shared_draft_series(review_dir, force=False):
        return {"status": "pushed", "draft_media_id": "draft-1", "batch_index": 1}

    monkeypatch.setattr(
        publish_module,
        "push_review_package_to_shared_draft_series",
        fake_push_review_package_to_shared_draft_series,
    )
    monkeypatch.setattr(
        publish_module,
        "build_local_attachments_from_review_dir",
        lambda review_dir: [],
    )
    monkeypatch.setattr(
        publish_module,
        "find_latest_summary_path",
        lambda root=None: newer_summary,
    )

    exit_code = publish_module.main(["--summary-path", str(older_summary)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "warning | summary_path_is_not_latest" in output
    assert str(older_summary) in output
    assert str(newer_summary) in output


def test_publish_latest_batch_falls_back_when_push_runtime_dependency_missing(tmp_path, monkeypatch):
    review_dir = tmp_path / "wechat_review" / "review_a"
    review_dir.mkdir(parents=True)

    import publish_latest_wechat_batch as publish_module

    async def fake_push_impl(review_dir, force=False):
        raise ModuleNotFoundError("No module named 'httpx'")

    async def fake_fallback(review_dir, force=False):
        return {
            "status": "pushed",
            "draft_media_id": "draft-fallback",
            "review_dir": str(review_dir),
        }

    monkeypatch.setattr(
        publish_module,
        "push_review_package_to_shared_draft_series",
        fake_push_impl,
    )
    monkeypatch.setattr(publish_module, "_push_via_fallback_cli", fake_fallback)

    result = asyncio.run(publish_module.push_review_dir(review_dir=review_dir, force=True))

    assert result["draft_media_id"] == "draft-fallback"
