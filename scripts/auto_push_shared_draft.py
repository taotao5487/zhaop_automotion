#!/usr/bin/env python3

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_SITE_URL = "http://127.0.0.1:5001"
DEFAULT_API_TIMEOUT = 900.0
DEFAULT_WEBHOOK_TIMEOUT = 30.0
ENV_FILE = ROOT_DIR / ".env"
NO_ARTICLE_MARKERS = (
    "当前没有未推送到共享草稿串的 confirmed 招聘文章",
    "当前没有 confirmed 招聘文章可推送到共享草稿串",
)


class PushOutcome:
    def __init__(self, kind, summary, payload):
        self.kind = kind
        self.summary = summary
        self.payload = payload


def _parse_timeout_env(name: str, default: float) -> float:
    raw_value = (os.getenv(name, "") or "").strip()
    if not raw_value:
        return float(default)
    try:
        parsed = float(raw_value)
    except ValueError:
        return float(default)
    if parsed <= 0:
        return float(default)
    return parsed


def _format_timeout_seconds(timeout: float) -> str:
    timeout_value = float(timeout)
    if timeout_value.is_integer():
        return str(int(timeout_value))
    return f"{timeout_value:g}"


def _first_result_error(data):
    results = data.get("results") if isinstance(data, dict) else []
    if not isinstance(results, list):
        return ""
    for item in results:
        if not isinstance(item, dict):
            continue
        error = str(item.get("error") or "").strip()
        if error:
            return error
    return ""


def classify_push_response(payload):
    success = bool(payload.get("success"))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    error = str(payload.get("error") or "").strip()

    if success:
        processed_count = int(data.get("processed_count", 0) or 0)
        skipped_count = int(data.get("skipped_count", 0) or 0)
        failed_count = int(data.get("failed_count", 0) or 0)
        if processed_count > 0:
            return PushOutcome(
                kind="success",
                summary=f"processed {processed_count} article(s)",
                payload=data,
            )
        if skipped_count > 0 and failed_count <= 0:
            return PushOutcome(
                kind="success",
                summary=f"skipped {skipped_count} unavailable article(s)",
                payload=data,
            )
        if failed_count > 0:
            return PushOutcome(
                kind="failure",
                summary=_first_result_error(data) or f"{failed_count} article(s) failed",
                payload=data,
            )
        if processed_count <= 0:
            return PushOutcome(
                kind="no_articles",
                summary="本次无可推送招聘文章",
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


def build_wecom_markdown_message(kind, data, run_label):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"> 执行时间: {now_str}", f"> 触发批次: {run_label}"]

    if kind == "success":
        processed_count = int(data.get("processed_count", 0) or 0)
        skipped_count = int(data.get("skipped_count", 0) or 0)
        failed_count = int(data.get("failed_count", 0) or 0)
        created_draft_count = int(data.get("created_draft_count", 0) or 0)
        draft_media_ids = [str(item) for item in data.get("draft_media_ids", []) if str(item).strip()]
        status_counts = data.get("status_counts") or {}
        status_text = ", ".join(f"{key}={value}" for key, value in status_counts.items()) or "无"

        message_lines = [
            "**自动推送成功**",
            *lines,
            f"> 推送篇数: {processed_count}",
            f"> 新建草稿串数: {created_draft_count}",
            f"> 草稿 media_id: {', '.join(draft_media_ids) if draft_media_ids else '无'}",
        ]
        if skipped_count > 0:
            message_lines.append(f"> 跳过失效文章: {skipped_count}")
        if failed_count > 0:
            message_lines.append(f"> 失败篇数: {failed_count}")
        message_lines.append(f"> 状态统计: {status_text}")
        return "\n".join(message_lines)

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


