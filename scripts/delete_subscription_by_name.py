#!/usr/bin/env python3

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_DB_PATH = ROOT_DIR / "data" / "rss.db"


def normalize_name(value):
    value = (value or "").strip()
    value = re.sub(r"\s+", "", value)
    value = value.replace("\u200b", "")
    return value


def get_subscriptions(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT fakeid, nickname, alias, created_at FROM subscriptions ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def choose_subscription(target, subscriptions):
    if not subscriptions:
        return None

    target_norm = normalize_name(target)

    for sub in subscriptions:
        if normalize_name(sub.get("fakeid", "")) == target_norm:
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

    return None


def delete_subscription_and_articles(db_path, fakeid):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        article_links = [
            row[0]
            for row in conn.execute("SELECT link FROM articles WHERE fakeid=?", (fakeid,)).fetchall()
            if row[0]
        ]
        if article_links:
            conn.executemany(
                "DELETE FROM official_preview_sync WHERE source_link=?",
                [(link,) for link in article_links],
            )
            conn.executemany(
                "DELETE FROM official_draft_sync WHERE source_link=?",
                [(link,) for link in article_links],
            )

        sub_row = conn.execute(
            "SELECT fakeid FROM subscriptions WHERE fakeid=?",
            (fakeid,),
        ).fetchone()
        if not sub_row:
            conn.rollback()
            return False, 0, 0

        article_count = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE fakeid=?",
            (fakeid,),
        ).fetchone()[0]

        conn.execute("DELETE FROM subscriptions WHERE fakeid=?", (fakeid,))
        conn.commit()
        return True, article_count, len(article_links)
    finally:
        conn.close()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Delete a subscribed WeChat account by name and remove its cached articles."
    )
    parser.add_argument("name", help="公众号名称")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite rss.db path")
    parser.add_argument("--max-candidates", type=int, default=8, help="How many ambiguous candidates to print")
    return parser.parse_args()


def print_candidates(candidates, max_candidates):
    print("找到多个相似订阅，请改用更精确的名称：")
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


def main():
    args = parse_args()
    db_path = Path(args.db_path).expanduser().resolve()
    subscriptions = get_subscriptions(db_path)
    account = choose_subscription(args.name, subscriptions)

    if not account:
        target_norm = normalize_name(args.name)
        candidates = [
            sub for sub in subscriptions
            if target_norm and (
                target_norm in normalize_name(sub.get("nickname", "")) or
                normalize_name(sub.get("nickname", "")) in target_norm or
                target_norm in normalize_name(sub.get("alias", "")) or
                normalize_name(sub.get("alias", "")) in target_norm
            )
        ]
        if len(candidates) > 1:
            print_candidates(candidates, args.max_candidates)
            return 2
        if not candidates:
            raise RuntimeError("未找到已订阅公众号: {}".format(args.name))
        account = candidates[0]

    fakeid = account.get("fakeid", "")
    nickname = account.get("nickname", "")

    removed, article_count, article_link_count = delete_subscription_and_articles(db_path, fakeid)
    if not removed:
        print("未找到该订阅: {} ({})".format(nickname, fakeid))
        return 1

    print("已删除订阅: {} ({})".format(nickname, fakeid))
    print("已删除文章数: {}".format(article_count))
    print("已清理同步记录: {}".format(article_link_count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
