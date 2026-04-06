#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
RSS 数据存储 — SQLite
管理订阅列表、文章缓存、招聘导出与官方草稿预览状态。
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

from shared.paths import DATA_DIR
from wechat_service.utils.recruitment_filter import recruitment_filter

logger = logging.getLogger(__name__)

_default_db = DATA_DIR / "rss.db"
DB_PATH = Path(os.getenv("RSS_DB_PATH", str(_default_db)))
DEFAULT_OFFICIAL_DRAFT_SERIES_KEY = "default"
DEFAULT_RECRUITMENT_PUSH_RECENT_DAYS = 7


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _get_scheduler_values() -> tuple[int, int]:
    min_interval = int(os.getenv("RSS_MIN_ACCOUNT_POLL_INTERVAL", "43200"))
    jitter = int(os.getenv("RSS_POLL_JITTER_SECONDS", "1800"))
    return min_interval, jitter


def get_recruitment_push_recent_days() -> int:
    raw = (os.getenv("RECRUITMENT_PUSH_RECENT_DAYS", str(DEFAULT_RECRUITMENT_PUSH_RECENT_DAYS)) or "").strip()
    try:
        return max(int(raw), 0)
    except ValueError:
        return DEFAULT_RECRUITMENT_PUSH_RECENT_DAYS


