#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from wechat_service.utils.recruitment_filter import recruitment_filter


def _article(title: str, plain_content: str = "", digest: str = "") -> dict:
    return {
        "title": title,
        "digest": digest,
        "plain_content": plain_content,
        "content": "",
        "images_json": "[]",
        "matched_keywords": "[]",
        "filter_stage": "",
        "review_status": "",
        "is_recruitment": 0,
    }


def test_filter_rejects_post_hiring_notice_title():
    article, matched = recruitment_filter.prepare_article(
        _article("2025年公开招聘编外专业技术人员考试总成绩、第二批入闱体检人员名单及体检相关事宜公告")
    )

    assert matched is False
    assert article["review_status"] == "rejected"
    assert article["filter_stage"] == "coarse_excluded"


def test_filter_rejects_post_hiring_candidate_list_title():
    article, matched = recruitment_filter.prepare_article(
        _article("某医院2026年公开招聘工作人员拟录取人员名单")
    )

    assert matched is False
    assert article["review_status"] == "rejected"
    assert article["filter_stage"] == "coarse_excluded"


def test_filter_rejects_non_job_recruitment_campaign_after_finalize():
    article, matched = recruitment_filter.prepare_article(
        _article("对白内障患者招募：50个名额，白内障手术免费做")
    )

    assert matched is False
    assert article["review_status"] == "rejected"
    assert article["filter_stage"] == "coarse_excluded"


def test_filter_rejects_recruitment_activity_title():
    article, matched = recruitment_filter.prepare_article(
        _article("兰州、西安招聘活动诚邀您参加")
    )

    assert matched is False
    assert article["review_status"] == "rejected"
    assert article["filter_stage"] == "coarse_excluded"


def test_filter_allows_job_recruitment_from_weak_title_when_content_is_job_like():
    article, matched = recruitment_filter.prepare_article(
        _article("宜宾四院招募4名见习医疗助理，你符合条件吗？")
    )

    assert matched is True
    assert article["review_status"] == ""
    assert article["filter_stage"] == "coarse_matched"

    article["plain_content"] = "岗位职责：协助临床。应聘条件：护理相关专业。报名方式：邮箱投递简历。"
    finalized = recruitment_filter.finalize_article(article)

    assert finalized["review_status"] == "confirmed"
    assert finalized["filter_stage"] == "content_confirmed"
    assert "岗位" in json.loads(finalized["matched_keywords"])
