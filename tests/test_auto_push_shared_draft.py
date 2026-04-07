#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from scripts.auto_push_shared_draft import (  # noqa: E402
    build_wecom_markdown_message,
    classify_push_response,
)


def test_classify_push_response_success():
    payload = {
        "success": True,
        "data": {
            "processed_count": 3,
            "created_draft_count": 1,
            "draft_media_ids": ["draft-1"],
            "status_counts": {"pushed": 1, "appended": 2},
        },
    }

    outcome = classify_push_response(payload)

    assert outcome.kind == "success"
    assert outcome.summary == "processed 3 article(s)"


def test_classify_push_response_skipped_unavailable_is_not_failure():
    payload = {
        "success": True,
        "data": {
            "processed_count": 0,
            "skipped_count": 1,
            "failed_count": 0,
            "status_counts": {"skipped_unavailable": 1},
            "results": [
                {
                    "source_url": "https://example.com/deleted",
                    "status": "skipped_unavailable",
                    "error": "文章正文不可用: 已被发布者删除",
                }
            ],
        },
    }

    outcome = classify_push_response(payload)

    assert outcome.kind == "success"
    assert outcome.summary == "skipped 1 unavailable article(s)"


def test_classify_push_response_all_failures_stays_failure():
    payload = {
        "success": True,
        "data": {
            "processed_count": 0,
            "skipped_count": 0,
            "failed_count": 1,
            "results": [
                {
                    "source_url": "https://example.com/broken",
                    "status": "failed",
                    "error": "推送共享草稿串失败: timeout",
                }
            ],
        },
    }

    outcome = classify_push_response(payload)

    assert outcome.kind == "failure"
    assert "timeout" in outcome.summary


def test_classify_push_response_no_articles_from_api_error():
    payload = {
        "success": False,
        "error": "当前没有未推送到共享草稿串的 confirmed 招聘文章",
    }

    outcome = classify_push_response(payload)

    assert outcome.kind == "no_articles"
    assert "无可推送招聘文章" in outcome.summary


def test_classify_push_response_failure_uses_error_message():
    payload = {
        "success": False,
        "error": "推送共享草稿串失败: timeout",
    }

    outcome = classify_push_response(payload)

    assert outcome.kind == "failure"
    assert "timeout" in outcome.summary


def test_build_wecom_markdown_message_for_success_contains_counts():
    message = build_wecom_markdown_message(
        "success",
        {
            "processed_count": 4,
            "skipped_count": 1,
            "failed_count": 0,
            "created_draft_count": 1,
            "draft_media_ids": ["draft-1"],
            "status_counts": {"pushed": 1, "appended": 3, "skipped_unavailable": 1},
        },
        run_label="10:00",
    )

    assert "自动推送成功" in message
    assert "10:00" in message
    assert "4" in message
    assert "draft-1" in message
    assert "跳过" in message


def test_build_wecom_markdown_message_for_no_articles_is_concise():
    message = build_wecom_markdown_message(
        "no_articles",
        {"summary": "本次无可推送招聘文章"},
        run_label="16:30",
    )

    assert "无新文章" in message
    assert "16:30" in message
