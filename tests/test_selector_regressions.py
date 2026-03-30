#!/usr/bin/env python3

from __future__ import annotations

import csv
from pathlib import Path

from bs4 import BeautifulSoup


HCRMYY_HOME_SNIPPET = """
<ul class="n2 xx2_lb">
  <li><a href="/list/442/5432.html">半导体激光脱毛仪、半导体激光治疗仪采购项目...</a></li>
  <li><a href="/list/442/5431.html">2026019除颤仪（第二次）</a></li>
  <li><a href="/list/287/5419.html">重庆小蚂蚁人力资源服务集团有限公司公开招聘...</a></li>
  <li><a href="/list/287/5399.html">重庆市合川区人民医院2026年公开招聘高层次...</a></li>
</ul>
"""


def _load_selector_row(hospital_name: str) -> dict[str, str]:
    path = Path("/Users/xiongtao/Documents/zhaop_automotion/config/医院招聘的selector.csv")
    with path.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("hospital_name") == hospital_name:
                return row
    raise AssertionError(f"selector row not found: {hospital_name}")


def test_hcrmyy_selector_only_matches_recruitment_links_on_homepage():
    row = _load_selector_row("重庆市合川区人民医院")
    soup = BeautifulSoup(HCRMYY_HOME_SNIPPET, "lxml")

    items = soup.select(row["listing_selector"])
    hrefs = [item.select_one(row["link_selector"]).get("href", "") for item in items]

    assert hrefs == [
        "/list/287/5419.html",
        "/list/287/5399.html",
    ]
