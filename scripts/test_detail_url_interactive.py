import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.parse import urlparse

# 兼容 PyCharm 从任意工作目录运行
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from crawler.core.crawler import CrawlerConfig, CrawlerEngine
from crawler.core.detail_pipeline import DetailPipeline


def _norm_netloc(url: str) -> str:
    return urlparse(url.strip()).netloc.lower()


def _norm_path(url: str) -> str:
    path = (urlparse(url.strip()).path or "/").rstrip("/")
    return path or "/"


def _match_configs(engine: CrawlerEngine, input_url: str) -> List[CrawlerConfig]:
    input_netloc = _norm_netloc(input_url)
    input_path = _norm_path(input_url)

    exact = [cfg for cfg in engine.site_configs.values() if (cfg.start_url or "").strip() == input_url.strip()]
    if exact:
        return exact

    same_domain = [
        cfg for cfg in engine.site_configs.values()
        if _norm_netloc(cfg.start_url or "") == input_netloc
    ]
    if not same_domain:
        return []

    strong_path_match = []
    for cfg in same_domain:
        cfg_path = _norm_path(cfg.start_url or "")
        if input_path.startswith(cfg_path) or cfg_path.startswith(input_path):
            strong_path_match.append(cfg)

    return strong_path_match or same_domain


def _hospital_name(cfg: CrawlerConfig) -> str:
    return (
        cfg.parsing.get("list_page", {})
        .get("fields", {})
        .get("hospital", {})
        .get("default", cfg.site_name)
    )


def _choose_config(matched: List[CrawlerConfig]) -> CrawlerConfig:
    chosen = matched[0]
    if len(matched) == 1:
        return chosen

    print("\n找到多个候选医院，请选择：")
    for index, cfg in enumerate(matched, start=1):
        print(f"  {index}. {_hospital_name(cfg)}")
        print(f"     CSV列表页: {cfg.start_url}")

    raw = input("请输入编号（默认1）: ").strip()
    if raw:
        try:
            selected_index = int(raw)
            if 1 <= selected_index <= len(matched):
                chosen = matched[selected_index - 1]
        except ValueError:
            pass
    return chosen


