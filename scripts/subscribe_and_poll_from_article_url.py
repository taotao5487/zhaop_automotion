#!/usr/bin/env python3

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib import parse, request

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from wechat_service.utils.helpers import extract_article_info, parse_article_url
from wechat_service.utils.http_client import fetch_page


DEFAULT_BASE_URL = "http://127.0.0.1:5001"
DEFAULT_DB_PATH = ROOT_DIR / "data" / "rss.db"


def normalize_name(value):
    value = (value or "").strip()
    value = re.sub(r"\s+", "", value)
    value = value.replace("\u200b", "")
    return value


def http_get_json(url, timeout=60):
    with request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url, payload, timeout=60):
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def extract_article_author(url):
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


def search_accounts(base_url, nickname):
    query = parse.quote(nickname)
    url = "{}/api/public/searchbiz?query={}".format(base_url.rstrip("/"), query)
    result = http_get_json(url)
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "searchbiz failed")
    return result.get("data", {}).get("list", []) or []


def choose_account(nickname, accounts):
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


def subscribe_account(base_url, account):
    payload = {
        "fakeid": account.get("fakeid", ""),
        "nickname": account.get("nickname", ""),
        "alias": account.get("alias", ""),
        "head_img": account.get("round_head_img", ""),
    }
    return http_post_json("{}/api/rss/subscribe".format(base_url.rstrip("/")), payload)


def priority_mark_subscription(fakeid, db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE subscriptions SET next_poll_at=? WHERE fakeid=?",
            (1, fakeid),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def trigger_poll(base_url):
    return http_post_json("{}/api/rss/poll".format(base_url.rstrip("/")), {})


def get_recent_articles(fakeid, db_path, limit):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT title, link, publish_time FROM articles WHERE fakeid=? ORDER BY publish_time DESC LIMIT ?",
            (fakeid, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def format_publish_time(timestamp):
    if not timestamp:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(timestamp)))


async def async_main(args):
    article = await extract_article_author(args.article_url)
    author = (article.get("author") or "").strip()
    title = (article.get("title") or "").strip()
    if not author:
        raise RuntimeError("无法从文章链接中提取公众号名称")

    accounts = search_accounts(args.base_url, author)
    account = choose_account(author, accounts)
    if not account:
        raise RuntimeError("未找到匹配公众号: {}".format(author))

    subscribe_result = subscribe_account(args.base_url, account)
    fakeid = account.get("fakeid", "")
    if not fakeid:
        raise RuntimeError("搜索结果中缺少 fakeid")

    if not priority_mark_subscription(fakeid, args.db_path):
        raise RuntimeError("未找到订阅记录，无法插队轮询: {}".format(fakeid))

    poll_result = trigger_poll(args.base_url)
    recent_articles = get_recent_articles(fakeid, args.db_path, args.limit)

    print("文章标题: {}".format(title or "未知标题"))
    print("公众号名称: {}".format(author))
    print("匹配公众号: {} ({})".format(account.get("nickname", ""), fakeid))
    print("订阅结果: {}".format(subscribe_result.get("message", "")))
    print("轮询结果: {}".format(poll_result.get("data", {}).get("message", "")))
    print("")
    print("最新文章:")
    if not recent_articles:
        print("- 暂未抓到文章")
        return 0

    for item in recent_articles:
        print("- [{}] {}".format(format_publish_time(item.get("publish_time", 0)), item.get("title", "")))
        print("  {}".format(item.get("link", "")))

    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Subscribe a WeChat account from one article URL and priority-poll it once.")
    parser.add_argument("article_url", help="WeChat article URL")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Local API base URL, default: http://127.0.0.1:5001")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite rss.db path")
    parser.add_argument("--limit", type=int, default=10, help="How many recent articles to print")
    return parser.parse_args()


def main():
    args = parse_args()
    args.db_path = Path(args.db_path).expanduser().resolve()
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
