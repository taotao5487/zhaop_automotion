#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
RSS 后台轮询器
采用分散调度模式，只处理到期账号并对招聘候选文章抓全文。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Dict, List, Optional

import httpx

from wechat_service.utils.auth_manager import auth_manager
from wechat_service.utils import rss_store
from wechat_service.utils.helpers import has_article_content, is_article_unavailable, get_unavailable_reason
from wechat_service.utils.recruitment_filter import recruitment_filter

logger = logging.getLogger(__name__)

SCHEDULER_TICK = int(os.getenv("RSS_SCHEDULER_TICK", os.getenv("RSS_POLL_INTERVAL", "60")))
POLL_INTERVAL = SCHEDULER_TICK
POLL_BATCH_SIZE = int(os.getenv("RSS_POLL_BATCH_SIZE", "1"))
ARTICLES_PER_POLL = int(os.getenv("ARTICLES_PER_POLL", "10"))
FETCH_FULL_CONTENT = os.getenv("RSS_FETCH_FULL_CONTENT", "true").lower() == "true"

NORMAL_RETRY_DELAY = 2 * 60 * 60
SEVERE_RETRY_DELAY = 12 * 60 * 60


class PollingError(Exception):
    def __init__(self, message: str, severe: bool = False):
        super().__init__(message)
        self.severe = severe


