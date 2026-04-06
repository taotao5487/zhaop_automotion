#!/usr/bin/env python3

import argparse
import asyncio
import html
import json
import re
import sys
import time
from pathlib import Path
from urllib import parse, request
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DEFAULT_BASE_URL = "http://127.0.0.1:5001"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://mp.weixin.qq.com/",
}


def normalize_name(value):
    value = (value or "").strip()
    value = re.sub(r"\s+", "", value)
    value = value.replace("\u200b", "")
    return value


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def http_get_json(url, timeout=60):
    with request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url, payload, timeout=60):
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_page(url, timeout=120):
    req = request.Request(url, headers=BROWSER_HEADERS, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            charset = "utf-8"
            if hasattr(resp.headers, "get_content_charset"):
                charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="ignore")
    except HTTPError as exc:
        raise RuntimeError("fetch failed: HTTP {}".format(exc.code))
    except URLError as exc:
        raise RuntimeError("fetch failed: {}".format(exc.reason))


def parse_article_url(url):
    try:
        if not url or "mp.weixin.qq.com/s" not in url:
            return None
        parsed = urlparse(str(url))
        params = parse_qs(parsed.query)
        __biz = params.get("__biz", [""])[0]
        mid = params.get("mid", [""])[0]
        idx = params.get("idx", [""])[0]
        sn = params.get("sn", [""])[0]
        if not all([__biz, mid, idx, sn]):
            return None
        return {"__biz": __biz, "mid": mid, "idx": idx, "sn": sn}
    except Exception:
        return None


def is_verification_page(html_text):
    markers = [
        "环境异常",
        "完成验证后即可继续访问",
        "secitptpage/verify",
        "js_verify",
    ]
    for marker in markers:
        if marker in html_text:
            return True
    return False


def _extract_first(pattern, text):
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def extract_article_info(html_text, params=None):
    title = _extract_first(r'<meta\s+property="og:title"\s+content="([^"]+)"', html_text)
    if not title:
        title = _extract_first(r"var\s+msg_title\s*=\s*'([^']+)'", html_text)
    if not title:
        title = _extract_first(r"<title>([^<]+)</title>", html_text)

    author = _extract_first(r"var\s+nickname\s*=\s*htmlDecode\(\"([^\"]+)\"\)", html_text)
    if not author:
        author = _extract_first(r"var\s+nickname\s*=\s*'([^']+)'", html_text)
    if not author:
        author = _extract_first(r"nick_name\s*:\s*JsDecode\('([^']+)'\)", html_text)
    if not author:
        author = _extract_first(
            r'<a[^>]*id="js_name"[^>]*>\s*([^<]+?)\s*</a>',
            html_text,
        )
    if not author:
        author = _extract_first(r'<meta\s+property="og:article:author"\s+content="([^"]+)"', html_text)
    if not author:
        author = _extract_first(r'var\s+user_name\s*=\s*"([^"]+)"', html_text)

    title = html.unescape(title).strip()
    author = html.unescape(author).strip()
    return {
        "title": title,
        "author": author,
        "__biz": params.get("__biz", "") if params else "",
    }


async def extract_article_author(url):
    loop = asyncio.get_running_loop()
    html = await loop.run_in_executor(None, fetch_page, url, 120)
    if is_verification_page(html):
        raise RuntimeError("当前环境触发微信验证，请先在浏览器中打开该文章完成验证后再重试")
    params = parse_article_url(url)
    article = extract_article_info(html, params)
    author = (article.get("author") or "").strip()
    title = (article.get("title") or "").strip()
    return {"url": url, "author": author, "title": title}


def search_accounts(base_url, nickname):
    query = parse.quote(nickname)
    url = "{}/api/public/searchbiz?query={}".format(base_url.rstrip("/"), query)
    result = http_get_json(url)
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "searchbiz failed")
    return result.get("data", {}).get("list", []) or []


def choose_account(nickname, accounts):
    if not accounts:
        return None

    nickname_norm = normalize_name(nickname)
    for account in accounts:
        if normalize_name(account.get("nickname", "")) == nickname_norm:
            return account

    partial_matches = []
    for account in accounts:
        account_name = normalize_name(account.get("nickname", ""))
        if nickname_norm and (nickname_norm in account_name or account_name in nickname_norm):
            partial_matches.append(account)
    if len(partial_matches) == 1:
        return partial_matches[0]

    if len(accounts) == 1:
        return accounts[0]

    return None


def normalize_search_queries(name):
    normalized = (name or "").strip()
    queries = []
    for candidate in [
        normalized,
        re.sub(r"(订阅号|服务号|公众号)$", "", normalized).strip(),
        re.sub(r"(医院订阅号|医院服务号)$", "医院", normalized).strip(),
    ]:
        if candidate and candidate not in queries:
            queries.append(candidate)
    return queries


def collect_account_candidates(nickname, accounts):
    nickname_norm = normalize_name(nickname)
    candidates = []
    seen = set()
    for account in accounts:
        fakeid = (account.get("fakeid") or "").strip()
        if fakeid in seen:
            continue
        account_name = normalize_name(account.get("nickname", ""))
        alias = normalize_name(account.get("alias", ""))
        if nickname_norm and (
            nickname_norm in account_name or
            account_name in nickname_norm or
            nickname_norm in alias or
            alias in nickname_norm
        ):
            seen.add(fakeid)
            candidates.append(account)
    return candidates


