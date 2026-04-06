#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
微信官方草稿箱手动推送工具
仅在显式调用时执行，不参与后台自动任务。
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import mimetypes
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    from PIL import Image
except ImportError:
    Image = None

from wechat_service.utils import rss_store
from shared.paths import ENV_FILE, resolve_from_root

logger = logging.getLogger(__name__)

WECHAT_API_BASE = "https://api.weixin.qq.com"
DEFAULT_OFFICIAL_AUTHOR = "白衣驿站"
DEFAULT_SHARED_DRAFT_LEASE_TTL_SECONDS = 120
WECHAT_TITLE_CHAR_LIMIT = 64
_OFFICIAL_DOTENV_PATH = ENV_FILE
_OFFICIAL_ENV_LOADED = False


class OfficialWechatDraftError(Exception):
    pass


def _load_official_env() -> None:
    global _OFFICIAL_ENV_LOADED
    if _OFFICIAL_ENV_LOADED:
        return
    if _OFFICIAL_DOTENV_PATH.exists():
        load_dotenv(_OFFICIAL_DOTENV_PATH, override=False)
    _OFFICIAL_ENV_LOADED = True


def _read_official_credentials() -> tuple[str, str]:
    _load_official_env()
    appid = (os.getenv("OFFICIAL_WX_APPID", "") or "").strip()
    appsecret = (os.getenv("OFFICIAL_WX_APPSECRET", "") or "").strip()
    if not appid or not appsecret:
        raise OfficialWechatDraftError(
            "OFFICIAL_WX_APPID / OFFICIAL_WX_APPSECRET 未配置，无法调用微信官方草稿接口"
        )
    return appid, appsecret


def _build_request_headers() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/133.0.0.0 Safari/537.36"
        ),
        "Referer": "https://mp.weixin.qq.com/",
    }


def _build_json_headers() -> Dict[str, str]:
    return {
        **_build_request_headers(),
        "Content-Type": "application/json; charset=utf-8",
    }


def _is_wechat_asset_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return any(
        marker in host
        for marker in (
            "mmbiz.qpic.cn",
            "mmecoa.qpic.cn",
            "qq.com",
            "qpic.cn",
        )
    )


def _resolve_original_image_url(url: str) -> str:
    normalized_url = html.unescape((url or "").strip())
    if not normalized_url:
        return ""

    parsed = urlparse(normalized_url)
    query = parse_qs(parsed.query)
    proxied = query.get("url")
    if proxied:
        return html.unescape(proxied[0])
    return normalized_url


def _resolve_local_binary_path(source: str) -> Optional[Path]:
    normalized = html.unescape((source or "").strip())
    if not normalized:
        return None

    parsed = urlparse(normalized)
    if parsed.scheme in {"http", "https"}:
        return None

    candidate = normalized
    if parsed.scheme == "file":
        candidate = unquote(parsed.path or "")

    path = Path(candidate).expanduser()
    if path.exists():
        return path.resolve()

    if not path.is_absolute():
        rooted = resolve_from_root(candidate)
        if rooted.exists():
            return rooted
    return None


async def _get_stable_access_token() -> str:
    appid, appsecret = _read_official_credentials()
    payload = {
        "grant_type": "client_credential",
        "appid": appid,
        "secret": appsecret,
        "force_refresh": False,
    }
    async with httpx.AsyncClient(
        timeout=30.0,
        headers=_build_json_headers(),
        trust_env=False,
    ) as client:
        resp = await client.post(
            f"{WECHAT_API_BASE}/cgi-bin/stable_token",
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("errcode", 0) not in (0, None):
        raise OfficialWechatDraftError(
            f"获取 stable access token 失败: errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
        )

    access_token = (data.get("access_token") or "").strip()
    if not access_token:
        raise OfficialWechatDraftError("微信未返回 access_token")
    return access_token


async def _download_binary(url: str) -> tuple[bytes, str]:
    normalized_url = _resolve_original_image_url(url)
    local_path = _resolve_local_binary_path(normalized_url)
    if local_path is not None:
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        return local_path.read_bytes(), content_type

    async with httpx.AsyncClient(
        timeout=60.0,
        headers=_build_request_headers(),
        follow_redirects=True,
        trust_env=False,
    ) as client:
        resp = await client.get(normalized_url)
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "")


def _guess_suffix(url: str, content_type: str, default: str = ".jpg") -> str:
    url_lower = url.lower()
    if ".png" in url_lower or "png" in content_type.lower():
        return ".png"
    if ".gif" in url_lower or "gif" in content_type.lower():
        return ".gif"
    return default


