#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from shared.paths import ROOT_DIR


DEFAULT_DB_PATH = ROOT_DIR / "data" / "rss.db"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "site"
DEFAULT_QR_SOURCE = ROOT_DIR / "static" / "official_wx_card_qr.jpg"


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
        SELECT title, link, publish_time
        FROM articles
        WHERE is_recruitment = 1
          AND review_status = 'confirmed'
          AND link != ''
          AND publish_time >= ?
        ORDER BY publish_time DESC, id DESC
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
                "publish_date": datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d"),
                "publish_timestamp": publish_ts,
            }
        )
    return items


def export_static_site(
    *,
    db_path: Path,
    output_dir: Path,
    qr_source: Path,
    days: int,
    now_ts: int | None = None,
) -> dict:
    items = query_recruitment_items(db_path=db_path, days=days, now_ts=now_ts)
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
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--qr-source", type=Path, default=DEFAULT_QR_SOURCE)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    payload = export_static_site(
        db_path=args.db_path,
        output_dir=args.output_dir,
        qr_source=args.qr_source,
        days=args.days,
    )
    print(
        f"Exported {payload['count']} recruitment item(s) to "
        f"{args.output_dir / 'recruitment.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
