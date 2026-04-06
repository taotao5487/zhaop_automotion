#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.paths import ENV_FILE, load_root_env

DEFAULT_SITE_URL = "http://127.0.0.1:5001"
DEFAULT_TIMEOUT = 120.0
NO_ARTICLE_MARKERS = (
    "当前没有未推送到共享草稿串的 confirmed 招聘文章",
    "当前没有 confirmed 招聘文章可推送到共享草稿串",
)


@dataclass
class PushOutcome:
    kind: str
    summary: str
    payload: Dict[str, Any]


def classify_push_response(payload: Dict[str, Any]) -> PushOutcome:
    success = bool(payload.get("success"))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    error = str(payload.get("error") or "").strip()

    if success:
        processed_count = int(data.get("processed_count", 0) or 0)
        if processed_count <= 0:
            return PushOutcome(
                kind="no_articles",
                summary="本次无可推送招聘文章",
                payload=data,
            )
        return PushOutcome(
            kind="success",
            summary=f"processed {processed_count} article(s)",
            payload=data,
        )

    if any(marker in error for marker in NO_ARTICLE_MARKERS):
        return PushOutcome(
            kind="no_articles",
            summary="本次无可推送招聘文章",
            payload={"error": error},
        )

    return PushOutcome(
        kind="failure",
        summary=error or "自动推送共享草稿串失败",
        payload={"error": error},
    )


def build_wecom_markdown_message(kind: str, data: Dict[str, Any], *, run_label: str) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"> 执行时间: {now_str}", f"> 触发批次: {run_label}"]

    if kind == "success":
        processed_count = int(data.get("processed_count", 0) or 0)
        created_draft_count = int(data.get("created_draft_count", 0) or 0)
        draft_media_ids = [str(item) for item in data.get("draft_media_ids", []) if str(item).strip()]
        status_counts = data.get("status_counts") or {}
        status_text = ", ".join(f"{key}={value}" for key, value in status_counts.items()) or "无"

        return "\n".join(
            [
                "**自动推送成功**",
                *lines,
                f"> 推送篇数: {processed_count}",
                f"> 新建草稿串数: {created_draft_count}",
                f"> 草稿 media_id: {', '.join(draft_media_ids) if draft_media_ids else '无'}",
                f"> 状态统计: {status_text}",
            ]
        )

    if kind == "no_articles":
        summary = str(data.get("summary") or "本次无可推送招聘文章")
        return "\n".join(
            [
                "**自动推送无新文章**",
                *lines,
                f"> {summary}",
            ]
        )

    error = str(data.get("summary") or data.get("error") or "自动推送共享草稿串失败")
    return "\n".join(
        [
            "**自动推送失败**",
            *lines,
            f"> 错误: {error}",
        ]
    )


def load_runtime_config() -> Dict[str, str]:
    load_root_env(override=False)
    return {
        "site_url": (os.getenv("SITE_URL", DEFAULT_SITE_URL) or DEFAULT_SITE_URL).rstrip("/"),
        "webhook_url": (os.getenv("WEBHOOK_URL", "") or "").strip(),
    }


def build_push_endpoint(site_url: str, *, limit: Optional[int] = None) -> str:
    endpoint = f"{site_url}/api/recruitment/official-draft/series/push?all=true"
    if limit is not None:
        endpoint += f"&limit={int(limit)}"
    return endpoint


def send_wecom_markdown(webhook_url: str, markdown_text: str, *, timeout: float = 10.0) -> bool:
    if not webhook_url:
        return False

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": markdown_text},
    }
    response = httpx.post(webhook_url, json=payload, timeout=timeout)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        body = response.json()
        if body.get("errcode", 0) != 0:
            raise RuntimeError(f"webhook errcode={body.get('errcode')}: {body.get('errmsg', 'unknown')}")
    return True


def run_auto_push(*, site_url: str, webhook_url: str, limit: Optional[int], run_label: str) -> PushOutcome:
    endpoint = build_push_endpoint(site_url, limit=limit)

    try:
        response = httpx.post(endpoint, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        error_payload: Dict[str, Any]
        try:
            error_payload = exc.response.json()
        except Exception:
            error_payload = {"success": False, "error": f"HTTP {exc.response.status_code}"}
        outcome = classify_push_response(error_payload)
    except Exception as exc:
        outcome = PushOutcome(
            kind="failure",
            summary=str(exc),
            payload={"error": str(exc)},
        )
    else:
        outcome = classify_push_response(payload)

    message = build_wecom_markdown_message(
        outcome.kind,
        {
            **outcome.payload,
            "summary": outcome.summary,
        },
        run_label=run_label,
    )

    try:
        send_wecom_markdown(webhook_url, message)
    except Exception as exc:
        print(f"[WARN] webhook send failed: {exc}", file=sys.stderr)

    return outcome


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-push recruitment articles into the shared WeChat draft series.")
    parser.add_argument("--site-url", default=None, help="Override SITE_URL from .env")
    parser.add_argument("--webhook-url", default=None, help="Override WEBHOOK_URL from .env")
    parser.add_argument("--limit", type=int, default=None, help="Optional batch limit appended to the push API")
    parser.add_argument("--label", default=None, help="Run label used in the webhook message, default current HH:MM")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_runtime_config()
    site_url = (args.site_url or config["site_url"]).rstrip("/")
    webhook_url = args.webhook_url or config["webhook_url"]
    run_label = args.label or datetime.now().strftime("%H:%M")

    outcome = run_auto_push(
        site_url=site_url,
        webhook_url=webhook_url,
        limit=args.limit,
        run_label=run_label,
    )

    print(
        json.dumps(
            {
                "kind": outcome.kind,
                "summary": outcome.summary,
                "payload": outcome.payload,
                "env_file": str(ENV_FILE),
                "site_url": site_url,
            },
            ensure_ascii=False,
        )
    )
    return 0 if outcome.kind in {"success", "no_articles"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
