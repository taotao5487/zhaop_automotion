#!/usr/bin/env python3
"""
川渝医护招聘爬虫主程序入口
支持多网站爬取、WAF自动绕过和增量数据存储
"""

import asyncio
import argparse
import logging
import sys
import os
from datetime import datetime
from urllib.parse import urlparse

from crawler.core.crawler import CrawlerEngine
from crawler.core.database import db_manager
from crawler.core.detail_pipeline import DetailPipeline
from crawler.core.exporter import export_new_jobs
from crawler.core.review_queue import review_jobs_for_detail_processing
from shared.paths import CONFIG_DIR, DATA_DIR, LOGS_DIR, ensure_runtime_dirs


def setup_logging(log_level: str = "INFO", log_file: str = "logs/crawler.log"):
    """配置日志"""
    ensure_runtime_dirs()
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 日志格式
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # 文件处理器
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)

    # 根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # 移除现有处理器
    root_logger.handlers.clear()

    # 添加处理器
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # 设置第三方库日志级别
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy').setLevel(logging.WARNING)


def _norm_netloc(url: str) -> str:
    return urlparse(url.strip()).netloc.lower()


def _norm_path(url: str) -> str:
    path = (urlparse(url.strip()).path or "/").rstrip("/")
    return path or "/"


def _hospital_name(config) -> str:
    return (
        config.parsing.get("list_page", {})
        .get("fields", {})
        .get("hospital", {})
        .get("default", config.site_name)
    )


def resolve_site_identifier(crawler: CrawlerEngine, raw_value: str) -> tuple[str | None, list[str]]:
    """支持通过site_name、医院名或列表页URL定位站点配置"""
    value = (raw_value or "").strip()
    if not value:
        return None, []

    if value in crawler.site_configs:
        return value, [value]

    exact_hospital_matches = [
        site_name
        for site_name, config in crawler.site_configs.items()
        if _hospital_name(config) == value
    ]
    if len(exact_hospital_matches) == 1:
        return exact_hospital_matches[0], exact_hospital_matches
    if len(exact_hospital_matches) > 1:
        return None, exact_hospital_matches

    exact_url_matches = [
        site_name
        for site_name, config in crawler.site_configs.items()
        if (config.start_url or "").strip() == value
    ]
    if len(exact_url_matches) == 1:
        return exact_url_matches[0], exact_url_matches
    if len(exact_url_matches) > 1:
        return None, exact_url_matches

    if value.startswith(("http://", "https://")):
        input_netloc = _norm_netloc(value)
        input_path = _norm_path(value)
        strong_url_matches = []
        for site_name, config in crawler.site_configs.items():
            start_url = (config.start_url or "").strip()
            if not start_url or _norm_netloc(start_url) != input_netloc:
                continue

            cfg_path = _norm_path(start_url)
            if input_path.startswith(cfg_path) or cfg_path.startswith(input_path):
                strong_url_matches.append(site_name)

        if len(strong_url_matches) == 1:
            return strong_url_matches[0], strong_url_matches
        if len(strong_url_matches) > 1:
            return None, strong_url_matches

    fuzzy_matches = [
        site_name
        for site_name, config in crawler.site_configs.items()
        if value.lower() in site_name.lower() or value in _hospital_name(config)
    ]
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0], fuzzy_matches

    return None, fuzzy_matches


def _log_site_resolution_help(
    logger: logging.Logger,
    crawler: CrawlerEngine,
    raw_value: str,
    matches: list[str],
) -> None:
    logger.error(f"网站配置不存在或匹配不唯一: {raw_value}")

    if matches:
        logger.info("匹配到以下候选站点:")
        for site_name in matches[:10]:
            config = crawler.site_configs[site_name]
            logger.info(
                "  %s -> %s (%s)",
                site_name,
                _hospital_name(config),
                config.start_url,
            )
        if len(matches) > 10:
            logger.info(f"  ... 共 {len(matches)} 个候选")
        return

    logger.info("可传入 site_name、hospital_name 或列表页URL。")
    logger.info(f"可用网站: {', '.join(crawler.site_configs.keys())}")


