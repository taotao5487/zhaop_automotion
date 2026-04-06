#!/usr/bin/env python3

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts import subscribe_and_poll_from_article_url


DEFAULT_REMOTE_BASE_URL = "http://8.137.176.176:5001"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prompt for a WeChat article URL, then subscribe and priority-poll on the remote server."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_REMOTE_BASE_URL,
        help="Remote API base URL, default: http://8.137.176.176:5001",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="How many recent articles to print",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    article_url = input("请输入微信文章链接: ").strip()
    if not article_url:
        print("未输入链接，程序已退出。")
        return 1

    runner_args = argparse.Namespace(
        article_url=article_url,
        name=None,
        fakeid=None,
        base_url=args.base_url,
        limit=args.limit,
    )
    return subscribe_and_poll_from_article_url.run_async(
        subscribe_and_poll_from_article_url.async_main(runner_args)
    )


if __name__ == "__main__":
    raise SystemExit(main())
