import argparse
import asyncio
import sys
from pathlib import Path

# 兼容PyCharm从任意工作目录运行
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from crawler.core.crawler import CrawlerEngine


async def _test_once(engine: CrawlerEngine, input_url: str, max_items: int) -> None:
    result = await engine.validate_list_page_url(input_url, max_items=max_items)

    print("\n诊断分类:", result.get("classification"))
    print("建议动作:", result.get("suggestion", ""))
    if result.get("hospital"):
        print("匹配到医院:", result.get("hospital"))
    if result.get("csv_list_url"):
        print("CSV列表页:", result.get("csv_list_url"))
    print("测试URL:", input_url)
    if result.get("fetch_backend"):
        print("抓取后端:", result.get("fetch_backend"))
    if result.get("failure_category"):
        print("失败分类:", result.get("failure_category"))
    if result.get("message"):
        print("说明:", result.get("message"))

    jobs = result.get("jobs") or []
    if not jobs:
        return

    print(f"\n提取到 {len(jobs)} 条招聘公告，展示前 {min(len(jobs), max_items)} 条：")
    for i, job in enumerate(jobs[:max_items], start=1):
        print(f"\n{i}. {job.get('title', '').strip()}")
        print(f"   {job.get('url', '').strip()}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="交互式医院招聘URL测试脚本")
    parser.add_argument(
        "--selectors-csv",
        default="医院招聘的selector.csv",
        help="医院选择器CSV路径（默认：医院招聘的selector.csv）",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=20,
        help="每次最多打印多少条结果（默认：20）",
    )
    args = parser.parse_args()

    engine = CrawlerEngine(selectors_csv_path=args.selectors_csv)
    await engine.init_session()

    try:
        print(f"已加载 {len(engine.site_configs)} 个医院配置。")
        print("粘贴医院列表页URL进行测试，输入 q 退出。\n")

        while True:
            input_url = input("医院URL> ").strip()
            if input_url.lower() in {"q", "quit", "exit"}:
                print("已退出。")
                break

            if not input_url.startswith(("http://", "https://")):
                print("请输入合法的 http(s) URL。\n")
                continue

            await _test_once(engine, input_url, max_items=args.max_items)
            print("\n" + "-" * 80 + "\n")

    finally:
        await engine.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