def get_recruitment_push_since_subscription() -> bool:
    raw = (os.getenv("RECRUITMENT_PUSH_SINCE_SUBSCRIPTION", "true") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def compute_initial_next_poll(now: Optional[int] = None) -> int:
    now = int(time.time()) if now is None else int(now)
    min_interval, _ = _get_scheduler_values()
    return now + random.randint(0, max(min_interval, 1))


def compute_success_next_poll(now: Optional[int] = None) -> int:
    now = int(time.time()) if now is None else int(now)
    min_interval, jitter = _get_scheduler_values()
    return now + min_interval + random.randint(0, max(jitter, 0))


def compute_retry_next_poll(delay_seconds: int, now: Optional[int] = None) -> int:
    now = int(time.time()) if now is None else int(now)
    return now + max(int(delay_seconds), 0)


def init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            fakeid      TEXT PRIMARY KEY,
            nickname    TEXT NOT NULL DEFAULT '',
            alias       TEXT NOT NULL DEFAULT '',
            head_img    TEXT NOT NULL DEFAULT '',
            created_at  INTEGER NOT NULL,
            last_poll   INTEGER NOT NULL DEFAULT 0,
            next_poll_at INTEGER NOT NULL DEFAULT 0,
            consecutive_failures INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fakeid      TEXT NOT NULL,
            aid         TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL DEFAULT '',
            link        TEXT NOT NULL DEFAULT '',
            digest      TEXT NOT NULL DEFAULT '',
            cover       TEXT NOT NULL DEFAULT '',
            author      TEXT NOT NULL DEFAULT '',
            content     TEXT NOT NULL DEFAULT '',
            plain_content TEXT NOT NULL DEFAULT '',
            publish_time INTEGER NOT NULL DEFAULT 0,
            fetched_at  INTEGER NOT NULL,
            is_recruitment INTEGER NOT NULL DEFAULT 0,
            review_status TEXT NOT NULL DEFAULT '',
            matched_keywords TEXT NOT NULL DEFAULT '[]',
            filter_stage TEXT NOT NULL DEFAULT '',
            images_json TEXT NOT NULL DEFAULT '[]',
            UNIQUE(fakeid, link),
            FOREIGN KEY (fakeid) REFERENCES subscriptions(fakeid) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS official_preview_sync (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_link TEXT NOT NULL UNIQUE,
            article_title TEXT NOT NULL DEFAULT '',
            fakeid      TEXT NOT NULL DEFAULT '',
            review_status TEXT NOT NULL DEFAULT '',
            preview_status TEXT NOT NULL DEFAULT '',
            preview_generated_at INTEGER NOT NULL DEFAULT 0,
            csv_exported_at INTEGER NOT NULL DEFAULT 0,
            blocked_reasons TEXT NOT NULL DEFAULT '[]',
            manifest_path TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS official_draft_sync (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_link TEXT NOT NULL UNIQUE,
            article_title TEXT NOT NULL DEFAULT '',
            fakeid      TEXT NOT NULL DEFAULT '',
            review_status TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            draft_status TEXT NOT NULL DEFAULT '',
            draft_media_id TEXT NOT NULL DEFAULT '',
            previous_draft_media_id TEXT NOT NULL DEFAULT '',
            append_mode TEXT NOT NULL DEFAULT '',
            series_batch_index INTEGER NOT NULL DEFAULT 0,
            pushed_at   INTEGER NOT NULL DEFAULT 0,
            updated_at  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS official_draft_series (
            series_key  TEXT PRIMARY KEY,
            current_media_id TEXT NOT NULL DEFAULT '',
            current_article_count INTEGER NOT NULL DEFAULT 0,
            batch_index INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT NOT NULL DEFAULT '',
            lease_expires_at INTEGER NOT NULL DEFAULT 0,
            updated_at  INTEGER NOT NULL DEFAULT 0
        );
    """)

    _ensure_column(conn, "subscriptions", "next_poll_at", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "subscriptions", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "articles", "is_recruitment", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "articles", "review_status", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "articles", "matched_keywords", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "articles", "filter_stage", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "articles", "images_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "official_preview_sync", "article_title", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_preview_sync", "fakeid", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_preview_sync", "review_status", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_preview_sync", "preview_status", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_preview_sync", "preview_generated_at", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "official_preview_sync", "csv_exported_at", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "official_preview_sync", "blocked_reasons", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "official_preview_sync", "manifest_path", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_draft_sync", "article_title", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_draft_sync", "fakeid", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_draft_sync", "review_status", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_draft_sync", "source_type", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_draft_sync", "draft_status", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_draft_sync", "draft_media_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_draft_sync", "previous_draft_media_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_draft_sync", "append_mode", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_draft_sync", "series_batch_index", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "official_draft_sync", "pushed_at", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "official_draft_sync", "updated_at", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "official_draft_series", "current_media_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_draft_series", "current_article_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "official_draft_series", "batch_index", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "official_draft_series", "lease_owner", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "official_draft_series", "lease_expires_at", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "official_draft_series", "updated_at", "INTEGER NOT NULL DEFAULT 0")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_articles_fakeid_time
            ON articles(fakeid, publish_time DESC);

        CREATE INDEX IF NOT EXISTS idx_subscriptions_next_poll
            ON subscriptions(next_poll_at ASC);

        CREATE INDEX IF NOT EXISTS idx_articles_review_status
            ON articles(review_status, publish_time DESC);

        CREATE INDEX IF NOT EXISTS idx_preview_status_generated
            ON official_preview_sync(preview_status, preview_generated_at DESC);

        CREATE INDEX IF NOT EXISTS idx_official_draft_status_pushed
            ON official_draft_sync(draft_status, pushed_at DESC);

        CREATE INDEX IF NOT EXISTS idx_official_draft_source_type
            ON official_draft_sync(source_type, pushed_at DESC);
    """)
    _ensure_official_draft_series_row(conn, DEFAULT_OFFICIAL_DRAFT_SERIES_KEY)
    _initialize_existing_subscription_schedule(conn)

    conn.commit()
    conn.close()
    logger.info("RSS database initialized: %s", DB_PATH)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str):
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_official_draft_series_row(conn: sqlite3.Connection, series_key: str):
    now = int(time.time())
    conn.execute(
        "INSERT OR IGNORE INTO official_draft_series "
        "(series_key, current_media_id, current_article_count, batch_index, lease_owner, lease_expires_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (series_key, "", 0, 0, "", 0, now),
    )


def _initialize_existing_subscription_schedule(conn: sqlite3.Connection):
    rows = conn.execute(
        "SELECT fakeid FROM subscriptions WHERE next_poll_at IS NULL OR next_poll_at <= 0"
    ).fetchall()
    if not rows:
        return

    now = int(time.time())
    for row in rows:
        conn.execute(
            "UPDATE subscriptions SET next_poll_at=?, consecutive_failures=0 WHERE fakeid=?",
            (compute_initial_next_poll(now), row["fakeid"]),
        )


def parse_json_list(value) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:
        pass
    return []


def _json_dumps(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value or [], ensure_ascii=False)


def _should_finalize_backfill(article: Dict) -> bool:
    if (article.get("plain_content") or "").strip():
        return True
    if (article.get("content") or "").strip():
        return True
    images = article.get("images")
    if images:
        return True
    return bool(parse_json_list(article.get("images_json")))


def _backfill_recruitment_metadata(
    conn: sqlite3.Connection,
    *,
    source_link: Optional[str] = None,
    limit: Optional[int] = 500,
) -> int:
    query = (
        "SELECT * FROM articles "
        "WHERE review_status = '' "
        "AND (title != '' OR digest != '' OR plain_content != '' OR content != '' OR images_json != '[]')"
    )
    params: List[object] = []

    if source_link:
        query += " AND link = ?"
        params.append(source_link)

    query += " ORDER BY publish_time DESC, id DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, tuple(params)).fetchall()
    updates: List[tuple[object, ...]] = []

    for row in rows:
        original = dict(row)
        updated, _ = recruitment_filter.prepare_article(dict(original))
        if updated.get("filter_stage") == "coarse_matched" and _should_finalize_backfill(updated):
            updated = recruitment_filter.finalize_article(updated)

        changed = any(
            updated.get(field) != original.get(field)
            for field in ("is_recruitment", "review_status", "matched_keywords", "filter_stage")
        )
        if not changed:
            continue

        updates.append(
            (
                int(updated.get("is_recruitment", 0)),
                updated.get("review_status", ""),
                _json_dumps(updated.get("matched_keywords", "[]")),
                updated.get("filter_stage", ""),
                int(original["id"]),
            )
        )

    if updates:
        conn.executemany(
            "UPDATE articles SET is_recruitment=?, review_status=?, matched_keywords=?, filter_stage=? "
            "WHERE id=?",
            updates,
        )
        conn.commit()

    return len(updates)


def add_subscription(fakeid: str, nickname: str = "",
                     alias: str = "", head_img: str = "") -> bool:
    conn = _get_conn()
    try:
        now = int(time.time())
        conn.execute(
            "INSERT OR IGNORE INTO subscriptions "
            "(fakeid, nickname, alias, head_img, created_at, next_poll_at, consecutive_failures) "
            "VALUES (?,?,?,?,?,?,0)",
            (fakeid, nickname, alias, head_img, now, compute_initial_next_poll(now)),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def remove_subscription(fakeid: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM subscriptions WHERE fakeid=?", (fakeid,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def list_subscriptions() -> List[Dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT s.*, "
            "(SELECT COUNT(*) FROM articles a WHERE a.fakeid=s.fakeid) AS article_count "
            "FROM subscriptions s ORDER BY s.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_subscription(fakeid: str) -> Optional[Dict]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE fakeid=?", (fakeid,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_due_subscriptions(limit: int = 1, now: Optional[int] = None) -> List[Dict]:
    now = int(time.time()) if now is None else int(now)
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM subscriptions "
            "WHERE next_poll_at <= ? "
            "ORDER BY next_poll_at ASC, created_at ASC "
            "LIMIT ?",
            (now, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def mark_poll_success(fakeid: str, polled_at: Optional[int] = None):
    polled_at = int(time.time()) if polled_at is None else int(polled_at)
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE subscriptions "
            "SET last_poll=?, next_poll_at=?, consecutive_failures=0 "
            "WHERE fakeid=?",
            (polled_at, compute_success_next_poll(polled_at), fakeid),
        )
        conn.commit()
    finally:
        conn.close()


def mark_poll_failure(fakeid: str, delay_seconds: int, polled_at: Optional[int] = None):
    polled_at = int(time.time()) if polled_at is None else int(polled_at)
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE subscriptions "
            "SET last_poll=?, next_poll_at=?, consecutive_failures=consecutive_failures+1 "
            "WHERE fakeid=?",
            (polled_at, compute_retry_next_poll(delay_seconds, polled_at), fakeid),
        )
        conn.commit()
    finally:
        conn.close()


def update_last_poll(fakeid: str):
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE subscriptions SET last_poll=? WHERE fakeid=?",
            (int(time.time()), fakeid),
        )
        conn.commit()
    finally:
        conn.close()


def mark_subscription_priority(fakeid: str, priority_at: Optional[int] = None) -> bool:
    priority_at = int(priority_at if priority_at is not None else time.time())
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE subscriptions SET next_poll_at=? WHERE fakeid=?",
            (-abs(priority_at), fakeid),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def save_articles(fakeid: str, articles: List[Dict]) -> int:
    conn = _get_conn()
    inserted = 0
    try:
        for a in articles:
            link = a.get("link", "")
            existed = False
            if link:
                existed = conn.execute(
                    "SELECT 1 FROM articles WHERE fakeid=? AND link=?",
                    (fakeid, link),
                ).fetchone() is not None

            conn.execute(
                "INSERT INTO articles "
                "(fakeid, aid, title, link, digest, cover, author, content, plain_content, "
                "publish_time, fetched_at, is_recruitment, review_status, matched_keywords, "
                "filter_stage, images_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(fakeid, link) DO UPDATE SET "
                "aid=excluded.aid, "
                "title=excluded.title, "
                "digest=excluded.digest, "
                "cover=excluded.cover, "
                "author=CASE WHEN excluded.author != '' THEN excluded.author ELSE articles.author END, "
                "content=CASE WHEN excluded.content != '' THEN excluded.content ELSE articles.content END, "
                "plain_content=CASE WHEN excluded.plain_content != '' THEN excluded.plain_content ELSE articles.plain_content END, "
                "publish_time=CASE WHEN excluded.publish_time != 0 THEN excluded.publish_time ELSE articles.publish_time END, "
                "fetched_at=excluded.fetched_at, "
                "is_recruitment=CASE WHEN excluded.is_recruitment != 0 THEN excluded.is_recruitment ELSE articles.is_recruitment END, "
                "review_status=CASE WHEN excluded.review_status != '' THEN excluded.review_status ELSE articles.review_status END, "
                "matched_keywords=CASE WHEN excluded.matched_keywords != '[]' THEN excluded.matched_keywords ELSE articles.matched_keywords END, "
                "filter_stage=CASE WHEN excluded.filter_stage != '' THEN excluded.filter_stage ELSE articles.filter_stage END, "
                "images_json=CASE WHEN excluded.images_json != '[]' THEN excluded.images_json ELSE articles.images_json END",
                (
                    fakeid,
                    a.get("aid", ""),
                    a.get("title", ""),
                    link,
                    a.get("digest", ""),
                    a.get("cover", ""),
                    a.get("author", ""),
                    a.get("content", ""),
                    a.get("plain_content", ""),
                    a.get("publish_time", 0),
                    int(time.time()),
                    int(a.get("is_recruitment", 0)),
                    a.get("review_status", ""),
                    _json_dumps(a.get("matched_keywords", "[]")),
                    a.get("filter_stage", ""),
                    _json_dumps(a.get("images_json", a.get("images", []))),
                ),
            )
            if not existed:
                inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def get_articles(fakeid: str, limit: int = 20) -> List[Dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM articles WHERE fakeid=? "
            "ORDER BY publish_time DESC LIMIT ?",
            (fakeid, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_fakeids() -> List[str]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT fakeid FROM subscriptions").fetchall()
        return [r["fakeid"] for r in rows]
    finally:
        conn.close()


def get_all_articles(limit: int = 50) -> List[Dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM articles ORDER BY publish_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_recruitment_articles(
    status: str = "confirmed",
    limit: int = 100,
    push_status: str = "all",
    recent_days: Optional[int] = None,
    since_subscription: bool = False,
) -> List[Dict]:
    conn = _get_conn()
    try:
        _backfill_recruitment_metadata(conn)

        if status == "all":
            where_clauses = ["a.review_status IN ('confirmed', 'manual_review', 'rejected')"]
            params: List[object] = []
        else:
            where_clauses = ["a.review_status = ?"]
            params = [status]

        if push_status == "pushed":
            where_clauses.append("d.source_link IS NOT NULL")
        elif push_status == "unpushed":
            where_clauses.append("d.source_link IS NULL")

        if since_subscription:
            where_clauses.append("a.publish_time >= s.created_at")

        if recent_days is not None and int(recent_days) > 0:
            cutoff = int(time.time()) - int(recent_days) * 86400
            where_clauses.append("a.publish_time >= ?")
            params.append(cutoff)

        params.append(limit)
        where_clause = " AND ".join(where_clauses)

        rows = conn.execute(
            "SELECT a.*, s.nickname, "
            "d.draft_status AS official_draft_status, "
            "d.draft_media_id AS official_draft_media_id, "
            "d.previous_draft_media_id AS official_draft_previous_media_id, "
            "d.append_mode AS official_draft_append_mode, "
            "d.pushed_at AS official_draft_pushed_at "
            "FROM articles a "
            "LEFT JOIN subscriptions s ON s.fakeid = a.fakeid "
            "LEFT JOIN official_draft_sync d ON d.source_link = a.link "
            f"WHERE {where_clause} "
            "ORDER BY a.publish_time DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_recruitment_article_by_link(source_link: str) -> Optional[Dict]:
    conn = _get_conn()
    try:
        _backfill_recruitment_metadata(conn, source_link=source_link, limit=None)

        row = conn.execute(
            "SELECT a.*, s.nickname, "
            "d.draft_status AS official_draft_status, "
            "d.draft_media_id AS official_draft_media_id, "
            "d.previous_draft_media_id AS official_draft_previous_media_id, "
            "d.append_mode AS official_draft_append_mode, "
            "d.pushed_at AS official_draft_pushed_at "
            "FROM articles a "
            "LEFT JOIN subscriptions s ON s.fakeid = a.fakeid "
            "LEFT JOIN official_draft_sync d ON d.source_link = a.link "
            "WHERE a.link = ? AND a.review_status = 'confirmed' "
            "ORDER BY a.publish_time DESC LIMIT 1",
            (source_link,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_official_draft_record(source_link: str) -> Optional[Dict]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM official_draft_sync WHERE source_link = ? LIMIT 1",
            (source_link,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_official_draft_record(record: Dict):
    source_link = (record.get("source_link") or "").strip()
    if not source_link:
        return

    conn = _get_conn()
    try:
        now = int(record.get("updated_at", 0) or time.time())
        pushed_at = int(record.get("pushed_at", 0) or now)
        conn.execute(
            "INSERT INTO official_draft_sync "
            "(source_link, article_title, fakeid, review_status, source_type, draft_status, "
            "draft_media_id, previous_draft_media_id, append_mode, series_batch_index, pushed_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source_link) DO UPDATE SET "
            "article_title=excluded.article_title, "
            "fakeid=excluded.fakeid, "
            "review_status=excluded.review_status, "
            "source_type=excluded.source_type, "
            "draft_status=excluded.draft_status, "
            "draft_media_id=excluded.draft_media_id, "
            "previous_draft_media_id=excluded.previous_draft_media_id, "
            "append_mode=excluded.append_mode, "
            "series_batch_index=excluded.series_batch_index, "
            "pushed_at=excluded.pushed_at, "
            "updated_at=excluded.updated_at",
            (
                source_link,
                record.get("article_title", ""),
                record.get("fakeid", ""),
                record.get("review_status", ""),
                record.get("source_type", ""),
                record.get("draft_status", ""),
                record.get("draft_media_id", ""),
                record.get("previous_draft_media_id", ""),
                record.get("append_mode", ""),
                int(record.get("series_batch_index", 0) or 0),
                pushed_at,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_official_draft_series(
    series_key: str = DEFAULT_OFFICIAL_DRAFT_SERIES_KEY,
) -> Dict:
    conn = _get_conn()
    try:
        _ensure_official_draft_series_row(conn, series_key)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM official_draft_series WHERE series_key = ? LIMIT 1",
            (series_key,),
        ).fetchone()
        return dict(row) if row else {
            "series_key": series_key,
            "current_media_id": "",
            "current_article_count": 0,
            "batch_index": 0,
            "lease_owner": "",
            "lease_expires_at": 0,
            "updated_at": 0,
        }
    finally:
        conn.close()


def update_official_draft_series(
    *,
    series_key: str = DEFAULT_OFFICIAL_DRAFT_SERIES_KEY,
    current_media_id: Optional[str] = None,
    current_article_count: Optional[int] = None,
    batch_index: Optional[int] = None,
    lease_owner: Optional[str] = None,
    lease_expires_at: Optional[int] = None,
    updated_at: Optional[int] = None,
) -> Dict:
    conn = _get_conn()
    try:
        _ensure_official_draft_series_row(conn, series_key)
        row = conn.execute(
            "SELECT * FROM official_draft_series WHERE series_key = ? LIMIT 1",
            (series_key,),
        ).fetchone()
        current = dict(row) if row else {}
        now = int(updated_at if updated_at is not None else time.time())
        payload = {
            "current_media_id": (
                current_media_id
                if current_media_id is not None
                else current.get("current_media_id", "")
            ),
            "current_article_count": int(
                current_article_count
                if current_article_count is not None
                else current.get("current_article_count", 0)
            ),
            "batch_index": int(
                batch_index if batch_index is not None else current.get("batch_index", 0)
            ),
            "lease_owner": (
                lease_owner if lease_owner is not None else current.get("lease_owner", "")
            ),
            "lease_expires_at": int(
                lease_expires_at
                if lease_expires_at is not None
                else current.get("lease_expires_at", 0)
            ),
            "updated_at": now,
        }
        conn.execute(
            "UPDATE official_draft_series SET "
            "current_media_id=?, current_article_count=?, batch_index=?, "
            "lease_owner=?, lease_expires_at=?, updated_at=? "
            "WHERE series_key=?",
            (
                payload["current_media_id"],
                payload["current_article_count"],
                payload["batch_index"],
                payload["lease_owner"],
                payload["lease_expires_at"],
                payload["updated_at"],
                series_key,
            ),
        )
        conn.commit()
        return get_official_draft_series(series_key)
    finally:
        conn.close()


def acquire_official_draft_series_lease(
    *,
    series_key: str = DEFAULT_OFFICIAL_DRAFT_SERIES_KEY,
    lease_owner: str,
    lease_ttl_seconds: int = 120,
    now: Optional[int] = None,
) -> bool:
    normalized_owner = (lease_owner or "").strip()
    if not normalized_owner:
        return False

    current_ts = int(time.time()) if now is None else int(now)
    expires_at = current_ts + max(int(lease_ttl_seconds), 1)

    conn = _get_conn()
    try:
        _ensure_official_draft_series_row(conn, series_key)
        result = conn.execute(
            "UPDATE official_draft_series SET lease_owner=?, lease_expires_at=?, updated_at=? "
            "WHERE series_key=? AND (lease_owner='' OR lease_owner=? OR lease_expires_at<=?)",
            (
                normalized_owner,
                expires_at,
                current_ts,
                series_key,
                normalized_owner,
                current_ts,
            ),
        )
        conn.commit()
        return result.rowcount > 0
    finally:
        conn.close()


def release_official_draft_series_lease(
    series_key: str = DEFAULT_OFFICIAL_DRAFT_SERIES_KEY,
    lease_owner: str = "",
) -> bool:
    normalized_owner = (lease_owner or "").strip()
    if not normalized_owner:
        return False

    conn = _get_conn()
    try:
        _ensure_official_draft_series_row(conn, series_key)
        result = conn.execute(
            "UPDATE official_draft_series SET lease_owner='', lease_expires_at=0, updated_at=? "
            "WHERE series_key=? AND lease_owner=?",
            (int(time.time()), series_key, normalized_owner),
        )
        conn.commit()
        return result.rowcount > 0
    finally:
        conn.close()


def get_pending_official_preview_articles(limit: int = 100) -> List[Dict]:
    conn = _get_conn()
    try:
        _backfill_recruitment_metadata(conn)

        rows = conn.execute(
            "SELECT a.*, s.nickname, p.preview_status, p.manifest_path "
            "FROM articles a "
            "LEFT JOIN subscriptions s ON s.fakeid = a.fakeid "
            "LEFT JOIN official_preview_sync p ON p.source_link = a.link "
            "WHERE a.review_status = 'confirmed' "
            "AND a.link != '' "
            "AND (p.source_link IS NULL OR p.preview_status != 'generated') "
            "ORDER BY a.publish_time ASC, a.id ASC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def save_official_preview_records(records: List[Dict]):
    if not records:
        return

    conn = _get_conn()
    try:
        for record in records:
            conn.execute(
                "INSERT INTO official_preview_sync "
                "(source_link, article_title, fakeid, review_status, preview_status, "
                "preview_generated_at, csv_exported_at, blocked_reasons, manifest_path) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source_link) DO UPDATE SET "
                "article_title=excluded.article_title, "
                "fakeid=excluded.fakeid, "
                "review_status=excluded.review_status, "
                "preview_status=excluded.preview_status, "
                "preview_generated_at=excluded.preview_generated_at, "
                "csv_exported_at=excluded.csv_exported_at, "
                "blocked_reasons=excluded.blocked_reasons, "
                "manifest_path=excluded.manifest_path",
                (
                    record.get("source_link", ""),
                    record.get("article_title", ""),
                    record.get("fakeid", ""),
                    record.get("review_status", ""),
                    record.get("preview_status", ""),
                    int(record.get("preview_generated_at", 0)),
                    int(record.get("csv_exported_at", 0)),
                    _json_dumps(record.get("blocked_reasons", [])),
                    record.get("manifest_path", ""),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_recent_official_preview_records(limit: int = 100) -> List[Dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM official_preview_sync "
            "WHERE preview_generated_at > 0 "
            "ORDER BY preview_generated_at DESC, id DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
