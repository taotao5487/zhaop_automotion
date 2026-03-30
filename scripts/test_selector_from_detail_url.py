import argparse
import asyncio
import sys
from pathlib import Path

# 兼容 PyCharm 从任意工作目录运行
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from crawler.core.crawler import CrawlerEngine, SelectorSuggestionResult


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="根据详情页 URL 反推 selector.csv 候选配置")
    parser.add_argument(
        "--detail-url",
        required=True,
        help="招聘公告详情页 URL",
    )
    parser.add_argument(
        "--selectors-csv",
        default="config/医院招聘的selector.csv",
        help="医院选择器 CSV 路径（默认：医院招聘的selector.csv）",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="确认后写入 selector.csv",
    )
    return parser.parse_args(argv)


def _print_result(result: SelectorSuggestionResult) -> None:
    print("\n诊断分类:", result.classification)
    print("说明:", result.message)
    if result.hospital_name:
        print("医院名称:", result.hospital_name)
    if result.selected_list_url:
        print("选中列表页:", result.selected_list_url)
    if result.base_url:
        print("base_url:", result.base_url)
    if result.listing_selector:
        print("listing_selector:", result.listing_selector)
    if result.list_title_selector:
        print("list_title_selector:", result.list_title_selector)
    if result.link_selector:
        print("link_selector:", result.link_selector)
    print("命中现有配置:", "是" if result.matched_existing_row else "否")
    print("允许写入:", "是" if result.write_allowed else "否")
    if result.matched_list_page_url:
        print("现有列表页:", result.matched_list_page_url)

    if result.candidate_list_urls:
        print("\n候选列表页 URL：")
        for index, url in enumerate(result.candidate_list_urls, start=1):
            print(f"  {index}. {url}")

    jobs = result.jobs or []
    if jobs:
        print(f"\n验证样本（前 {min(len(jobs), 5)} 条）：")
        for index, job in enumerate(jobs[:5], start=1):
            print(f"  {index}. {(job.get('title') or '').strip()}")
            print(f"     {(job.get('url') or '').strip()}")


async def _async_main(args: argparse.Namespace) -> int:
    engine = CrawlerEngine(selectors_csv_path=args.selectors_csv)
    await engine.init_session()

    try:
        result = await engine.suggest_selector_row_from_detail_url(args.detail_url)
        _print_result(result)

        if not args.write:
            return 0

        if not result.write_allowed:
            print("\n当前结果不允许写入，未修改 CSV。")
            return 1

        answer = input("\n确认将该候选行写入 CSV 吗？[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消写入。")
            return 0

        write_result = engine.append_selector_row_from_suggestion(result)
        if write_result.get("written"):
            print(f"已写入: {args.selectors_csv}")
            return 0

        print("未写入:", write_result.get("reason", "unknown"))
        return 1
    finally:
        await engine.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_async_main(parse_args())))