def _prepare_upload_image(
    content: bytes,
    source_url: str,
    content_type: str,
    purpose: str,
) -> tuple[bytes, str, str]:
    if not content:
        raise OfficialWechatDraftError(f"图片内容为空，无法上传: {source_url}")

    if Image is None:
        suffix = _guess_suffix(source_url, content_type)
        mime_type = (content_type or "").split(";", 1)[0].strip() or "image/jpeg"
        return content, mime_type, suffix

    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except Exception as exc:
        raise OfficialWechatDraftError(f"图片解析失败，无法上传: {source_url} ({exc})") from exc

    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )

    if has_alpha:
        target_format = "PNG"
        mime_type = "image/png"
        suffix = ".png"
        converted = io.BytesIO()
        image.save(converted, format=target_format, optimize=True)
        return converted.getvalue(), mime_type, suffix

    converted_image = image.convert("RGB")
    quality_candidates = [90, 82, 75, 68]
    for quality in quality_candidates:
        converted = io.BytesIO()
        converted_image.save(
            converted,
            format="JPEG",
            quality=quality,
            optimize=True,
        )
        data = converted.getvalue()
        if purpose != "content" or len(data) <= 1024 * 1024:
            return data, "image/jpeg", ".jpg"

    data = converted.getvalue()
    return data, "image/jpeg", ".jpg"


async def _upload_content_image(access_token: str, image_url: str) -> str:
    content, content_type = await _download_binary(image_url)
    prepared, mime_type, suffix = _prepare_upload_image(
        content,
        image_url,
        content_type,
        purpose="content",
    )
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(prepared)
        tmp.flush()
        with open(tmp.name, "rb") as fh:
            files = {"media": (Path(tmp.name).name, fh, mime_type)}
            async with httpx.AsyncClient(
                timeout=60.0,
                headers=_build_request_headers(),
                trust_env=False,
            ) as client:
                resp = await client.post(
                    f"{WECHAT_API_BASE}/cgi-bin/media/uploadimg",
                    params={"access_token": access_token},
                    files=files,
                )
                resp.raise_for_status()
                data = resp.json()

    if data.get("errcode", 0) not in (0, None):
        raise OfficialWechatDraftError(
            f"上传正文图片失败: errcode={data.get('errcode')} errmsg={data.get('errmsg')} url={image_url}"
        )

    uploaded_url = (data.get("url") or "").strip()
    if not uploaded_url:
        raise OfficialWechatDraftError(f"上传正文图片失败: 微信未返回 url, source={image_url}")
    return uploaded_url


async def _upload_cover_material(access_token: str, cover_url: str) -> str:
    content, content_type = await _download_binary(cover_url)
    prepared, mime_type, suffix = _prepare_upload_image(
        content,
        cover_url,
        content_type,
        purpose="cover",
    )
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(prepared)
        tmp.flush()
        with open(tmp.name, "rb") as fh:
            files = {"media": (Path(tmp.name).name, fh, mime_type)}
            async with httpx.AsyncClient(
                timeout=60.0,
                headers=_build_request_headers(),
                trust_env=False,
            ) as client:
                resp = await client.post(
                    f"{WECHAT_API_BASE}/cgi-bin/material/add_material",
                    params={"access_token": access_token, "type": "image"},
                    files=files,
                )
                resp.raise_for_status()
                data = resp.json()

    if data.get("errcode", 0) not in (0, None):
        raise OfficialWechatDraftError(
            f"上传封面失败: errcode={data.get('errcode')} errmsg={data.get('errmsg')} cover={cover_url}"
        )

    media_id = (data.get("media_id") or "").strip()
    if not media_id:
        raise OfficialWechatDraftError("上传封面失败: 微信未返回 media_id")
    return media_id


async def _rewrite_content_images(access_token: str, content_html: str) -> tuple[str, List[Dict[str, str]]]:
    soup = BeautifulSoup(content_html or "", "html.parser")
    replacements: List[Dict[str, str]] = []

    for img in soup.find_all("img"):
        source_url = ""
        for attr in ("src", "data-src", "data-original", "data-croporisrc"):
            candidate = _resolve_original_image_url(img.get(attr, ""))
            if candidate.startswith("http") or _resolve_local_binary_path(candidate):
                source_url = candidate
                break

        if not source_url:
            continue
        uploaded_url = await _upload_content_image(access_token, source_url)
        img["src"] = uploaded_url
        for attr in ("data-src", "data-original", "data-croporisrc"):
            img.attrs.pop(attr, None)
        replacements.append({"source": source_url, "uploaded": uploaded_url})

    return str(soup), replacements


def _sanitize_title(title: str) -> str:
    normalized = re.sub(r"\s+", " ", (title or "").strip())
    if len(normalized) <= WECHAT_TITLE_CHAR_LIMIT:
        return normalized
    return normalized[:WECHAT_TITLE_CHAR_LIMIT].rstrip()


def _sanitize_author(author: str) -> str:
    configured = (os.getenv("OFFICIAL_WX_AUTHOR", "") or "").strip()
    return configured or DEFAULT_OFFICIAL_AUTHOR


