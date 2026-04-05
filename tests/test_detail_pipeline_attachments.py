#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from crawler.core.detail_pipeline import DetailPipeline


@pytest.mark.asyncio
async def test_process_job_finds_attachment_links_outside_extracted_container(tmp_path):
    detail_html = """
    <html>
      <body>
        <div class="article-content">
          <h1>测试医院招聘公告</h1>
          <p>正文内容。</p>
          <p>附件：测试报名表</p>
        </div>
        <div class="attachBox">
          <ul id="attachContainer" class="attach-container">
            <li class="attach-item">
              <a class="attachName" href="//download.example.com/files/apply.docx">
                测试医院招聘报名表.docx
              </a>
            </li>
          </ul>
        </div>
      </body>
    </html>
    """

    class FakeCrawler:
        site_configs = {}
        session = object()

        async def init_session(self):
            self.session = object()

        async def fetch_page_with_metadata(self, url, config):
            return {
                "success": True,
                "content": detail_html,
                "fetch_backend": "aiohttp",
                "failure_category": "",
            }

    rules_path = tmp_path / "detail_rules.yaml"
    rules_path.write_text(
        """
sites:
  test_site:
    content_selectors:
      - ".article-content"
        """.strip(),
        encoding="utf-8",
    )

    pipeline = DetailPipeline(
        FakeCrawler(),
        detail_rules_path=str(rules_path),
        output_root=str(tmp_path / "details"),
        review_output_root=str(tmp_path / "wechat_review"),
    )

    async def fake_download(source_url, destination, config):
        destination.write_bytes(b"fake-docx")
        return True, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    pipeline._download_binary_resource = fake_download  # type: ignore[method-assign]

    result = await pipeline.process_job(
        {
            "title": "测试医院招聘公告",
            "url": "https://example.com/detail/1",
            "hospital": "测试医院",
            "source_site": "test_site",
        },
        tmp_path / "details" / "20260401_220000",
        datetime(2026, 4, 1, 22, 0, 0),
    )

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    assert len(manifest["attachments"]) == 1
    assert manifest["attachments"][0]["title"] == "测试医院招聘报名表.docx"
    assert manifest["attachments"][0]["source_url"] == "https://download.example.com/files/apply.docx"
    assert manifest["attachments"][0]["render_status"] == "link_only"


@pytest.mark.asyncio
async def test_render_attachment_supports_legacy_doc(tmp_path):
    class FakeCrawler:
        site_configs = {}
        session = object()

    pipeline = DetailPipeline(FakeCrawler())
    attachment_path = tmp_path / "jobs.doc"
    attachment_path.write_bytes(b"fake-doc")
    renders_dir = tmp_path / "renders"
    renders_dir.mkdir()
    render_path = renders_dir / "jobs_1.png"
    render_path.write_bytes(b"png")

    pipeline._render_doc = lambda path, target_dir: ([render_path], "rendered")  # type: ignore[attr-defined]

    paths, status = await pipeline._render_attachment(
        attachment_path,
        {"category": "position_table"},
        renders_dir,
    )

    assert status == "rendered"
    assert paths == [render_path]


def test_discover_attachments_falls_back_to_raw_attachment_blocks():
    from bs4 import BeautifulSoup

    class FakeCrawler:
        site_configs = {}
        session = object()

    pipeline = DetailPipeline(FakeCrawler())
    cleaned_soup = BeautifulSoup(
        """
        <article>
          <div class="jz_fix_ue_img">
            <p>正文内容。</p>
            <p>附件：测试报名表</p>
          </div>
        </article>
        """,
        "lxml",
    )
    raw_html = """
    <html>
      <body>
        <div class="attachBox">
          <ul id="attachContainer" class="attach-container">
            <li class="attach-item">
              <a class="attachName" href="//download.example.com/files/apply.docx">
                测试医院招聘报名表.docx
              </a>
            </li>
          </ul>
        </div>
      </body>
    </html>
    """

    attachments = pipeline._discover_attachments(
        cleaned_soup,
        "https://example.com/detail/1",
        pipeline.get_rule(""),
        raw_html=raw_html,
    )

    assert len(attachments) == 1
    assert attachments[0]["title"] == "测试医院招聘报名表.docx"
    assert attachments[0]["source_url"] == "https://download.example.com/files/apply.docx"


