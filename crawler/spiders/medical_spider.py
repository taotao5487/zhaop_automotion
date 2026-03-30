import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin
import aiohttp

from crawler.spiders.base_spider import BaseSpider

logger = logging.getLogger(__name__)


class MedicalSpider(BaseSpider):
    """医疗招聘爬虫"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.crawling_config = config.get('crawling', {})

    async def crawl(self) -> List[Dict[str, Any]]:
        """执行爬取"""
        all_jobs = []

        try:
            # 处理起始URL
            start_urls = self.crawling_config.get('start_urls', [])
            if not start_urls:
                start_urls = [self.base_url]

            for start_url in start_urls:
                # 处理分页
                if self.crawling_config.get('pagination', {}).get('enabled', False):
                    jobs = await self._crawl_with_pagination(start_url)
                else:
                    jobs = await self._crawl_single_page(start_url)

                all_jobs.extend(jobs)

            # 去重
            unique_jobs = self._deduplicate_jobs(all_jobs)
            logger.info(f"爬取完成: {self.site_name}, 总职位: {len(all_jobs)}, 去重后: {len(unique_jobs)}")

            return unique_jobs

        except Exception as e:
            logger.error(f"爬取失败 {self.site_name}: {e}")
            return []

    async def _crawl_single_page(self, url: str) -> List[Dict[str, Any]]:
        """爬取单页"""
        logger.info(f"爬取单页: {url}")

        html = await self.fetch(url)
        if not html:
            return []

        jobs = self.parse_list_page(html)

        # 如果需要爬取详情页
        if self.crawling_config.get('follow_links', False):
            jobs_with_details = []
            for job in jobs:
                if 'url' in job and job['url']:
                    detail_data = await self.crawl_detail_page(job['url'])
                    if detail_data:
                        job.update(detail_data)
                jobs_with_details.append(job)
            return jobs_with_details

        return jobs

    async def _crawl_with_pagination(self, start_url: str) -> List[Dict[str, Any]]:
        """爬取分页"""
        all_jobs = []
        pagination_config = self.crawling_config.get('pagination', {})

        page_type = pagination_config.get('type', 'url_param')
        pattern = pagination_config.get('pattern', '')
        start_page = pagination_config.get('start_page', 1)
        max_pages = pagination_config.get('max_pages', 10)
        page_increment = pagination_config.get('page_increment', 1)

        current_page = start_page
        page_count = 0

        while current_page <= max_pages and page_count < max_pages:
            try:
                # 生成分页URL
                page_url = self._generate_page_url(start_url, current_page, page_type, pattern)
                logger.info(f"爬取分页: {page_url} (第{current_page}页)")

                html = await self.fetch(page_url)
                if not html:
                    logger.warning(f"分页 {current_page} 获取失败，停止分页")
                    break

                jobs = self.parse_list_page(html)
                if not jobs:
                    logger.info(f"分页 {current_page} 没有数据，停止分页")
                    break

                # 爬取详情页
                if self.crawling_config.get('follow_links', False):
                    jobs_with_details = []
                    for job in jobs:
                        if 'url' in job and job['url']:
                            detail_data = await self.crawl_detail_page(job['url'])
                            if detail_data:
                                job.update(detail_data)
                        jobs_with_details.append(job)
                    jobs = jobs_with_details

                all_jobs.extend(jobs)
                page_count += 1

                # 检查是否还有更多页
                if len(jobs) < 10:  # 如果一页少于10个职位，可能没有更多页了
                    logger.info(f"分页 {current_page} 职位较少，停止分页")
                    break

                # 延迟
                await asyncio.sleep(self.crawling_config.get('request_interval', 2.0))

                # 下一页
                current_page += page_increment

            except Exception as e:
                logger.error(f"爬取分页 {current_page} 失败: {e}")
                break

        logger.info(f"分页爬取完成: {page_count} 页, {len(all_jobs)} 个职位")
        return all_jobs

    def _generate_page_url(self,
                          base_url: str,
                          page: int,
                          page_type: str,
                          pattern: str) -> str:
        """生成分页URL"""
        if page_type == 'url_param':
            if '?' in base_url:
                return f"{base_url}&{pattern.replace('{page}', str(page))}"
            else:
                return f"{base_url}?{pattern.replace('{page}', str(page))}"
        elif page_type == 'path':
            return urljoin(base_url, pattern.replace('{page}', str(page)))
        else:
            # 默认不修改URL
            return base_url

    def _deduplicate_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """职位去重"""
        unique_jobs = []
        seen_urls = set()

        for job in jobs:
            if 'url' in job and job['url']:
                if job['url'] not in seen_urls:
                    seen_urls.add(job['url'])
                    unique_jobs.append(job)
            else:
                # 如果没有URL，使用title和publish_date组合去重
                key = f"{job.get('title', '')}_{job.get('publish_date', '')}"
                if key not in seen_urls:
                    seen_urls.add(key)
                    unique_jobs.append(job)

        return unique_jobs

    def post_process_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """后处理职位数据"""
        processed_jobs = []
        post_config = self.config.get('post_processing', {})

        for job in jobs:
            processed_job = job.copy()

            # 字段映射
            field_mappings = post_config.get('field_mappings', {})
            for old_field, new_field in field_mappings.items():
                if old_field in processed_job:
                    processed_job[new_field] = processed_job.pop(old_field)

            # 字段默认值
            field_defaults = post_config.get('field_defaults', {})
            for field, default_value in field_defaults.items():
                if field not in processed_job or not processed_job[field]:
                    if default_value == '{site_name}':
                        processed_job[field] = self.site_name
                    elif default_value == 'now':
                        processed_job[field] = datetime.now()
                    else:
                        processed_job[field] = default_value

            # 应用转换器
            transformers = post_config.get('transformers', [])
            for transformer in transformers:
                field = transformer.get('field')
                function = transformer.get('function')
                args = transformer.get('args', {})

                if field in processed_job and processed_job[field]:
                    try:
                        if function == 'strip':
                            processed_job[field] = str(processed_job[field]).strip()
                        elif function == 'format_date':
                            date_format = args.get('format', '%Y-%m-%d')
                            if hasattr(processed_job[field], 'strftime'):
                                processed_job[field] = processed_job[field].strftime(date_format)
                        elif function == 'normalize_url':
                            processed_job[field] = self._normalize_url(processed_job[field])
                    except Exception as e:
                        logger.error(f"应用转换器失败 {field}.{function}: {e}")

            processed_jobs.append(processed_job)

        # 应用过滤器
        filters = post_config.get('filters', [])
        filtered_jobs = []
        for job in processed_jobs:
            if self._apply_filters(job, filters):
                filtered_jobs.append(job)

        return filtered_jobs

    def _apply_filters(self, job: Dict[str, Any], filters: List[Dict[str, Any]]) -> bool:
        """应用过滤器"""
        for filter_config in filters:
            field = filter_config.get('field')
            operator = filter_config.get('operator')
            value = filter_config.get('value')

            if field not in job:
                continue

            job_value = job[field]

            if operator == '>=':
                if isinstance(value, str) and value.endswith(' days ago'):
                    days = int(value.split(' ')[0])
                    cutoff_date = datetime.now() - timedelta(days=days)
                    if isinstance(job_value, str):
                        try:
                            job_date = self._parse_date(job_value)
                            if job_date and job_date < cutoff_date:
                                return False
                        except Exception:
                            pass
            elif operator == 'not_contains':
                if isinstance(value, list) and isinstance(job_value, str):
                    for keyword in value:
                        if keyword in job_value:
                            return False

        return True


# 爬虫工厂
class SpiderFactory:
    """爬虫工厂"""

    @staticmethod
    def create_spider(config: Dict[str, Any]) -> MedicalSpider:
        """创建爬虫实例"""
        return MedicalSpider(config)

    @staticmethod
    async def create_and_crawl(config: Dict[str, Any],
                              session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
        """创建爬虫并执行爬取"""
        spider = SpiderFactory.create_spider(config)
        await spider.init_session(session)
        return await spider.crawl()