#!/usr/bin/env python3

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from scripts.export_recruitment_static_site import (  # noqa: E402
    export_static_site,
    main,
    query_crawler_recruitment_items,
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
        CREATE TABLE subscriptions (
            fakeid TEXT PRIMARY KEY,
            nickname TEXT NOT NULL DEFAULT ''
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
    conn.executemany(
        "INSERT INTO subscriptions (fakeid, nickname) VALUES (?, ?)",
        [("f1", "测试医院公众号")],
    )
    conn.commit()
    conn.close()
    return now_ts


def _write_site_shell(output_dir: Path) -> None:
    (output_dir / "assets").mkdir(parents=True)
    (output_dir / "index.html").write_text("<!DOCTYPE html>", encoding="utf-8")
    (output_dir / "assets" / "site.css").write_text("body{}", encoding="utf-8")
    (output_dir / "assets" / "site.js").write_text("console.log('ok')", encoding="utf-8")


def _seed_crawler_jobs(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE medical_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title VARCHAR NOT NULL,
            url VARCHAR NOT NULL UNIQUE,
            publish_date DATETIME,
            hospital VARCHAR,
            location VARCHAR,
            source_site VARCHAR,
            crawl_time DATETIME,
            is_new BOOLEAN
        );
        CREATE TABLE review_decisions (
            url VARCHAR NOT NULL PRIMARY KEY,
            decision VARCHAR NOT NULL,
            decided_at DATETIME NOT NULL,
            last_seen_at DATETIME NOT NULL,
            source_site VARCHAR,
            title_snapshot VARCHAR,
            hospital_snapshot VARCHAR,
            publish_date_snapshot DATETIME
        );
        """
    )
    jobs = [
        (
            "爬虫审核通过招聘公告",
            "https://crawler.example.com/a",
            "2026-04-05 09:30:00",
            "测试医院",
            "重庆",
            "crawler_site",
            "2026-04-05 10:00:00",
            1,
        ),
        (
            "缺少发布日期但审核通过",
            "https://crawler.example.com/b",
            None,
            "",
            "重庆",
            "测试来源站",
            "2026-04-04 08:00:00",
            1,
        ),
        (
            "被丢弃的爬虫公告",
            "https://crawler.example.com/c",
            "2026-04-05 09:30:00",
            "测试医院",
            "重庆",
            "crawler_site",
            "2026-04-05 10:00:00",
            1,
        ),
        (
            "超出时间窗的爬虫公告",
            "https://crawler.example.com/d",
            "2026-02-01 09:30:00",
            "测试医院",
            "重庆",
            "crawler_site",
            "2026-02-01 10:00:00",
            1,
        ),
    ]
    conn.executemany(
        """
        INSERT INTO medical_jobs
            (title, url, publish_date, hospital, location, source_site, crawl_time, is_new)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        jobs,
    )
    decisions = [
        ("https://crawler.example.com/a", "keep"),
        ("https://crawler.example.com/b", "keep"),
        ("https://crawler.example.com/c", "discard"),
        ("https://crawler.example.com/d", "keep"),
    ]
    conn.executemany(
        """
        INSERT INTO review_decisions
            (url, decision, decided_at, last_seen_at, source_site, title_snapshot, hospital_snapshot, publish_date_snapshot)
        VALUES (?, ?, '2026-04-06 12:00:00', '2026-04-06 12:00:00', '', '', '', NULL)
        """,
        decisions,
    )
    conn.commit()
    conn.close()


def test_query_recruitment_items_filters_and_sorts(tmp_path: Path):
    db_path = tmp_path / "rss.db"
    now_ts = _seed_articles(db_path)

    items = query_recruitment_items(db_path=db_path, days=30, now_ts=now_ts)

    assert [item["title"] for item in items] == ["最新公开招聘公告", "人才引进公告"]
    assert [item["publish_date"] for item in items] == ["2026-04-06", "2026-04-06"]
    assert [item["source_name"] for item in items] == ["测试医院公众号", "测试医院公众号"]
    assert all(item["url"].startswith("https://example.com/") for item in items)
    assert all(item["source_type"] == "rss" for item in items)


def test_query_crawler_recruitment_items_uses_kept_review_decisions(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    _seed_crawler_jobs(db_path)
    now_ts = int(datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc).timestamp())

    items = query_crawler_recruitment_items(db_path=db_path, days=30, now_ts=now_ts)

    assert [item["title"] for item in items] == ["爬虫审核通过招聘公告", "缺少发布日期但审核通过"]
    assert items[0]["source_name"] == "测试医院"
    assert items[1]["source_name"] == "测试来源站"
    assert items[0]["publish_date"] == "2026-04-05"
    assert items[1]["publish_date"] == "2026-04-04"
    assert all(item["source_type"] == "crawler" for item in items)


def test_export_static_site_writes_payload_and_qr_copy(tmp_path: Path):
    db_path = tmp_path / "rss.db"
    jobs_db_path = tmp_path / "jobs.db"
    now_ts = _seed_articles(db_path)
    _seed_crawler_jobs(jobs_db_path)
    output_dir = tmp_path / "site"
    _write_site_shell(output_dir)
    qr_source = tmp_path / "official_wx_card_qr.jpg"
    qr_source.write_bytes(b"fake-image")

    export_static_site(
        db_path=db_path,
        jobs_db_path=jobs_db_path,
        output_dir=output_dir,
        qr_source=qr_source,
        days=30,
        now_ts=now_ts,
    )

    payload = json.loads((output_dir / "recruitment.json").read_text(encoding="utf-8"))
    assert payload["count"] == 4
    assert payload["items"][0]["title"] == "最新公开招聘公告"
    assert payload["items"][0]["source_name"] == "测试医院公众号"
    crawler_item = next(
        item for item in payload["items"] if item["title"] == "爬虫审核通过招聘公告"
    )
    assert crawler_item["source_name"] == "测试医院"
    assert crawler_item["source_type"] == "crawler"
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
            jobs_db_path=tmp_path / "missing-jobs.db",
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
    assert 'id="updateTime"' in html
    assert "最近 30 天已确认招聘公告" in html
    assert "点击阅读原文前会先提示关注公众号" not in html
    assert "输入医院、地区、人才引进等关键词" in html
    assert "输入职位、医院、人才引进等关键词" not in html
    assert '<script src="./assets/site.js?v=' in html
    assert "fetch('./recruitment.json', { cache: 'no-store' })" in js
    assert "cache: 'no-store'" in js
    assert "pendingUrl" in js
    assert "source_name" in js
    assert "generated_at" in js
    assert "更新时间：" in js
    assert "来源：" in js
    assert ".update-time" in css
    assert "font-size: 12px;" in css
    assert "@media (min-width: 768px)" in css
    assert "min-height: 48px;" in css


def test_export_static_site_returns_empty_payload_when_no_matches(tmp_path: Path):
    db_path = tmp_path / "rss.db"
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
        CREATE TABLE subscriptions (
            fakeid TEXT PRIMARY KEY,
            nickname TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.commit()
    conn.close()

    output_dir = tmp_path / "site"
    _write_site_shell(output_dir)
    qr_source = tmp_path / "official_wx_card_qr.jpg"
    qr_source.write_bytes(b"fake-image")

    payload = export_static_site(
        db_path=db_path,
        jobs_db_path=tmp_path / "missing-jobs.db",
        output_dir=output_dir,
        qr_source=qr_source,
        days=30,
        now_ts=int(datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc).timestamp()),
    )

    assert payload["count"] == 0
    assert payload["items"] == []


def test_main_prints_export_and_qr_paths(tmp_path: Path, monkeypatch, capsys):
    db_path = tmp_path / "rss.db"
    now_ts = _seed_articles(db_path)
    output_dir = tmp_path / "site"
    _write_site_shell(output_dir)
    qr_source = tmp_path / "official_wx_card_qr.jpg"
    qr_source.write_bytes(b"fake-image")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_recruitment_static_site.py",
            "--db-path",
            str(db_path),
            "--output-dir",
            str(output_dir),
            "--qr-source",
            str(qr_source),
            "--jobs-db-path",
            str(tmp_path / "missing-jobs.db"),
            "--days",
            "30",
            "--now-ts",
            str(now_ts),
        ],
    )

    result = main()

    captured = capsys.readouterr()
    assert result == 0
    assert "Exported 2 recruitment item(s)" in captured.out
    assert "Copied QR asset" in captured.out


def test_script_runs_directly_from_scripts_path(tmp_path: Path):
    db_path = tmp_path / "rss.db"
    _seed_articles(db_path)
    output_dir = tmp_path / "site"
    _write_site_shell(output_dir)
    qr_source = tmp_path / "official_wx_card_qr.jpg"
    qr_source.write_bytes(b"fake-image")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT_DIR / "scripts" / "export_recruitment_static_site.py"),
            "--db-path",
            str(db_path),
            "--output-dir",
            str(output_dir),
            "--qr-source",
            str(qr_source),
            "--jobs-db-path",
            str(tmp_path / "missing-jobs.db"),
        ],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Exported 2 recruitment item(s)" in result.stdout


def test_export_script_does_not_depend_on_shared_paths_for_cli_bootstrap():
    script = (ROOT_DIR / "scripts" / "export_recruitment_static_site.py").read_text(
        encoding="utf-8"
    )

    assert "from shared.paths import ROOT_DIR" not in script