def load_runtime_config():
    if ENV_FILE.exists():
        load_dotenv(str(ENV_FILE), override=False)
    return {
        "site_url": (os.getenv("SITE_URL", DEFAULT_SITE_URL) or DEFAULT_SITE_URL).rstrip("/"),
        "webhook_url": (os.getenv("WEBHOOK_URL", "") or "").strip(),
        "api_timeout": _parse_timeout_env("AUTO_PUSH_API_TIMEOUT_SECONDS", DEFAULT_API_TIMEOUT),
        "webhook_timeout": _parse_timeout_env(
            "AUTO_PUSH_WEBHOOK_TIMEOUT_SECONDS",
            DEFAULT_WEBHOOK_TIMEOUT,
        ),
    }


def build_push_endpoint(site_url, limit=None):
    endpoint = f"{site_url}/api/recruitment/official-draft/series/push?all=true"
    if limit is not None:
        endpoint += f"&limit={int(limit)}"
    return endpoint


def send_wecom_markdown(
    webhook_url,
    markdown_text,
    timeout=DEFAULT_WEBHOOK_TIMEOUT,
):
    if not webhook_url:
        return False

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": markdown_text},
    }
    try:
        response = httpx.post(webhook_url, json=payload, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            f"企业微信机器人通知超时（{_format_timeout_seconds(timeout)}秒）"
        ) from exc
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        body = response.json()
        if body.get("errcode", 0) != 0:
            raise RuntimeError(f"webhook errcode={body.get('errcode')}: {body.get('errmsg', 'unknown')}")
    return True


def run_auto_push(
    site_url,
    webhook_url,
    limit,
    run_label,
    api_timeout=DEFAULT_API_TIMEOUT,
    webhook_timeout=DEFAULT_WEBHOOK_TIMEOUT,
):
    endpoint = build_push_endpoint(site_url, limit=limit)

    try:
        response = httpx.post(endpoint, timeout=api_timeout)
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        timeout_text = _format_timeout_seconds(api_timeout)
        outcome = PushOutcome(
            kind="failure",
            summary=f"推送接口超时（{timeout_text}秒）",
            payload={
                "error": f"推送接口超时（{timeout_text}秒）",
                "exception": str(exc),
            },
        )
    except httpx.HTTPStatusError as exc:
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

    message_payload = dict(outcome.payload)
    message_payload["summary"] = outcome.summary
    message = build_wecom_markdown_message(
        outcome.kind,
        message_payload,
        run_label,
    )

    try:
        send_wecom_markdown(webhook_url, message, timeout=webhook_timeout)
    except Exception as exc:
        print(f"[WARN] webhook send failed: {exc}", file=sys.stderr)

    return outcome


def parse_args():
    parser = argparse.ArgumentParser(description="Auto-push recruitment articles into the shared WeChat draft series.")
    parser.add_argument("--site-url", default=None, help="Override SITE_URL from .env")
    parser.add_argument("--webhook-url", default=None, help="Override WEBHOOK_URL from .env")
    parser.add_argument("--limit", type=int, default=None, help="Optional batch limit appended to the push API")
    parser.add_argument("--label", default=None, help="Run label used in the webhook message, default current HH:MM")
    parser.add_argument(
        "--api-timeout",
        type=float,
        default=None,
        help=f"Push API timeout in seconds, default {int(DEFAULT_API_TIMEOUT)}",
    )
    parser.add_argument(
        "--webhook-timeout",
        type=float,
        default=None,
        help=f"WeCom webhook timeout in seconds, default {int(DEFAULT_WEBHOOK_TIMEOUT)}",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_runtime_config()
    site_url = (args.site_url or config["site_url"]).rstrip("/")
    webhook_url = args.webhook_url or config["webhook_url"]
    run_label = args.label or datetime.now().strftime("%H:%M")
    api_timeout = float(
        args.api_timeout if args.api_timeout and args.api_timeout > 0 else config["api_timeout"]
    )
    webhook_timeout = float(
        args.webhook_timeout if args.webhook_timeout and args.webhook_timeout > 0 else config["webhook_timeout"]
    )

    outcome = run_auto_push(
        site_url=site_url,
        webhook_url=webhook_url,
        limit=args.limit,
        run_label=run_label,
        api_timeout=api_timeout,
        webhook_timeout=webhook_timeout,
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