def test_extract_cleaned_html_prefers_vsb_content_container_over_body():
    class FakeCrawler:
        site_configs = {}
        session = object()

    pipeline = DetailPipeline(FakeCrawler())
    raw_html = """
    <html>
      <body>
        <div class="fixed">
          <ul>
            <li><a href="/ylfw/yygh.htm">预约挂号</a></li>
            <li><a href="/xjlb.jsp">网上咨询</a></li>
          </ul>
        </div>
        <div class="container page-con clearfix">
          <h1 class="page-tit">重庆医科大学附属大学城医院妇产科全职博士后招聘启事</h1>
          <div class="page-date">发布时间 : 2026-04-03</div>
          <div id="vsb_content_500" class="zhengwen">
            <div class="v_news_content">
              <p>因学科发展和科研工作需要，现面向海内外招聘优秀博士后研究人员若干名。</p>
              <p>申请者可将个人简历发送至指定邮箱。</p>
            </div>
          </div>
        </div>
      </body>
    </html>
    """

    cleaned_html, cleaned_title, extraction_method = pipeline._extract_cleaned_html(
        raw_html,
        "http://www.uhcmu.com/info/1259/13109.htm",
        "重庆医科大学附属大学城医院妇产科全职博士后招聘启事",
        pipeline.get_rule(""),
    )

    assert extraction_method == "selectors"
    assert "博士后研究人员若干名" in cleaned_html
    assert "预约挂号" not in cleaned_html
    assert cleaned_title == "重庆医科大学附属大学城医院妇产科全职博士后招聘启事"


def test_discover_attachments_ignores_download_zone_navigation_pages():
    from bs4 import BeautifulSoup

    class FakeCrawler:
        site_configs = {}
        session = object()

    pipeline = DetailPipeline(FakeCrawler())
    cleaned_soup = BeautifulSoup(
        """
        <article>
          <div class="v_news_content">
            <p>因学科发展和科研工作需要，现面向海内外招聘优秀博士后研究人员若干名。</p>
            <p>申请者可将个人简历发送至指定邮箱。</p>
            <p><a href="http://www.uhcmu.com/jypx/yjsjy/xzzq.htm">下载专区</a></p>
          </div>
        </article>
        """,
        "lxml",
    )

    attachments = pipeline._discover_attachments(
        cleaned_soup,
        "http://www.uhcmu.com/info/1259/13109.htm",
        pipeline.get_rule(""),
    )

    assert attachments == []


@pytest.mark.asyncio
async def test_process_job_fetches_cqgwzx_detail_content_from_api_when_shell_page_is_empty(tmp_path):
    shell_html = """
    <!doctype html>
    <html lang="zh-CN">
      <head>
        <title>重庆市公共卫生医疗救治中心</title>
        <script type="module" src="/assets/index-3d7b9b71.js"></script>
      </head>
      <body><div id="app"></div></body>
    </html>
    """
    detail_payload = {
        "code": 200,
        "msg": "操作成功",
        "data": {
            "id": 5776,
            "title": "中心公开招募高层次人才公告",
            "publishTime": "2026-04-03 08:47:46",
            "content": (
                "<p>因中心学科及专科发展需要，现面向社会公开招募高层次人才。</p>"
                "<p>有意者请将简历发送至指定邮箱。</p>"
            ),
            "fileList": [],
            "relationLinkVos": None,
            "outsideLink": "",
        },
    }

    class FakeResponse:
        status = 200
        charset = "utf-8"

        async def read(self):
            return json.dumps(detail_payload, ensure_ascii=False).encode("utf-8")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def get(self, url, **kwargs):
            assert url.endswith("/prod-api/api/article/getArticleDetail")
            assert kwargs["params"] == {"id": "5776"}
            return FakeResponse()

    class FakeCrawler:
        site_configs = {}

        def __init__(self):
            self.session = FakeSession()

        async def init_session(self):
            self.session = FakeSession()

        async def fetch_page_with_metadata(self, url, config):
            return {
                "success": True,
                "content": shell_html,
                "fetch_backend": "aiohttp",
                "failure_category": "",
            }

    pipeline = DetailPipeline(
        FakeCrawler(),
        output_root=str(tmp_path / "details"),
        review_output_root=str(tmp_path / "wechat_review"),
    )

    result = await pipeline.process_job(
        {
            "title": "中心公开招募高层次人才公告",
            "url": "https://www.cqgwzx.com/article-detail?id=5776",
            "hospital": "重庆市公共卫生医疗救治中心",
            "source_site": "site_158_158",
        },
        tmp_path / "details" / "20260403_220526",
        datetime(2026, 4, 3, 22, 5, 26),
    )

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    package = json.loads(Path(result["review_package_path"]).read_text(encoding="utf-8"))

    assert manifest["fetch_backend"] == "cqgwzx_api"
    assert manifest["extraction_method"] == "selectors"
    assert "公开招募高层次人才" in package["plain_text"]
    assert "有意者请将简历发送至指定邮箱" in package["content_html"]


