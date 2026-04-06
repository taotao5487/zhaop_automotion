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


def get_subscriptions(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT fakeid, nickname, alias FROM subscriptions ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def choose_subscription(target, subscriptions):
    if not subscriptions:
        return None

    target_norm = normalize_name(target)

    for sub in subscriptions:
        if (sub.get("fakeid") or "").strip() == target.strip():
            return sub

    for sub in subscriptions:
        nickname = normalize_name(sub.get("nickname", ""))
        alias = normalize_name(sub.get("alias", ""))
        if target_norm and (nickname == target_norm or alias == target_norm):
            return sub

    partial_matches = []
    for sub in subscriptions:
        nickname = normalize_name(sub.get("nickname", ""))
        alias = normalize_name(sub.get("alias", ""))
        if target_norm and (
            target_norm in nickname or nickname in target_norm or
            target_norm in alias or alias in target_norm
        ):
            partial_matches.append(sub)
    if len(partial_matches) == 1:
        return partial_matches[0]

    if len(subscriptions) == 1:
        return subscriptions[0]

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


def collect_subscription_candidates(target, subscriptions):
    target_norm = normalize_name(target)
    candidates = []
    for sub in subscriptions:
        nickname = normalize_name(sub.get("nickname", ""))
        alias = normalize_name(sub.get("alias", ""))
        if target_norm and (
            target_norm in nickname or
            nickname in target_norm or
            target_norm in alias or
            alias in target_norm
        ):
            candidates.append(sub)
    return candidates


def collect_account_candidates(target, accounts):
    target_norm = normalize_name(target)
    candidates = []
    for account in accounts:
        nickname = normalize_name(account.get("nickname", ""))
        alias = normalize_name(account.get("alias", ""))
        if target_norm and (
            target_norm in nickname or
            nickname in target_norm or
            target_norm in alias or
            alias in target_norm
        ):
            candidates.append(account)
    return candidates


def print_candidates(title, candidates, max_candidates):
    print(title)
    for index, item in enumerate(candidates[:max_candidates], start=1):
        print(
            "{idx}. {nickname} | alias={alias} | fakeid={fakeid}".format(
                idx=index,
                nickname=item.get("nickname", "") or "-",
                alias=item.get("alias", "") or "-",
                fakeid=item.get("fakeid", "") or "-",
            )
        )
    if len(candidates) > max_candidates:
        print("... 其余 {} 个候选未展示".format(len(candidates) - max_candidates))


async def async_main(args):
    subscribe_message = "已订阅，直接插队轮询"
    subscriptions = get_subscriptions(args.db_path)
    account = choose_subscription(args.name, subscriptions)

    if account:
        fakeid = account.get("fakeid", "")
        account_name = account.get("nickname", "")
    else:
        local_candidates = collect_subscription_candidates(args.name, subscriptions)
        if len(local_candidates) > 1:
            print_candidates("本地已订阅列表存在多个相似公众号，请改用更精确的名称：", local_candidates, args.max_candidates)
            return 2

        accounts = search_accounts(args.base_url, args.name)
        account = choose_account(args.name, accounts)
        if not account:
            if not accounts:
                raise RuntimeError("未找到匹配公众号: {}".format(args.name))
            candidates = collect_account_candidates(args.name, accounts) or accounts
            print_candidates("搜索结果存在多个候选，请改用更精确的名称：", candidates, args.max_candidates)
            return 2

        subscribe_result = subscribe_account(args.base_url, account)
        subscribe_message = subscribe_result.get("message", "") or "订阅请求已提交"
        fakeid = account.get("fakeid", "")
        account_name = account.get("nickname", "")
        if not fakeid:
            raise RuntimeError("搜索结果中缺少 fakeid")

    if not priority_mark_subscription(fakeid, args.db_path):
        raise RuntimeError("未找到订阅记录，无法插队轮询: {}".format(fakeid))

    poll_result = trigger_poll(args.base_url)
    recent_articles = get_recent_articles(fakeid, args.db_path, args.limit)

    print("公众号名称: {}".format(args.name))
    print("匹配公众号: {} ({})".format(account_name, fakeid))
    print("订阅结果: {}".format(subscribe_message))
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
    parser = argparse.ArgumentParser(
        description="Subscribe a WeChat account by name when needed, then priority-poll it once."
    )
    parser.add_argument("name", help="公众号名称")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Local API base URL, default: http://127.0.0.1:5001")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite rss.db path")
    parser.add_argument("--limit", type=int, default=10, help="How many recent articles to print")
    parser.add_argument("--max-candidates", type=int, default=8, help="How many ambiguous candidates to print")
    return parser.parse_args()


def main():
    args = parse_args()
    args.db_path = Path(args.db_path).expanduser().resolve()
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