async def crawl_single_site(crawler: CrawlerEngine, site_name: str, args):
    """爬取单个网站"""
    try:
        logger = logging.getLogger(__name__)
        resolved_site_name, matches = resolve_site_identifier(crawler, site_name)
        if not resolved_site_name:
            _log_site_resolution_help(logger, crawler, site_name, matches)
            return {'success': False, 'error': f'Config not found for {site_name}', 'site_name': site_name}

        if resolved_site_name != site_name:
            logger.info(f"已匹配站点: {site_name} -> {resolved_site_name}")
        site_name = resolved_site_name
        logger.info(f"开始爬取网站: {site_name}")

        incremental_mode = False if args.force else args.incremental
        result = await crawler.crawl_site(site_name, incremental=incremental_mode)

        if result.get('success'):
            if result.get('skipped'):
                logger.info(f"网站 {site_name} 已跳过: {result.get('reason')}")
            else:
                logger.info(
                    "网站 %s 爬取成功: %s 个新职位, %s 个更新, %s 个过滤",
                    site_name,
                    result.get('new_jobs', 0),
                    result.get('updated_jobs', 0),
                    result.get('filtered_jobs', 0),
                )
        else:
            logger.error(f"网站 {site_name} 爬取失败: {result.get('error')}")

        return result

    except Exception as e:
        logger.error(f"爬取网站 {site_name} 异常: {e}")
        return {'success': False, 'error': str(e), 'site_name': site_name}


async def crawl_all_sites(crawler: CrawlerEngine, args):
    """爬取所有网站"""
    logger = logging.getLogger(__name__)
    logger.info("开始爬取所有启用的网站")
    if args.site_limit:
        logger.info(f"当前仅联调前 {args.site_limit} 个CSV有效站点")

    incremental_mode = False if args.force else args.incremental
    results = await crawler.crawl_all_sites(
        incremental=incremental_mode,
        force=args.force
    )

    # 输出摘要
    logger.info("=" * 50)
    logger.info("爬取完成摘要:")
    logger.info(f"总网站数: {results['total_sites']}")
    logger.info(f"成功: {results['successful']}")
    logger.info(f"失败: {results['failed']}")
    logger.info(f"跳过: {results['skipped']}")
    logger.info(f"新职位总数: {results['total_new_jobs']}")
    logger.info(f"更新职位总数: {results.get('total_updated_jobs', 0)}")
    logger.info(f"过滤职位总数: {results.get('total_filtered_jobs', 0)}")
    logger.info("=" * 50)

    # 输出各网站详情
    if args.verbose:
        for site_name, result in results['site_results'].items():
            status = "✓" if result.get('success') else "✗"
            if result.get('skipped'):
                status = "↷"
            new_jobs = result.get('new_jobs', 0)
            updated_jobs = result.get('updated_jobs', 0)
            filtered_jobs = result.get('filtered_jobs', 0)
            logger.info(
                f"  {status} {site_name}: {new_jobs} 新职位, {updated_jobs} 更新, {filtered_jobs} 过滤"
            )

    return results


async def show_stats(crawler: CrawlerEngine, args):
    """显示统计信息"""
    logger = logging.getLogger(__name__)

    try:
        # 获取数据库统计
        total_jobs = await db_manager.get_jobs_count()
        recent_jobs = await db_manager.get_recent_jobs(days=30)

        logger.info("=" * 50)
        logger.info("数据库统计:")
        logger.info(f"职位总数: {total_jobs}")
        logger.info(f"最近30天职位: {len(recent_jobs)}")

        # 按网站统计
        logger.info("\n按网站统计:")
        for site_name in crawler.site_configs.keys():
            count = await db_manager.get_jobs_count(site_name)
            if count > 0:
                logger.info(f"  {site_name}: {count} 个职位")

        # 最近职位示例
        if args.verbose and recent_jobs:
            logger.info("\n最近职位示例:")
            for i, job in enumerate(recent_jobs[:5]):
                logger.info(f"  {i+1}. {job.title} - {job.hospital} ({job.publish_date})")

        logger.info("=" * 50)

    except Exception as e:
        logger.error(f"获取统计信息失败: {e}")


async def cleanup_old_data(args):
    """清理旧数据"""
    logger = logging.getLogger(__name__)

    try:
        logger.info(f"开始清理 {args.days} 天前的旧数据...")
        deleted_count = await db_manager.cleanup_old_jobs(args.days)
        logger.info(f"清理完成: 删除了 {deleted_count} 个旧职位")

    except Exception as e:
        logger.error(f"清理数据失败: {e}")


