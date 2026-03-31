#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from crawler.core.detail_pipeline import DetailPipeline
from crawler.core.wechat_review_builder import WechatReviewBuilder


HTML_WITH_GENERIC_H1 = """
<html>
  <head>
    <title>成都市第六人民医院2026年护士规范化培训招收简章-招聘公示-成都市第六人民医院</title>
  </head>
  <body>
    <h1>招标招聘</h1>
    <div class="content-title">成都市第六人民医院2026年护士规范化培训招收简章</div>
    <div class="news-title">成都市第六人民医院2026年护士规范化培训招收简章 发布时间：2026.03.30</div>
    <div class="content">
      <p>正文</p>
    </div>
  </body>
</html>
"""


def test_detail_pipeline_prefers_specific_detail_title_over_generic_h1():
    title = DetailPipeline._extract_title(
        HTML_WITH_GENERIC_H1,
        fallback_title="列表标题",
    )

    assert title == "成都市第六人民医院2026年护士规范化培训招收简章"


def test_wechat_review_builder_prefers_specific_detail_title_over_generic_h1():
    builder = WechatReviewBuilder()
    soup = BeautifulSoup(HTML_WITH_GENERIC_H1, "lxml")
    container = soup.body or soup

    title = builder._extract_display_title(
        container,
        "成都市第六人民医院2026年护士规范化培训招收简章",
    )

    assert title == "成都市第六人民医院2026年护士规范化培训招收简章"


def test_wechat_review_builder_uses_detail_pipeline_title_as_source_of_truth():
    builder = WechatReviewBuilder()
    soup = BeautifulSoup(
        """
        <html>
          <body>
            <div class="content-title">错误的栏目标题</div>
            <div class="content"><p>正文</p></div>
          </body>
        </html>
        """,
        "lxml",
    )
    container = soup.body or soup

    title = builder._extract_display_title(
        container,
        "详情页真实标题",
    )

    assert title == "详情页真实标题"


HTML_WITH_GENERIC_SECTION_TITLE = """
<html>
  <head>
    <title>宜宾市第四人民医院2026年第二次公开招募就业见习人员公告_宜宾市第四人民医院</title>
  </head>
  <body>
    <section id="banner">
      <div class="title"><span>人才招聘</span><em>Talent Recruitment</em></div>
    </section>
    <section class="newsInfo">
      <article class="infoLeft">
        <div class="neInfo">
          <h4>宜宾市第四人民医院2026年第二次公开招募就业见习人员公告</h4>
        </div>
        <div class="dis_content">
          <p>正文</p>
        </div>
      </article>
    </section>
  </body>
</html>
"""


def test_detail_pipeline_prefers_crawler_title_over_generic_section_title():
    title = DetailPipeline._extract_title(
        HTML_WITH_GENERIC_SECTION_TITLE,
        fallback_title="宜宾市第四人民医院2026年第二次公开招募就业见习人员公告2026-03-31",
    )

    assert title == "宜宾市第四人民医院2026年第二次公开招募就业见习人员公告"


def test_default_detail_rules_cover_known_problem_hospital_sites():
    pipeline = DetailPipeline(crawler=object())

    expected_sites = {
        "site_13_13",
        "site_72_72",
        "site_84_84",
        "site_118_118",
    }

    for site_name in expected_sites:
        rule = pipeline.get_rule(site_name)
        assert rule.content_selectors, site_name


def test_detail_pipeline_strips_trailing_publish_meta_from_crawler_title():
    title = DetailPipeline._extract_title(
        "<html><body><div class='newdet-bt'><span>栏目标题</span></div></body></html>",
        fallback_title="重庆大学附属江津医院 2026年医院自聘岗位招聘简章发布时间：2026-03-31访问人数：7",
    )

    assert title == "重庆大学附属江津医院 2026年医院自聘岗位招聘简章"
