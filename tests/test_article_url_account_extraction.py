#!/usr/bin/env python3

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from scripts.subscribe_and_poll_from_article_url import extract_article_info  # noqa: E402


def test_extract_article_info_prefers_account_nickname_over_article_author():
    html = """
    <meta property="og:title" content="【诚聘英才】川北医学院附属四川宝石花医院2026年招聘公告" />
    <meta property="og:article:author" content="央企直属国有医院" />
    <script>
    var nickname = htmlDecode("四川宝石花医院");
    var user_name = "gh_1982a9b52af5";
    </script>
    <span class="rich_media_meta rich_media_meta_nickname" id="profileBt">
      <a href="javascript:void(0);" id="js_name">四川宝石花医院</a>
    </span>
    """

    article = extract_article_info(html)

    assert article["title"] == "【诚聘英才】川北医学院附属四川宝石花医院2026年招聘公告"
    assert article["author"] == "四川宝石花医院"