def _build_manual_config(detail_url: str) -> CrawlerConfig:
    parsed = urlparse(detail_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else detail_url
    return CrawlerConfig(
        {
            "site_name": "detail_manual",
            "base_url": base_url,
            "start_url": detail_url,
            "enabled": True,
            "priority": 1,
            "request_timeout": 30,
            "max_retries": 3,
            "delay_between_requests": 0,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "parsing": {},
            "waf_strategy": {},
            "validation": {},
            "ssl_verify": True,
        }
    )


def _build_job_payload(cfg: CrawlerConfig, detail_url: str, title: str | None) -> dict:
    hospital = _hospital_name(cfg)
    return {
        "title": title or "详情页调试",
        "url": detail_url,
        "hospital": hospital,
        "source_site": cfg.site_name,
    }


def _print_result(result: dict) -> None:
    if not result.get("detail_success"):
        print("\n详情处理失败。")
        print("输出目录:", result.get("article_dir", ""))
        if result.get("wechat_error"):
            print("失败原因:", result.get("wechat_error"))
        return

    article_dir = Path(result["article_dir"])
    manifest_path = Path(result["manifest_path"])
    article_path = Path(result["article_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    print("\n详情抽取成功。")
    if result.get("wechat_ready"):
        print("公众号待审包: 已就绪")
    else:
        print("公众号待审包: 未就绪")
        if result.get("wechat_error"):
            print("未就绪原因:", result.get("wechat_error"))
    print("文章目录:", article_dir)
    print("文章稿件:", article_path)
    print("正文清洗:", article_dir / "cleaned.html")
    if result.get("review_dir"):
        print("待审目录:", result.get("review_dir"))
    if result.get("review_article_html_path"):
        print("待审HTML:", result.get("review_article_html_path"))
    if result.get("review_package_path"):
        print("待审清单:", result.get("review_package_path"))
    print("抽取方式:", manifest.get("extraction_method"))
    print("抓取后标题:", manifest.get("resolved_title"))
    print("抓取后端:", manifest.get("fetch_backend"))
    print("成稿类型:", manifest.get("wechat_review", {}).get("article_type", "unknown"))
    print("正文图片数:", len(manifest.get("inline_images", [])))
    print("附件数:", len(manifest.get("attachments", [])))

    attachments = manifest.get("attachments", [])
    if attachments:
        print("\n附件摘要：")
        for index, attachment in enumerate(attachments, start=1):
            print(
                f"  {index}. {attachment.get('title')} "
                f"[{attachment.get('render_status')}] -> {attachment.get('local_path')}"
            )

    errors = manifest.get("errors", [])
    if errors:
        print("\n错误/警告：")
        for error in errors:
            print(" -", error)

    article_preview = article_path.read_text(encoding="utf-8")
    preview_lines = article_preview.splitlines()[:20]
    print("\n文章预览（前20行）：")
    for line in preview_lines:
        print(line)


def _write_latest_result_files(output_root: Path, result: dict) -> None:
    if not result.get("detail_success"):
        latest_error_path = output_root / "latest_result.md"
        latest_error_path.parent.mkdir(parents=True, exist_ok=True)
        latest_error_path.write_text(
            "# 详情测试结果\n\n本次详情处理失败。\n",
            encoding="utf-8",
        )
        return

    article_dir = Path(result["article_dir"]).resolve()
    article_path = Path(result["article_path"]).resolve()
    manifest_path = Path(result["manifest_path"]).resolve()
    cleaned_path = (article_dir / "cleaned.html").resolve()
    source_path = (article_dir / "source.html").resolve()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    article_markdown = article_path.read_text(encoding="utf-8")

    latest_markdown_path = output_root / "latest_result.md"
    latest_paths_path = output_root / "latest_paths.json"
    latest_article_copy = output_root / "latest_article.md"
    latest_cleaned_copy = output_root / "latest_cleaned.html"
    latest_review_html = output_root / "latest_review.html"
    latest_review_package = output_root / "latest_review_package.json"

    latest_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    latest_article_copy.write_text(article_markdown, encoding="utf-8")
    shutil.copyfile(cleaned_path, latest_cleaned_copy)
    review_info = manifest.get("wechat_review") or {}
    review_html_path = Path(review_info["article_html_path"]).resolve() if review_info.get("article_html_path") else None
    review_package_path = Path(review_info["package_path"]).resolve() if review_info.get("package_path") else None
    if review_html_path and review_html_path.exists():
        shutil.copyfile(review_html_path, latest_review_html)
    if review_package_path and review_package_path.exists():
        shutil.copyfile(review_package_path, latest_review_package)

    attachment_lines = []
    for index, attachment in enumerate(manifest.get("attachments", []), start=1):
        attachment_lines.append(
            f"{index}. {attachment.get('title')} | {attachment.get('render_status')} | {attachment.get('local_path')}"
        )
    attachment_text = "\n".join(attachment_lines) if attachment_lines else "无"

    latest_markdown_path.write_text(
        (
            "# 详情测试结果\n\n"
            f"- 标题：{manifest.get('resolved_title') or result.get('title')}\n"
            f"- 来源链接：{result.get('url')}\n"
            f"- 抽取方式：{manifest.get('extraction_method')}\n"
            f"- 抓取后端：{manifest.get('fetch_backend')}\n"
            f"- 正文图片数：{len(manifest.get('inline_images', []))}\n"
            f"- 附件数：{len(manifest.get('attachments', []))}\n"
            f"- 原始目录：`{article_dir}`\n"
            f"- 文章稿件：`{article_path}`\n"
            f"- 清洗正文：`{cleaned_path}`\n"
            f"- 原始源码：`{source_path}`\n"
            f"- 固定文章副本：`{latest_article_copy.resolve()}`\n"
            f"- 固定正文副本：`{latest_cleaned_copy.resolve()}`\n\n"
            f"- 待审HTML：`{review_html_path or ''}`\n"
            f"- 固定待审HTML：`{latest_review_html.resolve() if latest_review_html.exists() else ''}`\n"
            f"- 待审清单：`{review_package_path or ''}`\n"
            f"- 固定待审清单：`{latest_review_package.resolve() if latest_review_package.exists() else ''}`\n\n"
            "## 附件摘要\n\n"
            f"{attachment_text}\n\n"
            "## 文章全文\n\n"
            f"{article_markdown}\n"
        ),
        encoding="utf-8",
    )

    latest_paths_path.write_text(
        json.dumps(
            {
                "article_dir": str(article_dir),
                "article_path": str(article_path),
                "cleaned_path": str(cleaned_path),
                "source_path": str(source_path),
                "manifest_path": str(manifest_path),
                "latest_markdown_path": str(latest_markdown_path.resolve()),
                "latest_article_copy": str(latest_article_copy.resolve()),
                "latest_cleaned_copy": str(latest_cleaned_copy.resolve()),
                "latest_review_html": str(latest_review_html.resolve()) if latest_review_html.exists() else "",
                "latest_review_package": str(latest_review_package.resolve()) if latest_review_package.exists() else "",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


async def _test_once(
    engine: CrawlerEngine,
    pipeline: DetailPipeline,
    input_url: str,
    title: str | None,
    output_root: Path,
) -> None:
    matched = _match_configs(engine, input_url)

    if matched:
        chosen = _choose_config(matched)
        print("\n匹配到医院:", _hospital_name(chosen))
        print("CSV列表页:", chosen.start_url)
    else:
        chosen = _build_manual_config(input_url)
        print("\n未在 CSV 中匹配到医院配置，使用手动详情配置继续测试。")

    print("测试详情URL:", input_url)
    result = await pipeline.process_jobs(
        [_build_job_payload(chosen, input_url, title)],
        run_time=datetime.now(),
    )

    if not result.get("results"):
        print("\n未生成任何详情结果。")
        return

    _write_latest_result_files(output_root, result["results"][0])
    _print_result(result["results"][0])
    print("\n固定查看文件：")
    print(f"- {output_root / 'latest_result.md'}")
    print(f"- {output_root / 'latest_article.md'}")
    print(f"- {output_root / 'latest_cleaned.html'}")
    print(f"- {output_root / 'latest_paths.json'}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="交互式详情页正文/附件测试脚本")
    parser.add_argument(
        "--selectors-csv",
        default="医院招聘的selector.csv",
        help="医院选择器CSV路径（默认：医院招聘的selector.csv）",
    )
    parser.add_argument(
        "--detail-rules",
        default="config/detail_rules.yaml",
        help="详情规则配置路径（默认：config/detail_rules.yaml）",
    )
    parser.add_argument(
        "--output-root",
        default="data/exports/detail_debug",
        help="详情测试输出目录（默认：data/exports/detail_debug）",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="直接测试单个详情页 URL；不传则进入交互模式",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="可选：给详情页临时指定标题，便于生成目录名",
    )
    args = parser.parse_args()

    engine = CrawlerEngine(selectors_csv_path=args.selectors_csv)
    await engine.init_session()
    pipeline = DetailPipeline(
        engine,
        detail_rules_path=args.detail_rules,
        output_root=args.output_root,
        max_items=1,
    )
    output_root = Path(args.output_root)

    try:
        if args.url:
            await _test_once(engine, pipeline, args.url, args.title, output_root)
            return

        print(f"已加载 {len(engine.site_configs)} 个医院配置。")
        print("粘贴公告详情页 URL 进行测试，输入 q 退出。\n")

        while True:
            input_url = input("详情URL> ").strip()
            if input_url.lower() in {"q", "quit", "exit"}:
                print("已退出。")
                break

            if not input_url.startswith(("http://", "https://")):
                print("请输入合法的 http(s) URL。\n")
                continue

            await _test_once(engine, pipeline, input_url, args.title, output_root)
            print("\n" + "-" * 80 + "\n")

    finally:
        await engine.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