def _sanitize_digest(digest: str) -> str:
    return ""


def _env_flag(name: str, default: bool = False) -> bool:
    _load_official_env()
    value = (os.getenv(name, "") or "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


def get_comment_settings_for_article_payload() -> tuple[int, int]:
    need_open_comment = 1 if _env_flag("OFFICIAL_WX_OPEN_COMMENT", default=True) else 0
    only_fans_can_comment = (
        1 if need_open_comment and _env_flag("OFFICIAL_WX_ONLY_FANS_CAN_COMMENT", default=False) else 0
    )
    return need_open_comment, only_fans_can_comment


def _build_official_account_card_html() -> str:
    _load_official_env()
    if not _env_flag("OFFICIAL_WX_CARD_ENABLED", default=True):
        return ""

    qr_url = (os.getenv("OFFICIAL_WX_CARD_QR_URL", "") or "").strip()
    escaped_qr_url = html.escape(qr_url, quote=True)
    if not escaped_qr_url:
        return ""

    return (
        '<section class="official-account-card" '
        'style="margin-top:20px;padding-top:8px;text-align:center;">'
        f'<img src="{escaped_qr_url}" alt="公众号二维码" '
        'style="display:inline-block;width:96px;max-width:96px;height:auto;border-radius:8px;" />'
        "</section>"
    )


def _append_official_account_card(content_html: str) -> str:
    card_html = _build_official_account_card_html()
    if not card_html:
        return content_html or ""
    return f"{content_html or ''}{card_html}"


def _has_meaningful_article_body(content_html: str) -> bool:
    soup = BeautifulSoup(content_html or "", "html.parser")
    if soup.find("img") is not None:
        return True
    text = soup.get_text(" ", strip=True)
    return bool(text)


async def _ensure_article_content_ready(row: Dict) -> Dict:
    normalized_row = _normalize_direct_article_row(row)
    if _has_meaningful_article_body(normalized_row.get("content", "")):
        return normalized_row

    source_url = normalized_row.get("link", "")
    if not source_url:
        return normalized_row

    from wechat_service.utils.article_fetcher import fetch_article_content
    from wechat_service.utils.content_processor import process_article_content
    from wechat_service.utils.helpers import (
        extract_article_info,
        get_unavailable_reason,
        has_article_content,
        is_article_unavailable,
        parse_article_url,
    )

    wechat_token = os.getenv("WECHAT_TOKEN", "")
    wechat_cookie = os.getenv("WECHAT_COOKIE", "")
    html_content = await fetch_article_content(
        source_url,
        timeout=60,
        wechat_token=wechat_token,
        wechat_cookie=wechat_cookie,
    )
    if not html_content:
        raise OfficialWechatDraftError(f"抓取文章正文失败: {source_url}")

    if is_article_unavailable(html_content):
        reason = get_unavailable_reason(html_content) or "unknown"
        raise OfficialWechatDraftError(f"文章正文不可用: {reason}")

    if not has_article_content(html_content):
        raise OfficialWechatDraftError(f"文章正文为空或未通过微信校验: {source_url}")

    site_url = os.getenv("SITE_URL", "http://localhost:5000").rstrip("/")
    processed = process_article_content(html_content, proxy_base_url=site_url)
    normalized_row["content"] = processed.get("content", "") or ""
    normalized_row["plain_content"] = processed.get("plain_content", "") or ""
    normalized_row["images"] = processed.get("images", []) or []
    normalized_row["images_json"] = json.dumps(normalized_row["images"], ensure_ascii=False)

    if not normalized_row.get("author"):
        article_info = extract_article_info(html_content, parse_article_url(source_url))
        normalized_row["author"] = article_info.get("author", "") or ""

    if not _has_meaningful_article_body(normalized_row.get("content", "")):
        raise OfficialWechatDraftError(f"文章正文抓取后仍为空: {source_url}")

    fakeid = (normalized_row.get("fakeid") or "").strip()
    if fakeid:
        rss_store.save_articles(fakeid, [normalized_row])

    return normalized_row


def _extract_article_row(
    source_url: Optional[str] = None,
    force: bool = False,
) -> Dict:
    if source_url:
        row = rss_store.get_recruitment_article_by_link(source_url.strip())
        if row:
            if not force and rss_store.get_official_draft_record(row.get("link", "")):
                raise OfficialWechatDraftError(
                    f"该文章已经推送过草稿箱，默认不再重复推送: {source_url}"
                )
            return row
        raise OfficialWechatDraftError(f"未找到指定招聘文章: {source_url}")

    rows = rss_store.get_recruitment_articles(
        status="confirmed",
        limit=1,
        push_status="all" if force else "unpushed",
        recent_days=rss_store.get_recruitment_push_recent_days(),
        since_subscription=rss_store.get_recruitment_push_since_subscription(),
    )
    if not rows:
        if force:
            raise OfficialWechatDraftError("当前没有 confirmed 招聘文章可推送到草稿箱")
        raise OfficialWechatDraftError("当前没有未推送到草稿箱的 confirmed 招聘文章")
    return rows[0]


def _save_push_record(
    row: Dict,
    draft_status: str,
    draft_media_id: str,
    previous_draft_media_id: str = "",
    append_mode: str = "",
    source_type: str = "",
    series_batch_index: int = 0,
):
    now = int(time.time())
    rss_store.save_official_draft_record({
        "source_link": row.get("link", ""),
        "article_title": row.get("title", ""),
        "fakeid": row.get("fakeid", ""),
        "review_status": row.get("review_status", ""),
        "source_type": source_type,
        "draft_status": draft_status,
        "draft_media_id": draft_media_id,
        "previous_draft_media_id": previous_draft_media_id,
        "append_mode": append_mode,
        "series_batch_index": int(series_batch_index or 0),
        "pushed_at": now,
        "updated_at": now,
    })


def _build_skipped_duplicate_result(row: Dict, existing_record: Dict) -> Dict:
    return {
        "success": True,
        "status": "skipped_duplicate",
        "skipped": True,
        "source_url": row.get("link", ""),
        "title": row.get("title", ""),
        "draft_media_id": existing_record.get("draft_media_id", ""),
        "previous_draft_media_id": existing_record.get("previous_draft_media_id", ""),
        "append_mode": existing_record.get("append_mode", ""),
        "batch_index": int(existing_record.get("series_batch_index", 0) or 0),
    }


async def _acquire_shared_series_lease(
    series_key: str,
    *,
    max_attempts: int = 10,
    delay_seconds: float = 0.2,
) -> str:
    lease_owner = f"shared-draft:{os.getpid()}:{time.time_ns()}"
    for _ in range(max_attempts):
        if rss_store.acquire_official_draft_series_lease(
            series_key=series_key,
            lease_owner=lease_owner,
            lease_ttl_seconds=DEFAULT_SHARED_DRAFT_LEASE_TTL_SECONDS,
        ):
            return lease_owner
        await asyncio.sleep(delay_seconds)
    raise OfficialWechatDraftError("共享草稿串当前正被其他任务使用，请稍后重试")


def _count_draft_articles(draft_data: Dict) -> int:
    news_item = (draft_data.get("news_item") or []) if isinstance(draft_data, dict) else []
    return len([article for article in news_item if isinstance(article, dict)])


def get_shared_draft_series_status(
    series_key: str = rss_store.DEFAULT_OFFICIAL_DRAFT_SERIES_KEY,
) -> Dict:
    state = rss_store.get_official_draft_series(series_key)
    return {
        "series_key": state.get("series_key", series_key),
        "current_media_id": state.get("current_media_id", ""),
        "current_article_count": int(state.get("current_article_count", 0) or 0),
        "batch_index": int(state.get("batch_index", 0) or 0),
        "lease_owner": state.get("lease_owner", ""),
        "lease_expires_at": int(state.get("lease_expires_at", 0) or 0),
        "updated_at": int(state.get("updated_at", 0) or 0),
        "has_active_draft": bool(state.get("current_media_id")),
    }


def _contains_unsupported_images(html: str) -> bool:
    soup = BeautifulSoup(html or "", "html.parser")
    for img in soup.find_all("img"):
        for attr in ("src", "data-src", "data-original", "data-croporisrc"):
            candidate = _resolve_original_image_url(img.get(attr, ""))
            if not candidate:
                continue
            if candidate.startswith("http") and not _is_wechat_asset_url(candidate):
                return True
            if _resolve_local_binary_path(candidate) is not None:
                return True
    return False


def _normalize_direct_article_row(row: Dict) -> Dict:
    normalized = dict(row or {})
    normalized["title"] = _sanitize_title(normalized.get("title", ""))
    normalized["link"] = (normalized.get("link") or "").strip()
    normalized["cover"] = (normalized.get("cover") or "").strip()
    normalized["content"] = normalized.get("content") or ""
    normalized["plain_content"] = normalized.get("plain_content") or ""
    normalized["digest"] = normalized.get("digest") or ""
    normalized["author"] = normalized.get("author") or ""
    return normalized


async def _build_recruitment_article_payload(
    access_token: str,
    row: Dict,
) -> tuple[Dict, str, List[Dict[str, str]]]:
    cover_url = (row.get("cover") or "").strip()
    if not cover_url:
        raise OfficialWechatDraftError("文章缺少 cover，无法生成草稿封面")

    if not _has_meaningful_article_body(row.get("content", "")):
        raise OfficialWechatDraftError("文章正文为空，无法生成完整草稿")

    content_with_card = _append_official_account_card(row.get("content", ""))
    rewritten_content, image_replacements = await _rewrite_content_images(
        access_token,
        content_with_card,
    )
    if _contains_unsupported_images(rewritten_content):
        raise OfficialWechatDraftError("正文中仍有未替换的外部图片链接，已中止草稿写入")

    thumb_media_id = await _upload_cover_material(access_token, cover_url)
    need_open_comment, only_fans_can_comment = get_comment_settings_for_article_payload()
    article_payload = {
        "title": _sanitize_title(row.get("title", "")),
        "author": _sanitize_author(row.get("author", "")),
        "digest": _sanitize_digest(row.get("digest", "")),
        "content": rewritten_content,
        "content_source_url": row.get("link", ""),
        "thumb_media_id": thumb_media_id,
        "need_open_comment": need_open_comment,
        "only_fans_can_comment": only_fans_can_comment,
    }
    return article_payload, thumb_media_id, image_replacements


def _normalize_existing_draft_article(article: Dict) -> Dict:
    return {
        "title": (article.get("title") or "").strip(),
        "author": (article.get("author") or "").strip(),
        "digest": article.get("digest") or "",
        "content": article.get("content") or "",
        "content_source_url": article.get("content_source_url") or "",
        "thumb_media_id": (article.get("thumb_media_id") or "").strip(),
        "thumb_url": (article.get("thumb_url") or "").strip(),
        "need_open_comment": int(article.get("need_open_comment") or 0),
        "only_fans_can_comment": int(article.get("only_fans_can_comment") or 0),
    }


async def _prepare_existing_draft_articles_for_rebuild(
    access_token: str,
    draft_data: Dict,
) -> List[Dict]:
    news_item = (draft_data.get("news_item") or []) if isinstance(draft_data, dict) else []
    prepared_articles: List[Dict] = []

    for raw_article in news_item:
        if not isinstance(raw_article, dict):
            continue

        normalized = _normalize_existing_draft_article(raw_article)
        if not normalized.get("thumb_media_id"):
            thumb_url = _resolve_original_image_url(normalized.get("thumb_url", ""))
            if not thumb_url:
                raise OfficialWechatDraftError("历史草稿文章缺少 thumb_media_id / thumb_url，无法重建草稿")
            normalized["thumb_media_id"] = await _upload_cover_material(access_token, thumb_url)
        prepared_articles.append(normalized)

    return prepared_articles


async def _add_draft(access_token: str, articles: List[Dict]) -> str:
    payload = {"articles": articles}

    async with httpx.AsyncClient(
        timeout=60.0,
        headers=_build_json_headers(),
        trust_env=False,
    ) as client:
        resp = await client.post(
            f"{WECHAT_API_BASE}/cgi-bin/draft/add",
            params={"access_token": access_token},
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("errcode", 0) not in (0, None):
        raise OfficialWechatDraftError(
            f"写入草稿箱失败: errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
        )

    media_id = (data.get("media_id") or "").strip()
    if not media_id:
        raise OfficialWechatDraftError("写入草稿箱失败: 微信未返回 media_id")
    return media_id


async def _get_draft(access_token: str, media_id: str) -> Dict:
    payload = {"media_id": media_id}

    async with httpx.AsyncClient(
        timeout=60.0,
        headers=_build_json_headers(),
        trust_env=False,
    ) as client:
        resp = await client.post(
            f"{WECHAT_API_BASE}/cgi-bin/draft/get",
            params={"access_token": access_token},
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("errcode", 0) not in (0, None):
        raise OfficialWechatDraftError(
            f"读取草稿箱失败: errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
        )
    return data


async def _update_draft_article(
    access_token: str,
    media_id: str,
    index: int,
    article: Dict,
) -> Dict:
    payload = {
        "media_id": media_id,
        "index": int(index),
        "articles": article,
    }

    async with httpx.AsyncClient(
        timeout=60.0,
        headers=_build_json_headers(),
        trust_env=False,
    ) as client:
        resp = await client.post(
            f"{WECHAT_API_BASE}/cgi-bin/draft/update",
            params={"access_token": access_token},
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("errcode", 0) not in (0, None):
        raise OfficialWechatDraftError(
            f"更新草稿失败: errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
        )
    return data


async def _delete_draft(access_token: str, media_id: str) -> None:
    payload = {"media_id": media_id}

    async with httpx.AsyncClient(
        timeout=60.0,
        headers=_build_json_headers(),
        trust_env=False,
    ) as client:
        resp = await client.post(
            f"{WECHAT_API_BASE}/cgi-bin/draft/delete",
            params={"access_token": access_token},
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("errcode", 0) not in (0, None):
        raise OfficialWechatDraftError(
            f"删除旧草稿失败: errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
        )


async def push_recruitment_article_to_draft(
    source_url: Optional[str] = None,
    force: bool = False,
) -> Dict:
    row = await _ensure_article_content_ready(
        _extract_article_row(source_url=source_url, force=force)
    )
    access_token = await _get_stable_access_token()
    article_payload, thumb_media_id, image_replacements = await _build_recruitment_article_payload(
        access_token,
        row,
    )
    media_id = await _add_draft(access_token, [article_payload])
    _save_push_record(
        row=row,
        draft_status="pushed",
        draft_media_id=media_id,
    )

    return {
        "success": True,
        "source_url": row.get("link", ""),
        "title": row.get("title", ""),
        "account_name": row.get("nickname") or row.get("fakeid"),
        "thumb_media_id": thumb_media_id,
        "draft_media_id": media_id,
        "image_replacements": image_replacements,
        "image_replacement_count": len(image_replacements),
    }


async def push_article_row_to_draft(
    row: Dict,
    *,
    save_record: bool = False,
) -> Dict:
    normalized_row = await _ensure_article_content_ready(_normalize_direct_article_row(row))
    access_token = await _get_stable_access_token()
    article_payload, thumb_media_id, image_replacements = await _build_recruitment_article_payload(
        access_token,
        normalized_row,
    )
    media_id = await _add_draft(access_token, [article_payload])
    if save_record and normalized_row.get("link"):
        _save_push_record(
            row=normalized_row,
            draft_status="pushed",
            draft_media_id=media_id,
        )

    return {
        "success": True,
        "source_url": normalized_row.get("link", ""),
        "title": normalized_row.get("title", ""),
        "account_name": normalized_row.get("account_name") or normalized_row.get("hospital") or "",
        "thumb_media_id": thumb_media_id,
        "draft_media_id": media_id,
        "image_replacements": image_replacements,
        "image_replacement_count": len(image_replacements),
    }


async def push_article_row_to_shared_draft_series(
    row: Dict,
    *,
    source_type: str,
    force: bool = False,
    series_key: str = rss_store.DEFAULT_OFFICIAL_DRAFT_SERIES_KEY,
    delete_old_on_rebuild: bool = True,
) -> Dict:
    normalized_row = await _ensure_article_content_ready(_normalize_direct_article_row(row))
    source_link = normalized_row.get("link", "")
    if not source_link:
        raise OfficialWechatDraftError("共享草稿串模式要求文章必须提供 source_url")

    existing_record = rss_store.get_official_draft_record(source_link)
    if existing_record and not force:
        return _build_skipped_duplicate_result(normalized_row, existing_record)

    lease_owner = await _acquire_shared_series_lease(series_key)
    try:
        state = rss_store.get_official_draft_series(series_key)
        current_media_id = (state.get("current_media_id") or "").strip()
        current_article_count = int(state.get("current_article_count", 0) or 0)
        batch_index = int(state.get("batch_index", 0) or 0)

        if current_media_id:
            try:
                access_token = await _get_stable_access_token()
                draft_data = await _get_draft(access_token, current_media_id)
                current_article_count = _count_draft_articles(draft_data)
                rss_store.update_official_draft_series(
                    series_key=series_key,
                    current_media_id=current_media_id,
                    current_article_count=current_article_count,
                    batch_index=batch_index,
                    lease_owner=lease_owner,
                    lease_expires_at=int(time.time()) + DEFAULT_SHARED_DRAFT_LEASE_TTL_SECONDS,
                )
            except OfficialWechatDraftError:
                current_media_id = ""
                current_article_count = 0

        if not current_media_id:
            result = await push_article_row_to_draft(normalized_row, save_record=False)
            next_batch_index = max(batch_index, 0) + 1
            rss_store.update_official_draft_series(
                series_key=series_key,
                current_media_id=result["draft_media_id"],
                current_article_count=1,
                batch_index=next_batch_index,
                lease_owner=lease_owner,
                lease_expires_at=int(time.time()) + DEFAULT_SHARED_DRAFT_LEASE_TTL_SECONDS,
            )
            _save_push_record(
                row=normalized_row,
                draft_status="pushed",
                draft_media_id=result["draft_media_id"],
                append_mode="draft/add",
                source_type=source_type,
                series_batch_index=next_batch_index,
            )
            return {
                **result,
                "status": "pushed",
                "append_mode": "draft/add",
                "article_count_before": 0,
                "article_count_after": 1,
                "batch_index": next_batch_index,
            }

        if current_article_count >= 8:
            result = await push_article_row_to_draft(normalized_row, save_record=False)
            next_batch_index = max(batch_index, 1) + 1
            rss_store.update_official_draft_series(
                series_key=series_key,
                current_media_id=result["draft_media_id"],
                current_article_count=1,
                batch_index=next_batch_index,
                lease_owner=lease_owner,
                lease_expires_at=int(time.time()) + DEFAULT_SHARED_DRAFT_LEASE_TTL_SECONDS,
            )
            _save_push_record(
                row=normalized_row,
                draft_status="pushed",
                draft_media_id=result["draft_media_id"],
                previous_draft_media_id=current_media_id,
                append_mode="rotated_new_batch",
                source_type=source_type,
                series_batch_index=next_batch_index,
            )
            return {
                **result,
                "status": "rotated_new_batch",
                "append_mode": "rotated_new_batch",
                "previous_draft_media_id": current_media_id,
                "article_count_before": current_article_count,
                "article_count_after": 1,
                "batch_index": next_batch_index,
            }

        result = await append_article_row_to_draft(
            draft_media_id=current_media_id,
            row=normalized_row,
            save_record=False,
            delete_old_on_rebuild=delete_old_on_rebuild,
        )
        effective_batch_index = max(batch_index, 1)
        rss_store.update_official_draft_series(
            series_key=series_key,
            current_media_id=result["draft_media_id"],
            current_article_count=int(result.get("article_count_after", current_article_count + 1) or 0),
            batch_index=effective_batch_index,
            lease_owner=lease_owner,
            lease_expires_at=int(time.time()) + DEFAULT_SHARED_DRAFT_LEASE_TTL_SECONDS,
        )
        _save_push_record(
            row=normalized_row,
            draft_status="appended",
            draft_media_id=result["draft_media_id"],
            previous_draft_media_id=result.get("previous_draft_media_id", ""),
            append_mode=result.get("append_mode", "draft/update"),
            source_type=source_type,
            series_batch_index=effective_batch_index,
        )
        return {
            **result,
            "status": "appended",
            "skipped": False,
            "batch_index": effective_batch_index,
        }
    finally:
        rss_store.release_official_draft_series_lease(series_key, lease_owner)


async def append_recruitment_article_to_draft(
    draft_media_id: str,
    source_url: Optional[str] = None,
    force: bool = False,
    delete_old_on_rebuild: bool = True,
) -> Dict:
    target_media_id = (draft_media_id or "").strip()
    if not target_media_id:
        raise OfficialWechatDraftError("draft_media_id 不能为空")

    row = await _ensure_article_content_ready(
        _extract_article_row(source_url=source_url, force=force)
    )
    access_token = await _get_stable_access_token()
    draft_data = await _get_draft(access_token, target_media_id)
    existing_articles = await _prepare_existing_draft_articles_for_rebuild(access_token, draft_data)
    current_count = len(existing_articles)
    if current_count >= 8:
        raise OfficialWechatDraftError("目标草稿已包含 8 篇文章，无法继续追加")

    article_payload, thumb_media_id, image_replacements = await _build_recruitment_article_payload(
        access_token,
        row,
    )

    appended_via = "draft/add_rebuild"
    combined_articles = existing_articles + [article_payload]
    resulting_media_id = await _add_draft(access_token, combined_articles)
    replaced_media_id = target_media_id
    if delete_old_on_rebuild:
        try:
            await _delete_draft(access_token, target_media_id)
        except OfficialWechatDraftError as delete_exc:
            logger.warning("delete old draft failed after rebuild: %s", delete_exc)

    _save_push_record(
        row=row,
        draft_status="appended",
        draft_media_id=resulting_media_id,
        previous_draft_media_id=replaced_media_id,
        append_mode=appended_via,
    )
    return {
        "success": True,
        "source_url": row.get("link", ""),
        "title": row.get("title", ""),
        "account_name": row.get("nickname") or row.get("fakeid"),
        "thumb_media_id": thumb_media_id,
        "draft_media_id": resulting_media_id,
        "previous_draft_media_id": replaced_media_id,
        "article_count_before": current_count,
        "article_count_after": current_count + 1,
        "append_mode": appended_via,
        "image_replacements": image_replacements,
        "image_replacement_count": len(image_replacements),
    }


async def append_article_row_to_draft(
    draft_media_id: str,
    row: Dict,
    *,
    save_record: bool = False,
    delete_old_on_rebuild: bool = True,
) -> Dict:
    target_media_id = (draft_media_id or "").strip()
    if not target_media_id:
        raise OfficialWechatDraftError("draft_media_id 不能为空")

    normalized_row = await _ensure_article_content_ready(_normalize_direct_article_row(row))
    access_token = await _get_stable_access_token()
    draft_data = await _get_draft(access_token, target_media_id)
    existing_articles = await _prepare_existing_draft_articles_for_rebuild(access_token, draft_data)
    current_count = len(existing_articles)
    if current_count >= 8:
        raise OfficialWechatDraftError("目标草稿已包含 8 篇文章，无法继续追加")

    article_payload, thumb_media_id, image_replacements = await _build_recruitment_article_payload(
        access_token,
        normalized_row,
    )

    appended_via = "draft/add_rebuild"
    combined_articles = existing_articles + [article_payload]
    resulting_media_id = await _add_draft(access_token, combined_articles)
    replaced_media_id = target_media_id
    if delete_old_on_rebuild:
        try:
            await _delete_draft(access_token, target_media_id)
        except OfficialWechatDraftError as delete_exc:
            logger.warning("delete old draft failed after rebuild: %s", delete_exc)

    if save_record and normalized_row.get("link"):
        _save_push_record(
            row=normalized_row,
            draft_status="appended",
            draft_media_id=resulting_media_id,
            previous_draft_media_id=replaced_media_id,
            append_mode=appended_via,
        )
    return {
        "success": True,
        "source_url": normalized_row.get("link", ""),
        "title": normalized_row.get("title", ""),
        "account_name": normalized_row.get("account_name") or normalized_row.get("hospital") or "",
        "thumb_media_id": thumb_media_id,
        "draft_media_id": resulting_media_id,
        "previous_draft_media_id": replaced_media_id,
        "article_count_before": current_count,
        "article_count_after": current_count + 1,
        "append_mode": appended_via,
        "image_replacements": image_replacements,
        "image_replacement_count": len(image_replacements),
    }


async def push_recruitment_article_to_shared_draft_series(
    source_url: Optional[str] = None,
    force: bool = False,
    series_key: str = rss_store.DEFAULT_OFFICIAL_DRAFT_SERIES_KEY,
    push_all: bool = False,
    limit: Optional[int] = None,
) -> Dict:
    if push_all and source_url:
        raise OfficialWechatDraftError("批量推送模式不支持同时指定 source_url")

    if push_all:
        max_items = int(limit or 1000)
        rows = rss_store.get_recruitment_articles(
            status="confirmed",
            limit=max_items,
            push_status="all" if force else "unpushed",
            recent_days=rss_store.get_recruitment_push_recent_days(),
            since_subscription=rss_store.get_recruitment_push_since_subscription(),
        )
        if not rows:
            if force:
                raise OfficialWechatDraftError("当前没有 confirmed 招聘文章可推送到共享草稿串")
            raise OfficialWechatDraftError("当前没有未推送到共享草稿串的 confirmed 招聘文章")

        results: List[Dict] = []
        draft_media_ids: List[str] = []
        status_counts: Dict[str, int] = {}

        for row in rows:
            result = await push_article_row_to_shared_draft_series(
                row,
                source_type="recruitment",
                force=force,
                series_key=series_key,
            )
            results.append({
                "source_url": result.get("source_url", row.get("link", "")),
                "title": result.get("title", row.get("title", "")),
                "status": result.get("status", ""),
                "append_mode": result.get("append_mode", ""),
                "draft_media_id": result.get("draft_media_id", ""),
                "batch_index": int(result.get("batch_index", 0) or 0),
            })

            status = str(result.get("status", "") or "")
            if status:
                status_counts[status] = status_counts.get(status, 0) + 1

            draft_media_id = (result.get("draft_media_id") or "").strip()
            if draft_media_id and draft_media_id not in draft_media_ids:
                draft_media_ids.append(draft_media_id)

        return {
            "success": True,
            "mode": "batch",
            "total_candidates": len(rows),
            "processed_count": len(results),
            "created_draft_count": len(draft_media_ids),
            "draft_media_ids": draft_media_ids,
            "status_counts": status_counts,
            "results": results,
        }

    if source_url:
        row = _extract_article_row(source_url=source_url, force=True)
    else:
        rows = rss_store.get_recruitment_articles(
            status="confirmed",
            limit=1,
            push_status="all" if force else "unpushed",
            recent_days=rss_store.get_recruitment_push_recent_days(),
            since_subscription=rss_store.get_recruitment_push_since_subscription(),
        )
        if not rows:
            if force:
                raise OfficialWechatDraftError("当前没有 confirmed 招聘文章可推送到共享草稿串")
            raise OfficialWechatDraftError("当前没有未推送到共享草稿串的 confirmed 招聘文章")
        row = rows[0]

    return await push_article_row_to_shared_draft_series(
        row,
        source_type="recruitment",
        force=force,
        series_key=series_key,
    )


def push_recruitment_article_to_draft_sync(
    source_url: Optional[str] = None,
    force: bool = False,
) -> Dict:
    return asyncio.run(
        push_recruitment_article_to_draft(
            source_url=source_url,
            force=force,
        )
    )


def append_recruitment_article_to_draft_sync(
    draft_media_id: str,
    source_url: Optional[str] = None,
    force: bool = False,
) -> Dict:
    return asyncio.run(
        append_recruitment_article_to_draft(
            draft_media_id=draft_media_id,
            source_url=source_url,
            force=force,
        )
    )
