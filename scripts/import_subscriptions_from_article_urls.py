#!/usr/bin/env python3
"""
Import WeChat RSS subscriptions from a CSV of article URLs.

Workflow:
1. Read article URLs from a CSV file with a `URL` column.
2. Fetch each article page and extract the account nickname (author).
3. Search the logged-in WeChat backend for each nickname to get fakeid.
4. Subscribe the resolved accounts through the local API.
5. Save a JSON report with resolved and unresolved items.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib import parse, request

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from wechat_service.utils.helpers import extract_article_info, parse_article_url
from wechat_service.utils.http_client import fetch_page


def normalize_name(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"\s+", "", value)
    value = value.replace("\u200b", "")
    return value


def read_article_urls(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "URL" not in reader.fieldnames:
            raise ValueError("CSV must contain a 'URL' column")
        urls = []
        for row in reader:
            url = (row.get("URL") or "").strip()
            if url:
                urls.append(url)
    return urls


async def extract_author(url: str) -> dict[str, Any]:
    html = await fetch_page(
        url,
        extra_headers={"Referer": "https://mp.weixin.qq.com/"},
        timeout=120,
    )
    params = parse_article_url(url)
    article = extract_article_info(html, params)
    author = (article.get("author") or "").strip()
    title = (article.get("title") or "").strip()
    return {"url": url, "author": author, "title": title}


def http_get_json(url: str, timeout: float = 60) -> dict:
    with request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url: str, payload: dict, timeout: float = 60) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_account(base_url: str, nickname: str) -> dict[str, Any] | None:
    query = parse.quote(nickname)
    url = f"{base_url.rstrip('/')}/api/public/searchbiz?query={query}"
    result = http_get_json(url)
    if not result.get("success"):
        return None

    accounts = result.get("data", {}).get("list", []) or []
    if not accounts:
        return None

    nickname_norm = normalize_name(nickname)

    for account in accounts:
        if normalize_name(account.get("nickname", "")) == nickname_norm:
            return account

    partial_matches = []
    for account in accounts:
        account_name = normalize_name(account.get("nickname", ""))
        if nickname_norm and (nickname_norm in account_name or account_name in nickname_norm):
            partial_matches.append(account)
    if len(partial_matches) == 1:
        return partial_matches[0]

    if len(accounts) == 1:
        return accounts[0]

    return None


def subscribe_account(base_url: str, account: dict[str, Any]) -> dict:
    payload = {
        "fakeid": account.get("fakeid", ""),
        "nickname": account.get("nickname", ""),
        "alias": account.get("alias", ""),
        "head_img": account.get("round_head_img", ""),
    }
    return http_post_json(f"{base_url.rstrip('/')}/api/rss/subscribe", payload)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Import subscriptions from article URLs")
    parser.add_argument("input_csv", help="CSV file with a URL column")
    parser.add_argument(
        "--base-url",
        default="http://localhost:5001",
        help="Local API base URL, default: http://localhost:5001",
    )
    parser.add_argument(
        "--article-delay",
        type=float,
        default=1.2,
        help="Delay in seconds between article fetches, default: 1.2",
    )
    parser.add_argument(
        "--search-delay",
        type=float,
        default=0.6,
        help="Delay in seconds between search/subscribe requests, default: 0.6",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit for article URLs, 0 means all",
    )
    parser.add_argument(
        "--report",
        default="data/import_subscriptions_report.json",
        help="Where to write the JSON report",
    )
    args = parser.parse_args()

    input_path = Path(args.input_csv).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    urls = read_article_urls(input_path)
    if args.limit > 0:
        urls = urls[:args.limit]

    if not urls:
        print("No article URLs found", file=sys.stderr)
        return 1

    extracted: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    print(f"Processing {len(urls)} article URLs from {input_path}")
    for index, url in enumerate(urls, start=1):
        try:
            item = await extract_author(url)
            author = item["author"]
            if author:
                extracted.append(item)
                print(f"[article {index}/{len(urls)}] {author} <- {item['title'][:50]}")
            else:
                failures.append({"url": url, "error": "empty author"})
                print(f"[article {index}/{len(urls)}] empty author", file=sys.stderr)
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})
            print(f"[article {index}/{len(urls)}] FAIL {url} - {exc}", file=sys.stderr)

        if index < len(urls) and args.article_delay > 0:
            await asyncio.sleep(args.article_delay)

    author_counter = Counter(item["author"] for item in extracted if item["author"])
    unique_authors = list(author_counter.keys())
    print(f"Resolved {len(unique_authors)} unique account names from article URLs")

    resolved_accounts: list[dict[str, Any]] = []
    unresolved_accounts: list[dict[str, Any]] = []

    for index, author in enumerate(unique_authors, start=1):
        try:
            account = search_account(args.base_url, author)
            if account:
                resolved_accounts.append({
                    "author": author,
                    "article_count": author_counter[author],
                    "account": account,
                })
                print(f"[search {index}/{len(unique_authors)}] OK   {author} -> {account.get('fakeid', '')}")
            else:
                unresolved_accounts.append({
                    "author": author,
                    "article_count": author_counter[author],
                })
                print(f"[search {index}/{len(unique_authors)}] MISS {author}", file=sys.stderr)
        except Exception as exc:
            unresolved_accounts.append({
                "author": author,
                "article_count": author_counter[author],
                "error": str(exc),
            })
            print(f"[search {index}/{len(unique_authors)}] FAIL {author} - {exc}", file=sys.stderr)

        if index < len(unique_authors) and args.search_delay > 0:
            time.sleep(args.search_delay)

    subscribed: list[dict[str, Any]] = []
    subscribe_failures: list[dict[str, Any]] = []

    for index, item in enumerate(resolved_accounts, start=1):
        account = item["account"]
        author = item["author"]
        try:
            result = subscribe_account(args.base_url, account)
            if result.get("success"):
                subscribed.append({
                    "author": author,
                    "fakeid": account.get("fakeid", ""),
                    "nickname": account.get("nickname", ""),
                    "message": result.get("message", ""),
                    "article_count": item["article_count"],
                })
                print(f"[subscribe {index}/{len(resolved_accounts)}] OK   {author} - {result.get('message', '')}")
            else:
                subscribe_failures.append({
                    "author": author,
                    "account": account,
                    "result": result,
                })
                print(f"[subscribe {index}/{len(resolved_accounts)}] FAIL {author} - {result}", file=sys.stderr)
        except Exception as exc:
            subscribe_failures.append({
                "author": author,
                "account": account,
                "error": str(exc),
            })
            print(f"[subscribe {index}/{len(resolved_accounts)}] FAIL {author} - {exc}", file=sys.stderr)

        if index < len(resolved_accounts) and args.search_delay > 0:
            time.sleep(args.search_delay)

    report = {
        "input_csv": str(input_path),
        "processed_urls": len(urls),
        "article_failures": failures,
        "unique_authors": len(unique_authors),
        "resolved_accounts": resolved_accounts,
        "unresolved_accounts": unresolved_accounts,
        "subscribed": subscribed,
        "subscribe_failures": subscribe_failures,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "Finished: "
        f"processed_urls={len(urls)}, "
        f"unique_authors={len(unique_authors)}, "
        f"resolved={len(resolved_accounts)}, "
        f"subscribed={len(subscribed)}, "
        f"unresolved={len(unresolved_accounts)}, "
        f"article_failures={len(failures)}, "
        f"subscribe_failures={len(subscribe_failures)}"
    )
    print(f"Report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
