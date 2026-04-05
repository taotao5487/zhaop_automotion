#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
医院招聘文章过滤规则
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple

from shared.paths import CONFIG_DIR
from wechat_service.utils.recruitment_keyword_sets import (
    POST_RECRUITMENT_EXCLUDE_KEYWORDS,
)

logger = logging.getLogger(__name__)


DEFAULT_RULES = {
    "title_include": [
        "招聘",
        "招募",
        "诚聘",
        "公开招聘",
        "人才招聘",
        "人才引进",
        "招聘公告",
        "招聘简章",
        "招聘启事",
        "聘用",
        "岗位需求",
        "报名通知",
        "offer已就位",
        "offer",
        "等您来发光",
        "等你来发光",
        "职等你来",
        "虚位以待",
        "加入",
        "加入我们",
        "加入我们吧",
        "加入团队",
        "欢迎加入",
        "期待你的加入",
        "期待您的加入",
        "等你加入",
        "等您加入",
        "诚邀加入",
        "诚邀加盟",
        "诚邀英才",
        "英才",
        "纳贤",
        "引才",
        "招才",
        "伯乐",
        "火热招募",
        "寻找发光的你",
        "寻找发光的您",
    ],
    "title_exclude": list(POST_RECRUITMENT_EXCLUDE_KEYWORDS) + [
        "成绩",
        "复审",
        "面试相关事项",
    ],
    "content_include": [
        "招聘",
        "岗位",
        "报名",
        "应聘",
        "笔试",
        "面试",
        "录用",
        "聘用",
        "岗位职责",
        "招聘条件",
        "报名方式",
        "报名时间",
        "薪资待遇",
        "人才引进",
    ],
    "content_confirm": [
        "招聘",
        "岗位",
        "应聘",
        "岗位职责",
        "招聘条件",
        "报名方式",
        "薪资待遇",
        "人才引进",
    ],
    "exclude_any": [
        "会议",
        "讲座",
        "培训",
        "义诊",
        "直播",
        "活动预告",
        "新闻速递",
        "科普",
        "节日",
        "宣传",
        "表彰",
        "投票",
    ],
}

SHORT_TEXT_REVIEW_THRESHOLD = 80
TITLE_AUTO_CONFIRM_KEYWORDS = [
    "招聘",
    "公开招聘",
    "招聘公告",
    "招聘启事",
    "招聘简章",
    "诚聘",
    "人才招聘",
    "人才引进",
    "招贤纳士",
    "聘用",
]


def _normalize_text(value: str) -> str:
    return (value or "").strip().lower()


