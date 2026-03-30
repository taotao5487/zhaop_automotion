import asyncio
import logging
import re
from datetime import datetime, date
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import aiohttp
import soupsieve

logger = logging.getLogger(__name__)


class BaseSpider:
    """基础爬虫类"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.site_name = config.get('site_name', '')
        self.base_url = config.get('base_url', '')
        self.session: Optional[aiohttp.ClientSession] = None

    async def init_session(self, session: aiohttp.ClientSession):
        """初始化会话"""
        self.session = session

    async def fetch(self, url: str, **kwargs) -> Optional[str]:
        """获取页面内容"""
        if not self.session:
            raise RuntimeError("Session not initialized")

        try:
            async with self.session.get(url, **kwargs) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    logger.warning(f"获取页面失败: {url}, 状态码: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"获取页面异常 {url}: {e}")
            return None

    async def crawl(self) -> List[Dict[str, Any]]:
        """执行爬取，返回职位数据列表"""
        raise NotImplementedError("子类必须实现crawl方法")

    def parse_list_page(self, html: str) -> List[Dict[str, Any]]:
        """解析列表页"""
        try:
            parsing_config = self.config.get('parsing', {}).get('list_page', {})

            container_selector = parsing_config.get('container_selector')
            item_selector = parsing_config.get('item_selector')

            if not container_selector or not item_selector:
                logger.warning(f"网站 {self.site_name} 未配置列表页解析规则")
                return []

            parsers = ['lxml']
            if self._looks_like_multi_body_html(html):
                parsers.append('html.parser')

            for index, parser in enumerate(parsers):
                soup = BeautifulSoup(html, parser)
                containers = soup.select(container_selector)
                if not containers:
                    if index == len(parsers) - 1:
                        logger.warning(f"未找到列表容器: {container_selector}")
                    continue

                job_elements = self._collect_job_elements(soup, containers, item_selector)
                if not job_elements and index < len(parsers) - 1:
                    continue

                logger.info(f"解析到 {len(job_elements)} 个职位项")

                jobs = []
                for element in job_elements:
                    job_data = self._extract_job_from_element(element, parsing_config)
                    if job_data and self._validate_job_data(job_data):
                        jobs.append(job_data)

                return jobs

            return []

        except Exception as e:
            logger.error(f"解析列表页失败: {e}")
            return []

    @staticmethod
    def _looks_like_multi_body_html(html: str) -> bool:
        """识别被前端/模板拼接后的双 body 畸形文档"""
        normalized = html.lower()
        return normalized.count('<body') > 1 or '</html>' in normalized and normalized.rfind('</html>') < normalized.rfind('<body')

    @staticmethod
    def _collect_job_elements(soup: BeautifulSoup, containers: List[Any], item_selector: str) -> List[Any]:
        """支持多容器页面，并在容器失效时回退到整页查找"""
        job_elements: List[Any] = []
        seen_ids = set()

        for container in containers:
            for element in container.select(item_selector):
                element_id = id(element)
                if element_id in seen_ids:
                    continue
                seen_ids.add(element_id)
                job_elements.append(element)

        if job_elements:
            return job_elements

        for element in soup.select(item_selector):
            element_id = id(element)
            if element_id in seen_ids:
                continue
            seen_ids.add(element_id)
            job_elements.append(element)

        return job_elements

    def _extract_job_from_element(self,
                                element: Any,
                                parsing_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从HTML元素提取职位数据"""
        try:
            job_data = {}
            fields_config = parsing_config.get('fields', {})

            for field_name, field_config in fields_config.items():
                value = self._extract_field(element, field_config)
                if value is not None or not field_config.get('required', False):
                    job_data[field_name] = value
                else:
                    logger.warning(f"字段 {field_name} 提取失败，但为必需字段")
                    return None

            # 添加基础信息
            job_data['source_site'] = self.site_name
            job_data['crawl_time'] = datetime.now()

            # 处理URL
            if 'url' in job_data and job_data['url']:
                job_data['url'] = self._make_absolute_url(job_data['url'])

            return job_data

        except Exception as e:
            logger.error(f"提取职位数据失败: {e}")
            return None

    def _extract_field(self,
                      element: Any,
                      field_config: Dict[str, Any]) -> Any:
        """提取单个字段"""
        try:
            selector = field_config.get('selector')
            if not selector:
                return field_config.get('default')

            field_element = self._select_field_element(element, selector)
            if not field_element:
                # 如果是必需字段且没有找到元素，则返回None
                if field_config.get('required', False):
                    field_name = field_config.get('field_name', 'unknown')
                    logger.warning(f"字段 {field_name} 提取失败: 选择器 '{selector}' 未匹配到任何元素")
                    return None
                return field_config.get('default')

            field_type = field_config.get('type', 'text')
            transform = field_config.get('transform')

            # 提取值
            if field_type == 'text':
                value = field_element.get_text(strip=True)
            elif field_type == 'attr':
                attr_name = field_config.get('attr')
                if not attr_name:
                    logger.warning(f"属性字段未指定attr: {field_config}")
                    return None
                value = field_element.get(attr_name)

                # 某些医院站点把真实跳转写在 onclick 里，href 只放 "#"。
                if attr_name == 'href' and self._is_placeholder_link(value):
                    value = self._extract_url_from_onclick(field_element.get('onclick'))

                # 对于URL字段，如果获取到的值是None或空，需要特别处理
                if field_config.get('required', False) and not value:
                    field_name = field_config.get('field_name', 'unknown')
                    logger.warning(f"字段 {field_name} 提取失败: 选择器 '{field_config['selector']}' 找到了元素但属性值为空")
                    return None
            elif field_type == 'html':
                value = str(field_element)
            else:
                value = field_element.get_text(strip=True)

            # 应用转换
            if value and transform:
                value = self._apply_transform(value, transform, field_config)

            return value

        except Exception as e:
            logger.error(f"提取字段失败: {field_config}, 错误: {e}")
            return field_config.get('default')

    @staticmethod
    def _select_field_element(element: Any, selector: str) -> Any:
        """优先查找后代节点，必要时回退到当前节点自身"""
        field_element = element.select_one(selector)
        if field_element:
            return field_element

        try:
            if soupsieve.match(selector, element):
                return element
        except Exception:
            return None

        return None

    @staticmethod
    def _is_placeholder_link(value: Optional[str]) -> bool:
        """判断href是否只是占位符"""
        if value is None:
            return True
        normalized = value.strip().lower()
        return normalized in {'', '#', 'javascript:;', 'javascript:void(0);', 'javascript:void(0)'}

    def _extract_url_from_onclick(self, onclick: Optional[str]) -> Optional[str]:
        """从常见的 onclick 跳转函数中恢复真实URL"""
        if not onclick:
            return None

        match = re.search(r'([A-Za-z_]\w*)\((.*)\)\s*;?\s*$', onclick.strip())
        if not match:
            return None

        func_name, raw_args = match.groups()
        args = [self._clean_js_arg(arg) for arg in self._split_js_args(raw_args)]

        if func_name == 'turnInfoArt' and args:
            page = args[1] if len(args) > 1 and args[1] else '1'
            return f"pageInfoArt.html?artId={args[0]}&page={page}"
        if func_name == 'turnInfo' and args:
            return f"pageInfo.html?artId={args[0]}"
        if func_name == 'turnList' and args:
            page = args[1] if len(args) > 1 and args[1] else '1'
            return f"pageList.html?mId={args[0]}&page={page}"
        if func_name == 'turnzjInfo' and args:
            return f"pageZhuanJiaInfo.html?zjid={args[0]}"
        if func_name == 'turnKSInfo' and args:
            return f"pageKeShiInfo.html?deptId={args[0]}"
        if func_name == 'turnKESHI':
            if args and args[0]:
                return f"pageKeShi.html?sx={args[0]}"
            return "pageKeShi.html"
        if func_name == 'turnUrl' and args:
            return args[0]

        return None

    @staticmethod
    def _split_js_args(raw_args: str) -> List[str]:
        """按JS函数参数拆分，尽量忽略引号内的逗号"""
        args: List[str] = []
        current: List[str] = []
        quote: Optional[str] = None
        depth = 0

        for ch in raw_args:
            if quote:
                current.append(ch)
                if ch == quote:
                    quote = None
                continue

            if ch in {"'", '"'}:
                quote = ch
                current.append(ch)
                continue

            if ch == '(':
                depth += 1
                current.append(ch)
                continue

            if ch == ')' and depth > 0:
                depth -= 1
                current.append(ch)
                continue

            if ch == ',' and depth == 0:
                args.append(''.join(current).strip())
                current = []
                continue

            current.append(ch)

        if current:
            args.append(''.join(current).strip())

        return args

    @staticmethod
    def _clean_js_arg(arg: str) -> str:
        """去掉JS参数外层引号"""
        cleaned = arg.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
            return cleaned[1:-1]
        return cleaned

    def _apply_transform(self,
                        value: str,
                        transform: str,
                        field_config: Dict[str, Any]) -> Any:
        """应用转换函数"""
        try:
            if transform == 'strip':
                return value.strip()
            elif transform == 'lowercase':
                return value.lower()
            elif transform == 'uppercase':
                return value.upper()
            elif transform == 'absolute_url':
                return self._make_absolute_url(value)
            elif transform == 'parse_date':
                return self._parse_date(value, field_config.get('date_format'))
            elif transform == 'format_date':
                date_format = field_config.get('args', {}).get('format', '%Y-%m-%d')
                if isinstance(value, (datetime, date)):
                    return value.strftime(date_format)
                return value
            elif transform == 'extract_salary':
                return self._extract_salary(value)
            elif transform == 'clean_html':
                return self._clean_html(value)
            elif transform == 'normalize_url':
                return self._normalize_url(value)
            else:
                return value
        except Exception as e:
            logger.error(f"应用转换失败: {transform}, 值: {value}, 错误: {e}")
            return value

    def _make_absolute_url(self, url: str) -> str:
        """转换为绝对URL"""
        if not url:
            return url

        if url.startswith(('http://', 'https://')):
            return url
        elif url.startswith('//'):
            return f"https:{url}"
        else:
            return urljoin(self.base_url, url)

    def _parse_date(self, date_str: str, date_format: Optional[str] = None) -> Optional[datetime]:
        """解析日期字符串"""
        if not date_str:
            return None

        try:
            # 尝试多种日期格式
            formats_to_try = [
                '%Y-%m-%d',
                '%Y/%m/%d',
                '%d/%m/%Y',
                '%Y年%m月%d日',
                '%Y.%m.%d',
                '%d-%m-%Y',
                '%b %d, %Y',
                '%B %d, %Y'
            ]

            if date_format:
                formats_to_try.insert(0, date_format)

            for fmt in formats_to_try:
                try:
                    return datetime.strptime(date_str.strip(), fmt)
                except ValueError:
                    continue

            # 尝试提取日期部分
            date_patterns = [
                r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})',
                r'(\d{1,2})[-/月](\d{1,2})[-/年](\d{4})'
            ]

            for pattern in date_patterns:
                match = re.search(pattern, date_str)
                if match:
                    groups = match.groups()
                    if len(groups) == 3:
                        try:
                            # 尝试解析为日期
                            if '-' in date_str or '/' in date_str:
                                date_part = f"{groups[0]}-{groups[1]}-{groups[2]}"
                                for fmt in ['%Y-%m-%d', '%d-%m-%Y']:
                                    try:
                                        return datetime.strptime(date_part, fmt)
                                    except ValueError:
                                        continue
                        except Exception:
                            pass

            logger.warning(f"无法解析日期: {date_str}")
            return None

        except Exception as e:
            logger.error(f"解析日期异常: {date_str}, 错误: {e}")
            return None

    def _extract_salary(self, salary_str: str) -> Optional[str]:
        """提取薪资信息"""
        if not salary_str:
            return None

        try:
            # 提取数字范围
            pattern = r'(\d+[\d,]*\.?\d*)\s*[-~至]\s*(\d+[\d,]*\.?\d*)'
            match = re.search(pattern, salary_str)
            if match:
                return f"{match.group(1)}-{match.group(2)}"

            # 提取单个数字
            pattern = r'(\d+[\d,]*\.?\d*)'
            matches = re.findall(pattern, salary_str)
            if matches:
                return matches[0]

            return salary_str.strip()
        except Exception as e:
            logger.error(f"提取薪资失败: {salary_str}, 错误: {e}")
            return salary_str.strip()

    def _clean_html(self, html: str) -> str:
        """清理HTML，提取纯文本"""
        try:
            soup = BeautifulSoup(html, 'lxml')
            # 移除脚本和样式
            for script in soup(["script", "style"]):
                script.decompose()
            # 获取文本
            text = soup.get_text(separator='\n')
            # 清理空白字符
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return '\n'.join(lines)
        except Exception as e:
            logger.error(f"清理HTML失败: {e}")
            return html

    def _normalize_url(self, url: str) -> str:
        """标准化URL"""
        try:
            parsed = urlparse(url)
            # 移除查询参数和片段
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            return normalized.rstrip('/')
        except Exception:
            return url

    def _validate_job_data(self, job_data: Dict[str, Any]) -> bool:
        """验证职位数据"""
        try:
            validation_config = self.config.get('validation', {})
            required_fields = validation_config.get('required_fields', [])

            # 检查必需字段
            for field in required_fields:
                if field not in job_data or not job_data[field]:
                    logger.warning(f"职位数据缺少必需字段: {field}")
                    return False

            # 检查字段规则
            field_rules = validation_config.get('field_rules', {})
            for field, rules in field_rules.items():
                if field in job_data and job_data[field]:
                    value = job_data[field]

                    # 最小长度检查
                    if 'min_length' in rules and isinstance(value, str):
                        if len(value) < rules['min_length']:
                            logger.warning(f"字段 {field} 长度不足: {len(value)} < {rules['min_length']}")
                            return False

                    # 最大长度检查
                    if 'max_length' in rules and isinstance(value, str):
                        if len(value) > rules['max_length']:
                            logger.warning(f"字段 {field} 长度超限: {len(value)} > {rules['max_length']}")
                            return False

                    # 正则表达式检查
                    if 'pattern' in rules and isinstance(value, str):
                        pattern = rules['pattern']
                        if pattern and not re.match(pattern, value):
                            logger.warning(f"字段 {field} 不符合模式: {pattern}")
                            return False

            return True

        except Exception as e:
            logger.error(f"验证职位数据失败: {e}")
            return False

    async def crawl_detail_page(self, url: str) -> Optional[Dict[str, Any]]:
        """爬取详情页"""
        if not self.config.get('parsing', {}).get('detail_page', {}).get('enabled', False):
            return None

        try:
            html = await self.fetch(url)
            if not html:
                return None

            detail_data = self.parse_detail_page(html)
            if detail_data:
                detail_data['detail_url'] = url
            return detail_data

        except Exception as e:
            logger.error(f"爬取详情页失败 {url}: {e}")
            return None

    def parse_detail_page(self, html: str) -> Dict[str, Any]:
        """解析详情页"""
        detail_data = {}
        try:
            parsing_config = self.config.get('parsing', {}).get('detail_page', {})
            if not parsing_config.get('enabled', False):
                return detail_data

            soup = BeautifulSoup(html, 'lxml')
            fields_config = parsing_config.get('fields', {})

            for field_name, field_config in fields_config.items():
                value = self._extract_field_from_detail(soup, field_config)
                if value is not None or not field_config.get('required', False):
                    detail_data[field_name] = value

            return detail_data

        except Exception as e:
            logger.error(f"解析详情页失败: {e}")
            return detail_data

    def _extract_field_from_detail(self,
                                 soup: BeautifulSoup,
                                 field_config: Dict[str, Any]) -> Any:
        """从详情页提取字段"""
        try:
            selector = field_config.get('selector')
            if not selector:
                return field_config.get('default')

            element = soup.select_one(selector)
            if not element:
                return field_config.get('default')

            field_type = field_config.get('type', 'text')
            transform = field_config.get('transform')

            # 提取值
            if field_type == 'text':
                value = element.get_text(strip=True)
            elif field_type == 'attr':
                attr_name = field_config.get('attr')
                if not attr_name:
                    return None
                value = element.get(attr_name)
            elif field_type == 'html':
                value = str(element)
            else:
                value = element.get_text(strip=True)

            # 应用转换
            if value and transform:
                value = self._apply_transform(value, transform, field_config)

            return value

        except Exception as e:
            logger.error(f"从详情页提取字段失败: {field_config}, 错误: {e}")
            return field_config.get('default')
