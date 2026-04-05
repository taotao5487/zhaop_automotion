
#!/usr/bin/env python3
"""
基础功能测试
测试数据库、配置加载、解析器等基本功能
"""

import asyncio
import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

# 添加项目根目录到Python路径
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import logging
from sqlalchemy import select
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from crawler.core.database import DatabaseManager, db_manager
from crawler.core.exporter import export_new_jobs
from crawler.models.job import MedicalJob, CrawlStatus


async def test_database():
    """测试数据库功能"""
    logger.info("测试数据库功能...")

    try:
        # 初始化数据库
        await db_manager.init_async()

        # 测试保存职位
        test_job = {
            'title': '测试职位 - 护士',
            'url': 'https://www.example.com/job/123',
            'publish_date': datetime(2024, 1, 15),
            'hospital': '测试医院',
            'location': '重庆',
            'source_site': 'test_site'
        }

        saved_job = await db_manager.save_job(test_job)
        assert saved_job is not None
        assert saved_job.title == test_job['title']
        logger.info(f"✓ 职位保存成功: {saved_job.title}")

        # 测试检查存在性
        exists = await db_manager.job_exists(test_job['url'])
        assert exists is True
        logger.info("✓ 职位存在性检查成功")

        # 测试获取职位数
        count = await db_manager.get_jobs_count()
        assert count >= 1
        logger.info(f"✓ 获取职位数成功: {count}")

        # 测试更新爬取状态
        success = await db_manager.update_crawl_status(
            'test_site', 'success', new_jobs=1
        )
        assert success is True
        logger.info("✓ 爬取状态更新成功")

        # 测试获取上次爬取时间
        last_time = await db_manager.get_last_crawl_time('test_site')
        assert last_time is not None
        logger.info(f"✓ 获取上次爬取时间成功: {last_time}")

        # 测试清理旧数据（插入一条旧数据后再清理）
        old_job = {
            'title': '旧职位 - 清理测试',
            'url': 'https://www.example.com/job/old-cleanup-test',
            'publish_date': datetime.now() - timedelta(days=365),
            'hospital': '测试医院',
            'location': '重庆',
            'source_site': 'test_site'
        }
        await db_manager.save_job(old_job)

        deleted = await db_manager.cleanup_old_jobs(days=180)
        assert deleted >= 1
        old_exists = await db_manager.job_exists(old_job['url'])
        assert old_exists is False
        logger.info("✓ 清理旧数据测试成功")

        logger.info("数据库测试全部通过！")
        return True

    except Exception as e:
        logger.error(f"数据库测试失败: {e}")
        return False


async def test_config_loading():
    """测试配置加载"""
    logger.info("测试配置加载...")

    try:
        from crawler.core.crawler import CrawlerEngine

        # 创建爬虫引擎
        crawler = CrawlerEngine("config/global.yaml")

        # 检查全局配置
        assert 'database' in crawler.global_config
        assert 'crawler' in crawler.global_config
        assert 'recruitment_filter' in crawler.global_config
        assert 'export' in crawler.global_config
        logger.info("✓ 全局配置加载成功")

        # 检查网站配置
        assert len(crawler.site_configs) >= 1  # 至少有一个模板
        logger.info(f"✓ 网站配置加载成功: {len(crawler.site_configs)} 个网站")

        # 检查示例网站配置
        if 'yilzhaopin' in crawler.site_configs:
            config = crawler.site_configs['yilzhaopin']
            assert config.site_name == 'yilzhaopin'
            assert config.base_url == 'https://www.ylzhaopin.com'
            logger.info("✓ 示例网站配置加载成功")

        logger.info("配置加载测试全部通过！")
        return True

    except Exception as e:
        logger.error(f"配置加载测试失败: {e}")
        return False


async def test_recruitment_filter():
    """测试招聘过滤规则"""
    logger.info("测试招聘过滤规则...")

    try:
        from crawler.core.crawler import CrawlerEngine

        crawler = CrawlerEngine("config/global.yaml", site_limit=1)
        jobs_data = [
            {'title': '设备采购公告', 'url': 'https://example.com/1'},
            {'title': '试剂比选公告', 'url': 'https://example.com/2'},
            {'title': '公开招聘进入面试资格复审名单', 'url': 'https://example.com/3'},
            {'title': '住院医师规范化培训招收简章', 'url': 'https://example.com/4'},
            {'title': '人才引进项目招聘公告', 'url': 'https://example.com/5'},
        ]

        filtered_jobs, filtered_count = crawler.filter_jobs(jobs_data, 'test_site')
        filtered_titles = {job['title'] for job in filtered_jobs}

        assert '设备采购公告' not in filtered_titles
        assert '试剂比选公告' not in filtered_titles
        assert '公开招聘进入面试资格复审名单' not in filtered_titles
        assert '住院医师规范化培训招收简章' in filtered_titles
        assert '人才引进项目招聘公告' in filtered_titles
        assert filtered_count == 3
        logger.info("✓ 招聘过滤规则生效正确")
        return True

    except Exception as e:
        logger.error(f"招聘过滤规则测试失败: {e}")
        return False


async def test_incremental_save_modes():
    """测试增量/非增量保存语义"""
    logger.info("测试增量/非增量保存语义...")

    try:
        with TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "jobs.db")
            temp_db = DatabaseManager(db_path=db_path)
            await temp_db.init_async()

            base_job = {
                'title': '测试职位 - 护士',
                'url': 'https://www.example.com/job/keep',
                'publish_date': datetime(2024, 1, 15),
                'hospital': '测试医院',
                'location': '重庆',
                'source_site': 'test_site',
            }
            updated_job = {**base_job, 'title': '测试职位 - 护士长'}

            saved_job, is_new = await temp_db.save_or_update_job(base_job, update_existing=False)
            assert saved_job is not None
            assert is_new is True

            saved_job, is_new = await temp_db.save_or_update_job(updated_job, update_existing=False)
            assert saved_job is not None
            assert is_new is False

            async with await temp_db.get_async_session() as session:
                result = await session.execute(
                    select(MedicalJob).where(MedicalJob.url == base_job['url'])
                )
                persisted_job = result.scalar_one()
                assert persisted_job.title == base_job['title']

            saved_job, is_new = await temp_db.save_or_update_job(updated_job, update_existing=True)
            assert saved_job is not None
            assert is_new is False

            async with await temp_db.get_async_session() as session:
                result = await session.execute(
                    select(MedicalJob).where(MedicalJob.url == base_job['url'])
                )
                persisted_job = result.scalar_one()
                assert persisted_job.title == updated_job['title']
                assert persisted_job.is_new is False

            await temp_db.async_engine.dispose()

        logger.info("✓ 增量/非增量保存语义正确")
        return True

    except Exception as e:
        logger.error(f"增量/非增量保存语义测试失败: {e}")
        return False


def test_save_or_update_job_coerces_string_publish_date():
    """测试字符串发布时间会在入库前转换为 datetime"""
    logger.info("测试字符串发布时间入库转换...")

    async def _run():
        with TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "jobs.db")
            temp_db = DatabaseManager(db_path=db_path)
            await temp_db.init_async()

            job_data = {
                'title': '测试职位 - 医生',
                'url': 'https://www.example.com/job/string-date',
                'publish_date': '2026-03-31',
                'hospital': '测试医院',
                'location': '重庆',
                'source_site': 'test_site',
            }

            saved_job, is_new = await temp_db.save_or_update_job(job_data, update_existing=True)

            assert saved_job is not None
            assert is_new is True

            async with await temp_db.get_async_session() as session:
                result = await session.execute(
                    select(MedicalJob).where(MedicalJob.url == job_data['url'])
                )
                persisted_job = result.scalar_one()
                assert persisted_job.publish_date == datetime(2026, 3, 31)

            await temp_db.async_engine.dispose()

    asyncio.run(_run())
    logger.info("✓ 字符串发布时间入库转换成功")


def test_save_or_update_job_allows_missing_publish_date():
    """测试缺少真实发布日期时允许保存空 publish_date"""
    logger.info("测试空 publish_date 可保存...")

    async def _run():
        with TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "jobs.db")
            temp_db = DatabaseManager(db_path=db_path)
            await temp_db.init_async()

            job_data = {
                'title': '测试职位 - 无发布日期',
                'url': 'https://www.example.com/job/no-publish-date',
                'hospital': '测试医院',
                'location': '重庆',
                'source_site': 'test_site',
            }

            saved_job, is_new = await temp_db.save_or_update_job(job_data, update_existing=True)

            assert saved_job is not None
            assert is_new is True

            async with await temp_db.get_async_session() as session:
                result = await session.execute(
                    select(MedicalJob).where(MedicalJob.url == job_data['url'])
                )
                persisted_job = result.scalar_one()
                assert persisted_job.publish_date is None

            await temp_db.async_engine.dispose()

    asyncio.run(_run())
    logger.info("✓ 空 publish_date 保存成功")