def test_extract_legacy_doc_table_rows_from_text_parses_bell_delimited_rows():
    from crawler.core.detail_pipeline import _extract_legacy_doc_table_rows_from_text

    text = (
        "附件1\n"
        "测试医院岗位需求表\n"
        "序号\x07单位\x07岗位\x07数量（人）\x07岗位条件\x07\x07\x07\x07科室\x07岗位\x07\x07学历\x07专业\x07执业（职称）条件\x07其他条件\x07\x071\x07测试医院\n"
        "（8人）\x07普外科\x07普外科医师\x071\x07研究生学历并取得相应学位\x07外科学\x07初级及以上\x071.年龄要求\u20282.其他条件\x07\x072\x07\x07放射科\x07放射技师\x071\x07本科及以上学历\x07医学影像技术\x07初级及以上\x071.有经验优先"
    )

    title, rows = _extract_legacy_doc_table_rows_from_text(text)

    assert title == "测试医院岗位需求表"
    assert rows[0] == ["序号", "单位", "科室", "岗位", "数量（人）", "学历", "专业", "执业（职称）条件", "其他条件"]
    assert rows[1][0] == "1"
    assert rows[1][1] == "测试医院\n（8人）"
    assert rows[1][2] == "普外科"
    assert rows[2][0] == "2"
    assert rows[2][1] == "测试医院\n（8人）"
    assert rows[2][2] == "放射科"


def test_extract_legacy_doc_table_rows_uses_modal_column_count_and_skips_page_markers():
    from crawler.core.detail_pipeline import _extract_legacy_doc_table_rows_from_text

    text = (
        "附件1\n"
        "测试医院岗位需求表\n"
        "序号\x07单位\x07岗位\x07数量（人）\x07岗位条件\x07\x07\x07\x07科室\x07岗位\x07\x07学历\x07专业\x07执业（职称）条件\x07其他条件\x07\x07"
        "1\x07测试医院\n（8人）\x07普外科\x07普外科医师\x071\x07研究生学历并取得相应学位\x07外科学\x07初级及以上\x071.年龄要求"
        "\x07\x072\x07\x07放射科\x07放射技师\x071\x07本科及以上学历\x07医学影像技术\x07初级及以上\x071.有经验优先"
        "\x07\x0718\x07\x07临床科室\x07护士\x071\x07普通高等教育大专及以上学历\x07护理学类\x07初级及以上\x071.2年经验\x07\x07合计"
        "\x07\x0731\x07\x07\x07—  PAGE  \\* MERGEFORMAT 20 —"
    )

    title, rows = _extract_legacy_doc_table_rows_from_text(text)

    assert title == "测试医院岗位需求表"
    assert len(rows) == 4
    assert all(len(row) == 9 for row in rows)
    assert rows[1][1] == "测试医院\n（8人）"
    assert rows[2][1] == "测试医院\n（8人）"
    assert rows[3][-1] == "1.2年经验\n合计"
    assert all("MERGEFORMAT" not in cell for row in rows for cell in row)
