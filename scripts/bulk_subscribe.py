#!/usr/bin/env python3
"""
Batch import WeChat RSS subscriptions through the local API.

Supported input formats:
1. CSV with headers: fakeid,nickname,alias,head_img
2. JSON array with objects containing the same keys

Example:
  python scripts/bulk_subscribe.py subscriptions.csv
  python scripts/bulk_subscribe.py subscriptions.json --base-url http://localhost:5001
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Iterable
from urllib import error, request


REQUIRED_KEY = "fakeid"
OPTIONAL_KEYS = ("nickname", "alias", "head_img")


def load_rows(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return load_csv(path)
    if suffix == ".json":
        return load_json(path)
    raise ValueError(f"Unsupported file type: {path.suffix}")


def load_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or REQUIRED_KEY not in reader.fieldnames:
            raise ValueError("CSV must include a 'fakeid' header")
        rows = []
        for row in reader:
            rows.append(normalize_row(row))
        return rows


def load_json(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("JSON input must be a list")
    return [normalize_row(item) for item in data]


def normalize_row(row: dict) -> dict:
    fakeid = str(row.get("fakeid", "")).strip()
    if not fakeid:
        raise ValueError("Every row must include a non-empty fakeid")
    normalized = {"fakeid": fakeid}
    for key in OPTIONAL_KEYS:
        normalized[key] = str(row.get(key, "") or "").strip()
    return normalized


def dedupe_rows(rows: Iterable[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for row in rows:
        fakeid = row["fakeid"]
        if fakeid in seen:
            continue
        seen.add(fakeid)
        deduped.append(row)
    return deduped


def post_json(url: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch import RSS subscriptions")
    parser.add_argument("input_file", help="CSV or JSON file containing subscriptions")
    parser.add_argument(
        "--base-url",
        default="http://localhost:5001",
        help="API base URL, default: http://localhost:5001",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="Delay in seconds between requests, default: 0.2",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10,
        help="HTTP timeout in seconds, default: 10",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate input and print what would be imported without calling the API",
    )

    args = parser.parse_args()
    input_path = Path(args.input_file).expanduser().resolve()

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        rows = dedupe_rows(load_rows(input_path))
    except Exception as exc:
        print(f"Failed to parse input: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("No subscriptions found in input file", file=sys.stderr)
        return 1

    print(f"Loaded {len(rows)} unique subscriptions from {input_path}")

    if args.dry_run:
        for row in rows[:10]:
            print(json.dumps(row, ensure_ascii=False))
        if len(rows) > 10:
            print(f"... and {len(rows) - 10} more")
        return 0

    subscribe_url = args.base_url.rstrip("/") + "/api/rss/subscribe"

    success = 0
    failed = 0

    for index, row in enumerate(rows, start=1):
        label = row.get("nickname") or row["fakeid"]
        try:
            response = post_json(subscribe_url, row, timeout=args.timeout)
            if response.get("success"):
                success += 1
                print(f"[{index}/{len(rows)}] OK    {label} - {response.get('message', '')}")
            else:
                failed += 1
                print(f"[{index}/{len(rows)}] FAIL  {label} - {response}", file=sys.stderr)
        except error.HTTPError as exc:
            failed += 1
            detail = exc.read().decode("utf-8", errors="replace")
            print(f"[{index}/{len(rows)}] FAIL  {label} - HTTP {exc.code}: {detail}", file=sys.stderr)
        except Exception as exc:
            failed += 1
            print(f"[{index}/{len(rows)}] FAIL  {label} - {exc}", file=sys.stderr)

        if index < len(rows) and args.delay > 0:
            time.sleep(args.delay)

    print(f"Finished: success={success}, failed={failed}, total={len(rows)}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
