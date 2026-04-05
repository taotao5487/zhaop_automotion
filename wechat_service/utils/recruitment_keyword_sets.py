#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
共享的招聘标题关键词集合。
"""

from __future__ import annotations

from typing import Iterable, List


POST_RECRUITMENT_EXCLUDE_KEYWORDS = [
    "公示",
    "名单",
    "拟聘",
    "拟聘用",
    "拟录",
    "拟录用",
    "拟录取",
    "拟补充录用",
    "录取",
    "录用",
    "体检",
    "入围",
    "入闱",
    "递补",
    "补录名单",
    "总成绩",
    "综合成绩",
    "考试成绩",
    "成绩公布",
    "成绩查询",
    "资格复审",
    "资格初审",
    "资格审核",
    "资格审查",
    "资格审查结果",
    "资格复审结果",
    "现场资格审查",
    "进入面试",
    "面试",
    "面试成绩",
    "笔试",
    "笔试成绩",
    "考核安排",
    "考试安排",
    "理论考核",
    "实操考试",
    "考务安排",
    "考核流程",
    "报名统计",
    "排名",
    "通过人员名单",
    "入围人员名单",
    "体检人员名单",
    "录用人员名单",
    "拟聘用人员名单",
    "拟录取人员名单",
    "结果公示",
    "排班公示",
    "患者",
    "志愿者",
    "筛查",
    "公益",
    "活动",
    "考核公告",
]

NON_RECRUITMENT_SUPPLEMENTAL_KEYWORDS = [
    "招标",
    "采购",
    "比选",
    "遴选",
    "询价",
    "谈判",
    "磋商",
    "中标",
    "成交",
    "废标",
    "流标",
    "维保",
    "项目",
    "调研",
    "征集",
    "设备",
    "耗材",
    "试剂",
    "招租",
    "需求调查",
    "热线接线员",
    "更正公告",
    "补充公告",
    "延期",
    "延长报名时间",
    "取消的公告",
    "调减和取消",
    "岗位取消",
    "岗位调减",
    "简历模板",
    "报名表",
    "考试活动",
]


def merge_keyword_lists(*keyword_groups: Iterable[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for group in keyword_groups:
        for keyword in group or []:
            text = str(keyword or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged
