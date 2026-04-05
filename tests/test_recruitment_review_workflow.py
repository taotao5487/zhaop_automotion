#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from wechat_service.utils.recruitment_review_workflow import (
    classify_title_for_review,
    derive_rule_suggestions,
    merge_rules,
)


def test_classify_title_marks_primary_recruitment_as_keep():
    result = classify_title_for_review("川北医学院附属医院2026年度招聘公告")

    assert result.decision == "keep"
    assert "招聘公告" in result.reasons


def test_classify_title_marks_post_hiring_notice_as_exclude():
    result = classify_title_for_review("广元市中医医院2026年公开招聘编外工作人员体检入围人员公示")

    assert result.decision == "exclude"
    assert "公示" in result.reasons


def test_classify_title_marks_process_notice_as_exclude():
    result = classify_title_for_review("重庆市区县事业单位2026年第一季度考核招聘工作人员考试报名统计2026-02-04")

    assert result.decision == "exclude"
    assert "报名统计" in result.reasons


def test_classify_title_marks_theory_exam_notice_as_exclude():
    result = classify_title_for_review("关于重医附一院编外人员招聘理论考核的通知")

    assert result.decision == "exclude"
    assert "理论考核" in result.reasons


def test_classify_title_marks_borderline_recruitment_as_keep():
    result = classify_title_for_review("宜宾市第四人民医院2026年第二次公开招募就业见习人员公告")

    assert result.decision == "keep"
    assert "见习" in result.reasons


def test_classify_title_marks_hotline_operator_as_exclude():
    result = classify_title_for_review("重庆市12356心理援助热线接线员招募")

    assert result.decision == "exclude"
    assert "热线接线员" in result.reasons


def test_classify_title_marks_training_notice_as_keep():
    result = classify_title_for_review("自贡市第一人民医院2026年住院医师规范化培训招收简章（第四批）")

    assert result.decision == "keep"
    assert "规培" in result.reasons or "培训" in result.reasons or "规范化培训" in result.reasons


def test_classify_title_marks_further_study_notice_as_keep():
    result = classify_title_for_review("某医院2026年进修招生公告")

    assert result.decision == "keep"
    assert "进修" in result.reasons


def test_classify_title_marks_announcement_style_recruitment_as_keep():
    result = classify_title_for_review("遂宁市第一人民医院关于招聘护理专业技术人员公告")

    assert result.decision == "keep"
    assert "公告型招聘标题" in result.reasons


def test_derive_rule_suggestions_prefers_discard_only_terms():
    rows = [
        {
            "title": "公开招聘工作人员面试成绩及体检安排公告",
            "manual_label": "",
        },
        {
            "title": "公开招聘工作人员资格复审通知",
            "manual_label": "discard",
        },
        {
            "title": "医院2026年度招聘公告",
            "manual_label": "keep",
        },
    ]

    suggestions = derive_rule_suggestions(rows)

    assert "资格复审" in suggestions["title_exclude"]
    assert "面试" in suggestions["title_exclude"]
    assert "招聘公告" not in suggestions["title_exclude"]


def test_merge_rules_appends_without_duplicates():
    merged = merge_rules(
        {
            "title_include": ["招聘公告"],
            "title_exclude": ["公示"],
            "content_include": ["招聘"],
            "content_confirm": ["岗位职责"],
            "exclude_any": ["活动"],
        },
        {
            "title_exclude": ["公示", "面试"],
            "exclude_any": ["活动", "采购"],
        },
    )

    assert merged["title_exclude"] == ["公示", "面试"]
    assert merged["exclude_any"] == ["活动", "采购"]
