#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
医院招聘导出接口
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response

from wechat_service.utils import rss_store
from wechat_service.utils.official_wechat_draft import (
    OfficialWechatDraftError,
    append_recruitment_article_to_draft,
    get_shared_draft_series_status,
    push_recruitment_article_to_draft,
    push_recruitment_article_to_shared_draft_series,
)
from wechat_service.utils.official_wx_preview import official_wx_preview_worker

router = APIRouter()


def _format_publish_time(timestamp: int) -> str:
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_article_row(row: dict) -> dict:
    images = rss_store.parse_json_list(row.get("images_json"))
    matched_keywords = rss_store.parse_json_list(row.get("matched_keywords"))
    return {
        "account_name": row.get("nickname") or row.get("fakeid"),
        "fakeid": row.get("fakeid", ""),
        "publish_time": row.get("publish_time", 0),
        "publish_time_str": _format_publish_time(row.get("publish_time", 0)),
        "title": row.get("title", ""),
        "author": row.get("author", ""),
        "digest": row.get("digest", ""),
        "link": row.get("link", ""),
        "cover": row.get("cover", ""),
        "plain_content": row.get("plain_content", ""),
        "content_html": row.get("content", ""),
        "images": images,
        "matched_keywords": matched_keywords,
        "review_status": row.get("review_status", ""),
        "official_draft_status": row.get("official_draft_status", ""),
        "official_draft_media_id": row.get("official_draft_media_id", ""),
        "official_draft_previous_media_id": row.get("official_draft_previous_media_id", ""),
        "official_draft_append_mode": row.get("official_draft_append_mode", ""),
        "official_draft_pushed_at": row.get("official_draft_pushed_at", 0),
        "official_draft_pushed_at_str": _format_publish_time(
            row.get("official_draft_pushed_at", 0)
        ),
    }


@router.get("/recruitment/export", summary="导出医院招聘文章")
async def export_recruitment_articles(
    format: str = Query("json", pattern="^(json|csv)$", description="导出格式"),
    status: str = Query(
        "confirmed",
        pattern="^(confirmed|manual_review|all)$",
        description="筛选状态",
    ),
    push_status: str = Query(
        "all",
        pattern="^(all|pushed|unpushed)$",
        description="是否按草稿推送状态筛选",
    ),
    limit: int = Query(100, ge=1, le=1000, description="导出数量上限"),
    profile: str = Query(
        "full",
        pattern="^(full|title_url)$",
        description="导出视图: full 或 title_url",
    ),
):
    rows = rss_store.get_recruitment_articles(
        status=status,
        limit=limit,
        push_status=push_status,
    )
    normalized = [_normalize_article_row(row) for row in rows]

    if profile == "title_url":
        normalized = [
            {
                "title": row["title"],
                "source_url": row["link"],
                "publish_time": row["publish_time"],
                "publish_time_str": row["publish_time_str"],
                "official_draft_status": row["official_draft_status"],
                "official_draft_pushed_at_str": row["official_draft_pushed_at_str"],
            }
            for row in normalized
        ]

    if format == "json":
        return JSONResponse({
            "success": True,
            "count": len(normalized),
            "status": status,
            "push_status": push_status,
            "profile": profile,
            "data": normalized,
        })

    return _build_csv_response(normalized, status, push_status, profile)


def _build_csv_response(rows: list[dict], status: str, push_status: str, profile: str) -> Response:
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    if profile == "title_url":
        writer.writerow([
            "title",
            "source_url",
            "publish_time_str",
            "official_draft_status",
            "official_draft_pushed_at_str",
        ])
        for row in rows:
            writer.writerow([
                row["title"],
                row["source_url"],
                row["publish_time_str"],
                row["official_draft_status"],
                row["official_draft_pushed_at_str"],
            ])
    else:
        writer.writerow([
            "Account Name",
            "FakeID",
            "Publish Time",
            "Title",
            "Digest",
            "Link",
            "Plain Content",
            "Image URLs",
            "Matched Keywords",
            "Review Status",
            "Official Draft Status",
            "Official Draft Media ID",
            "Official Draft Pushed At",
        ])

        for row in rows:
            writer.writerow([
                row["account_name"],
                row["fakeid"],
                row["publish_time_str"],
                row["title"],
                row["digest"],
                row["link"],
                row["plain_content"],
                "\n".join(row["images"]),
                json.dumps(row["matched_keywords"], ensure_ascii=False),
                row["review_status"],
                row["official_draft_status"],
                row["official_draft_media_id"],
                row["official_draft_pushed_at_str"],
            ])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="recruitment_export_{status}_{push_status}_{profile}.csv"'
            )
        },
    )


@router.post("/recruitment/official-preview/run", summary="手动生成微信官方预览包")
async def run_official_preview(
    limit: int = Query(100, ge=1, le=1000, description="本次最多处理多少篇文章"),
):
    result = await official_wx_preview_worker.run_once(limit=limit)
    return JSONResponse({"success": bool(result.get("success")), "data": result})


@router.get("/recruitment/official-preview/status", summary="查看微信官方预览包状态")
async def official_preview_status():
    return JSONResponse({
        "success": True,
        "data": official_wx_preview_worker.get_status(),
    })


@router.post("/recruitment/official-draft/push", summary="手动推送招聘文章到微信官方草稿箱")
async def push_recruitment_to_official_draft(
    source_url: Optional[str] = Query(None, description="指定要推送的招聘文章原文链接，留空则默认最新一篇 confirmed 文章"),
    force: bool = Query(False, description="是否允许重复推送已按 URL 记录过的文章"),
):
    try:
        result = await push_recruitment_article_to_draft(
            source_url=source_url,
            force=force,
        )
    except OfficialWechatDraftError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(exc)},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"推送草稿箱失败: {exc}"},
        )

    return JSONResponse({"success": True, "data": result})


@router.post("/recruitment/official-draft/series/push", summary="手动推送招聘文章到共享微信草稿串")
async def push_recruitment_to_shared_official_draft_series(
    source_url: Optional[str] = Query(
        None,
        description="指定要推送的招聘文章原文链接，留空则默认最新一篇未进入共享草稿串的 confirmed 文章",
    ),
    force: bool = Query(False, description="是否允许重复推送已按 URL 记录过的文章"),
):
    try:
        result = await push_recruitment_article_to_shared_draft_series(
            source_url=source_url,
            force=force,
        )
    except OfficialWechatDraftError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(exc)},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"推送共享草稿串失败: {exc}"},
        )

    return JSONResponse({"success": True, "data": result})


@router.get("/recruitment/official-draft/series/status", summary="查看共享微信草稿串状态")
async def shared_official_draft_series_status():
    return JSONResponse({
        "success": True,
        "data": get_shared_draft_series_status(),
    })


@router.post("/recruitment/official-draft/append", summary="向已有微信官方草稿追加招聘文章")
async def append_recruitment_to_official_draft(
    draft_media_id: str = Query(..., description="要追加到的微信草稿 media_id"),
    source_url: Optional[str] = Query(None, description="指定要追加的招聘文章原文链接，留空则默认最新一篇 confirmed 文章"),
    force: bool = Query(False, description="是否允许重复追加已按 URL 记录过的文章"),
):
    try:
        result = await append_recruitment_article_to_draft(
            draft_media_id=draft_media_id,
            source_url=source_url,
            force=force,
        )
    except OfficialWechatDraftError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(exc)},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"追加草稿箱失败: {exc}"},
        )

    return JSONResponse({"success": True, "data": result})