class RSSPoller:
    """后台轮询单例"""

    _instance = None
    _task: Optional[asyncio.Task] = None
    _running = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "RSS poller started (tick=%ds, batch=%d, full_content=%s)",
            SCHEDULER_TICK,
            POLL_BATCH_SIZE,
            FETCH_FULL_CONTENT,
        )

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("RSS poller stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    async def _loop(self):
        while self._running:
            try:
                await self._poll_due_subscriptions()
            except Exception as exc:
                logger.error("RSS poll cycle error: %s", exc, exc_info=True)
            await asyncio.sleep(SCHEDULER_TICK)

    async def _poll_due_subscriptions(self) -> int:
        due_subscriptions = rss_store.get_due_subscriptions(limit=POLL_BATCH_SIZE)
        if not due_subscriptions:
            return 0

        creds = auth_manager.get_credentials()
        if not creds or not creds.get("token") or not creds.get("cookie"):
            logger.warning("RSS poll skipped: not logged in")
            return 0

        processed = 0
        for subscription in due_subscriptions:
            fakeid = subscription["fakeid"]
            try:
                articles = await self._fetch_article_list(fakeid, creds)
                articles = self._prepare_articles_for_recruitment(articles)
                articles = await self._fetch_candidate_content(
                    articles,
                    subscription_created_at=int(subscription.get("created_at", 0) or 0),
                )

                if articles:
                    new_count = rss_store.save_articles(fakeid, articles)
                    if new_count > 0:
                        logger.info("RSS: %d new articles for %s", new_count, fakeid[:8])

                rss_store.mark_poll_success(fakeid)
                processed += 1
            except PollingError as exc:
                delay = SEVERE_RETRY_DELAY if exc.severe else NORMAL_RETRY_DELAY
                rss_store.mark_poll_failure(fakeid, delay)
                logger.warning("RSS poll error for %s: %s", fakeid[:8], exc)
            except Exception as exc:
                rss_store.mark_poll_failure(fakeid, NORMAL_RETRY_DELAY)
                logger.error("RSS poll error for %s: %s", fakeid[:8], exc, exc_info=True)

        return processed

    def _prepare_articles_for_recruitment(self, articles: List[Dict]) -> List[Dict]:
        prepared = []
        for article in articles:
            updated, _ = recruitment_filter.prepare_article(article)
            prepared.append(updated)
        return prepared

    def _is_push_eligible_article(
        self,
        article: Dict,
        *,
        subscription_created_at: int = 0,
        now: Optional[int] = None,
    ) -> bool:
        publish_time = int(article.get("publish_time", 0) or 0)
        if publish_time <= 0:
            return False

        if subscription_created_at and rss_store.get_recruitment_push_since_subscription():
            if publish_time < int(subscription_created_at):
                return False

        recent_days = rss_store.get_recruitment_push_recent_days()
        if recent_days > 0:
            current_ts = int(time.time()) if now is None else int(now)
            cutoff = current_ts - recent_days * 86400
            if publish_time < cutoff:
                return False

        return True

    async def _fetch_candidate_content(
        self,
        articles: List[Dict],
        *,
        subscription_created_at: int = 0,
    ) -> List[Dict]:
        if not FETCH_FULL_CONTENT or not articles:
            return articles

        candidates = [
            article
            for article in articles
            if article.get("filter_stage") in {"coarse_matched", "title_confirmed"}
            and self._is_push_eligible_article(
                article,
                subscription_created_at=subscription_created_at,
            )
        ]
        if not candidates:
            return articles

        enriched_candidates = await self._enrich_articles_content(candidates)
        candidate_map = {
            article.get("link"): article
            for article in enriched_candidates
            if article.get("link")
        }

        merged = []
        for article in articles:
            link = article.get("link")
            merged.append(candidate_map.get(link, article))
        return merged

    async def _fetch_article_list(self, fakeid: str, creds: Dict) -> List[Dict]:
        params = {
            "sub": "list",
            "search_field": "null",
            "begin": 0,
            "count": ARTICLES_PER_POLL,
            "query": "",
            "fakeid": fakeid,
            "type": "101_1",
            "free_publish_type": 1,
            "sub_action": "list_ex",
            "token": creds["token"],
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://mp.weixin.qq.com/",
            "Cookie": creds["cookie"],
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    "https://mp.weixin.qq.com/cgi-bin/appmsgpublish",
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.RequestError as exc:
            raise PollingError(f"network error: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise PollingError(f"http error: {exc.response.status_code}") from exc

        base_resp = result.get("base_resp", {})
        ret_code = base_resp.get("ret")
        err_msg = str(base_resp.get("err_msg", ""))
        if ret_code != 0:
            severe = ret_code == 200003 or any(
                marker in err_msg.lower() for marker in ("login", "verify", "验证", "环境异常")
            )
            raise PollingError(
                f"WeChat API error ret={ret_code}, msg={err_msg or 'unknown'}",
                severe=severe,
            )

        publish_page = result.get("publish_page", {})
        if isinstance(publish_page, str):
            try:
                publish_page = json.loads(publish_page)
            except (json.JSONDecodeError, ValueError):
                return []

        if not isinstance(publish_page, dict):
            return []

        articles = []
        for item in publish_page.get("publish_list", []):
            publish_info = item.get("publish_info", {})
            if isinstance(publish_info, str):
                try:
                    publish_info = json.loads(publish_info)
                except (json.JSONDecodeError, ValueError):
                    continue
            if not isinstance(publish_info, dict):
                continue
            for article in publish_info.get("appmsgex", []):
                articles.append({
                    "aid": article.get("aid", ""),
                    "title": article.get("title", ""),
                    "link": article.get("link", ""),
                    "digest": article.get("digest", ""),
                    "cover": article.get("cover", ""),
                    "author": article.get("author", ""),
                    "publish_time": article.get("update_time", 0),
                    "content": "",
                    "plain_content": "",
                    "images_json": "[]",
                })
        return articles

    async def poll_now(self) -> int:
        """手动触发一次调度周期，仅处理到期账号。"""
        return await self._poll_due_subscriptions()

    async def _enrich_articles_content(self, articles: List[Dict]) -> List[Dict]:
        from wechat_service.utils.article_fetcher import fetch_articles_batch
        from wechat_service.utils.content_processor import process_article_content

        article_links = [a.get("link", "") for a in articles if a.get("link")]
        if not article_links:
            return articles

        max_fetch = 20
        if len(article_links) > max_fetch:
            logger.info(
                "候选招聘文章 %d 篇超过限制，仅获取最近 %d 篇的完整内容",
                len(article_links),
                max_fetch,
            )
            article_links = article_links[:max_fetch]
            articles = articles[:max_fetch]

        logger.info("开始批量获取 %d 篇候选招聘文章的完整内容", len(article_links))

        wechat_token = os.getenv("WECHAT_TOKEN", "")
        wechat_cookie = os.getenv("WECHAT_COOKIE", "")
        results = await fetch_articles_batch(
            article_links,
            max_concurrency=3,
            timeout=60,
            wechat_token=wechat_token,
            wechat_cookie=wechat_cookie,
        )

        enriched = []
        for article in articles:
            link = article.get("link", "")
            if not link:
                enriched.append(recruitment_filter.finalize_article(article))
                continue

            html = results.get(link)
            if not html:
                logger.warning("Empty HTML: %s", link[:80])
                enriched.append(recruitment_filter.finalize_article(article))
                continue
            if is_article_unavailable(html):
                reason = get_unavailable_reason(html) or "unknown"
                logger.warning("Article permanently unavailable (%s): %s", reason, link[:80])
                article["content"] = f"<p>[unavailable] {reason}</p>"
                article["plain_content"] = f"[unavailable] {reason}"
                article["images_json"] = "[]"
                enriched.append(recruitment_filter.finalize_article(article))
                continue
            if not has_article_content(html):
                logger.warning("No content in HTML: %s", link[:80])
                enriched.append(recruitment_filter.finalize_article(article))
                continue

            try:
                site_url = os.getenv("SITE_URL", "http://localhost:5000").rstrip("/")
                result = process_article_content(html, proxy_base_url=site_url)
                article["content"] = result.get("content", "")
                article["plain_content"] = result.get("plain_content", "")
                article["images"] = result.get("images", [])
                article["images_json"] = json.dumps(article["images"], ensure_ascii=False)

                if not article.get("author"):
                    from wechat_service.utils.helpers import extract_article_info, parse_article_url

                    article_info = extract_article_info(html, parse_article_url(link))
                    article["author"] = article_info.get("author", "")

                logger.info(
                    "Content fetched: %s... (%d chars, %d images)",
                    link[:50],
                    len(article["content"]),
                    len(article["images"]),
                )
            except Exception as exc:
                logger.error("Failed to process content for %s: %s", link[:80], exc)

            enriched.append(recruitment_filter.finalize_article(article))

        return enriched


rss_poller = RSSPoller()
