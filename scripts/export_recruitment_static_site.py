#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "rss.db"
DEFAULT_JOBS_DB_PATH = PROJECT_ROOT / "data" / "jobs.db"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "site"
DEFAULT_QR_SOURCE = PROJECT_ROOT / "static" / "official_wx_card_qr.jpg"


def ensure_site_shell(output_dir: Path) -> None:
    required = (
        output_dir / "index.html",
        output_dir / "assets" / "site.css",
        output_dir / "assets" / "site.js",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "site shell is incomplete: missing " + ", ".join(missing)
        )


def query_recruitment_items(
    *,
    db_path: Path,
    days: int,
    now_ts: int | None = None,
) -> list[dict]:
    if now_ts is None:
        now_ts = int(datetime.now().timestamp())
    threshold = now_ts - (days * 86400)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            articles.title,
            articles.link,
            articles.publish_time,
            COALESCE(NULLIF(subscriptions.nickname, ''), articles.fakeid) AS source_name
        FROM articles
        LEFT JOIN subscriptions ON subscriptions.fakeid = articles.fakeid
        WHERE articles.is_recruitment = 1
          AND articles.review_status = 'confirmed'
          AND articles.link != ''
          AND articles.publish_time >= ?
        ORDER BY articles.publish_time DESC, articles.id DESC
        """,
        (threshold,),
    ).fetchall()
    conn.close()

    items: list[dict] = []
    for row in rows:
        publish_ts = int(row["publish_time"] or 0)
        items.append(
            {
                "title": row["title"],
                "url": row["link"],
                "source_name": row["source_name"] or "",
                "publish_date": datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d"),
                "publish_timestamp": publish_ts,
                "source_type": "rss",
            }
        )
    return items


def _parse_datetime_to_timestamp(value: str | None) -> int:
    if not value:
        return 0

    text = str(value).strip()
    if not text:
        return 0

    normalized = text.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return 0


def query_crawler_recruitment_items(
    *,
    db_path: Path,
    days: int,
    now_ts: int | None = None,
) -> list[dict]:
    if not db_path.exists():
        return []
    if now_ts is None:
        now_ts = int(datetime.now().timestamp())
    threshold = now_ts - (days * 86400)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            medical_jobs.title,
            medical_jobs.url,
            COALESCE(
                NULLIF(medical_jobs.publish_date, ''),
                NULLIF(medical_jobs.crawl_time, '')
            ) AS display_time,
            COALESCE(
                NULLIF(medical_jobs.hospital, ''),
                NULLIF(medical_jobs.source_site, ''),
                '未知来源'
            ) AS source_name
        FROM medical_jobs
        INNER JOIN review_decisions ON review_decisions.url = medical_jobs.url
        WHERE review_decisions.decision = 'keep'
          AND medical_jobs.url != ''
        ORDER BY COALESCE(medical_jobs.publish_date, medical_jobs.crawl_time) DESC, medical_jobs.id DESC
        """
    ).fetchall()
    conn.close()

    items: list[dict] = []
    for row in rows:
        publish_ts = _parse_datetime_to_timestamp(row["display_time"])
        if publish_ts and publish_ts < threshold:
            continue
        publish_date = (
            datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d")
            if publish_ts
            else ""
        )
        items.append(
            {
                "title": row["title"],
                "url": row["url"],
                "source_name": row["source_name"] or "未知来源",
                "publish_date": publish_date,
                "publish_timestamp": publish_ts,
                "source_type": "crawler",
            }
        )
    return items


def merge_recruitment_items(items: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in items:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        existing = merged.get(url)
        if existing is None:
            merged[url] = item
            continue

        existing_ts = int(existing.get("publish_timestamp") or 0)
        item_ts = int(item.get("publish_timestamp") or 0)
        if item_ts > existing_ts or (
            item_ts == existing_ts and item.get("source_type") == "rss"
        ):
            merged[url] = item

    return sorted(
        merged.values(),
        key=lambda item: int(item.get("publish_timestamp") or 0),
        reverse=True,
    )


def export_static_site(
    *,
    db_path: Path,
    jobs_db_path: Path,
    output_dir: Path,
    qr_source: Path,
    days: int,
    now_ts: int | None = None,
) -> dict:
    ensure_site_shell(output_dir)
    items = merge_recruitment_items(
        [
            *query_recruitment_items(db_path=db_path, days=days, now_ts=now_ts),
            *query_crawler_recruitment_items(
                db_path=jobs_db_path,
                days=days,
                now_ts=now_ts,
            ),
        ]
    )
    export_time = datetime.fromtimestamp(now_ts) if now_ts is not None else datetime.now()

    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": export_time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(items),
        "items": items,
    }
    (output_dir / "recruitment.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    shutil.copy2(qr_source, assets_dir / "official_wx_card_qr.jpg")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Export recruitment data into site/")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--jobs-db-path", type=Path, default=DEFAULT_JOBS_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--qr-source", type=Path, default=DEFAULT_QR_SOURCE)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--now-ts", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    payload = export_static_site(
        db_path=args.db_path,
        jobs_db_path=args.jobs_db_path,
        output_dir=args.output_dir,
        qr_source=args.qr_source,
        days=args.days,
        now_ts=args.now_ts,
    )
    print(
        f"Exported {payload['count']} recruitment item(s) to "
        f"{args.output_dir / 'recruitment.json'}"
    )
    print(f"Copied QR asset to {args.output_dir / 'assets' / 'official_wx_card_qr.jpg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