async def export_job_details(crawler: CrawlerEngine, jobs_data: list[dict], args) -> dict:
    """将新增职位增强为详情文章包。"""
    logger = logging.getLogger(__name__)

    if not jobs_data:
        logger.info("本次无新增职位，未生成详情文章包")
        return {"generated": False, "reason": "no_jobs"}

    detail_output_root = os.path.join(crawler.export_directory, "details")
    pipeline = DetailPipeline(
        crawler,
        output_root=detail_output_root,
        max_items=args.details_max_items,
    )
    result = await pipeline.process_jobs(jobs_data, run_time=datetime.now())

    if result.get("generated"):
        logger.info(
            "详情文章包生成完成: 详情成功 %s/%s, 公众号就绪 %s/%s, 详情目录: %s, 待审目录: %s",
            result.get("detail_success_count", 0),
            result.get("processed_count", 0),
            result.get("wechat_ready_count", 0),
            result.get("processed_count", 0),
            result.get("output_root"),
            result.get("review_output_root"),
        )
        if result.get("summary_path"):
            logger.info(f"详情文章汇总清单: {result['summary_path']}")
    else:
        logger.info("详情文章包未生成成功")

    return result


def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="川渝医护招聘爬虫系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --all                    # 爬取所有网站（增量模式）
  %(prog)s --all --site-limit 3     # 仅跑CSV前3个站点（联调）
  %(prog)s --site 重庆市精神卫生中心   # 按医院名爬取指定网站
  %(prog)s --all --force            # 强制重新爬取所有网站
  %(prog)s --stats                  # 显示统计信息
  %(prog)s --cleanup --days 180     # 清理180天前的旧数据
  %(prog)s --test https://www.cqsjwzx.com/html/rczp/  # 按URL测试指定网站配置
  %(prog)s --site 重庆市人民医院 --with-details --select-detail-urls  # 启动浏览器审核队列后再进入下一步
        """
    )

    # 爬取模式
    mode_group = parser.add_mutually_exclusive_group(required=False)
    mode_group.add_argument(
        "--all", "-a",
        action="store_true",
        help="爬取所有启用的网站"
    )
    mode_group.add_argument(
        "--site", "-s",
        metavar="SITE_NAME",
        help="爬取指定网站，支持 site_name、医院名或列表页URL"
    )
    mode_group.add_argument(
        "--stats",
        action="store_true",
        help="显示统计信息"
    )
    mode_group.add_argument(
        "--cleanup",
        action="store_true",
        help="清理旧数据"
    )
    mode_group.add_argument(
        "--test",
        metavar="SITE_NAME",
        help="测试网站配置（不保存到数据库，支持 site_name、医院名或列表页URL）"
    )

    # 爬取选项
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="强制全量刷新，按非增量模式处理已存在链接"
    )
    parser.add_argument(
        "--incremental", "-i",
        action="store_true",
        default=True,
        help="增量模式：已存在链接不更新，只保存本次新增（默认）"
    )
    parser.add_argument(
        "--no-incremental",
        action="store_false",
        dest="incremental",
        help="非增量模式：已存在链接也刷新内容，但仍不会重复入库"
    )

    # 清理选项
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=180,
        help="清理多少天前的数据（默认：180）"
    )

    # 输出选项
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细输出模式"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="安静模式，只输出错误"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="日志级别（默认：INFO）"
    )
    parser.add_argument(
        "--log-file",
        default=str(LOGS_DIR / "crawler.log"),
        help="日志文件路径（默认：logs/crawler.log）"
    )
    parser.add_argument(
        "--config",
        default=str(CONFIG_DIR / "global.yaml"),
        help="配置文件路径（默认：config/global.yaml）"
    )
    parser.add_argument(
        "--selectors-csv",
        default=str(CONFIG_DIR / "医院招聘的selector.csv"),
        help="医院与选择器CSV路径（默认：医院招聘的selector.csv）"
    )
    parser.add_argument(
        "--site-limit",
        type=int,
        default=None,
        help="仅加载CSV前N个有效站点，用于联调"
    )
    parser.add_argument(
        "--with-details",
        action="store_true",
        help="对本次新增公告继续抓取正文、附件并生成公众号文章包"
    )
    parser.add_argument(
        "--details-max-items",
        type=int,
        default=None,
        help="详情增强时最多处理多少条新增公告（默认：不限制）"
    )
    parser.add_argument(
        "--select-detail-urls",
        action="store_true",
        help="在详情增强前，启动本地浏览器审核队列，确认哪些URL进入下一步处理"
    )

    # 数据库选项
    parser.add_argument(
        "--db-path",
        default=str(DATA_DIR / "jobs.db"),
        help="数据库文件路径（默认：data/jobs.db）"
    )

    args = parser.parse_args()

    if not any([args.all, args.site, args.stats, args.cleanup, args.test]):
        args.stats = True

    return args


async def test_site_config(crawler: CrawlerEngine, site_name: str, args):
    """测试网站配置"""
    logger = logging.getLogger(__name__)
    target_url = site_name
    if not target_url.startswith(("http://", "https://")):
        resolved_site_name, matches = resolve_site_identifier(crawler, site_name)
        if not resolved_site_name:
            _log_site_resolution_help(logger, crawler, site_name, matches)
            return
        if resolved_site_name != site_name:
            logger.info(f"已匹配站点: {site_name} -> {resolved_site_name}")
        target_url = crawler.site_configs[resolved_site_name].start_url

    try:
        await crawler.init_session()
        result = await crawler.validate_list_page_url(target_url, max_items=5)
        logger.info("诊断分类: %s", result.get("classification"))
        logger.info("建议动作: %s", result.get("suggestion"))
        if result.get("hospital"):
            logger.info("匹配医院: %s", result.get("hospital"))
        if result.get("csv_list_url"):
            logger.info("CSV列表页: %s", result.get("csv_list_url"))
        if result.get("fetch_backend"):
            logger.info("抓取后端: %s", result.get("fetch_backend"))
        if result.get("failure_category"):
            logger.info("失败分类: %s", result.get("failure_category"))
        if result.get("message"):
            logger.info("说明: %s", result.get("message"))

        jobs_data = result.get("jobs") or []
        if jobs_data and args.verbose:
            logger.info("\n样本数据:")
            for i, job in enumerate(jobs_data[:3], start=1):
                logger.info("\n职位 %s:", i)
                logger.info("  title: %s", job.get("title"))
                logger.info("  url: %s", job.get("url"))
    except Exception as e:
        logger.error(f"测试失败: {e}")
    finally:
        await crawler.close_session()


async def main():
    """主函数"""
    args = parse_arguments()

    # 设置日志
    log_level = "ERROR" if args.quiet else args.log_level
    setup_logging(log_level, args.log_file)

    logger = logging.getLogger(__name__)
    crawler = None

    try:
        # 初始化爬虫引擎
        logger.info("初始化爬虫引擎...")
        crawler = CrawlerEngine(
            args.config,
            selectors_csv_path=args.selectors_csv,
            site_limit=args.site_limit,
        )

        # 设置数据库路径
        db_manager.db_path = args.db_path

        # 确保数据目录存在
        db_dir = os.path.dirname(args.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        await db_manager.init_async()

        # 根据模式执行
        if args.all:
            results = await crawl_all_sites(crawler, args)
            if crawler.export_enabled:
                export_result = export_new_jobs(
                    results.get('new_jobs_data', []),
                    crawler.export_directory,
                )
                if export_result.get('exported'):
                    logger.info(f"本次新增CSV导出: {export_result['csv_path']}")
                    logger.info(f"本次新增公众号清单: {export_result['markdown_path']}")
                else:
                    logger.info("本次无新增，未生成导出文件")
            if args.with_details:
                detail_jobs = results.get('new_jobs_data', [])
                if args.select_detail_urls:
                    detail_jobs = await review_jobs_for_detail_processing(
                        crawler,
                        detail_jobs,
                        logger_instance=logger,
                    )
                await export_job_details(crawler, detail_jobs, args)
            # 如果有失败，返回非零退出码
            if results['failed'] > 0:
                return 1

        elif args.site:
            result = await crawl_single_site(crawler, args.site, args)
            if result.get('success') and crawler.export_enabled:
                export_result = export_new_jobs(
                    result.get('new_jobs_data', []),
                    crawler.export_directory,
                )
                if export_result.get('exported'):
                    logger.info(f"本次新增CSV导出: {export_result['csv_path']}")
                    logger.info(f"本次新增公众号清单: {export_result['markdown_path']}")
                else:
                    logger.info("本次无新增，未生成导出文件")
            if args.with_details and result.get('success'):
                detail_jobs = result.get('new_jobs_data', [])
                if args.select_detail_urls:
                    detail_jobs = await review_jobs_for_detail_processing(
                        crawler,
                        detail_jobs,
                        logger_instance=logger,
                    )
                await export_job_details(crawler, detail_jobs, args)
            if not result.get('success'):
                return 1

        elif args.stats:
            await show_stats(crawler, args)

        elif args.cleanup:
            await cleanup_old_data(args)

        elif args.test:
            await test_site_config(crawler, args.test, args)

        logger.info("程序执行完成")
        return 0

    except KeyboardInterrupt:
        logger.info("用户中断程序")
        return 130
    except Exception as e:
        logger.error(f"程序执行失败: {e}", exc_info=args.verbose)
        return 1
    finally:
        if crawler:
            await crawler.cleanup()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