class RecruitmentFilter:
    def __init__(self):
        self._cached_path: str | None = None
        self._cached_mtime: float | None = None
        self._cached_rules: Dict[str, List[str]] | None = None

    def get_rules(self) -> Dict[str, List[str]]:
        path = self._find_rules_path()
        mtime = path.stat().st_mtime if path else None

        if (
            self._cached_rules is not None
            and self._cached_path == (str(path) if path else None)
            and self._cached_mtime == mtime
        ):
            return self._cached_rules

        rules = dict(DEFAULT_RULES)
        if path:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    for key in DEFAULT_RULES:
                        values = loaded.get(key)
                        if isinstance(values, list):
                            rules[key] = [
                                str(item).strip()
                                for item in values
                                if str(item).strip()
                            ]
                    logger.info("Loaded recruitment rules from %s", path)
            except Exception as exc:
                logger.warning("Failed to load recruitment rules from %s: %s", path, exc)

        self._cached_path = str(path) if path else None
        self._cached_mtime = mtime
        self._cached_rules = rules
        return rules

    def coarse_match(self, title: str, digest: str = "") -> Tuple[bool, List[str], bool]:
        rules = self.get_rules()
        combined = "\n".join([title or "", digest or ""])
        include_hits = self._match_keywords(combined, rules["title_include"])
        title_exclude_hits = self._match_keywords(title, rules["title_exclude"])
        exclude_hits = self._match_keywords(combined, rules["exclude_any"])

        if title_exclude_hits:
            return False, self._dedupe(include_hits), True

        return bool(include_hits) and not bool(exclude_hits), include_hits, bool(exclude_hits)

    def prepare_article(self, article: Dict) -> Tuple[Dict, bool]:
        matched, include_hits, excluded = self.coarse_match(
            article.get("title", ""),
            article.get("digest", ""),
        )
        rules = self.get_rules()
        title_exclude_hits = self._match_keywords(article.get("title", ""), rules["title_exclude"])
        title_hits = self._dedupe(
            self._match_keywords(article.get("title", ""), TITLE_AUTO_CONFIRM_KEYWORDS)
        )

        article["is_recruitment"] = 0
        article["matched_keywords"] = json.dumps(include_hits, ensure_ascii=False)
        article["filter_stage"] = ""
        article["review_status"] = ""

        if title_exclude_hits:
            article["filter_stage"] = "coarse_excluded"
            article["review_status"] = "rejected"
            return article, False

        if title_hits:
            article["is_recruitment"] = 1
            article["matched_keywords"] = json.dumps(title_hits, ensure_ascii=False)
            article["filter_stage"] = "title_confirmed"
            article["review_status"] = "confirmed"
            return article, True

        if excluded:
            article["filter_stage"] = "coarse_excluded"
            article["review_status"] = "rejected"
            return article, False

        if matched:
            article["filter_stage"] = "coarse_matched"
            return article, True

        return article, False

    def finalize_article(self, article: Dict) -> Dict:
        rules = self.get_rules()
        existing_hits = self._safe_load_json_list(article.get("matched_keywords"))
        plain_content = article.get("plain_content", "") or ""
        title = article.get("title", "") or ""
        digest = article.get("digest", "") or ""
        title_exclude_hits = self._match_keywords(title, rules["title_exclude"])
        title_priority_hits = self._dedupe(
            self._match_keywords(title, TITLE_AUTO_CONFIRM_KEYWORDS)
        )
        full_text = "\n".join([title, digest, plain_content])
        exclude_hits = self._match_keywords(full_text, rules["exclude_any"])
        images = article.get("images")
        if images is None:
            images = self._safe_load_json_list(article.get("images_json"))

        if title_exclude_hits:
            article["is_recruitment"] = 0
            article["review_status"] = "rejected"
            article["filter_stage"] = "content_excluded"
            article["matched_keywords"] = json.dumps(existing_hits, ensure_ascii=False)
            return article

        if title_priority_hits:
            merged_hits = self._dedupe(existing_hits + title_priority_hits)
            content_hits = self._match_keywords(plain_content, rules["content_include"])
            merged_hits = self._dedupe(merged_hits + content_hits)
            article["matched_keywords"] = json.dumps(merged_hits, ensure_ascii=False)
            article["is_recruitment"] = 1
            article["review_status"] = "confirmed"
            article["filter_stage"] = "content_confirmed" if content_hits else "title_confirmed"
            return article

        if exclude_hits:
            article["is_recruitment"] = 0
            article["review_status"] = "rejected"
            article["filter_stage"] = "content_excluded"
            article["matched_keywords"] = json.dumps(existing_hits, ensure_ascii=False)
            return article

        content_hits = self._match_keywords(plain_content, rules["content_include"])
        confirm_hits = self._match_keywords(plain_content, rules["content_confirm"])
        all_hits = self._dedupe(existing_hits + content_hits)
        article["matched_keywords"] = json.dumps(all_hits, ensure_ascii=False)

        if confirm_hits:
            article["is_recruitment"] = 1
            article["review_status"] = "confirmed"
            article["filter_stage"] = "content_confirmed"
            return article

        if existing_hits and images and len(plain_content.strip()) < SHORT_TEXT_REVIEW_THRESHOLD:
            article["is_recruitment"] = 0
            article["review_status"] = "manual_review"
            article["filter_stage"] = "manual_review"
            return article

        if existing_hits:
            article["is_recruitment"] = 0
            article["review_status"] = "rejected"
            article["filter_stage"] = "content_rejected"

        return article

    def _find_rules_path(self) -> Path | None:
        candidates = []
        configured_path = os.getenv("RECRUITMENT_RULES_PATH", "").strip()
        if configured_path:
            candidates.append(Path(configured_path).expanduser())

        candidates.append(CONFIG_DIR / "recruitment_rules.json")
        candidates.append(CONFIG_DIR / "recruitment_rules.example.json")

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _match_keywords(text: str, keywords: List[str]) -> List[str]:
        normalized_text = _normalize_text(text)
        if not normalized_text:
            return []
        hits = []
        for keyword in keywords:
            normalized_keyword = _normalize_text(keyword)
            if normalized_keyword and normalized_keyword in normalized_text:
                hits.append(keyword)
        return RecruitmentFilter._dedupe(hits)

    @staticmethod
    def _dedupe(items: List[str]) -> List[str]:
        seen = set()
        result = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    @staticmethod
    def _safe_load_json_list(value) -> List[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if not value:
            return []
        try:
            loaded = json.loads(value)
            if isinstance(loaded, list):
                return [str(item) for item in loaded]
        except Exception:
            pass
        return []


recruitment_filter = RecruitmentFilter()
