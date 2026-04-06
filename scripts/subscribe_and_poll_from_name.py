#!/usr/bin/env python3

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.subscribe_and_poll_from_article_url import (
    DEFAULT_BASE_URL,
    DEFAULT_DB_PATH,
    choose_account,
    choose_subscription,
    format_publish_time,
    get_recent_articles,
    get_subscriptions,
    normalize_name,
    priority_mark_subscription,
    search_accounts,
    subscribe_account,
    trigger_poll,
)


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
