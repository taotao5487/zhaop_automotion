#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
微信官方草稿预览任务
测试阶段只生成本地 CSV/JSON 预览包，不调用微信官方写接口。
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from shared.paths import DATA_DIR, ROOT_DIR
from wechat_service.utils import rss_store
from wechat_service.utils.official_wechat_draft import get_comment_settings_for_article_payload

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = DATA_DIR / "official_wx_preview"
DEFAULT_TICK_SECONDS = 300
DEFAULT_BATCH_LIMIT = 100
PREVIEW_ONLY_MODE = "preview_only"

EXTERNAL_IMAGE_BLOCK_REASON = "正文中仍含外部图片 URL，尚未替换为微信图文内图片 URL"
MISSING_THUMB_BLOCK_REASON = "缺少封面 thumb_media_id"


def get_preview_mode() -> str:
    return (os.getenv("OFFICIAL_WX_MODE", PREVIEW_ONLY_MODE) or PREVIEW_ONLY_MODE).strip()


def get_preview_tick_seconds() -> int:
    try:
        return max(int(os.getenv("OFFICIAL_WX_PREVIEW_TICK_SECONDS", str(DEFAULT_TICK_SECONDS))), 30)
    except ValueError:
        return DEFAULT_TICK_SECONDS


def get_preview_output_dir() -> Path:
    raw = (os.getenv("OFFICIAL_WX_OUTPUT_DIR", "") or "").strip()
    path = Path(raw).expanduser() if raw else DEFAULT_OUTPUT_DIR
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _format_publish_time(timestamp: int) -> str:
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _build_digest(row: Dict) -> str:
    digest = (row.get("digest") or "").strip()
    if digest:
        return digest

    plain_content = (row.get("plain_content") or "").strip()
    plain_content = re.sub(r"\s+", " ", plain_content)
    if not plain_content:
        return ""
    return plain_content[:54]


def _has_external_images(row: Dict) -> bool:
    images = rss_store.parse_json_list(row.get("images_json"))
    if images:
        return True

    content = row.get("content") or ""
    return bool(re.search(r"<img[^>]+src=[\"']https?://", content, flags=re.IGNORECASE))


def _build_blocked_reasons(row: Dict) -> List[str]:
    blocked_reasons = [MISSING_THUMB_BLOCK_REASON]
    if _has_external_images(row):
        blocked_reasons.append(EXTERNAL_IMAGE_BLOCK_REASON)
    return blocked_reasons


def _build_preview_article(row: Dict) -> Dict:
    title = (row.get("title") or "").strip()
    author = (row.get("author") or "").strip()
    digest = _build_digest(row)
    source_url = (row.get("link") or "").strip()
    account_name = (row.get("nickname") or row.get("fakeid") or "").strip()
    images = rss_store.parse_json_list(row.get("images_json"))
    blocked_reasons = _build_blocked_reasons(row)
    need_open_comment, only_fans_can_comment = get_comment_settings_for_article_payload()

    return {
        "account_name": account_name,
        "fakeid": row.get("fakeid", ""),
        "publish_time": row.get("publish_time", 0),
        "publish_time_str": _format_publish_time(row.get("publish_time", 0)),
        "title": title,
        "author": author,
        "digest": digest,
        "source_url": source_url,
        "cover_url": row.get("cover", ""),
        "image_urls": images,
        "blocked_reasons": blocked_reasons,
        "required_but_missing": ["thumb_media_id"],
        "official_draft_payload": {
            "articles": [
                {
                    "title": title,
                    "author": author,
                    "digest": digest,
                    "content": row.get("content", ""),
                    "content_source_url": source_url,
                    "need_open_comment": need_open_comment,
                    "only_fans_can_comment": only_fans_can_comment,
                }
            ]
        },
    }


def _write_preview_csv(csv_path: Path, articles: List[Dict]):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "title",
            "source_url",
            "account_name",
            "publish_time",
            "publish_time_str",
            "fakeid",
        ])
        for article in articles:
            writer.writerow([
                article["title"],
                article["source_url"],
                article["account_name"],
                article["publish_time"],
                article["publish_time_str"],
                article["fakeid"],
            ])


def _write_preview_manifest(manifest_path: Path, payload: Dict):
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_manifest_csv_path(manifest_path: str) -> str:
    if not manifest_path:
        return ""

    path = Path(manifest_path)
    if not path.exists():
        return ""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(payload.get("csv_path", "") or "")


