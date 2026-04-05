#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
招聘标题审核工作流工具。

目标是先用高精度规则把明显的招聘/非招聘分开，只把边界样本交给人工复核。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List

from wechat_service.utils.recruitment_filter import DEFAULT_RULES
from wechat_service.utils.recruitment_keyword_sets import (
    NON_RECRUITMENT_SUPPLEMENTAL_KEYWORDS,
    POST_RECRUITMENT_EXCLUDE_KEYWORDS,
    merge_keyword_lists,
)


STRONG_KEEP_KEYWORDS = [
    "招聘公告",
    "招聘简章",
    "招聘启事",
    "公开招聘",
    "诚聘",
    "招贤纳士",
    "人才引进",
    "招聘正式启动",
]

STRONG_EXCLUDE_KEYWORDS = merge_keyword_lists(
    POST_RECRUITMENT_EXCLUDE_KEYWORDS,
    NON_RECRUITMENT_SUPPLEMENTAL_KEYWORDS,
)

REVIEW_KEYWORDS = [
    "见习",
    "科研助理",
    "劳务派遣",
    "派遣",
    "合同制",
    "辅助岗",
]

NON_RECRUITMENT_REVIEW_KEYWORDS = [
    "培训",
    "志愿者",
    "志愿服务",
    "实习生",
    "外包岗位",
    "外包",
]

DERIVATION_CANDIDATES = STRONG_EXCLUDE_KEYWORDS + REVIEW_KEYWORDS + NON_RECRUITMENT_REVIEW_KEYWORDS + [
    "补充公告",
    "延期",
    "调减",
    "取消",
]


@dataclass(frozen=True)
class TitleReviewDecision:
    decision: str
    reasons: List[str]


def _normalize_label(label: str) -> str:
    value = (label or "").strip().lower()
    if value in {"keep", "yes", "y", "1", "招聘", "recruitment"}:
        return "keep"
    if value in {"discard", "drop", "exclude", "no", "n", "0", "非招聘"}:
        return "discard"
    return ""


def _collect_hits(title: str, keywords: Iterable[str]) -> List[str]:
    text = (title or "").strip().lower()
    hits: List[str] = []
    for keyword in keywords:
        normalized = keyword.lower()
        if normalized and normalized in text and keyword not in hits:
            hits.append(keyword)
    return hits


def classify_title_for_review(title: str) -> TitleReviewDecision:
    exclude_hits = _collect_hits(title, STRONG_EXCLUDE_KEYWORDS)
    if exclude_hits:
        return TitleReviewDecision("exclude", exclude_hits)

    keep_hits = _collect_hits(title, STRONG_KEEP_KEYWORDS)
    if keep_hits:
        return TitleReviewDecision("keep", keep_hits)

    training_hits = _collect_hits(title, ["规培", "规范化培训", "进修"])
    if training_hits and any(token in (title or "") for token in ["招聘", "招收", "招生", "简章", "公告", "通知"]):
        return TitleReviewDecision("keep", training_hits)

    non_recruitment_hits = _collect_hits(title, NON_RECRUITMENT_REVIEW_KEYWORDS)
    if non_recruitment_hits:
        return TitleReviewDecision("exclude", non_recruitment_hits)

    review_hits = _collect_hits(title, REVIEW_KEYWORDS)
    if review_hits:
        return TitleReviewDecision("keep", review_hits)

    normalized_title = (title or "").strip()
    if (
        ("招聘" in normalized_title or "招募" in normalized_title or "引进" in normalized_title)
        and any(token in normalized_title for token in ["公告", "通告", "简章", "启事", "信息"])
    ):
        return TitleReviewDecision("keep", ["公告型招聘标题"])

    if "招聘" in normalized_title or "招募" in normalized_title or "引进" in normalized_title:
        return TitleReviewDecision("keep", ["泛招聘词"])

    return TitleReviewDecision("exclude", ["未命中招聘特征"])


def derive_rule_suggestions(rows: Iterable[dict]) -> Dict[str, List[str]]:
    discard_counter: Counter[str] = Counter()
    keep_counter: Counter[str] = Counter()

    for row in rows:
        title = row.get("title", "")
        normalized_label = _normalize_label(row.get("manual_label", ""))
        if not normalized_label:
            normalized_label = "discard"

        hits = _collect_hits(title, DERIVATION_CANDIDATES)
        for hit in hits:
            if normalized_label == "keep":
                keep_counter[hit] += 1
            else:
                discard_counter[hit] += 1

    title_exclude = [
        keyword
        for keyword, count in discard_counter.most_common()
        if count >= 1 and keep_counter.get(keyword, 0) == 0
    ]

    return {
        "title_exclude": title_exclude,
        "title_include": [],
        "content_include": [],
        "content_confirm": [],
        "exclude_any": [],
    }


def merge_rules(
    base_rules: Dict[str, List[str]],
    suggestions: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    merged: Dict[str, List[str]] = {}
    for key, default_values in DEFAULT_RULES.items():
        values: List[str] = list(base_rules.get(key, default_values))
        for suggestion in suggestions.get(key, []):
            if suggestion not in values:
                values.append(suggestion)
        merged[key] = values
    return merged
