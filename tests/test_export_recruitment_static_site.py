#!/usr/bin/env python3

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from scripts.export_recruitment_static_site import (  # noqa: E402
    export_static_site,
    query_recruitment_items,
)


def _seed_articles(db_path: Path) -> int:
    now_ts = int(datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc).timestamp())
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fakeid TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            link TEXT NOT NULL DEFAULT '',
            publish_time INTEGER NOT NULL DEFAULT 0,
            is_recruitment INTEGER NOT NULL DEFAULT 0,
            review_status TEXT NOT NULL DEFAULT ''
        );
        """
    )
    rows = [
        ("f1", "最新公开招聘公告", "https://example.com/a", now_ts - 3600, 1, "confirmed"),
        ("f1", "人才引进公告", "https://example.com/b", now_ts - 7200, 1, "confirmed"),
        ("f1", "待人工复核招聘", "https://example.com/c", now_ts - 7200, 1, "manual_review"),
        ("f1", "采购公告", "https://example.com/d", now_ts - 7200, 0, "confirmed"),
        ("f1", "超出 30 天窗口", "https://example.com/e", now_ts - (31 * 86400), 1, "confirmed"),
        ("f1", "缺少原文链接", "", now_ts - 1800, 1, "confirmed"),
    ]
    conn.executemany(
        """
        INSERT INTO articles (fakeid, title, link, publish_time, is_recruitment, review_status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    return now_ts


def _write_site_shell(output_dir: Path) -> None:
    (output_dir / "assets").mkdir(parents=True)
    (output_dir / "index.html").write_text("<!DOCTYPE html>", encoding="utf-8")
    (output_dir / "assets" / "site.css").write_text("body{}", encoding="utf-8")
    (output_dir / "assets" / "site.js").write_text("console.log('ok')", encoding="utf-8")


def test_query_recruitment_items_filters_and_sorts(tmp_path: Path):
    db_path = tmp_path / "rss.db"
    now_ts = _seed_articles(db_path)

    items = query_recruitment_items(db_path=db_path, days=30, now_ts=now_ts)

    assert [item["title"] for item in items] == ["最新公开招聘公告", "人才引进公告"]
    assert [item["publish_date"] for item in items] == ["2026-04-06", "2026-04-06"]
    assert all(item["url"].startswith("https://example.com/") for item in items)


def test_export_static_site_writes_payload_and_qr_copy(tmp_path: Path):
    db_path = tmp_path / "rss.db"
    now_ts = _seed_articles(db_path)
    output_dir = tmp_path / "site"
    _write_site_shell(output_dir)
    qr_source = tmp_path / "official_wx_card_qr.jpg"
    qr_source.write_bytes(b"fake-image")

    export_static_site(
        db_path=db_path,
        output_dir=output_dir,
        qr_source=qr_source,
        days=30,
        now_ts=now_ts,
    )

    payload = json.loads((output_dir / "recruitment.json").read_text(encoding="utf-8"))
    assert payload["count"] == 2
    assert payload["items"][0]["title"] == "最新公开招聘公告"
    assert payload["items"][1]["title"] == "人才引进公告"
    assert payload["generated_at"].startswith("2026-04-06")
    assert (output_dir / "assets" / "official_wx_card_qr.jpg").read_bytes() == b"fake-image"


def test_export_static_site_requires_static_shell_files(tmp_path: Path):
    db_path = tmp_path / "rss.db"
    now_ts = _seed_articles(db_path)
    output_dir = tmp_path / "site"
    qr_source = tmp_path / "official_wx_card_qr.jpg"
    qr_source.write_bytes(b"fake-image")

    try:
        export_static_site(
            db_path=db_path,
            output_dir=output_dir,
            qr_source=qr_source,
            days=30,
            now_ts=now_ts,
        )
    except FileNotFoundError as exc:
        assert "site shell" in str(exc)
    else:
        raise AssertionError("expected export_static_site to reject missing shell files")


def test_static_shell_files_include_mobile_and_modal_hooks():
    root = ROOT_DIR
    html = (root / "site" / "index.html").read_text(encoding="utf-8")
    css = (root / "site" / "assets" / "site.css").read_text(encoding="utf-8")
    js = (root / "site" / "assets" / "site.js").read_text(encoding="utf-8")

    assert 'id="searchInput"' in html
    assert 'id="resultsList"' in html
    assert 'id="readOriginalModal"' in html
    assert 'id="continueButton"' in html
    assert "fetch('./recruitment.json')" in js
    assert "pendingUrl" in js
    assert "@media (min-width: 768px)" in css
    assert "min-height: 48px;" in css
