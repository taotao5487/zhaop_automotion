#!/usr/bin/env python3
"""
使用示例
演示如何调用爬虫系统
"""

import asyncio
import sys
import os
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def example_crawl_single_site():
    """示例：爬取单个网站"""
    from crawler.core.crawler import CrawlerEngine
    from crawler.core.database import db_manager

    logger.info("示例：爬取单个网站")

    try:
        # 1. 初始化爬虫引擎
        crawler = CrawlerEngine("config/global.yaml")

        # 2. 爬取指定网站
        site_name = "yilzhaopin"  # 使用示例网站
        result = await crawler.crawl_site(site_name, incremental=True)

        if result.get('success'):
            if result.get('skipped'):
                logger.info(f"网站 {site_name} 已跳过: {result.get('reason')}")
            else:
                logger.info(f"网站 {site_name} 爬取成功!")
                logger.info(f"  新职位: {result.get('new_jobs', 0)}")
                logger.info(f"  总职位: {result.get('jobs_count', 0)}")
        else:
            logger.error(f"网站 {site_name} 爬取失败: {result.get('error')}")

        # 3. 清理资源
        await crawler.cleanup()

        return result

    except Exception as e:
        logger.error(f"示例执行失败: {e}")
        return None


async def example_crawl_all_sites():
    """示例：爬取所有网站"""
    from crawler.core.crawler import CrawlerEngine
    from crawler.core.database import db_manager

    logger.info("示例：爬取所有网站")

    try:
        # 1. 初始化爬虫引擎
        crawler = CrawlerEngine("config/global.yaml")

        # 2. 爬取所有网站
        results = await crawler.crawl_all_sites(incremental=True)

        logger.info("爬取完成摘要:")
        logger.info(f"  总网站数: {results['total_sites']}")
        logger.info(f"  成功: {results['successful']}")
        logger.info(f"  失败: {results['failed']}")
        logger.info(f"  跳过: {results['skipped']}")
        logger.info(f"  新职位总数: {results['total_new_jobs']}")

        # 3. 清理资源
        await crawler.cleanup()

        return results

    except Exception as e:
        logger.error(f"示例执行失败: {e}")
        return None


async def example_query_data():
    """示例：查询数据"""
    from crawler.core.database import db_manager

    logger.info("示例：查询数据")

    try:
        # 1. 初始化数据库
        await db_manager.init_async()

        # 2. 查询最近30天的职位
        recent_jobs = await db_manager.get_recent_jobs(days=30, limit=5)

        if recent_jobs:
            logger.info(f"最近30天职位 (前{len(recent_jobs)}个):")
            for i, job in enumerate(recent_jobs, 1):
                logger.info(f"  {i}. {job.title}")
                logger.info(f"     医院: {job.hospital or '未知'}")
                logger.info(f"     地点: {job.location or '未知'}")
                logger.info(f"     发布日期: {job.publish_date}")
                logger.info(f"     来源: {job.source_site}")
                logger.info("")
        else:
            logger.info("没有找到最近30天的职位")

        # 3. 获取统计信息
        total_jobs = await db_manager.get_jobs_count()
        logger.info(f"数据库中共有 {total_jobs} 个职位")

        return recent_jobs

    except Exception as e:
        logger.error(f"示例执行失败: {e}")
        return None


async def example_test_config():
    """示例：测试网站配置"""
    from crawler.core.crawler import CrawlerEngine

    logger.info("示例：测试网站配置")

    try:
        # 1. 初始化爬虫引擎
        crawler = CrawlerEngine("config/global.yaml")

        # 2. 测试网站配置
        site_name = "yilzhaopin"
        if site_name in crawler.site_configs:
            config = crawler.site_configs[site_name]
            logger.info(f"网站配置信息:")
            logger.info(f"  名称: {config.site_name}")
            logger.info(f"  URL: {config.base_url}")
            logger.info(f"  是否启用: {config.enabled}")
            logger.info(f"  优先级: {config.priority}")
            logger.info(f"  解析规则: {len(config.parsing.get('fields', {}))} 个字段")
        else:
            logger.warning(f"网站 {site_name} 配置不存在")

        # 3. 清理资源
        await crawler.cleanup()

        return True

    except Exception as e:
        logger.error(f"示例执行失败: {e}")
        return False


async def run_all_examples():
    """运行所有示例"""
    logger.info("开始运行使用示例...")
    logger.info("=" * 50)

    # 运行示例
    logger.info("\n1. 测试网站配置")
    await example_test_config()

    logger.info("\n2. 查询现有数据")
    await example_query_data()

    logger.info("\n3. 爬取单个网站（需要网络连接）")
    logger.info("   注意：需要实际网站可访问，这里只演示流程")
    # await example_crawl_single_site()  # 注释掉，避免实际请求

    logger.info("\n4. 爬取所有网站（需要网络连接）")
    logger.info("   注意：需要实际网站可访问，这里只演示流程")
    # await example_crawl_all_sites()  # 注释掉，避免实际请求

    logger.info("=" * 50)
    logger.info("示例运行完成！")
    logger.info("\n要实际运行爬虫，请执行:")
    logger.info("  python main.py --all")
    logger.info("或:")
    logger.info("  python main.py --site yilzhaopin")


if __name__ == "__main__":
    asyncio.run(run_all_examples())