def search_best_account(base_url, nickname):
    all_accounts = []
    seen = set()
    for query in normalize_search_queries(nickname):
        for account in search_accounts(base_url, query):
            fakeid = (account.get("fakeid") or "").strip()
            if not fakeid or fakeid in seen:
                continue
            seen.add(fakeid)
            all_accounts.append(account)

    account = choose_account(nickname, all_accounts)
    return account, all_accounts


def print_account_candidates(extracted_name, accounts, max_candidates=8):
    print("解析出的公众号名: {}".format(extracted_name or "-"))
    if not accounts:
        print("未搜索到可用候选，请换一篇该公众号文章再试。")
        return
    print("搜索到多个候选，请改用更明确的文章或手动按名字订阅：")
    for index, item in enumerate(accounts[:max_candidates], start=1):
        print(
            "{}. {} | alias={} | fakeid={}".format(
                index,
                item.get("nickname", "") or "-",
                item.get("alias", "") or "-",
                item.get("fakeid", "") or "-",
            )
        )
    if len(accounts) > max_candidates:
        print("... 其余 {} 个候选未展示".format(len(accounts) - max_candidates))


def get_subscriptions(base_url):
    result = http_get_json("{}/api/rss/subscriptions".format(base_url.rstrip("/")))
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "failed to fetch subscriptions")
    return result.get("data", []) or []


def choose_subscription(target, subscriptions):
    if not subscriptions:
        return None

    target_norm = normalize_name(target)

    for sub in subscriptions:
        if (sub.get("fakeid") or "").strip() == target.strip():
            return sub

    for sub in subscriptions:
        nickname = normalize_name(sub.get("nickname", ""))
        alias = normalize_name(sub.get("alias", ""))
        if target_norm and (nickname == target_norm or alias == target_norm):
            return sub

    partial_matches = []
    for sub in subscriptions:
        nickname = normalize_name(sub.get("nickname", ""))
        alias = normalize_name(sub.get("alias", ""))
        if target_norm and (
            target_norm in nickname or nickname in target_norm or
            target_norm in alias or alias in target_norm
        ):
            partial_matches.append(sub)
    if len(partial_matches) == 1:
        return partial_matches[0]

    if len(subscriptions) == 1:
        return subscriptions[0]

    return None


def subscribe_account(base_url, account):
    payload = {
        "fakeid": account.get("fakeid", ""),
        "nickname": account.get("nickname", ""),
        "alias": account.get("alias", ""),
        "head_img": account.get("round_head_img", ""),
    }
    return http_post_json("{}/api/rss/subscribe".format(base_url.rstrip("/")), payload)


def priority_poll_subscription(base_url, fakeid, limit):
    url = "{}/api/rss/priority-poll/{}?limit={}".format(
        base_url.rstrip("/"),
        parse.quote(fakeid),
        int(limit),
    )
    return http_post_json(url, {})


def format_publish_time(timestamp):
    if not timestamp:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(timestamp)))


async def async_main(args):
    title = ""
    author = ""
    subscribe_message = "已订阅，直接插队轮询"

    if args.name or args.fakeid:
        target = args.fakeid or args.name
        subscriptions = get_subscriptions(args.base_url)
        account = choose_subscription(target, subscriptions)
        if not account:
            raise RuntimeError("未找到已订阅公众号: {}".format(target))
        fakeid = account.get("fakeid", "")
        author = account.get("nickname", "")
    else:
        article = await extract_article_author(args.article_url)
        author = (article.get("author") or "").strip()
        title = (article.get("title") or "").strip()
        if not author:
            raise RuntimeError("无法从文章链接中提取公众号名称")

        account, accounts = search_best_account(args.base_url, author)
        if not account:
            print_account_candidates(author, collect_account_candidates(author, accounts) or accounts)
            return 2

        subscribe_result = subscribe_account(args.base_url, account)
        subscribe_message = subscribe_result.get("message", "")
        fakeid = account.get("fakeid", "")
        if not fakeid:
            raise RuntimeError("搜索结果中缺少 fakeid")

    poll_result = priority_poll_subscription(args.base_url, fakeid, args.limit)
    if not poll_result.get("success"):
        raise RuntimeError(poll_result.get("data", {}).get("message", "插队轮询失败"))
    recent_articles = poll_result.get("data", {}).get("articles", []) or []

    if title:
        print("文章标题: {}".format(title))
    print("公众号名称: {}".format(author))
    print("匹配公众号: {} ({})".format(account.get("nickname", ""), fakeid))
    print("订阅结果: {}".format(subscribe_message))
    print("轮询结果: {}".format(poll_result.get("data", {}).get("message", "")))
    print("")
    print("最新文章:")
    if not recent_articles:
        print("- 暂未抓到文章")
        return 0

    for item in recent_articles:
        print("- [{}] {}".format(format_publish_time(item.get("publish_time", 0)), item.get("title", "")))
        print("  {}".format(item.get("link", "")))

    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Subscribe a WeChat account from one article URL and priority-poll it once.")
    parser.add_argument("article_url", nargs="?", help="WeChat article URL")
    parser.add_argument("--name", default=None, help="Already-subscribed account nickname or alias")
    parser.add_argument("--fakeid", default=None, help="Already-subscribed fakeid")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Local API base URL, default: http://127.0.0.1:5001")
    parser.add_argument("--limit", type=int, default=10, help="How many recent articles to print")
    args = parser.parse_args()
    if not args.article_url and not args.name and not args.fakeid:
        parser.error("article_url 或 --name / --fakeid 至少提供一个")
    return args


def main():
    args = parse_args()
    return run_async(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