def test_init_async_migrates_legacy_publish_date_not_null_schema():
    """测试旧版 publish_date NOT NULL 的 SQLite 表会自动迁移"""
    logger.info("测试旧版 publish_date 非空约束迁移...")

    async def _run():
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobs.db"
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute(
                '''
                CREATE TABLE medical_jobs (
                    id INTEGER PRIMARY KEY,
                    title VARCHAR NOT NULL,
                    url VARCHAR NOT NULL UNIQUE,
                    publish_date DATETIME NOT NULL,
                    hospital VARCHAR,
                    location VARCHAR,
                    source_site VARCHAR,
                    crawl_time DATETIME,
                    is_new BOOLEAN
                )
                '''
            )
            conn.execute(
                '''
                INSERT INTO medical_jobs
                (title, url, publish_date, hospital, location, source_site, crawl_time, is_new)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    '旧职位',
                    'https://example.com/legacy',
                    '2026-04-01 00:00:00',
                    '测试医院',
                    '重庆',
                    'test_site',
                    '2026-04-03 16:00:00',
                    0,
                ),
            )
            conn.commit()
            conn.close()

            temp_db = DatabaseManager(db_path=str(db_path))
            await temp_db.init_async()

            verify_conn = sqlite3.connect(db_path)
            columns = list(verify_conn.execute("PRAGMA table_info(medical_jobs)"))
            publish_date_column = next(row for row in columns if row[1] == 'publish_date')
            rows = list(verify_conn.execute("SELECT title, url, publish_date FROM medical_jobs"))
            verify_conn.close()

            assert publish_date_column[3] == 0
            assert rows == [('旧职位', 'https://example.com/legacy', '2026-04-01 00:00:00')]

            await temp_db.async_engine.dispose()

    asyncio.run(_run())
    logger.info("✓ 旧版 publish_date 非空约束迁移成功")


async def test_export_outputs():
    """测试新增导出产物"""
    logger.info("测试新增导出产物...")

    try:
        with TemporaryDirectory() as tmpdir:
            export_time = datetime(2026, 3, 24, 18, 0, 0)
            jobs_data = [
                {
                    'id': 1,
                    'title': '重庆市人民医院招聘公告',
                    'url': 'https://example.com/job/1',
                    'hospital': '重庆市人民医院',
                    'location': '重庆',
                    'source_site': 'site_1',
                    'publish_date': export_time,
                    'crawl_time': export_time,
                    'is_new': True,
                },
                {
                    'id': 2,
                    'title': '四川省人民医院招聘公告',
                    'url': 'https://example.com/job/2',
                    'hospital': '四川省人民医院',
                    'location': '成都',
                    'source_site': 'site_2',
                    'publish_date': export_time,
                    'crawl_time': export_time,
                    'is_new': True,
                },
            ]

            export_result = export_new_jobs(jobs_data, tmpdir, run_time=export_time)
            assert export_result.get('exported') is True

            csv_path = Path(export_result['csv_path'])
            markdown_path = Path(export_result['markdown_path'])
            assert csv_path.exists()
            assert markdown_path.exists()

            markdown_content = markdown_path.read_text(encoding='utf-8')
            assert '本次新增招聘清单' in markdown_content
            assert '重庆市人民医院' in markdown_content
            assert 'https://example.com/job/1' in markdown_content

            empty_result = export_new_jobs([], tmpdir, run_time=export_time)
            assert empty_result.get('exported') is False
            assert len(list(Path(tmpdir).iterdir())) == 2

        logger.info("✓ 新增导出产物生成正确")
        return True

    except Exception as e:
        logger.error(f"新增导出产物测试失败: {e}")
        return False


async def test_site_concurrency():
    """测试站点级并发控制"""
    logger.info("测试站点级并发控制...")

    try:
        from crawler.core.crawler import CrawlerConfig, CrawlerEngine

        crawler = CrawlerEngine("config/global.yaml", site_limit=1)
        crawler.concurrent_sites = 2
        crawler.session = object()  # 避免初始化真实会话
        crawler.site_configs = {
            f'site_{index}': CrawlerConfig({
                'site_name': f'site_{index}',
                'base_url': 'https://example.com',
                'enabled': True,
                'priority': index,
            })
            for index in range(4)
        }

        running = 0
        max_running = 0

        async def fake_crawl_site(site_name: str, incremental: bool = True):
            nonlocal running, max_running
            running += 1
            max_running = max(max_running, running)
            await asyncio.sleep(0.05)
            running -= 1
            return {
                'success': True,
                'new_jobs': 1,
                'updated_jobs': 0,
                'filtered_jobs': 0,
                'new_jobs_data': [{'title': site_name, 'url': f'https://example.com/{site_name}'}],
            }

        crawler.crawl_site = fake_crawl_site  # type: ignore[method-assign]

        results = await crawler.crawl_all_sites(incremental=True)
        assert results['successful'] == 4
        assert results['total_new_jobs'] == 4
        assert 1 < max_running <= 2
        logger.info("✓ 站点级并发控制正确")
        return True

    except Exception as e:
        logger.error(f"站点级并发控制测试失败: {e}")
        return False


async def test_parsing():
    """测试解析功能"""
    logger.info("测试解析功能...")

    try:
        from crawler.spiders.base_spider import BaseSpider

        # 创建测试配置
        test_config = {
            'site_name': 'test_site',
            'base_url': 'https://www.example.com',
            'parsing': {
                'list_page': {
                    'container_selector': 'div.job-list',
                    'item_selector': 'li.job-item',
                    'fields': {
                        'title': {
                            'selector': 'h2.job-title a',
                            'type': 'text',
                            'required': True
                        },
                        'url': {
                            'selector': 'h2.job-title a',
                            'type': 'attr',
                            'attr': 'href',
                            'required': True,
                            'transform': 'absolute_url'
                        }
                    }
                }
            }
        }

        # 创建爬虫实例
        spider = BaseSpider(test_config)

        # 测试HTML
        test_html = """
        <html>
            <body>
                <div class="job-list">
                    <li class="job-item">
                        <h2 class="job-title"><a href="/job/1">护士</a></h2>
                    </li>
                    <li class="job-item">
                        <h2 class="job-title"><a href="/job/2">医生</a></h2>
                    </li>
                </div>
            </body>
        </html>
        """

        # 测试解析
        jobs = spider.parse_list_page(test_html)
        assert len(jobs) == 2
        logger.info(f"✓ 解析到 {len(jobs)} 个职位")

        # 检查第一个职位
        job1 = jobs[0]
        assert job1['title'] == '护士'
        assert job1['url'] == 'https://www.example.com/job/1'
        logger.info(f"✓ 职位1解析正确: {job1['title']}")

        # 检查第二个职位
        job2 = jobs[1]
        assert job2['title'] == '医生'
        assert job2['url'] == 'https://www.example.com/job/2'
        logger.info(f"✓ 职位2解析正确: {job2['title']}")

        logger.info("解析测试全部通过！")
        return True

    except Exception as e:
        logger.error(f"解析测试失败: {e}")
        return False


def test_parse_list_page_skips_invalid_candidates_without_warning_spam(caplog):
    """测试宽列表选择器下会跳过无效候选节点，避免重复 warning"""
    logger.info("测试宽列表候选节点过滤...")

    from crawler.spiders.base_spider import BaseSpider

    test_config = {
        'site_name': 'test_site',
        'base_url': 'https://www.example.com',
        'parsing': {
            'list_page': {
                'container_selector': 'body',
                'item_selector': 'div.card',
                'fields': {
                    'title': {
                        'selector': 'h2.job-title a',
                        'type': 'text',
                        'required': True,
                    },
                    'url': {
                        'selector': 'h2.job-title a',
                        'type': 'attr',
                        'attr': 'href',
                        'required': True,
                        'transform': 'absolute_url',
                    },
                    'hospital': {
                        'default': '测试医院',
                    },
                },
            }
        }
    }

    spider = BaseSpider(test_config)
    test_html = """
    <html>
        <body>
            <div class="card"><span>首页</span></div>
            <div class="card"><h2 class="job-title"><a href="/job/1">护士</a></h2></div>
            <div class="card"><span>联系我们</span></div>
            <div class="card"><h2 class="job-title"><a href="/job/2">医生</a></h2></div>
        </body>
    </html>
    """

    with caplog.at_level(logging.INFO, logger='crawler.spiders.base_spider'):
        jobs = spider.parse_list_page(test_html)

    assert len(jobs) == 2
    assert [job['title'] for job in jobs] == ['护士', '医生']
    assert '字段 title 提取失败，但为必需字段' not in caplog.text
    assert "选择器 'h2.job-title a' 未匹配到任何元素" not in caplog.text
    logger.info("✓ 宽列表候选节点过滤成功")


def test_parse_list_page_reports_summary_when_all_candidates_invalid(caplog):
    """测试全部候选节点无效时输出可定位的汇总 warning"""
    logger.info("测试无效候选节点汇总 warning...")

    from crawler.spiders.base_spider import BaseSpider

    test_config = {
        'site_name': 'test_site',
        'base_url': 'https://www.example.com',
        'parsing': {
            'list_page': {
                'container_selector': 'body',
                'item_selector': 'div.card',
                'fields': {
                    'title': {
                        'selector': 'h2.job-title a',
                        'type': 'text',
                        'required': True,
                        'field_name': 'title',
                    },
                    'url': {
                        'selector': 'h2.job-title a',
                        'type': 'attr',
                        'attr': 'href',
                        'required': True,
                        'transform': 'absolute_url',
                        'field_name': 'url',
                    },
                    'hospital': {
                        'default': '测试医院',
                    },
                },
            }
        }
    }

    spider = BaseSpider(test_config)
    test_html = """
    <html>
        <body>
            <div class="card"><span>首页</span></div>
            <div class="card"><span>联系我们</span></div>
        </body>
    </html>
    """

    with caplog.at_level(logging.WARNING, logger='crawler.spiders.base_spider'):
        jobs = spider.parse_list_page(test_html)

    assert jobs == []
    assert 'site=test_site' in caplog.text
    assert 'hospital=测试医院' in caplog.text
    assert 'title' in caplog.text
    assert 'url' in caplog.text
    logger.info("✓ 无效候选节点汇总 warning 正确")


def test_parse_list_page_inferrs_real_publish_date_from_item_text():
    """测试列表项文本中的真实日期会被自动提取"""
    logger.info("测试列表项真实日期自动提取...")

    from crawler.spiders.base_spider import BaseSpider

    test_config = {
        'site_name': 'test_site',
        'base_url': 'https://www.example.com',
        'parsing': {
            'list_page': {
                'container_selector': 'ul.jobs',
                'item_selector': 'li',
                'fields': {
                    'title': {
                        'selector': 'a',
                        'type': 'text',
                        'required': True,
                    },
                    'url': {
                        'selector': 'a',
                        'type': 'attr',
                        'attr': 'href',
                        'required': True,
                        'transform': 'absolute_url',
                    },
                    'hospital': {
                        'default': '测试医院',
                    },
                },
            }
        }
    }

    spider = BaseSpider(test_config)
    test_html = """
    <html>
        <body>
            <ul class="jobs">
                <li><a href="/job/1">护士招聘公告</a><span class="date">2026-04-02</span></li>
            </ul>
        </body>
    </html>
    """

    jobs = spider.parse_list_page(test_html)

    assert len(jobs) == 1
    assert jobs[0]['publish_date'] == datetime(2026, 4, 2)
    logger.info("✓ 列表项真实日期自动提取成功")


def test_parse_list_page_leaves_publish_date_empty_when_no_real_date():
    """测试没有真实日期时不会伪造 publish_date"""
    logger.info("测试缺少真实日期时不伪造 publish_date...")

    from crawler.spiders.base_spider import BaseSpider

    test_config = {
        'site_name': 'test_site',
        'base_url': 'https://www.example.com',
        'parsing': {
            'list_page': {
                'container_selector': 'ul.jobs',
                'item_selector': 'li',
                'fields': {
                    'title': {
                        'selector': 'a',
                        'type': 'text',
                        'required': True,
                    },
                    'url': {
                        'selector': 'a',
                        'type': 'attr',
                        'attr': 'href',
                        'required': True,
                        'transform': 'absolute_url',
                    },
                    'hospital': {
                        'default': '测试医院',
                    },
                },
            }
        }
    }

    spider = BaseSpider(test_config)
    test_html = """
    <html>
        <body>
            <ul class="jobs">
                <li><a href="/job/1">护士招聘公告</a></li>
            </ul>
        </body>
    </html>
    """

    jobs = spider.parse_list_page(test_html)

    assert len(jobs) == 1
    assert 'publish_date' not in jobs[0] or jobs[0]['publish_date'] is None
    logger.info("✓ 缺少真实日期时不会伪造 publish_date")


async def test_onclick_link_parsing():
    """测试 onclick 链接恢复"""
    logger.info("测试 onclick 链接恢复...")

    try:
        from crawler.spiders.base_spider import BaseSpider

        test_config = {
            'site_name': 'test_site',
            'base_url': 'https://www.example.com',
            'parsing': {
                'list_page': {
                    'container_selector': 'div.job-list',
                    'item_selector': 'li.job-item',
                    'fields': {
                        'title': {
                            'selector': 'a',
                            'type': 'text',
                            'required': True
                        },
                        'url': {
                            'selector': 'a',
                            'type': 'attr',
                            'attr': 'href',
                            'required': True,
                            'transform': 'absolute_url'
                        }
                    }
                }
            }
        }

        spider = BaseSpider(test_config)
        test_html = """
        <html>
            <body>
                <div class="job-list">
                    <li class="job-item">
                        <a href="#" onclick="turnInfoArt(1176,1)">招聘公告</a>
                    </li>
                </div>
            </body>
        </html>
        """

        jobs = spider.parse_list_page(test_html)
        assert len(jobs) == 1
        assert jobs[0]['url'] == 'https://www.example.com/pageInfoArt.html?artId=1176&page=1'
        logger.info("✓ onclick 链接恢复成功")
        return True

    except Exception as e:
        logger.error(f"onclick 链接恢复测试失败: {e}")
        return False


async def test_anchor_item_self_matching():
    """测试列表项本身就是链接节点时的字段提取"""
    logger.info("测试 anchor 列表项自匹配...")

    try:
        from crawler.spiders.base_spider import BaseSpider

        test_config = {
            'site_name': 'test_site',
            'base_url': 'https://www.example.com',
            'parsing': {
                'list_page': {
                    'container_selector': 'div.navBox',
                    'item_selector': 'a.flex-jsb',
                    'fields': {
                        'title': {
                            'selector': 'span.title',
                            'type': 'text',
                            'required': True
                        },
                        'url': {
                            'selector': 'a',
                            'type': 'attr',
                            'attr': 'href',
                            'required': True,
                            'transform': 'absolute_url'
                        }
                    }
                }
            }
        }

        spider = BaseSpider(test_config)
        test_html = """
        <html>
            <body>
                <div class="navBox">
                    <a class="flex-jsb item cur brb" href="/index.php/news/detail?id=1748">
                        <div>
                            <span class="title cur">重庆医科大学附属大足医院招聘启事</span>
                        </div>
                        <div>2026-01-06</div>
                    </a>
                </div>
            </body>
        </html>
        """

        jobs = spider.parse_list_page(test_html)
        assert len(jobs) == 1
        assert jobs[0]['title'] == '重庆医科大学附属大足医院招聘启事'
        assert jobs[0]['url'] == 'https://www.example.com/index.php/news/detail?id=1748'
        logger.info("✓ anchor 列表项自匹配成功")
        return True

    except Exception as e:
        logger.error(f"anchor 列表项自匹配测试失败: {e}")
        return False


async def test_duplicate_body_container_fallback():
    """测试重复 body 场景下回退到有效容器/整页搜索"""
    logger.info("测试重复 body 容器回退...")

    try:
        from crawler.spiders.base_spider import BaseSpider

        test_config = {
            'site_name': 'test_site',
            'base_url': 'https://www.example.com',
            'parsing': {
                'list_page': {
                    'container_selector': 'body',
                    'item_selector': 'div.navBox a.flex-jsb',
                    'fields': {
                        'title': {
                            'selector': 'span.title',
                            'type': 'text',
                            'required': True
                        },
                        'url': {
                            'selector': 'a',
                            'type': 'attr',
                            'attr': 'href',
                            'required': True,
                            'transform': 'absolute_url'
                        }
                    }
                }
            }
        }

        spider = BaseSpider(test_config)
        test_html = """
        <!DOCTYPE html>
        <html>
            <body>
                <div id="app"></div>
            </body>
        </html>
        <body>
            <div class="navBox">
                <a class="flex-jsb item cur brb" href="/index.php/news/detail?id=1748">
                    <div>
                        <span class="title cur">重庆医科大学附属大足医院招聘启事</span>
                    </div>
                </a>
            </div>
        </body>
        """

        jobs = spider.parse_list_page(test_html)
        assert len(jobs) == 1
        assert jobs[0]['title'] == '重庆医科大学附属大足医院招聘启事'
        assert jobs[0]['url'] == 'https://www.example.com/index.php/news/detail?id=1748'
        logger.info("✓ 重复 body 容器回退成功")
        return True

    except Exception as e:
        logger.error(f"重复 body 容器回退测试失败: {e}")
        return False


async def test_ruifox_fallback():
    """测试 Ruifox CMS 接口兜底"""
    logger.info("测试 Ruifox CMS 接口兜底...")

    try:
        from crawler.core.crawler import CrawlerConfig, CrawlerEngine

        engine = CrawlerEngine(site_limit=1)
        config = CrawlerConfig({
            'site_name': 'test_ruifox',
            'base_url': 'https://www.example.com',
            'start_url': 'https://www.example.com/pageList.html?mId=39&page=1',
            'parsing': {
                'list_page': {
                    'fields': {
                        'hospital': {'default': '测试医院'}
                    }
                }
            }
        })

        shell_html = """
        <html>
            <head>
                <meta name="author" content="Ruifox Inc.">
                <script src="indexJS/page/list.js"></script>
            </head>
            <body>
                <div id="pagerAppender"></div>
            </body>
        </html>
        """

        async def fake_fetch_page(url, cfg, retry_count=0, visited_cookie=False):
            return '{"page":1,"indexMain":[{"artId":1176,"artTitle":"招聘公告"}]}'

        engine.fetch_page = fake_fetch_page  # type: ignore[method-assign]
        jobs = await engine.parse_page(shell_html, config)

        assert len(jobs) == 1
        assert jobs[0]['title'] == '招聘公告'
        assert jobs[0]['url'] == 'https://www.example.com/pageInfoArt.html?artId=1176&page=1'
        logger.info("✓ Ruifox CMS 接口兜底成功")
        return True

    except Exception as e:
        logger.error(f"Ruifox CMS 接口兜底测试失败: {e}")
        return False


async def test_waf_slider_detection():
    """测试滑块验证页识别"""
    logger.info("测试滑块验证页识别...")

    try:
        from crawler.core.crawler import CrawlerEngine

        engine = CrawlerEngine(site_limit=1)

        waf_html = """
        <html>
            <body>
                <h1>人机身份验证</h1>
                <div id="slider-thumb"></div>
                <script>
                    document.cookie = `Waf_Tag=${Date.now()}; path=/`;
                </script>
            </body>
        </html>
        """
        normal_html = """
        <html>
            <body>
                <ul class="news-text">
                    <li><h2><a href="/detail/1">招聘公告</a></h2></li>
                </ul>
            </body>
        </html>
        """

        assert engine._needs_waf_tag_cookie(waf_html) is True
        assert engine._needs_waf_tag_cookie(normal_html) is False
        logger.info("✓ 滑块验证页识别成功")
        return True

    except Exception as e:
        logger.error(f"滑块验证页识别测试失败: {e}")
        return False


async def test_date_parsing():
    """测试日期解析"""
    logger.info("测试日期解析...")

    try:
        from crawler.spiders.base_spider import BaseSpider

        # 创建测试配置
        test_config = {'site_name': 'test', 'base_url': 'https://example.com'}
        spider = BaseSpider(test_config)

        # 测试各种日期格式
        test_cases = [
            ('2024-01-15', '2024-01-15'),
            ('2024/01/15', '2024-01-15'),
            ('15/01/2024', '2024-01-15'),
            ('2024年01月15日', '2024-01-15'),
            ('Jan 15, 2024', '2024-01-15'),
        ]

        for date_str, expected in test_cases:
            parsed = spider._parse_date(date_str)
            if parsed:
                result = parsed.strftime('%Y-%m-%d')
                logger.info(f"✓ {date_str} -> {result}")
            else:
                logger.warning(f"✗ {date_str} 解析失败")

        logger.info("日期解析测试完成！")
        return True

    except Exception as e:
        logger.error(f"日期解析测试失败: {e}")
        return False


async def test_detail_pipeline_article_export():
    """测试详情文章包导出"""
    logger.info("测试详情文章包导出...")

    try:
        from crawler.core.crawler import CrawlerConfig
        from crawler.core.detail_pipeline import DetailPipeline
        from PIL import Image

        detail_html = """
        <html>
            <head><title>重庆市人民医院招聘公告正文</title></head>
            <body>
                <div class="sidebar">右侧广告</div>
                <div class="article-content">
                    <h1>重庆市人民医院招聘公告正文</h1>
                    <p>这里是公告正文第一段。</p>
                    <p>这里是公告正文第二段，包含岗位说明和报名要求。</p>
                    <img src="/images/poster.png" alt="海报" />
                    <p><a href="/files/岗位一览表.xlsx">岗位一览表下载</a></p>
                </div>
            </body>
        </html>
        """

        class FakeCrawler:
            def __init__(self):
                self.site_configs = {
                    "test_site": CrawlerConfig(
                        {
                            "site_name": "test_site",
                            "base_url": "https://example.com",
                            "start_url": "https://example.com/list",
                            "request_timeout": 30,
                            "max_retries": 1,
                            "delay_between_requests": 0,
                            "user_agent": "Mozilla/5.0",
                            "parsing": {},
                            "waf_strategy": {},
                            "validation": {},
                            "ssl_verify": True,
                        }
                    )
                }
                self.session = object()

            async def init_session(self):
                self.session = object()

            async def fetch_page(self, url, config):
                return detail_html

            async def fetch_page_with_metadata(self, url, config):
                return {
                    "success": True,
                    "content": detail_html,
                    "fetch_backend": "aiohttp",
                    "failure_category": "",
                }

        with TemporaryDirectory() as tmpdir:
            rules_path = Path(tmpdir) / "detail_rules.yaml"
            rules_path.write_text(
                """
sites:
  test_site:
    content_selectors:
      - ".article-content"
    remove_selectors:
      - ".sidebar"
                """.strip(),
                encoding="utf-8",
            )

            crawler = FakeCrawler()
            pipeline = DetailPipeline(
                crawler,
                detail_rules_path=str(rules_path),
                output_root=str(Path(tmpdir) / "details"),
            )

            async def fake_download(source_url, destination, config):
                if source_url.endswith(".png"):
                    Image.new("RGB", (20, 20), "white").save(destination)
                    return True, "image/png"
                destination.write_bytes(b"fake-xlsx")
                return True, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

            async def fake_render(attachment_path, attachment, renders_dir):
                render_path = renders_dir / "attachment_sheet_1.png"
                Image.new("RGB", (30, 30), "white").save(render_path)
                return [render_path], "rendered"

            pipeline._download_binary_resource = fake_download  # type: ignore[method-assign]
            pipeline._render_attachment = fake_render  # type: ignore[method-assign]

            summary = await pipeline.process_jobs(
                [
                    {
                        "title": "重庆市人民医院招聘公告",
                        "url": "https://example.com/detail/1",
                        "hospital": "重庆市人民医院",
                        "source_site": "test_site",
                    }
                ],
                run_time=datetime(2026, 3, 25, 21, 0, 0),
            )

            assert summary["generated"] is True
            assert summary["detail_success_count"] == 1
            assert summary["wechat_ready_count"] == 1
            result = summary["results"][0]
            article_dir = Path(result["article_dir"])
            article_path = article_dir / "article.md"
            manifest_path = article_dir / "manifest.json"
            cleaned_path = article_dir / "cleaned.html"
            review_dir = Path(result["review_dir"])
            review_html_path = Path(result["review_article_html_path"])
            review_package_path = Path(result["review_package_path"])

            assert article_path.exists()
            assert manifest_path.exists()
            assert cleaned_path.exists()
            assert result["detail_success"] is True
            assert result["wechat_ready"] is True
            assert result["success"] is True
            assert review_dir.exists()
            assert review_html_path.exists()
            assert review_package_path.exists()
            assert (review_dir / "article.txt").exists()
            assert (review_dir / "review.md").exists()

            article_content = article_path.read_text(encoding="utf-8")
            cleaned_content = cleaned_path.read_text(encoding="utf-8")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            review_package = json.loads(review_package_path.read_text(encoding="utf-8"))

            assert "## 正文" in article_content
            assert "岗位一览表下载" in article_content
            assert "assets/inline/" in article_content
            assert "assets/renders/" in article_content
            assert "右侧广告" not in cleaned_content
            assert manifest["extraction_method"] == "selectors"
            assert len(manifest["inline_images"]) == 1
            assert len(manifest["attachments"]) == 1
            assert manifest["attachments"][0]["category"] == "position_table"
            assert manifest["attachments"][0]["render_status"] == "rendered"
            assert result["article_type"] == "attachment_driven"
            assert review_package["article_type"] == "attachment_driven"
            assert "招聘速览" not in review_package["content_html"]
            assert "招聘岗位" in review_package["content_html"]
            assert "岗位信息图为准" in review_package["content_html"]

        logger.info("✓ 详情文章包导出成功")
        return True

    except Exception as e:
        logger.error(f"详情文章包导出测试失败: {e}")
        return False


async def test_detail_pipeline_readability_fallback():
    """测试详情正文的 readability 兜底"""
    logger.info("测试详情正文 readability 兜底...")

    try:
        from crawler.core.detail_pipeline import DetailPipeline, Document

        class FakeCrawler:
            site_configs = {}
            session = object()

            async def init_session(self):
                self.session = object()

        class FakeDocument:
            def __init__(self, raw_html):
                self.raw_html = raw_html

            def summary(self, html_partial=True):
                return """
                <div class="article">
                    <p>这是经过 readability 提取后的正文内容，长度足够用于测试兜底逻辑。</p>
                </div>
                """

        import core.detail_pipeline as detail_module

        original_document = Document
        detail_module.Document = FakeDocument
        try:
            pipeline = DetailPipeline(FakeCrawler())
            cleaned_html, cleaned_title, extraction_method = pipeline._extract_cleaned_html(
                """
                <html>
                    <head><title>readability 测试标题</title></head>
                    <body>
                        <div class="noise">噪音区域</div>
                    </body>
                </html>
                """,
                "https://example.com/detail/2",
                "回退标题",
                pipeline.get_rule(None),
            )
        finally:
            detail_module.Document = original_document

        assert extraction_method == "readability"
        assert "readability 提取后的正文内容" in cleaned_html
        assert cleaned_title == "readability 测试标题"
        logger.info("✓ readability 兜底成功")
        return True

    except Exception as e:
        logger.error(f"详情正文 readability 兜底测试失败: {e}")
        return False


async def test_xlsx_attachment_rendering():
    """测试 XLSX 附件渲染为图片"""
    logger.info("测试 XLSX 附件渲染...")

    try:
        from openpyxl import Workbook
        from crawler.core.detail_pipeline import DetailPipeline

        class FakeCrawler:
            site_configs = {}
            session = object()

            async def init_session(self):
                self.session = object()

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "岗位表"
            sheet.append(["岗位", "人数", "学历"])
            sheet.append(["护士", "10", "本科"])
            sheet.append(["医师", "5", "硕士"])
            xlsx_path = tmp_path / "jobs.xlsx"
            workbook.save(xlsx_path)

            renders_dir = tmp_path / "renders"
            renders_dir.mkdir()

            pipeline = DetailPipeline(FakeCrawler(), output_root=str(tmp_path / "details"))
            render_paths, render_status = pipeline._render_xlsx(xlsx_path, renders_dir)

            assert render_status == "rendered"
            assert len(render_paths) >= 1
            assert all(path.exists() for path in render_paths)

        logger.info("✓ XLSX 附件渲染成功")
        return True

    except Exception as e:
        logger.error(f"XLSX 附件渲染测试失败: {e}")
        return False


async def test_attachment_table_long_text_column_gets_more_width():
    """测试长文本列会分配到更宽列宽，避免整张岗位图过高"""
    logger.info("测试岗位表长文本列宽分配...")

    try:
        from PIL import Image, ImageDraw

        from crawler.core.detail_pipeline import _estimate_column_widths, _load_font

        rows = [
            ["招聘科室", "岗位名称", "招聘人数", "学历要求", "专业要求", "其他条件"],
            [
                "急诊科",
                "护士",
                "10",
                "本科",
                "护理学",
                "需具备三级医院工作经验，熟悉急危重症护理流程，能够独立完成夜班工作，"
                "具有良好的沟通协调能力和团队合作精神，年龄不超过35周岁。",
            ],
        ]

        image = Image.new("RGB", (10, 10))
        drawer = ImageDraw.Draw(image)
        font = _load_font(20)

        col_widths = _estimate_column_widths(rows, drawer, font, 1520)

        assert len(col_widths) == 6
        assert col_widths[-1] >= 520
        assert col_widths[-1] > max(col_widths[:-1])
        assert max(col_widths[:-1]) <= 240

        logger.info("✓ 岗位表长文本列宽分配正确")
        return True

    except Exception as e:
        logger.error(f"岗位表长文本列宽分配测试失败: {e}")
        return False


async def test_detail_pipeline_qr_article_review():
    """测试正文二维码保留原位置并生成待审包"""
    logger.info("测试正文二维码待审成稿...")

    try:
        from crawler.core.crawler import CrawlerConfig
        from crawler.core.detail_pipeline import DetailPipeline
        from PIL import Image

        detail_html = """
        <html>
            <head><title>二维码报名公告</title></head>
            <body>
                <div class="article-content">
                    <h1>二维码报名公告</h1>
                    <p>请符合条件的应聘人员按要求报名。</p>
                    <p>请扫描下方二维码报名：</p>
                    <img src="/images/qr.png" alt="报名二维码" />
                    <p>咨询电话：12345678</p>
                </div>
            </body>
        </html>
        """

        class FakeCrawler:
            def __init__(self):
                self.site_configs = {
                    "test_site": CrawlerConfig(
                        {
                            "site_name": "test_site",
                            "base_url": "https://example.com",
                            "start_url": "https://example.com/list",
                            "request_timeout": 30,
                            "max_retries": 1,
                            "delay_between_requests": 0,
                            "user_agent": "Mozilla/5.0",
                            "parsing": {},
                            "waf_strategy": {},
                            "validation": {},
                            "ssl_verify": True,
                        }
                    )
                }
                self.session = object()

            async def init_session(self):
                self.session = object()

            async def fetch_page(self, url, config):
                return detail_html

            async def fetch_page_with_metadata(self, url, config):
                return {
                    "success": True,
                    "content": detail_html,
                    "fetch_backend": "aiohttp",
                    "failure_category": "",
                }

        with TemporaryDirectory() as tmpdir:
            rules_path = Path(tmpdir) / "detail_rules.yaml"
            rules_path.write_text(
                """
sites:
  test_site:
    content_selectors:
      - ".article-content"
                """.strip(),
                encoding="utf-8",
            )

            crawler = FakeCrawler()
            pipeline = DetailPipeline(
                crawler,
                detail_rules_path=str(rules_path),
                output_root=str(Path(tmpdir) / "details"),
                review_output_root=str(Path(tmpdir) / "wechat_review"),
            )

            async def fake_download(source_url, destination, config):
                Image.new("RGB", (260, 260), "white").save(destination)
                return True, "image/png"

            pipeline._download_binary_resource = fake_download  # type: ignore[method-assign]

            summary = await pipeline.process_jobs(
                [
                    {
                        "title": "二维码报名公告",
                        "url": "https://example.com/detail/qr",
                        "hospital": "测试医院",
                        "source_site": "test_site",
                    }
                ],
                run_time=datetime(2026, 3, 25, 21, 30, 0),
            )

            result = summary["results"][0]
            package = json.loads(Path(result["review_package_path"]).read_text(encoding="utf-8"))
            assert result["article_type"] == "qr_in_body"
            assert package["article_type"] == "qr_in_body"
            assert "二维码报名" in package["plain_text"]
            assert any(item["category"] == "qr_in_body" and item["keep"] for item in package["images"])

        logger.info("✓ 二维码待审成稿成功")
        return True

    except Exception as e:
        logger.error(f"二维码待审成稿测试失败: {e}")
        return False


async def test_detail_pipeline_wechat_not_ready():
    """测试详情成功但公众号待审包未就绪时的状态语义"""
    logger.info("测试详情成功但公众号包未就绪...")

    try:
        from crawler.core.crawler import CrawlerConfig
        from crawler.core.detail_pipeline import DetailPipeline

        detail_html = """
        <html>
            <head><title>公众号包失败测试</title></head>
            <body>
                <div class="article-content">
                    <h1>公众号包失败测试</h1>
                    <p>这里是正文内容。</p>
                </div>
            </body>
        </html>
        """

        class FakeCrawler:
            def __init__(self):
                self.site_configs = {
                    "test_site": CrawlerConfig(
                        {
                            "site_name": "test_site",
                            "base_url": "https://example.com",
                            "start_url": "https://example.com/list",
                            "request_timeout": 30,
                            "max_retries": 1,
                            "delay_between_requests": 0,
                            "user_agent": "Mozilla/5.0",
                            "parsing": {},
                            "waf_strategy": {},
                            "validation": {},
                            "ssl_verify": True,
                        }
                    )
                }
                self.session = object()

            async def init_session(self):
                self.session = object()

            async def fetch_page_with_metadata(self, url, config):
                return {
                    "success": True,
                    "content": detail_html,
                    "fetch_backend": "aiohttp",
                    "failure_category": "",
                }

        with TemporaryDirectory() as tmpdir:
            crawler = FakeCrawler()
            pipeline = DetailPipeline(
                crawler,
                output_root=str(Path(tmpdir) / "details"),
                review_output_root=str(Path(tmpdir) / "wechat_review"),
            )
            pipeline.review_builder.build_package = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
                RuntimeError("review package boom")
            )

            summary = await pipeline.process_jobs(
                [
                    {
                        "title": "公众号包失败测试",
                        "url": "https://example.com/detail/fail-review",
                        "hospital": "测试医院",
                        "source_site": "test_site",
                    }
                ],
                run_time=datetime(2026, 3, 26, 9, 0, 0),
            )

            assert summary["generated"] is True
            assert summary["detail_success_count"] == 1
            assert summary["wechat_ready_count"] == 0
            assert summary["wechat_not_ready_count"] == 1

            result = summary["results"][0]
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            assert result["detail_success"] is True
            assert result["wechat_ready"] is False
            assert result["success"] is False
            assert result["status"] == "wechat_not_ready"
            assert "wechat_review_build_failed:review package boom" in manifest["errors"][0]

        logger.info("✓ 详情成功但公众号包未就绪语义正确")
        return True

    except Exception as e:
        logger.error(f"公众号包未就绪语义测试失败: {e}")
        return False


async def test_list_page_validation_diagnostics():
    """测试列表页结构化诊断分类"""
    logger.info("测试列表页结构化诊断...")

    try:
        from crawler.core.crawler import CrawlerConfig, CrawlerEngine

        crawler = CrawlerEngine("config/global.yaml", site_limit=1)
        crawler.site_configs = {
            "test_site": CrawlerConfig(
                {
                    "site_name": "test_site",
                    "base_url": "https://example.com",
                    "start_url": "https://example.com/jobs",
                    "parsing": {
                        "list_page": {
                            "container_selector": "body",
                            "item_selector": "li",
                            "fields": {
                                "title": {"selector": "a", "type": "text", "required": True},
                                "url": {
                                    "selector": "a",
                                    "type": "attr",
                                    "attr": "href",
                                    "required": True,
                                    "transform": "absolute_url",
                                },
                                "hospital": {"default": "测试医院"},
                            },
                        }
                    },
                    "waf_strategy": {},
                    "validation": {},
                    "ssl_verify": True,
                }
            )
        }

        async def fake_fetch_ok(url, config):
            return {
                "success": True,
                "content": "<html><body><ul><li><a href='/detail/1'>招聘公告</a></li></ul></body></html>",
                "fetch_backend": "aiohttp",
                "failure_category": "",
            }

        async def fake_parse_ok(content, config):
            return [{"title": "招聘公告", "url": "https://example.com/detail/1"}]

        crawler.fetch_page_with_metadata = fake_fetch_ok  # type: ignore[method-assign]
        crawler.parse_page = fake_parse_ok  # type: ignore[method-assign]
        ok_result = await crawler.validate_list_page_url("https://example.com/jobs", max_items=5)
        assert ok_result["classification"] == "ok"
        assert len(ok_result["jobs"]) == 1

        async def fake_fetch_shell(url, config):
            return {
                "success": True,
                "content": "<html><body><div id='app'></div><script src='/assets/index.js'></script></body></html>",
                "fetch_backend": "aiohttp",
                "failure_category": "",
            }

        async def fake_parse_empty(content, config):
            return []

        crawler.fetch_page_with_metadata = fake_fetch_shell  # type: ignore[method-assign]
        crawler.parse_page = fake_parse_empty  # type: ignore[method-assign]
        shell_result = await crawler.validate_list_page_url("https://example.com/jobs", max_items=5)
        assert shell_result["classification"] == "frontend_shell"

        async def fake_fetch_blocked(url, config):
            return {
                "success": False,
                "content": None,
                "fetch_backend": "aiohttp",
                "failure_category": "waf_or_tls_blocked",
                "error_message": "SSL certificate verify failed",
            }

        crawler.fetch_page_with_metadata = fake_fetch_blocked  # type: ignore[method-assign]
        blocked_result = await crawler.validate_list_page_url("https://example.com/jobs", max_items=5)
        assert blocked_result["classification"] == "waf_or_tls_blocked"

        crawler.site_configs = {}
        crawler.csv_row_issues = [
            {
                "hospital_name": "缺少选择器医院",
                "list_page_url": "https://missing.example.com/jobs",
                "_skip_reason": "listing_selector为空",
            }
        ]
        empty_selector_result = await crawler.validate_list_page_url("https://missing.example.com/jobs", max_items=5)
        assert empty_selector_result["classification"] == "empty_selector"

        logger.info("✓ 列表页结构化诊断正确")
        return True

    except Exception as e:
        logger.error(f"列表页结构化诊断测试失败: {e}")
        return False


async def test_parse_arguments_with_details():
    """测试详情增强命令行参数"""
    logger.info("测试详情增强命令行参数...")

    try:
        from main import parse_arguments

        original_argv = sys.argv[:]
        try:
            sys.argv = [
                "main.py",
                "--site",
                "重庆市人民医院",
                "--with-details",
                "--details-max-items",
                "3",
                "--select-detail-urls",
            ]
            args = parse_arguments()
        finally:
            sys.argv = original_argv

        assert args.site == "重庆市人民医院"
        assert args.with_details is True
        assert args.details_max_items == 3
        assert args.select_detail_urls is True
        logger.info("✓ 详情增强命令行参数解析正确")
        return True

    except Exception as e:
        logger.error(f"详情增强命令行参数测试失败: {e}")
        return False


async def test_selector_suggestion_matches_existing_row():
    """测试详情页命中现有医院配置时不给重复新增"""
    logger.info("测试详情页命中现有医院配置...")

    try:
        from crawler.core.crawler import CrawlerEngine

        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "selectors.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        "hospital_name,list_page_url,base_url,listing_selector,list_title_selector,link_selector",
                        "测试医院,https://example.com/jobs,https://example.com/,ul.notice-list li,a,a",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            crawler = CrawlerEngine("config/global.yaml", selectors_csv_path=str(csv_path))
            detail_url = "https://example.com/recruit/show1001/"

            async def fake_fetch(url, config):
                if url == detail_url:
                    return {
                        "success": True,
                        "content": """
                        <html>
                            <head><title>测试医院公开招聘公告</title></head>
                            <body>
                                <a href="/jobs">招聘信息</a>
                            </body>
                        </html>
                        """,
                        "fetch_backend": "aiohttp",
                        "failure_category": "",
                    }
                if url == "https://example.com/jobs":
                    return {
                        "success": True,
                        "content": """
                        <html>
                            <body>
                                <ul class="notice-list">
                                    <li><a href="/recruit/show1001/">公开招聘公告</a></li>
                                    <li><a href="/recruit/show1002/">规培招收简章</a></li>
                                </ul>
                            </body>
                        </html>
                        """,
                        "fetch_backend": "aiohttp",
                        "failure_category": "",
                    }
                raise AssertionError(f"unexpected url: {url}")

            crawler.fetch_page_with_metadata = fake_fetch  # type: ignore[method-assign]
            result = await crawler.suggest_selector_row_from_detail_url(detail_url)

            assert result.matched_existing_row is True
            assert result.write_allowed is False
            assert result.selected_list_url == "https://example.com/jobs"
            assert result.hospital_name == "测试医院"
            assert result.listing_selector == "ul.notice-list li"
            assert "现有医院配置" in result.message
            await crawler.cleanup()

        logger.info("✓ 详情页命中现有医院配置时会阻止重复新增")
        return True

    except Exception as e:
        logger.error(f"详情页命中现有医院配置测试失败: {e}")
        return False


async def test_selector_suggestion_inferrs_static_selectors():
    """测试详情页可反推静态列表页选择器"""
    logger.info("测试详情页反推静态列表页选择器...")

    try:
        from crawler.core.crawler import CrawlerEngine

        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "selectors.csv"
            csv_path.write_text(
                "hospital_name,list_page_url,base_url,listing_selector,list_title_selector,link_selector\n",
                encoding="utf-8",
            )

            crawler = CrawlerEngine("config/global.yaml", selectors_csv_path=str(csv_path))
            detail_url = "https://new.example.com/recruit/show123/"
            list_url = "https://new.example.com/recruit/"
            fetch_counts = {detail_url: 0, list_url: 0}

            async def fake_fetch(url, config):
                fetch_counts[url] = fetch_counts.get(url, 0) + 1

                if url == detail_url:
                    if fetch_counts[url] == 1:
                        return {
                            "success": True,
                            "content": """
                            <html>
                                <body>
                                    <script>
                                        if (!document.cookie.includes('visited=1')) {
                                            document.cookie = "visited=1; path=/; max-age=31536000";
                                            window.location.reload();
                                        }
                                    </script>
                                </body>
                            </html>
                            """,
                            "fetch_backend": "aiohttp",
                            "failure_category": "",
                        }
                    return {
                        "success": True,
                        "content": """
                        <html>
                            <head><title>示例医院公开招聘公告</title></head>
                            <body>
                                <div class="crumb">
                                    <a href="/recruit/">招聘信息</a>
                                </div>
                            </body>
                        </html>
                        """,
                        "fetch_backend": "aiohttp",
                        "failure_category": "",
                    }

                if url == list_url:
                    if fetch_counts[url] == 1:
                        return {
                            "success": True,
                            "content": """
                            <html>
                                <body>
                                    <script>
                                        if (!document.cookie.includes('visited=1')) {
                                            document.cookie = "visited=1; path=/; max-age=31536000";
                                            window.location.reload();
                                        }
                                    </script>
                                </body>
                            </html>
                            """,
                            "fetch_backend": "aiohttp",
                            "failure_category": "",
                        }
                    return {
                        "success": True,
                        "content": """
                        <html>
                            <body>
                                <ul class="notice-list">
                                    <li><a href="/recruit/show123/">示例医院公开招聘公告</a></li>
                                    <li><a href="/recruit/show124/">示例医院规培招收简章</a></li>
                                    <li><a href="/recruit/show125/">示例医院护理人员招聘公告</a></li>
                                </ul>
                            </body>
                        </html>
                        """,
                        "fetch_backend": "aiohttp",
                        "failure_category": "",
                    }

                raise AssertionError(f"unexpected url: {url}")

            crawler.fetch_page_with_metadata = fake_fetch  # type: ignore[method-assign]
            result = await crawler.suggest_selector_row_from_detail_url(detail_url)

            assert result.matched_existing_row is False
            assert result.write_allowed is True
            assert result.selected_list_url == list_url
            assert result.base_url == "https://new.example.com"
            assert result.hospital_name == "示例医院"
            assert result.listing_selector
            assert result.list_title_selector
            assert result.link_selector
            assert len(result.jobs) >= 3
            assert fetch_counts[detail_url] >= 2
            assert fetch_counts[list_url] >= 2
            await crawler.cleanup()

        logger.info("✓ 详情页可反推出静态列表页选择器")
        return True

    except Exception as e:
        logger.error(f"详情页反推静态列表页选择器测试失败: {e}")
        return False


async def test_selector_suggestion_csv_append_and_script_args():
    """测试建议结果写入 CSV 去重与脚本参数解析"""
    logger.info("测试建议结果写入 CSV 与脚本参数...")

    try:
        from crawler.core.crawler import CrawlerEngine, SelectorSuggestionResult
        from test_selector_from_detail_url import parse_args

        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "selectors.csv"
            csv_path.write_text(
                "hospital_name,list_page_url,base_url,listing_selector,list_title_selector,link_selector\n",
                encoding="utf-8",
            )

            crawler = CrawlerEngine("config/global.yaml", selectors_csv_path=str(csv_path))
            suggestion = SelectorSuggestionResult(
                hospital_name="新增医院",
                detail_url="https://example.org/jobs/show1/",
                candidate_list_urls=["https://example.org/jobs/"],
                selected_list_url="https://example.org/jobs/",
                base_url="https://example.org",
                listing_selector="ul.jobs li",
                list_title_selector="a",
                link_selector="a",
                classification="suggestion_ready",
                confidence=0.92,
                matched_existing_row=False,
                write_allowed=True,
                message="可写入 CSV",
                jobs=[
                    {"title": "新增医院招聘公告", "url": "https://example.org/jobs/show1/"},
                ],
            )

            first_write = crawler.append_selector_row_from_suggestion(suggestion)
            second_write = crawler.append_selector_row_from_suggestion(suggestion)

            csv_lines = csv_path.read_text(encoding="utf-8").splitlines()
            args = parse_args(["--detail-url", suggestion.detail_url, "--write"])

            assert first_write["written"] is True
            assert second_write["written"] is False
            assert len(csv_lines) == 2
            assert csv_lines[1].startswith("新增医院,https://example.org/jobs/")
            assert args.detail_url == suggestion.detail_url
            assert args.write is True
            await crawler.cleanup()

        logger.info("✓ 建议结果写入 CSV 去重与脚本参数解析正确")
        return True

    except Exception as e:
        logger.error(f"建议结果写入 CSV 去重与脚本参数测试失败: {e}")
        return False


async def test_review_queue_candidate_classification():
    """测试审核队列候选分类与结果汇总"""
    logger.info("测试审核队列候选分类...")

    try:
        from crawler.core.review_queue import (
            ReviewQueueConfig,
            apply_review_decisions,
            build_review_candidates,
            build_review_summary,
        )

        class Decision:
            def __init__(self, decision):
                self.decision = decision

        now = datetime.now()
        jobs = [
            {
                "title": "重庆医院公开招聘公告",
                "url": "https://example.com/notice/1",
                "hospital": "重庆医院",
                "source_site": "site_a",
                "publish_date": (now - timedelta(days=1)).isoformat(),
            },
            {
                "title": "重庆医院笔试成绩公示",
                "url": "https://example.com/notice/2",
                "hospital": "重庆医院",
                "source_site": "site_a",
                "publish_date": (now - timedelta(days=2)).isoformat(),
            },
            {
                "title": "重庆医院往年招聘公告",
                "url": "https://example.com/notice/3",
                "hospital": "重庆医院",
                "source_site": "site_a",
                "publish_date": (now - timedelta(days=60)).isoformat(),
            },
            {
                "title": "重庆医院临时招聘启事",
                "url": "https://example.com/notice/4",
                "hospital": "重庆医院",
                "source_site": "site_a",
                "publish_date": "",
            },
            {
                "title": "重庆医院历史已保留招聘公告",
                "url": "https://example.com/notice/5",
                "hospital": "重庆医院",
                "source_site": "site_a",
                "publish_date": (now - timedelta(days=90)).isoformat(),
            },
            {
                "title": "重庆医院历史已丢弃公告",
                "url": "https://example.com/notice/6",
                "hospital": "重庆医院",
                "source_site": "site_a",
                "publish_date": (now - timedelta(days=1)).isoformat(),
            },
        ]
        decisions = {
            "https://example.com/notice/5": Decision("keep"),
            "https://example.com/notice/6": Decision("discard"),
        }

        candidates = build_review_candidates(
            jobs,
            decisions,
            ReviewQueueConfig(recent_days=30),
            recruitment_keywords=["招聘", "引进", "护士", "医师"],
        )
        summary = build_review_summary(candidates)
        by_url = {item.url: item for item in candidates}

        assert candidates[0].url == "https://example.com/notice/5"
        assert by_url["https://example.com/notice/1"].recent is True
        assert by_url["https://example.com/notice/2"].non_recruitment_likely is True
        assert by_url["https://example.com/notice/3"].default_hidden is True
        assert by_url["https://example.com/notice/3"].hidden_reason == "old"
        assert by_url["https://example.com/notice/4"].missing_publish_date is True
        assert by_url["https://example.com/notice/5"].auto_keep is True
        assert by_url["https://example.com/notice/6"].default_hidden is True
        assert by_url["https://example.com/notice/6"].hidden_reason == "previously_discarded"

        assert summary["candidate_total"] == 6
        assert summary["auto_kept_count"] == 1
        assert summary["visible_count"] == 3
        assert summary["hidden_old_count"] == 1
        assert summary["hidden_discarded_count"] == 1
        assert summary["non_recruitment_likely_count"] == 1

        outcome = apply_review_decisions(
            candidates,
            {
                "candidate-1": "keep",
                "candidate-2": "discard",
            },
        )
        kept_urls = {item["url"] for item in outcome["kept_jobs"]}
        assert "https://example.com/notice/1" in kept_urls
        assert "https://example.com/notice/5" in kept_urls
        assert "https://example.com/notice/2" not in kept_urls
        assert outcome["summary"]["manual_keep_count"] == 1
        assert outcome["summary"]["manual_discard_count"] == 1
        assert outcome["summary"]["manual_later_count"] == 1
        assert outcome["summary"]["final_detail_count"] == 2

        logger.info("✓ 审核队列候选分类正确")
        return True

    except Exception as e:
        logger.error(f"审核队列候选分类测试失败: {e}")
        return False


async def test_review_queue_marks_candidate_lists_as_non_recruitment():
    """测试拟录取/名单类标题在审核队列中标记为疑似非招聘"""
    logger.info("测试审核队列名单类标题识别...")

    try:
        from crawler.core.review_queue import ReviewQueueConfig, build_review_candidates

        now = datetime.now()
        jobs = [
            {
                "title": "重庆医院2026年公开招聘工作人员拟录取人员名单",
                "url": "https://example.com/notice/list",
                "hospital": "重庆医院",
                "source_site": "site_a",
                "publish_date": (now - timedelta(days=1)).isoformat(),
            },
            {
                "title": "重庆医院2026年公开招聘工作人员公告",
                "url": "https://example.com/notice/job",
                "hospital": "重庆医院",
                "source_site": "site_a",
                "publish_date": (now - timedelta(days=1)).isoformat(),
            },
        ]

        candidates = build_review_candidates(
            jobs,
            {},
            ReviewQueueConfig(recent_days=30),
            recruitment_keywords=["招聘", "引进", "护士", "医师"],
        )
        by_url = {item.url: item for item in candidates}

        assert by_url["https://example.com/notice/list"].non_recruitment_likely is True
        assert by_url["https://example.com/notice/job"].non_recruitment_likely is False
        logger.info("✓ 审核队列名单类标题识别正确")
        return True

    except Exception as e:
        logger.error(f"审核队列名单类标题识别测试失败: {e}")
        return False


async def test_review_decision_persistence():
    """测试审核决策的数据库持久化"""
    logger.info("测试审核决策持久化...")

    try:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review_queue.db"
            temp_db = DatabaseManager(db_path=str(db_path))
            await temp_db.init_async()

            saved = await temp_db.save_review_decisions(
                [
                    {
                        "url": "https://example.com/review/keep",
                        "decision": "keep",
                        "source_site": "site_a",
                        "title": "医院招聘公告",
                        "hospital": "测试医院",
                        "publish_date": "2026-03-27",
                    }
                ]
            )
            assert saved == 1

            decisions = await temp_db.get_review_decisions(["https://example.com/review/keep"])
            assert decisions["https://example.com/review/keep"].decision == "keep"
            assert decisions["https://example.com/review/keep"].hospital_snapshot == "测试医院"

            touched = await temp_db.touch_review_decisions(
                [
                    {
                        "url": "https://example.com/review/keep",
                        "title": "医院招聘公告（更新）",
                        "hospital": "测试医院",
                        "source_site": "site_b",
                        "publish_date": "2026-03-28",
                    }
                ]
            )
            assert touched == 1

            updated = await temp_db.get_review_decisions(["https://example.com/review/keep"])
            row = updated["https://example.com/review/keep"]
            assert row.title_snapshot == "医院招聘公告（更新）"
            assert row.source_site == "site_b"
            assert row.publish_date_snapshot.strftime("%Y-%m-%d") == "2026-03-28"

            if temp_db.async_engine is not None:
                await temp_db.async_engine.dispose()

        logger.info("✓ 审核决策持久化正确")
        return True

    except Exception as e:
        logger.error(f"审核决策持久化测试失败: {e}")
        return False


async def test_review_queue_pipeline_outputs():
    """测试审核队列主流程输出"""
    logger.info("测试审核队列主流程输出...")

    try:
        import core.review_queue as review_queue_module

        class FakeCrawler:
            def __init__(self, export_directory: str):
                self.export_directory = export_directory
                self.global_config = {
                    "review_queue": {
                        "enabled": True,
                        "recent_days": 30,
                        "auto_open_browser": False,
                        "hide_previously_discarded": True,
                        "hide_old_without_keep": True,
                    }
                }
                self.recruitment_keep_keywords = ["招聘", "引进", "护士", "医师"]

        class FakeServer:
            def __init__(self, state, html):
                self.state = state
                self.html = html

            async def start(self):
                return "http://127.0.0.1:9999"

            async def wait_for_submission(self):
                return {
                    "candidate-1": "keep",
                    "candidate-2": "discard",
                }

            async def close(self):
                return None

        with TemporaryDirectory() as tmpdir:
            temp_db = DatabaseManager(db_path=str(Path(tmpdir) / "review_queue_flow.db"))
            await temp_db.init_async()

            original_server = review_queue_module._ReviewQueueServer
            original_db_manager = review_queue_module.db_manager
            review_queue_module._ReviewQueueServer = FakeServer
            review_queue_module.db_manager = temp_db
            try:
                crawler = FakeCrawler(export_directory=tmpdir)
                jobs = [
                    {
                        "title": "第一人民医院招聘公告",
                        "url": "https://example.com/review-flow/1",
                        "hospital": "第一人民医院",
                        "source_site": "site_a",
                        "publish_date": datetime.now().strftime("%Y-%m-%d"),
                    },
                    {
                        "title": "第一人民医院笔试成绩公示",
                        "url": "https://example.com/review-flow/2",
                        "hospital": "第一人民医院",
                        "source_site": "site_a",
                        "publish_date": datetime.now().strftime("%Y-%m-%d"),
                    },
                ]

                kept_jobs = await review_queue_module.review_jobs_for_detail_processing(
                    crawler,
                    jobs,
                    logger_instance=logger,
                )
            finally:
                review_queue_module._ReviewQueueServer = original_server
                review_queue_module.db_manager = original_db_manager
                if temp_db.async_engine is not None:
                    await temp_db.async_engine.dispose()

            assert len(kept_jobs) == 1
            assert kept_jobs[0]["url"] == "https://example.com/review-flow/1"

            review_root = Path(tmpdir) / "detail_review"
            run_dirs = [path for path in review_root.iterdir() if path.is_dir()]
            assert len(run_dirs) == 1
            run_dir = run_dirs[0]
            assert (run_dir / "candidates.json").exists()
            assert (run_dir / "review_results.json").exists()
            assert (run_dir / "index.html").exists()

            review_results = json.loads((run_dir / "review_results.json").read_text(encoding="utf-8"))
            assert review_results["summary"]["manual_keep_count"] == 1
            assert review_results["summary"]["manual_discard_count"] == 1
            assert review_results["summary"]["final_detail_count"] == 1

        logger.info("✓ 审核队列主流程输出正确")
        return True

    except Exception as e:
        logger.error(f"审核队列主流程输出测试失败: {e}")
        return False


async def test_review_queue_html_embeds_valid_json():
    """测试审核页内嵌状态JSON可被前端直接解析"""
    logger.info("测试审核页内嵌JSON...")

    try:
        from crawler.core.review_queue import render_review_page

        state = {
            "summary": {"candidate_total": 1},
            "auto_kept": [],
            "reviewable": [
                {
                    "candidate_id": "candidate-1",
                    "index": 1,
                    "title": '包含 "引号" 与 <script> 字样的招聘公告',
                    "url": "https://example.com/review/1",
                    "hospital": "测试医院",
                    "source_site": "site_a",
                    "publish_date": None,
                    "publish_date_display": "未解析",
                    "recent": False,
                    "old": False,
                    "recruitment_likely": True,
                    "non_recruitment_likely": False,
                    "missing_publish_date": True,
                    "previously_kept": False,
                    "previously_discarded": False,
                    "default_hidden": False,
                    "hidden_reason": None,
                    "default_action": "later",
                    "sort_bucket": 2,
                    "auto_keep": False,
                    "tags": [],
                }
            ],
        }

        html = render_review_page(state)
        marker = '<script id="review-state" type="application/json">'
        start = html.index(marker) + len(marker)
        end = html.index("</script>", start)
        embedded = html[start:end]
        parsed = json.loads(embedded)

        assert parsed["reviewable"][0]["candidate_id"] == "candidate-1"
        assert parsed["reviewable"][0]["title"] == '包含 "引号" 与 <script> 字样的招聘公告'
        assert "&quot;" not in embedded

        logger.info("✓ 审核页内嵌JSON正确")
        return True

    except Exception as e:
        logger.error(f"审核页内嵌JSON测试失败: {e}")
        return False


async def test_attachment_backfill_outputs():
    """测试附件链接回填与清单生成"""
    logger.info("测试附件链接回填...")

    try:
        from crawler.core.wechat_attachment_backfill import backfill_review_dir

        with TemporaryDirectory() as tmpdir:
            review_dir = Path(tmpdir) / "wechat_review" / "item_001"
            attachments_dir = review_dir / "assets" / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)

            file_one = attachments_dir / "attachment_01_demo.xlsx"
            file_two = attachments_dir / "attachment_02_demo.docx"
            file_one.write_bytes(b"demo-1")
            file_two.write_bytes(b"demo-2")

            package = {
                "title": "测试公告",
                "source_url": "https://example.com/article",
                "content_html": (
                    '<article><section><h2>附件说明</h2><ul class="wechat-review-attachments">'
                    '<li><a href="https://example.com/original-1.xlsx">附件1：岗位表.xlsx</a></li>'
                    '<li><a href="https://example.com/original-2.docx">附件2：报名表.docx</a></li>'
                    "</ul></section></article>"
                ),
                "attachments": [
                    {
                        "title": "附件1：岗位表.xlsx",
                        "source_url": "https://example.com/original-1.xlsx",
                        "local_path": "assets/attachments/attachment_01_demo.xlsx",
                        "description": "岗位信息附件",
                    },
                    {
                        "title": "附件2：报名表.docx",
                        "source_url": "https://example.com/original-2.docx",
                        "local_path": "assets/attachments/attachment_02_demo.docx",
                        "description": "报名或信息填报附件",
                    },
                ],
            }
            (review_dir / "package.json").write_text(
                json.dumps(package, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (review_dir / "article.html").write_text(
                (
                    "<!DOCTYPE html><html><body>"
                    '<section class="wechat-review-section"><h2>附件说明</h2>'
                    '<ul class="wechat-review-attachments">'
                    '<li><a href="https://example.com/original-1.xlsx">附件1：岗位表.xlsx</a></li>'
                    '<li><a href="https://example.com/original-2.docx">附件2：报名表.docx</a></li>'
                    "</ul></section></body></html>"
                ),
                encoding="utf-8",
            )

            result = backfill_review_dir(
                review_dir,
                [
                    {
                        "title": "附件1：岗位表.xlsx",
                        "local_path": str(file_one.resolve()),
                        "upload_path": str(file_one.resolve()),
                        "upload_status": "uploaded",
                        "insert_link": "pages/file/dl?fid=1&sid=2",
                        "uploaded_at": "2026-03-27T15:00:00",
                        "remote_file_id": "file-1",
                        "remote_file_name": "附件1：岗位表.xlsx",
                        "mini_program_path": "pages/file/dl?fid=1&sid=2",
                        "quick_paste_html": (
                            '<a class="weapp_text_link" data-miniprogram-appid="wx55" '
                            'data-miniprogram-path="pages/file/dl?fid=1&amp;sid=2" '
                            'href="">附件1：岗位表.xlsx</a>'
                        ),
                    },
                    {
                        "title": "附件2：报名表.docx",
                        "local_path": str(file_two.resolve()),
                        "upload_path": str(file_two.resolve()),
                        "upload_status": "missing",
                        "upload_error": "not found",
                    },
                ],
            )

            updated_package = json.loads((review_dir / "package.json").read_text(encoding="utf-8"))
            updated_html = (review_dir / "article.html").read_text(encoding="utf-8")
            links_json = json.loads((review_dir / "attachment_links.json").read_text(encoding="utf-8"))
            links_md = (review_dir / "attachment_links.md").read_text(encoding="utf-8")

            assert updated_package["attachments"][0]["insert_link"] == "pages/file/dl?fid=1&sid=2"
            assert updated_package["attachments"][0]["original_source_url"] == "https://example.com/original-1.xlsx"
            assert updated_package["attachments"][1]["insert_link"] == ""
            assert updated_package["attachments"][1]["upload_error"] == "not found"
            assert 'class="weapp_text_link"' in updated_package["content_html"]
            assert 'data-miniprogram-path="pages/file/dl?fid=1&amp;sid=2"' in updated_package["content_html"]
            assert 'class="weapp_text_link"' in updated_html
            assert 'data-miniprogram-path="pages/file/dl?fid=1&amp;sid=2"' in updated_html
            assert 'href="https://example.com/original-2.docx"' in updated_html
            assert links_json["success_count"] == 1
            assert result["failed_count"] == 1
            assert "附件1：岗位表.xlsx" in links_md
            assert "### 附件2：报名表.docx" in links_md
            assert "- 错误：not found" in links_md
            assert "直达路径 HTML" in links_md
            assert 'data-miniprogram-path="pages/file/dl?fid=1&amp;sid=2"' in links_md

        logger.info("✓ 附件链接回填成功")
        return True

    except Exception as e:
        logger.error(f"附件链接回填测试失败: {e}")
        return False


async def test_attachment_clipboard_text():
    """测试附件剪贴板文本生成"""
    logger.info("测试附件剪贴板文本...")

    try:
        from crawler.core.wechat_attachment_backfill import build_attachment_clipboard_text

        single = build_attachment_clipboard_text(
            [
                {
                    "title": "附件1",
                    "insert_link": "https://bohao.wsqytec.com/link.html?type=f&id=single",
                }
            ]
        )
        assert single == "https://bohao.wsqytec.com/link.html?type=f&id=single"

        multiple = build_attachment_clipboard_text(
            [
                {
                    "title": "附件1",
                    "insert_link": "https://bohao.wsqytec.com/link.html?type=f&id=1",
                    "quick_paste_html": "<a>附件1</a>",
                },
                {
                    "title": "附件2",
                    "insert_link": "",
                    "original_source_url": "https://example.com/original-2.docx",
                    "quick_paste_html": "<a>附件2</a>",
                },
            ]
        )
        assert "<a>附件1</a>" in multiple
        assert "<a>附件2</a>" in multiple

        logger.info("✓ 附件剪贴板文本生成正确")
        return True

    except Exception as e:
        logger.error(f"附件剪贴板文本测试失败: {e}")
        return False


async def test_xzf_attachment_uploader_helpers():
    """测试小正方上传器的检索与等待逻辑"""
    logger.info("测试小正方上传器辅助逻辑...")

    try:
        from crawler.core.xzf_attachment_uploader import (
            LocalAttachment,
            XzfAttachmentUploader,
            XzfRemoteFile,
        )

        class FakeLocator:
            def __init__(self, visible: bool):
                self.visible = visible

            def is_visible(self):
                return self.visible

            def click(self):
                return None

        class FakePage:
            def __init__(self, payloads: dict[str, dict], logged_in: bool = True):
                self.payloads = payloads
                self.logged_in = logged_in
                self.searches = []

            def locator(self, selector):
                if selector == "#logoutDiv":
                    return FakeLocator(self.logged_in)
                return FakeLocator(True)

            def evaluate(self, script, data):
                self.searches.append(data["searchKey"])
                return self.payloads.get(data["searchKey"], {"data": []})

            def goto(self, url, wait_until=None):
                return None

        class FakeSession:
            def __init__(self, page):
                self.page = page

            def __enter__(self):
                return self.page

            def __exit__(self, exc_type, exc, tb):
                return False

        with TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            local_path = temp_root / "assets" / "attachments" / "attachment_ugly.docx"
            upload_path = temp_root / "attachment_upload_staging" / "附件2_报名表.docx"
            local_path.parent.mkdir(parents=True, exist_ok=True)
            upload_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(b"demo")
            upload_path.write_bytes(b"demo")

            attachment = LocalAttachment(
                title="附件2：报名表.docx",
                local_path=local_path,
                upload_path=upload_path,
                source_url="https://example.com/original.docx",
            )
            payloads = {
                "附件2_报名表.docx": {
                    "data": [
                        {
                            "_id": "abc123",
                            "name": "附件2_报名表.docx",
                            "createTime": "2026-03-27 15:00:00",
                        }
                    ]
                }
            }
            page = FakePage(payloads)
            uploader = XzfAttachmentUploader(
                profile_dir=temp_root / "profile",
                lookup_timeout_seconds=1,
                poll_interval_seconds=0,
            )

            assert uploader._is_logged_in(page) is True
            remote = uploader._find_remote_file(page, attachment)
            assert remote is not None
            assert remote.insert_link.endswith("abc123")
            assert remote.mini_program_path == "/file/down?id=abc123"
            assert page.searches[0] == "附件2_报名表.docx"

            waiting_uploader = XzfAttachmentUploader(
                profile_dir=temp_root / "profile_wait",
                lookup_timeout_seconds=1,
                poll_interval_seconds=0,
            )
            wait_page = FakePage({})
            state = {"count": 0}
            remote_file = XzfRemoteFile(
                file_id="wait123",
                name="附件2_报名表.docx",
                create_time="2026-03-27 15:05:00",
                insert_link="https://bohao.wsqytec.com/link.html?type=f&id=wait123",
                mini_program_path="/file/down?id=wait123",
                quick_paste_html="<a>附件2</a>",
                auto_reply_html="<a>附件2</a>",
                raw={},
            )

            def fake_find_remote_file(_page, _attachment):
                state["count"] += 1
                return remote_file if state["count"] >= 2 else None

            waiting_uploader._browser_session = lambda: FakeSession(wait_page)  # type: ignore[method-assign]
            waiting_uploader._ensure_logged_in = lambda page, prompt_fn=None: None  # type: ignore[method-assign]
            waiting_uploader._assist_manual_upload = lambda attachments, prompt_fn=None: None  # type: ignore[method-assign]
            waiting_uploader._find_remote_file = fake_find_remote_file  # type: ignore[method-assign]
            waiting_uploader._sleep = lambda seconds: None  # type: ignore[method-assign]

            results = waiting_uploader.upload_attachments(
                [attachment],
                allow_manual_upload=True,
                prompt_fn=None,
            )
            assert results[0].upload_status == "uploaded"
            assert results[0].insert_link.endswith("wait123")

        logger.info("✓ 小正方上传器辅助逻辑正确")
        return True

    except Exception as e:
        logger.error(f"小正方上传器辅助逻辑测试失败: {e}")
        return False


async def test_upload_wechat_attachments_parse_args():
    """测试附件上传 CLI 参数解析"""
    logger.info("测试附件上传 CLI 参数解析...")

    try:
        from upload_wechat_attachments import parse_args

        args = parse_args(
            [
                "--review-dir",
                "/tmp/demo",
                "--service",
                "idocx",
                "--profile-dir",
                "data/browser_profiles/idocx",
                "--headless",
                "--skip-manual-upload",
                "--no-clipboard",
            ]
        )

        assert args.review_dir == "/tmp/demo"
        assert args.service == "idocx"
        assert args.profile_dir == "data/browser_profiles/idocx"
        assert args.headless is True
        assert args.skip_manual_upload is True
        assert args.no_clipboard is True
        logger.info("✓ 附件上传 CLI 参数解析正确")
        return True

    except Exception as e:
        logger.error(f"附件上传 CLI 参数解析测试失败: {e}")
        return False


async def test_review_package_draft_adapter():
    """测试 review package 到官方草稿文章行的适配"""
    logger.info("测试 review package 草稿适配...")

    try:
        from PIL import Image
        from wechat_service.utils.crawler_review_package_draft import build_article_row_from_review_package

        with TemporaryDirectory() as tmpdir:
            review_dir = Path(tmpdir) / "wechat_review" / "item_001"
            inline_dir = review_dir / "assets" / "inline"
            render_dir = review_dir / "assets" / "renders"
            inline_dir.mkdir(parents=True, exist_ok=True)
            render_dir.mkdir(parents=True, exist_ok=True)

            cover_path = inline_dir / "cover.png"
            render_path = render_dir / "岗位表.png"
            Image.new("RGB", (320, 180), "white").save(cover_path)
            Image.new("RGB", (480, 640), "white").save(render_path)

            package = {
                "title": "测试草稿适配公告",
                "source_url": "https://example.com/source",
                "hospital": "测试医院",
                "plain_text": "这里是正文摘要",
                "content_html": '<article><p>正文</p><img src="assets/inline/cover.png" /></article>',
                "images": [
                    {
                        "src": "assets/inline/cover.png",
                        "category": "content_image",
                        "keep": True,
                    }
                ],
                "attachments": [
                    {
                        "title": "附件1：岗位表.xlsx",
                        "category": "position_table",
                        "rendered_images": ["assets/renders/岗位表.png"],
                        "insert_link": "pages/file/dl?fid=1&sid=2",
                    }
                ],
            }
            (review_dir / "package.json").write_text(
                json.dumps(package, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            row = build_article_row_from_review_package(review_dir)
            assert row["title"] == "测试草稿适配公告"
            assert row["link"] == "https://example.com/source"
            assert row["cover"] == str(cover_path.resolve())
            assert "..." not in row["digest"]
            assert str(cover_path.resolve()) in row["content"]

        logger.info("✓ review package 草稿适配正确")
        return True

    except Exception as e:
        logger.error(f"review package 草稿适配测试失败: {e}")
        return False


async def test_review_package_draft_adapter_auto_renders_position_table():
    """测试旧 review package 会在推送前自动补渲染岗位表并插入正文"""
    logger.info("测试 review package 自动补渲染岗位表...")

    try:
        from PIL import Image
        from openpyxl import Workbook
        from wechat_service.utils.crawler_review_package_draft import build_article_row_from_review_package

        with TemporaryDirectory() as tmpdir:
            review_dir = Path(tmpdir) / "wechat_review" / "item_003"
            attachments_dir = review_dir / "assets" / "attachments"
            inline_dir = review_dir / "assets" / "inline"
            attachments_dir.mkdir(parents=True, exist_ok=True)
            inline_dir.mkdir(parents=True, exist_ok=True)

            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "岗位表"
            sheet.append(["岗位", "人数", "学历"])
            sheet.append(["护士", "10", "本科"])
            sheet.append(["医师", "5", "硕士"])
            attachment_path = attachments_dir / "attachment_jobs.xlsx"
            workbook.save(attachment_path)

            cover_path = inline_dir / "cover.png"
            Image.new("RGB", (320, 180), "white").save(cover_path)

            package = {
                "title": "测试旧审核包岗位表补渲染",
                "source_url": "https://example.com/source",
                "hospital": "测试医院",
                "article_type": "text_only",
                "plain_text": "这里是正文摘要",
                "content_html": (
                    '<article><p>正文</p><section><h2>附件说明</h2><ul>'
                    '<li><a href="pages/file/dl?fid=1&sid=2">附件1：岗位表.xlsx</a></li>'
                    "</ul></section></article>"
                ),
                "images": [
                    {
                        "src": "assets/inline/cover.png",
                        "category": "content_image",
                        "keep": True,
                    }
                ],
                "attachments": [
                    {
                        "title": "附件1：岗位表.xlsx",
                        "category": "position_table",
                        "local_path": "assets/attachments/attachment_jobs.xlsx",
                        "render_status": "missing_dependency",
                        "rendered_images": [],
                        "insert_link": "pages/file/dl?fid=1&sid=2",
                    }
                ],
            }
            (review_dir / "package.json").write_text(
                json.dumps(package, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            row = build_article_row_from_review_package(review_dir)
            updated_package = json.loads((review_dir / "package.json").read_text(encoding="utf-8"))

            assert updated_package["article_type"] == "attachment_driven"
            assert updated_package["attachments"][0]["render_status"] == "rendered"
            assert updated_package["attachments"][0]["rendered_images"]
            assert "assets/renders/" in updated_package["content_html"]
            assert str(review_dir / updated_package["attachments"][0]["rendered_images"][0]) in row["content"]

        logger.info("✓ review package 自动补渲染岗位表成功")
        return True

    except Exception as e:
        logger.error(f"review package 自动补渲染岗位表测试失败: {e}")
        return False


async def test_review_package_draft_adapter_generates_default_cover():
    """测试纯文字 review package 会自动生成默认封面"""
    logger.info("测试 review package 默认封面生成...")

    try:
        from wechat_service.utils.crawler_review_package_draft import build_article_row_from_review_package

        with TemporaryDirectory() as tmpdir:
            review_dir = Path(tmpdir) / "wechat_review" / "item_002"
            review_dir.mkdir(parents=True, exist_ok=True)

            package = {
                "title": "测试纯文字公告",
                "source_url": "https://example.com/text-only",
                "hospital": "测试医院",
                "plain_text": "这里只有正文，没有图片。",
                "content_html": "<article><p>这里只有正文，没有图片。</p></article>",
                "images": [],
                "attachments": [],
            }
            (review_dir / "package.json").write_text(
                json.dumps(package, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            row = build_article_row_from_review_package(review_dir)
            cover_path = Path(row["cover"])
            assert cover_path.exists()
            assert cover_path.suffix.lower() == ".png"
            assert "assets/generated/default_cover.png" in row["cover"].replace("\\", "/")

        logger.info("✓ review package 默认封面生成正确")
        return True

    except Exception as e:
        logger.error(f"review package 默认封面生成测试失败: {e}")
        return False


async def test_wechat_review_builder_removes_meta_noise():
    """测试公众号待审包会移除发布时间、发布人、浏览量等元信息"""
    logger.info("测试公众号待审包正文元信息清洗...")

    try:
        from crawler.core.wechat_review_builder import WechatReviewBuilder

        with TemporaryDirectory() as tmpdir:
            article_dir = Path(tmpdir) / "details" / "item_003"
            article_dir.mkdir(parents=True, exist_ok=True)
            builder = WechatReviewBuilder(output_root=str(Path(tmpdir) / "wechat_review"))

            result = builder.build_package(
                job={
                    "url": "https://example.com/detail/meta",
                    "hospital": "测试医院",
                },
                title="测试医院招聘公告",
                localized_html="""
                <html>
                    <body>
                        <div class="article-content">
                            <h1>测试医院招聘公告</h1>
                            <div>日期：2026-03-28 来源：测试医院 发布人：管理员 浏览：123次</div>
                            <p>发布时间：2026-03-28 09:00</p>
                            <p>发布人：党委组织部</p>
                            <p>点击量：456</p>
                            <p>这里是正文第一段。</p>
                            <p>这里是正文第二段。</p>
                        </div>
                    </body>
                </html>
                """,
                manifest={"attachments": []},
                run_time=datetime(2026, 3, 28, 12, 0, 0),
                article_dir=article_dir,
            )

            package = json.loads(Path(result["package_path"]).read_text(encoding="utf-8"))

            assert "这里是正文第一段。" in package["plain_text"]
            assert "这里是正文第二段。" in package["plain_text"]
            assert "日期：" not in package["plain_text"]
            assert "发布时间：" not in package["plain_text"]
            assert "发布人：" not in package["plain_text"]
            assert "点击量：" not in package["plain_text"]
            assert "浏览：" not in package["plain_text"]

        logger.info("✓ 公众号待审包正文元信息清洗正确")
        return True

    except Exception as e:
        logger.error(f"公众号待审包正文元信息清洗测试失败: {e}")
        return False


async def test_default_cover_display_text_selection():
    """测试默认封面只显示医院名，过长时退化为招聘公告"""
    logger.info("测试默认封面文案选择...")

    try:
        from wechat_service.utils.crawler_review_package_draft import _build_cover_display_text

        assert _build_cover_display_text("测试医院") == "测试医院"
        assert (
            _build_cover_display_text("重庆大学附属某某医院国家区域医疗中心联合招聘公告专用测试机构")
            == "招聘公告"
        )

        logger.info("✓ 默认封面文案选择正确")
        return True

    except Exception as e:
        logger.error(f"默认封面文案选择测试失败: {e}")
        return False


async def test_push_wechat_review_to_official_draft_parse_args():
    """测试官方草稿推送 CLI 参数解析"""
    logger.info("测试官方草稿推送 CLI 参数解析...")

    try:
        from push_wechat_review_to_official_draft import parse_args

        args = parse_args(
            [
                "--review-dir",
                "/tmp/review-demo",
                "--draft-media-id",
                "MEDIA_ID_123",
            ]
        )

        assert args.review_dir == "/tmp/review-demo"
        assert args.draft_media_id == "MEDIA_ID_123"
        logger.info("✓ 官方草稿推送 CLI 参数解析正确")
        return True

    except Exception as e:
        logger.error(f"官方草稿推送 CLI 参数解析测试失败: {e}")
        return False


async def run_all_tests():
    """运行所有测试"""
    logger.info("开始运行所有测试...")
    logger.info("=" * 50)

    results = []

    # 运行测试
    results.append(await test_database())
    results.append(await test_config_loading())
    results.append(await test_recruitment_filter())
    results.append(await test_incremental_save_modes())
    results.append(await test_export_outputs())
    results.append(await test_site_concurrency())
    results.append(await test_parsing())
    results.append(await test_onclick_link_parsing())
    results.append(await test_anchor_item_self_matching())
    results.append(await test_duplicate_body_container_fallback())
    results.append(await test_ruifox_fallback())
    results.append(await test_waf_slider_detection())
    results.append(await test_date_parsing())
    results.append(await test_detail_pipeline_article_export())
    results.append(await test_detail_pipeline_readability_fallback())
    results.append(await test_xlsx_attachment_rendering())
    results.append(await test_attachment_table_long_text_column_gets_more_width())
    results.append(await test_detail_pipeline_qr_article_review())
    results.append(await test_detail_pipeline_wechat_not_ready())
    results.append(await test_list_page_validation_diagnostics())
    results.append(await test_parse_arguments_with_details())
    results.append(await test_selector_suggestion_matches_existing_row())
    results.append(await test_selector_suggestion_inferrs_static_selectors())
    results.append(await test_selector_suggestion_csv_append_and_script_args())
    results.append(await test_review_queue_candidate_classification())
    results.append(await test_review_decision_persistence())
    results.append(await test_review_queue_pipeline_outputs())
    results.append(await test_review_queue_html_embeds_valid_json())
    results.append(await test_attachment_backfill_outputs())
    results.append(await test_attachment_clipboard_text())
    results.append(await test_xzf_attachment_uploader_helpers())
    results.append(await test_upload_wechat_attachments_parse_args())
    results.append(await test_review_package_draft_adapter())
    results.append(await test_review_package_draft_adapter_auto_renders_position_table())
    results.append(await test_review_package_draft_adapter_generates_default_cover())
    results.append(await test_default_cover_display_text_selection())
    results.append(await test_push_wechat_review_to_official_draft_parse_args())

    # 统计结果
    logger.info("=" * 50)
    total = len(results)
    passed = sum(results)
    failed = total - passed

    logger.info(f"测试完成: 总计 {total}, 通过 {passed}, 失败 {failed}")

    if failed == 0:
        logger.info("🎉 所有测试通过！")
        return True
    else:
        logger.error("❌ 有测试失败，请检查问题")
        return False


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