def generate_preview_batch(limit: int = DEFAULT_BATCH_LIMIT) -> Dict:
    mode = get_preview_mode()
    if mode != PREVIEW_ONLY_MODE:
        logger.warning("Official preview skipped: unsupported mode=%s", mode)
        return {
            "success": False,
            "mode": mode,
            "generated_count": 0,
            "message": f"unsupported OFFICIAL_WX_MODE: {mode}",
        }

    pending_rows = rss_store.get_pending_official_preview_articles(limit=limit)
    if not pending_rows:
        return {
            "success": True,
            "mode": mode,
            "generated_count": 0,
            "message": "no pending confirmed recruitment articles",
            "csv_path": "",
            "manifest_path": "",
            "blocked_reasons_summary": {},
        }

    now = int(time.time())
    batch_name = datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S")
    date_dir = get_preview_output_dir() / datetime.fromtimestamp(now).strftime("%Y%m%d")
    csv_path = date_dir / f"official_wx_preview_{batch_name}.csv"
    manifest_path = date_dir / f"official_wx_preview_{batch_name}.json"

    preview_articles = [_build_preview_article(row) for row in pending_rows]
    blocked_summary = Counter()
    for article in preview_articles:
        blocked_summary.update(article["blocked_reasons"])

    _write_preview_csv(csv_path, preview_articles)

    manifest_payload = {
        "mode": mode,
        "generated_at": now,
        "generated_at_str": _format_publish_time(now),
        "article_count": len(preview_articles),
        "csv_path": str(csv_path),
        "manifest_path": str(manifest_path),
        "articles": preview_articles,
        "blocked_reasons_summary": dict(blocked_summary),
        "official_api_pipeline": [
            "stable_token",
            "素材上传/图片替换",
            "draft/add",
            "freepublish/submit",
        ],
        "next_steps": [
            "补充封面 thumb_media_id",
            "将正文图片替换为微信图文内图片 URL",
            "确认账号具备官方发布权限后再启用正式写接口",
        ],
    }
    _write_preview_manifest(manifest_path, manifest_payload)

    rss_store.save_official_preview_records([
        {
            "source_link": row.get("link", ""),
            "article_title": row.get("title", ""),
            "fakeid": row.get("fakeid", ""),
            "review_status": row.get("review_status", ""),
            "preview_status": "generated",
            "preview_generated_at": now,
            "csv_exported_at": now,
            "blocked_reasons": article["blocked_reasons"],
            "manifest_path": str(manifest_path),
        }
        for row, article in zip(pending_rows, preview_articles)
    ])

    return {
        "success": True,
        "mode": mode,
        "generated_count": len(preview_articles),
        "message": "preview package generated",
        "csv_path": str(csv_path),
        "manifest_path": str(manifest_path),
        "blocked_reasons_summary": dict(blocked_summary),
    }


def get_recent_preview_batches(limit: int = 10) -> List[Dict]:
    rows = rss_store.get_recent_official_preview_records(limit=max(limit * 20, 50))
    grouped: Dict[str, Dict] = {}

    for row in rows:
        manifest_path = row.get("manifest_path", "")
        if not manifest_path:
            continue

        batch = grouped.get(manifest_path)
        if batch is None:
            batch = {
                "manifest_path": manifest_path,
                "csv_path": _read_manifest_csv_path(manifest_path),
                "generated_count": 0,
                "preview_generated_at": row.get("preview_generated_at", 0),
                "preview_generated_at_str": _format_publish_time(row.get("preview_generated_at", 0)),
                "blocked_reasons_summary": Counter(),
            }
            grouped[manifest_path] = batch

        batch["generated_count"] += 1
        batch["preview_generated_at"] = max(
            batch["preview_generated_at"],
            row.get("preview_generated_at", 0),
        )
        batch["preview_generated_at_str"] = _format_publish_time(batch["preview_generated_at"])
        batch["blocked_reasons_summary"].update(
            rss_store.parse_json_list(row.get("blocked_reasons"))
        )

    batches = []
    for batch in grouped.values():
        batches.append({
            **batch,
            "blocked_reasons_summary": dict(batch["blocked_reasons_summary"]),
        })

    batches.sort(key=lambda item: item.get("preview_generated_at", 0), reverse=True)
    return batches[:limit]


class OfficialWxPreviewWorker:
    """本地预览任务单例。"""

    _instance = None
    _task: Optional[asyncio.Task] = None
    _running = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._last_run = {}
        return cls._instance

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Official preview worker started (mode=%s, tick=%ds, output_dir=%s)",
            get_preview_mode(),
            get_preview_tick_seconds(),
            get_preview_output_dir(),
        )

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Official preview worker stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    async def _loop(self):
        while self._running:
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("Official preview cycle error: %s", exc, exc_info=True)
            await asyncio.sleep(get_preview_tick_seconds())

    async def run_once(self, limit: int = DEFAULT_BATCH_LIMIT) -> Dict:
        result = await asyncio.to_thread(generate_preview_batch, limit)
        self._last_run = {
            **result,
            "ran_at": int(time.time()),
            "ran_at_str": _format_publish_time(int(time.time())),
        }
        return result

    def get_status(self, recent_limit: int = 10) -> Dict:
        return {
            "running": self.is_running,
            "mode": get_preview_mode(),
            "tick_seconds": get_preview_tick_seconds(),
            "output_dir": str(get_preview_output_dir()),
            "last_run": self._last_run,
            "recent_batches": get_recent_preview_batches(limit=recent_limit),
        }


official_wx_preview_worker = OfficialWxPreviewWorker()
