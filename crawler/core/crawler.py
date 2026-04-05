import asyncio
import csv
import hashlib
import json
import logging
import os
import random
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import parse_qs, urlparse, urlencode, urljoin

import yaml
import aiohttp
import cloudscraper
from aiohttp import ClientSession, ClientTimeout, ClientError
from bs4 import BeautifulSoup
from bs4.element import Tag

from crawler.core.database import db_manager
from crawler.core.waf_detector import WAFDetector
from crawler.spiders.base_spider import BaseSpider
from wechat_service.utils.recruitment_keyword_sets import (
    NON_RECRUITMENT_SUPPLEMENTAL_KEYWORDS,
    POST_RECRUITMENT_EXCLUDE_KEYWORDS,
    merge_keyword_lists,
)

logger = logging.getLogger(__name__)

DEFAULT_RECRUITMENT_KEEP_KEYWORDS = [
    '招聘', '招考', '招录', '引进', '人才', '医师', '护士', '规培',
    '住培', '博士后', '面试', '笔试', '资格复审', '录用', '应聘',
]
DEFAULT_RECRUITMENT_EXCLUDE_KEYWORDS = merge_keyword_lists(
    POST_RECRUITMENT_EXCLUDE_KEYWORDS,
    NON_RECRUITMENT_SUPPLEMENTAL_KEYWORDS,
)
SELECTOR_DISCOVERY_KEYWORDS = tuple(dict.fromkeys([
    *DEFAULT_RECRUITMENT_KEEP_KEYWORDS,
    '公告', '通知', '简章', '公示', '录取', '报名', '招聘信息',
    '招贤', '人才引进', '人事', '聘用',
]))
SELECTOR_DISCOVERY_HINTS = (
    'recruit', 'job', 'jobs', 'hr', 'inform', 'notice', 'tender',
)


@dataclass
class SelectorSuggestionResult:
    hospital_name: str = ''
    detail_url: str = ''
    candidate_list_urls: List[str] = field(default_factory=list)
    selected_list_url: str = ''
    base_url: str = ''
    listing_selector: str = ''
    list_title_selector: str = ''
    link_selector: str = ''
    classification: str = ''
    confidence: float = 0.0
    matched_existing_row: bool = False
    write_allowed: bool = False
    message: str = ''
    jobs: List[Dict[str, Any]] = field(default_factory=list)
    matched_hospital_name: str = ''
    matched_list_page_url: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hospital_name': self.hospital_name,
            'detail_url': self.detail_url,
            'candidate_list_urls': list(self.candidate_list_urls),
            'selected_list_url': self.selected_list_url,
            'base_url': self.base_url,
            'listing_selector': self.listing_selector,
            'list_title_selector': self.list_title_selector,
            'link_selector': self.link_selector,
            'classification': self.classification,
            'confidence': self.confidence,
            'matched_existing_row': self.matched_existing_row,
            'write_allowed': self.write_allowed,
            'message': self.message,
            'jobs': list(self.jobs),
            'matched_hospital_name': self.matched_hospital_name,
            'matched_list_page_url': self.matched_list_page_url,
        }

    def to_csv_row(self) -> Dict[str, str]:
        return {
            'hospital_name': self.hospital_name.strip(),
            'list_page_url': self.selected_list_url.strip(),
            'base_url': self.base_url.strip(),
            'listing_selector': self.listing_selector.strip(),
            'list_title_selector': self.list_title_selector.strip(),
            'link_selector': self.link_selector.strip(),
        }


def _decode_response_body(body: bytes, charset: Optional[str]) -> str:
    """尽量稳健地解码响应体"""
    encodings = []
    if charset:
        encodings.append(charset)
    encodings.extend(['utf-8', 'gb18030', 'gbk'])

    for encoding in encodings:
        try:
            return body.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue

    return body.decode('utf-8', errors='ignore')


def _looks_like_upstream_error_page(content: str) -> bool:
    """识别被网关/反向代理替换后的错误页"""
    if not content:
        return False

    normalized = re.sub(r'\s+', ' ', content).lower()
    markers = (
        '<title>502 bad gateway</title>',
        '<h1>502 bad gateway</h1>',
        '<title>503 service temporarily unavailable</title>',
        '<h1>503 service temporarily unavailable</h1>',
        '<title>504 gateway time-out</title>',
        '<h1>504 gateway time-out</h1>',
    )
    return any(marker in normalized for marker in markers)


def _string_to_hex(raw: str) -> str:
    """按云锁验证页脚本规则将字符串转为十六进制"""
    return ''.join(f"{ord(ch):x}" for ch in raw)


def _looks_like_yunsuo_verify_page(content: str) -> bool:
    """识别云锁的 JS 跳转验证页"""
    if not content:
        return False

    normalized = re.sub(r'\s+', ' ', content).lower()
    markers = (
        'yunsuoautojump',
        'security_verify_data',
        'security_session_verify',
        'srcurl=',
        'stringtohex',
    )
    hit_count = sum(1 for marker in markers if marker in normalized)
    return hit_count >= 3


def _build_yunsuo_verify_url(url: str, screen_size: str = '1920,1080') -> str:
    """构造云锁验证页二次跳转URL"""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query['security_verify_data'] = [_string_to_hex(screen_size)]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def _looks_like_safeline_js_challenge(content: str) -> bool:
    """识别 SafeLine 的 JS Challenge 验证页"""
    if not content:
        return False

    normalized = re.sub(r'\s+', ' ', content).lower()
    markers = (
        'safeline_bot_challenge',
        'safeline_bot_challenge_ans',
        'leading_zero_bit',
        "window.data.type = 'js-challenge'",
        'bin_sha1(prefix + suffix)',
    )
    hit_count = sum(1 for marker in markers if marker in normalized)
    return hit_count >= 3


def _extract_safeline_js_challenge(content: str) -> tuple[Optional[str], Optional[int]]:
    """从 SafeLine Challenge 页面提取求解参数"""
    if not content:
        return None, None

    prefix_match = re.search(r"prefix\s*=\s*['\"]([^'\"]+)['\"]", content)
    zero_bit_match = re.search(r'leading_zero_bit\s*=\s*(\d+)', content)
    if not prefix_match or not zero_bit_match:
        return None, None

    try:
        return prefix_match.group(1), int(zero_bit_match.group(1))
    except ValueError:
        return None, None


def _solve_safeline_js_challenge(
    prefix: str,
    leading_zero_bit: int,
    max_iterations: int = 2_000_000,
) -> Optional[str]:
    """求解 SafeLine Challenge 所需的十六进制后缀"""
    if not prefix or leading_zero_bit < 0 or leading_zero_bit > 160:
        return None

    full_zero_bytes, remaining_bits = divmod(leading_zero_bit, 8)
    for counter in range(max_iterations):
        suffix = format(counter, 'x')
        digest = hashlib.sha1(f"{prefix}{suffix}".encode('utf-8')).digest()

        if any(digest[index] != 0 for index in range(full_zero_bytes)):
            continue

        if remaining_bits and digest[full_zero_bytes] >> (8 - remaining_bits) != 0:
            continue

        return suffix

    return None


class CrawlerConfig:
    """爬虫配置类"""

    def __init__(self, config_data: Dict[str, Any]):
        self.site_name = config_data.get('site_name', '')
        self.base_url = config_data.get('base_url', '')
        self.start_url = config_data.get('start_url', self.base_url)
        self.enabled = config_data.get('enabled', True)
        self.priority = config_data.get('priority', 1)
        self.request_timeout = config_data.get('request_timeout', 30)
        self.max_retries = config_data.get('max_retries', 3)
        self.delay_between_requests = config_data.get('delay_between_requests', 1.0)
        self.user_agent = config_data.get('user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        self.parsing = config_data.get('parsing', {})
        self.waf_strategy = config_data.get('waf_strategy', config_data.get('waf', {}))
        self.validation = config_data.get('validation', {})
        self.ssl_verify = config_data.get('ssl_verify', True)


class CrawlerEngine:
    """爬虫引擎"""

    def __init__(
        self,
        config_path: str = "config/global.yaml",
        selectors_csv_path: str = "医院招聘的selector.csv",
        site_limit: Optional[int] = None,
    ):
        self.config_path = config_path
        self.selectors_csv_path = selectors_csv_path
        self.site_limit = site_limit
        self.global_config = {}
        self.site_configs: Dict[str, CrawlerConfig] = {}
        self.waf_detector = WAFDetector()
        self.session: Optional[ClientSession] = None
        self.concurrent_sites = 1
        self.export_enabled = True
        self.export_directory = "data/exports"
        self.recruitment_filter_enabled = True
        self.recruitment_keep_keywords: List[str] = []
        self.recruitment_exclude_keywords: List[str] = []
        self.global_waf_config: Dict[str, Any] = {}
        self.csv_rows: List[Dict[str, Any]] = []
        self.csv_row_issues: List[Dict[str, Any]] = []
        self._load_config()
        self._apply_runtime_settings()

    def _load_config(self):
        """加载配置文件"""
        try:
            # 加载全局配置
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.global_config = yaml.safe_load(f) or {}
                self.global_waf_config = dict(
                    self.global_config.get('waf') or self.global_config.get('waf_strategy') or {}
                )
            else:
                logger.warning(f"全局配置文件不存在: {self.config_path}，使用默认配置")

            # 优先从CSV加载网站配置
            csv_loaded = self._load_sites_from_csv(self.selectors_csv_path)

            # CSV不存在时，回退到原YAML站点配置
            if not csv_loaded:
                self._load_sites_from_yaml()

            logger.info(f"配置加载完成: {len(self.site_configs)} 个网站")

        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")

    def _apply_runtime_settings(self):
        """应用全局运行时配置"""
        crawler_config = self.global_config.get('crawler', {})
        export_config = self.global_config.get('export', {})
        recruitment_filter = self.global_config.get('recruitment_filter', {})

        concurrent_sites = crawler_config.get('concurrent_sites', 1)
        try:
            concurrent_sites = int(concurrent_sites)
        except (TypeError, ValueError):
            concurrent_sites = 1

        self.concurrent_sites = max(1, concurrent_sites)
        self.export_enabled = export_config.get('enabled', True)
        self.export_directory = export_config.get('directory', 'data/exports')
        self.recruitment_filter_enabled = recruitment_filter.get('enabled', True)
        configured_keep_keywords = recruitment_filter.get('keep_keywords') or []
        configured_exclude_keywords = recruitment_filter.get('exclude_keywords') or []
        self.recruitment_keep_keywords = merge_keyword_lists(
            DEFAULT_RECRUITMENT_KEEP_KEYWORDS,
            configured_keep_keywords,
        )
        self.recruitment_exclude_keywords = merge_keyword_lists(
            DEFAULT_RECRUITMENT_EXCLUDE_KEYWORDS,
            configured_exclude_keywords,
        )
        self.global_waf_config = dict(
            self.global_config.get('waf') or self.global_config.get('waf_strategy') or {}
        )

    def _load_sites_from_yaml(self):
        """回退加载YAML网站配置"""
        sites_dir = os.path.join(os.path.dirname(self.config_path), 'sites')
        if not os.path.exists(sites_dir):
            return

        for filename in os.listdir(sites_dir):
            if not filename.endswith(('.yaml', '.yml')):
                continue

            filepath = os.path.join(sites_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    site_data = yaml.safe_load(f)
                    if site_data:
                        config = CrawlerConfig(site_data)
                        if config.site_name:
                            self.site_configs[config.site_name] = config
                            logger.info(f"加载YAML网站配置: {config.site_name}")
            except Exception as e:
                logger.error(f"加载网站配置文件失败 {filename}: {e}")

    def _load_sites_from_csv(self, csv_path: str) -> bool:
        """从CSV加载网站与选择器配置"""
        if not os.path.exists(csv_path):
            logger.warning(f"CSV配置文件不存在: {csv_path}")
            return False

        total_rows = 0
        valid_rows = 0
        skipped_rows = 0
        self.csv_rows = []
        self.csv_row_issues = []

        try:
            with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    logger.error("CSV表头为空")
                    return False

                for row_index, row in enumerate(reader, start=2):
                    total_rows += 1
                    row_snapshot = dict(row)
                    row_snapshot['_row_index'] = row_index
                    config_data, skip_reason = self._build_site_config_from_csv(row_index, row)

                    if not config_data:
                        skipped_rows += 1
                        row_snapshot['_skip_reason'] = skip_reason
                        self.csv_row_issues.append(row_snapshot)
                        logger.warning(f"跳过CSV第{row_index}行: {skip_reason}")
                        continue

                    site_name = config_data['site_name']
                    row_snapshot['_site_name'] = site_name
                    row_snapshot['_skip_reason'] = None
                    self.csv_rows.append(row_snapshot)
                    self.site_configs[site_name] = CrawlerConfig(config_data)
                    valid_rows += 1

                    if self.site_limit and valid_rows >= self.site_limit:
                        break

            logger.info(
                f"CSV加载完成: total={total_rows}, valid={valid_rows}, skipped={skipped_rows}, path={csv_path}"
            )
            return valid_rows > 0

        except Exception as e:
            logger.error(f"读取CSV配置失败: {e}")
            return False

    def _build_site_config_from_csv(
        self, row_index: int, row: Dict[str, str]
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        """将CSV行转换为运行时配置"""
        hospital_name = (row.get('hospital_name') or '').strip()
        list_page_url = (row.get('list_page_url') or '').strip()
        base_url = (row.get('base_url') or '').strip()
        listing_selector = (row.get('listing_selector') or '').strip()
        list_title_selector = (row.get('list_title_selector') or '').strip()
        link_selector = (row.get('link_selector') or '').strip()

        if not any([hospital_name, list_page_url, base_url, listing_selector, list_title_selector, link_selector]):
            return None, '空行'

        if not hospital_name:
            return None, 'hospital_name为空'
        if not list_page_url:
            return None, 'list_page_url为空'
        if not listing_selector:
            if self._supports_dynamic_fallback_without_selector(list_page_url):
                listing_selector = 'codex-dynamic-fallback'
            else:
                return None, 'listing_selector为空'

        if not list_page_url.startswith(('http://', 'https://')):
            return None, 'list_page_url不是合法HTTP(S)地址'

        if not base_url:
            parsed = urlparse(list_page_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

        if not base_url.startswith(('http://', 'https://')):
            return None, 'base_url不是合法HTTP(S)地址'

        title_selector = list_title_selector or 'a'
        url_selector = link_selector or title_selector or 'a'

        site_name = self._slugify_site_name(hospital_name, row_index)

        crawler_defaults = self.global_config.get('crawler', {})
        waf_strategy = self.global_waf_config
        ssl_relax_sites = set((self.global_config.get('crawler', {}).get('ssl_relax_sites') or []))

        config_data: Dict[str, Any] = {
            'site_name': site_name,
            'enabled': True,
            'priority': row_index,
            'base_url': base_url,
            'start_url': list_page_url,
            'request_timeout': crawler_defaults.get('request_timeout', 30),
            'max_retries': crawler_defaults.get('max_retries', 3),
            'delay_between_requests': crawler_defaults.get('delay_between_requests', 1.5),
            'user_agent': crawler_defaults.get(
                'user_agent',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            ),
            'waf_strategy': waf_strategy,
            'ssl_verify': site_name not in ssl_relax_sites,
            'parsing': {
                'list_page': {
                    'container_selector': 'body',
                    'item_selector': listing_selector,
                    'fields': {
                        'title': {
                            'selector': title_selector,
                            'type': 'text',
                            'required': True,
                            'field_name': 'title',
                            'transform': 'strip',
                        },
                        'url': {
                            'selector': url_selector,
                            'type': 'attr',
                            'attr': 'href',
                            'required': True,
                            'field_name': 'url',
                            'transform': 'absolute_url',
                        },
                        'hospital': {
                            'default': hospital_name,
                            'required': False,
                        },
                    },
                }
            },
            'validation': {
                'required_fields': ['title', 'url'],
                'field_rules': {
                    'title': {'min_length': 2, 'max_length': 300},
                    'url': {'pattern': r'^https?://.+'},
                },
            },
        }

        return config_data, None

    @staticmethod
    def _norm_netloc(url: str) -> str:
        return urlparse((url or '').strip()).netloc.lower()

    @staticmethod
    def _norm_path(url: str) -> str:
        path = (urlparse((url or '').strip()).path or "/").rstrip("/")
        return path or "/"

    def match_site_configs(self, input_url: str) -> List[CrawlerConfig]:
        """按URL精确匹配/同域匹配已有站点配置"""
        input_netloc = self._norm_netloc(input_url)
        input_path = self._norm_path(input_url)

        exact = [cfg for cfg in self.site_configs.values() if (cfg.start_url or "").strip() == input_url.strip()]
        if exact:
            return exact

        same_domain = [
            cfg for cfg in self.site_configs.values()
            if self._norm_netloc(cfg.start_url or "") == input_netloc
        ]
        if not same_domain:
            return []

        strong_path_match = []
        for cfg in same_domain:
            cfg_path = self._norm_path(cfg.start_url or "")
            if input_path.startswith(cfg_path) or cfg_path.startswith(input_path):
                strong_path_match.append(cfg)

        return strong_path_match or same_domain

    def match_csv_rows(self, input_url: str) -> List[Dict[str, Any]]:
        """按URL匹配CSV原始行，包含跳过原因，便于诊断未接入站点"""
        input_netloc = self._norm_netloc(input_url)
        input_path = self._norm_path(input_url)
        matched: List[Dict[str, Any]] = []

        for row in [*self.csv_rows, *self.csv_row_issues]:
            list_url = (row.get('list_page_url') or '').strip()
            if not list_url:
                continue

            row_netloc = self._norm_netloc(list_url)
            if row_netloc != input_netloc:
                continue

            row_path = self._norm_path(list_url)
            if input_path.startswith(row_path) or row_path.startswith(input_path):
                matched.append(row)

        return matched

    @staticmethod
    def _hospital_name(config: CrawlerConfig) -> str:
        return (
            config.parsing.get("list_page", {})
            .get("fields", {})
            .get("hospital", {})
            .get("default", config.site_name)
        )

    @staticmethod
    def _diagnostic_suggestion(classification: str) -> str:
        suggestions = {
            'ok': '当前选择器和抓取链路可用，可以进入详情URL审核环节。',
            'selector_mismatch': '优先检查 listing_selector、list_title_selector、link_selector 是否过宽或未命中真正公告项。',
            'onclick_placeholder': '列表项链接疑似写在 onclick 中，请调整 link_selector，或扩展 onclick URL 恢复规则。',
            'frontend_shell': '页面更像前端壳页，请优先确认浏览器DOM和接口请求，再决定是否走动态浏览器。',
            'api_backed': '页面数据疑似来自接口，请抓浏览器 Network，补站点级 API/vendor 兜底。',
            'waf_or_tls_blocked': '当前更像 WAF/TLS/传输异常，先看证书、403/429、浏览器可访问性，再决定是否放宽 SSL 或走浏览器抓取。',
            'empty_selector': 'CSV 当前缺少可用 listing_selector，请先补配置，若是纯动态页则补对应动态/API 兜底规则。',
        }
        return suggestions.get(classification, '请结合原始HTML、浏览器DOM和日志继续定位。')

    @classmethod
    def _classify_fetch_failure(cls, error_message: str, status_code: Optional[int] = None) -> str:
        message = (error_message or '').lower()
        if status_code in {403, 429, 503, 504}:
            return 'waf_or_tls_blocked'
        if any(marker in message for marker in (
            'ssl',
            'tls',
            'certificate',
            'hostname mismatch',
            'decryption failed',
            'bad record mac',
            'captcha',
            'access denied',
            'challenge',
            'cloudflare',
            'cannot connect to host',
        )):
            return 'waf_or_tls_blocked'
        return 'network_error'

    @staticmethod
    def _looks_like_generic_frontend_shell(content: str) -> bool:
        if not content:
            return False

        normalized = re.sub(r'\s+', ' ', content).lower()
        has_app_root = bool(re.search(r"<div[^>]+id=['\"]app['\"]", normalized))
        shell_markers = (
            'chunk-vendors',
            '/assets/index',
            'window.__initial_state__',
        )
        return has_app_root and any(marker in normalized for marker in shell_markers)

    @staticmethod
    def _looks_like_onclick_placeholder_page(content: str) -> bool:
        if not content:
            return False
        normalized = re.sub(r'\s+', ' ', content).lower()
        return (
            ('href="#"' in normalized or 'javascript:void(0)' in normalized or 'javascript:;' in normalized)
            and 'onclick=' in normalized
        )

    def _classify_list_page_content(
        self,
        content: str,
        config: CrawlerConfig,
    ) -> str:
        if self._looks_like_ruifox_dynamic_list(content, config.start_url):
            return 'api_backed'
        if self._looks_like_ruifox_recruitment_notice_shell(content, config.start_url):
            return 'api_backed'
        if self._looks_like_cqgwzx_dynamic_list(content, config.start_url):
            return 'api_backed'
        if self._looks_like_sns120_dynamic_list(content, config.start_url):
            return 'frontend_shell'
        if self._looks_like_666120_jobs_shell(content, config.start_url):
            return 'frontend_shell'
        if self._looks_like_generic_frontend_shell(content):
            return 'frontend_shell'
        if self._looks_like_onclick_placeholder_page(content):
            return 'onclick_placeholder'
        return 'selector_mismatch'

    async def validate_list_page_url(
        self,
        input_url: str,
        max_items: int = 20,
    ) -> Dict[str, Any]:
        """结构化验证列表页URL，供新增医院调试与审核"""
        matched = self.match_site_configs(input_url)
        csv_rows = self.match_csv_rows(input_url)
        issue_row = csv_rows[0] if csv_rows else {}

        if not matched:
            classification = 'selector_mismatch'
            skip_reason = str(issue_row.get('_skip_reason') or '')
            if skip_reason == 'listing_selector为空':
                classification = 'empty_selector'

            return {
                'success': False,
                'classification': classification,
                'suggestion': self._diagnostic_suggestion(classification),
                'hospital': issue_row.get('hospital_name') or '',
                'site_name': issue_row.get('_site_name') or '',
                'csv_list_url': issue_row.get('list_page_url') or '',
                'input_url': input_url,
                'fetch_backend': None,
                'failure_category': skip_reason or 'config_not_found',
                'message': skip_reason or '未在CSV里匹配到可用站点配置',
                'jobs': [],
                'matched_count': 0,
            }

        chosen = matched[0]
        runtime_cfg = CrawlerConfig(
            {
                "site_name": chosen.site_name,
                "base_url": chosen.base_url,
                "start_url": input_url,
                "enabled": chosen.enabled,
                "priority": chosen.priority,
                "request_timeout": chosen.request_timeout,
                "max_retries": chosen.max_retries,
                "delay_between_requests": chosen.delay_between_requests,
                "user_agent": chosen.user_agent,
                "parsing": chosen.parsing,
                "waf_strategy": chosen.waf_strategy,
                "validation": chosen.validation,
                "ssl_verify": chosen.ssl_verify,
            }
        )

        fetch_result = await self.fetch_page_with_metadata(input_url, runtime_cfg)
        if not fetch_result.get('success'):
            classification = 'waf_or_tls_blocked'
            failure_category = fetch_result.get('failure_category') or 'network_error'
            return {
                'success': False,
                'classification': classification,
                'suggestion': self._diagnostic_suggestion(classification),
                'hospital': self._hospital_name(chosen),
                'site_name': chosen.site_name,
                'csv_list_url': chosen.start_url,
                'input_url': input_url,
                'fetch_backend': fetch_result.get('fetch_backend'),
                'failure_category': failure_category,
                'message': fetch_result.get('error_message') or failure_category,
                'jobs': [],
                'matched_count': len(matched),
            }

        content = str(fetch_result.get('content') or '')
        if _looks_like_upstream_error_page(content) or self._looks_like_fetch_blocked_page(content):
            classification = 'waf_or_tls_blocked'
            return {
                'success': False,
                'classification': classification,
                'suggestion': self._diagnostic_suggestion(classification),
                'hospital': self._hospital_name(chosen),
                'site_name': chosen.site_name,
                'csv_list_url': chosen.start_url,
                'input_url': input_url,
                'fetch_backend': fetch_result.get('fetch_backend'),
                'failure_category': 'blocked_page',
                'message': '返回内容更像被拦截页或上游错误页',
                'jobs': [],
                'matched_count': len(matched),
            }

        jobs = await self.parse_page(content, runtime_cfg)
        if jobs:
            return {
                'success': True,
                'classification': 'ok',
                'suggestion': self._diagnostic_suggestion('ok'),
                'hospital': self._hospital_name(chosen),
                'site_name': chosen.site_name,
                'csv_list_url': chosen.start_url,
                'input_url': input_url,
                'fetch_backend': fetch_result.get('fetch_backend'),
                'failure_category': '',
                'message': f'提取到 {len(jobs)} 条招聘公告',
                'jobs': jobs[:max_items],
                'matched_count': len(matched),
            }

        classification = self._classify_list_page_content(content, runtime_cfg)
        return {
            'success': False,
            'classification': classification,
            'suggestion': self._diagnostic_suggestion(classification),
            'hospital': self._hospital_name(chosen),
            'site_name': chosen.site_name,
            'csv_list_url': chosen.start_url,
            'input_url': input_url,
            'fetch_backend': fetch_result.get('fetch_backend'),
            'failure_category': classification,
            'message': '页面可获取，但当前未提取到招聘公告',
            'jobs': [],
            'matched_count': len(matched),
        }

    @staticmethod
    def _absolute_base_url(url: str) -> str:
        parsed = urlparse((url or '').strip())
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return ''

    @staticmethod
    def _detail_family_tokens(url: str) -> List[str]:
        segments = [segment for segment in urlparse((url or '').strip()).path.split('/') if segment]
        tokens: List[str] = []
        for segment in segments:
            normalized = re.sub(r'\d+', '', segment.lower()).strip('-_. ')
            if not normalized or normalized in {'show', 'detail', 'view', 'info'}:
                continue
            tokens.append(normalized)
        return tokens[:3]

    @classmethod
    def _job_matches_detail_family(cls, job_url: str, detail_url: str) -> bool:
        if (job_url or '').strip() == (detail_url or '').strip():
            return True

        detail_tokens = set(cls._detail_family_tokens(detail_url))
        if not detail_tokens:
            return False

        job_tokens = set(cls._detail_family_tokens(job_url))
        return bool(detail_tokens & job_tokens)

    @staticmethod
    def _text_has_recruitment_signal(text: str) -> bool:
        normalized = (text or '').strip()
        if not normalized:
            return False
        return any(keyword in normalized for keyword in SELECTOR_DISCOVERY_KEYWORDS)

    @staticmethod
    def _text_has_exclusion_signal(text: str) -> bool:
        normalized = (text or '').strip()
        if not normalized:
            return False
        return any(keyword in normalized for keyword in DEFAULT_RECRUITMENT_EXCLUDE_KEYWORDS)

    @staticmethod
    def _clean_selector_token(node: Tag) -> str:
        if not getattr(node, 'name', None):
            return ''

        token = node.name
        node_id = (node.get('id') or '').strip()
        if node_id and re.match(r'^[A-Za-z_][\w\-:.]*$', node_id):
            return f"{token}#{node_id}"

        classes = [
            css_class.strip()
            for css_class in node.get('class', [])
            if css_class and re.match(r'^[A-Za-z_][\w\-:]*$', css_class)
        ]
        if classes:
            token = f"{token}." + ".".join(classes[:3])
        return token

    @classmethod
    def _build_selector_for_node(cls, soup: BeautifulSoup, node: Tag, max_depth: int = 3) -> str:
        parts: List[str] = []
        current: Optional[Tag] = node

        while current and getattr(current, 'name', None) and current.name not in {'[document]', 'html'}:
            token = cls._clean_selector_token(current)
            if not token:
                break
            parts.insert(0, token)
            selector = " ".join(parts)
            try:
                if len(soup.select(selector)) == 1:
                    return selector
            except Exception:
                pass

            if len(parts) >= max_depth or current.name == 'body':
                break
            parent = current.parent
            current = parent if isinstance(parent, Tag) else None

        return " ".join(parts)

    @classmethod
    def _build_relative_selector(cls, item: Tag, target: Tag) -> str:
        if item == target:
            return cls._clean_selector_token(target) or target.name

        if len(item.select('a')) == 1 and target.name == 'a':
            return 'a'

        parts: List[str] = []
        current: Optional[Tag] = target
        while current and current != item:
            token = cls._clean_selector_token(current)
            if not token:
                break
            parts.insert(0, token)
            parent = current.parent
            current = parent if isinstance(parent, Tag) else None

        if not parts:
            return target.name or 'a'
        if len(parts) == 1:
            return parts[0]
        return " > ".join(parts[-2:])

    def _build_probe_config(self, url: str) -> CrawlerConfig:
        base_url = self._absolute_base_url(url) or url
        return CrawlerConfig(
            {
                'site_name': 'selector_probe',
                'base_url': base_url,
                'start_url': url,
                'enabled': True,
                'priority': 1,
                'request_timeout': self.global_config.get('crawler', {}).get('request_timeout', 30),
                'max_retries': 2,
                'delay_between_requests': 0,
                'user_agent': self.global_config.get('crawler', {}).get(
                    'user_agent',
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                ),
                'parsing': {},
                'waf_strategy': self.global_waf_config,
                'validation': {},
                'ssl_verify': True,
            }
        )

    def _build_selector_validation_config(
        self,
        hospital_name: str,
        list_page_url: str,
        base_url: str,
        listing_selector: str,
        list_title_selector: str,
        link_selector: str,
    ) -> CrawlerConfig:
        return CrawlerConfig(
            {
                'site_name': self._slugify_site_name(hospital_name or 'selector_suggestion', 1),
                'base_url': base_url,
                'start_url': list_page_url,
                'enabled': True,
                'priority': 1,
                'request_timeout': self.global_config.get('crawler', {}).get('request_timeout', 30),
                'max_retries': 2,
                'delay_between_requests': 0,
                'user_agent': self.global_config.get('crawler', {}).get(
                    'user_agent',
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                ),
                'waf_strategy': self.global_waf_config,
                'ssl_verify': True,
                'parsing': {
                    'list_page': {
                        'container_selector': 'body',
                        'item_selector': listing_selector,
                        'fields': {
                            'title': {
                                'selector': list_title_selector or 'a',
                                'type': 'text',
                                'required': True,
                                'transform': 'strip',
                            },
                            'url': {
                                'selector': link_selector or list_title_selector or 'a',
                                'type': 'attr',
                                'attr': 'href',
                                'required': True,
                                'transform': 'absolute_url',
                            },
                            'hospital': {
                                'default': hospital_name,
                                'required': False,
                            },
                        },
                    }
                },
                'validation': {
                    'required_fields': ['title', 'url'],
                    'field_rules': {
                        'title': {'min_length': 2, 'max_length': 300},
                        'url': {'pattern': r'^https?://.+'},
                    },
                },
            }
        )

    def _same_domain_csv_rows(self, input_url: str) -> List[Dict[str, Any]]:
        input_netloc = self._norm_netloc(input_url)
        rows: List[Dict[str, Any]] = []
        for row in self.csv_rows:
            list_url = (row.get('list_page_url') or '').strip()
            if list_url and self._norm_netloc(list_url) == input_netloc:
                rows.append(row)
        return rows

    def _find_existing_selector_duplicate(
        self,
        hospital_name: str,
        list_page_url: str,
    ) -> Optional[Dict[str, Any]]:
        normalized_name = (hospital_name or '').strip()
        normalized_url = (list_page_url or '').strip()
        for row in self.csv_rows:
            if (row.get('hospital_name') or '').strip() == normalized_name:
                return row
            if (row.get('list_page_url') or '').strip() == normalized_url:
                return row
        return None

    async def _fetch_page_for_selector_analysis(
        self,
        url: str,
        config: Optional[CrawlerConfig] = None,
    ) -> Dict[str, Any]:
        probe_config = config or self._build_probe_config(url)
        result = await self.fetch_page_with_metadata(url, probe_config)
        content = result.get('content') or ''

        if content and (self._needs_visited_cookie(content) or self._looks_like_generic_frontend_shell(content)):
            retry_result = await self.fetch_page_with_metadata(url, probe_config)
            if retry_result.get('content'):
                return retry_result

        return result

    def _extract_hospital_name_from_text(self, text: str) -> str:
        candidates = re.findall(
            r'([\u4e00-\u9fffA-Za-z0-9·（）()\-]+?(?:医院|卫生院|医疗中心|中心医院|中医院|人民医院|妇幼保健院))',
            text or '',
        )
        if not candidates:
            return ''
        return max(((candidate.strip(" -_|【】[]")) for candidate in candidates), key=len)

    def _infer_hospital_name(
        self,
        detail_soup: BeautifulSoup,
        detail_html: str,
        existing_rows: List[Dict[str, Any]],
    ) -> str:
        existing_names = list(dict.fromkeys(
            (row.get('hospital_name') or '').strip()
            for row in existing_rows
            if (row.get('hospital_name') or '').strip()
        ))
        if len(existing_names) == 1:
            return existing_names[0]

        candidates: List[str] = []
        if detail_soup.title:
            candidates.append(detail_soup.title.get_text(" ", strip=True))

        meta_keywords = detail_soup.select_one("meta[name='keywords']")
        if meta_keywords and meta_keywords.get('content'):
            candidates.append(meta_keywords.get('content'))

        meta_desc = detail_soup.select_one("meta[name='description']")
        if meta_desc and meta_desc.get('content'):
            candidates.append(meta_desc.get('content'))

        candidates.append(detail_soup.get_text(" ", strip=True)[:500])
        candidates.append(detail_html[:500])

        for candidate_text in candidates:
            hospital_name = self._extract_hospital_name_from_text(candidate_text)
            if hospital_name:
                return hospital_name

        return existing_names[0] if existing_names else ''

    def _build_candidate_list_urls(
        self,
        detail_url: str,
        detail_soup: BeautifulSoup,
        existing_rows: List[Dict[str, Any]],
    ) -> List[str]:
        ordered: Dict[str, None] = {}

        def add(url: str) -> None:
            cleaned = (url or '').strip()
            if not cleaned.startswith(('http://', 'https://')):
                return
            if self._norm_netloc(cleaned) != self._norm_netloc(detail_url):
                return
            segments = [segment for segment in urlparse(cleaned).path.split('/') if segment]
            if segments and re.fullmatch(r'(show)?\d+', segments[-1], flags=re.IGNORECASE):
                return
            ordered.setdefault(cleaned, None)

        for row in existing_rows:
            add((row.get('list_page_url') or '').strip())

        parsed = urlparse(detail_url)
        path_segments = [segment for segment in parsed.path.split('/') if segment]
        if len(path_segments) >= 2:
            parent_path = "/" + "/".join(path_segments[:-1]) + "/"
            add(parsed._replace(path=parent_path, query='', fragment='').geturl())

        for anchor in detail_soup.select('a[href]'):
            href = (anchor.get('href') or '').strip()
            if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                continue
            text = anchor.get_text(" ", strip=True)
            href_lower = href.lower()
            if self._text_has_recruitment_signal(text) or any(hint in href_lower for hint in SELECTOR_DISCOVERY_HINTS):
                add(urljoin(detail_url, href))

        return list(ordered)

    def _candidate_anchor_score(self, list_url: str, detail_url: str, anchor: Tag) -> int:
        href = (anchor.get('href') or '').strip()
        text = anchor.get_text(" ", strip=True)
        absolute_url = urljoin(list_url, href)
        score = 0

        if self._norm_netloc(absolute_url) == self._norm_netloc(list_url):
            score += 2
        if self._text_has_recruitment_signal(text):
            score += 4
        if self._job_matches_detail_family(absolute_url, detail_url):
            score += 5
        if any(hint in absolute_url.lower() for hint in SELECTOR_DISCOVERY_HINTS):
            score += 2
        if self._text_has_exclusion_signal(text):
            score -= 6
        if len(text.strip()) < 4:
            score -= 2
        return score

    def _infer_selector_row_from_html(
        self,
        html: str,
        list_url: str,
        detail_url: str,
    ) -> Optional[Dict[str, str]]:
        soup = BeautifulSoup(html, 'lxml')
        best_candidate: Optional[Dict[str, Any]] = None

        for anchor in soup.select('a[href]'):
            href = (anchor.get('href') or '').strip()
            if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                continue

            score = self._candidate_anchor_score(list_url, detail_url, anchor)
            if score <= 0:
                continue

            current: Optional[Tag] = anchor if anchor.name != 'a' else anchor.parent if isinstance(anchor.parent, Tag) else anchor
            depth = 0
            while current and getattr(current, 'name', None) and current.name != 'body' and depth < 4:
                parent = current.parent if isinstance(current.parent, Tag) else None
                if not parent:
                    break

                item_token = self._clean_selector_token(current)
                if not item_token:
                    current = parent
                    depth += 1
                    continue

                siblings = [
                    child for child in parent.find_all(current.name, recursive=False)
                    if isinstance(child, Tag)
                ]
                qualified_siblings = []
                for sibling in siblings:
                    for sibling_anchor in sibling.select('a[href]'):
                        if self._candidate_anchor_score(list_url, detail_url, sibling_anchor) > 0:
                            qualified_siblings.append(sibling)
                            break

                qualified_count = len(qualified_siblings)
                if qualified_count >= 2:
                    parent_selector = self._build_selector_for_node(soup, parent)
                    if not parent_selector:
                        current = parent
                        depth += 1
                        continue

                    direct_selector = f"{parent_selector} > {item_token}"
                    descendant_selector = f"{parent_selector} {item_token}"
                    try:
                        if len(soup.select(direct_selector)) == qualified_count:
                            listing_selector = direct_selector
                        else:
                            listing_selector = descendant_selector
                    except Exception:
                        listing_selector = descendant_selector

                    link_selector = self._build_relative_selector(current, anchor)
                    title_selector = link_selector or 'a'
                    candidate = {
                        'listing_selector': listing_selector,
                        'list_title_selector': title_selector,
                        'link_selector': link_selector or title_selector or 'a',
                        'score': score + qualified_count,
                    }
                    if not best_candidate or candidate['score'] > best_candidate['score']:
                        best_candidate = candidate
                    break

                current = parent
                depth += 1

        if not best_candidate:
            return None

        return {
            'listing_selector': best_candidate['listing_selector'],
            'list_title_selector': best_candidate['list_title_selector'],
            'link_selector': best_candidate['link_selector'],
        }

    async def _evaluate_existing_selector_row(
        self,
        detail_url: str,
        row: Dict[str, Any],
    ) -> Optional[tuple[SelectorSuggestionResult, int]]:
        hospital_name = (row.get('hospital_name') or '').strip()
        list_page_url = (row.get('list_page_url') or '').strip()
        base_url = (row.get('base_url') or '').strip() or self._absolute_base_url(list_page_url)
        listing_selector = (row.get('listing_selector') or '').strip()
        list_title_selector = (row.get('list_title_selector') or '').strip() or 'a'
        link_selector = (row.get('link_selector') or '').strip() or list_title_selector or 'a'

        if not list_page_url or not listing_selector:
            return None

        config = self._build_selector_validation_config(
            hospital_name=hospital_name,
            list_page_url=list_page_url,
            base_url=base_url,
            listing_selector=listing_selector,
            list_title_selector=list_title_selector,
            link_selector=link_selector,
        )
        fetch_result = await self._fetch_page_for_selector_analysis(list_page_url, config)
        content = fetch_result.get('content')
        if not content:
            return None

        jobs = await self.parse_page(content, config)
        if not jobs:
            return None

        family_match = any(self._job_matches_detail_family(job.get('url', ''), detail_url) for job in jobs)
        score = len(jobs) + (6 if family_match else 0)

        return SelectorSuggestionResult(
            hospital_name=hospital_name,
            detail_url=detail_url,
            candidate_list_urls=[list_page_url],
            selected_list_url=list_page_url,
            base_url=base_url,
            listing_selector=listing_selector,
            list_title_selector=list_title_selector,
            link_selector=link_selector,
            classification='existing_match',
            confidence=min(0.99, 0.72 + min(len(jobs), 6) * 0.03 + (0.08 if family_match else 0)),
            matched_existing_row=True,
            write_allowed=False,
            message='该详情页已匹配到现有医院配置，可复用/核对，不建议新增重复行。',
            jobs=jobs[:5],
            matched_hospital_name=hospital_name,
            matched_list_page_url=list_page_url,
        ), score

    async def suggest_selector_row_from_detail_url(self, detail_url: str) -> SelectorSuggestionResult:
        detail_url = (detail_url or '').strip()
        if not detail_url.startswith(('http://', 'https://')):
            return SelectorSuggestionResult(
                detail_url=detail_url,
                classification='invalid_detail_url',
                message='详情页 URL 不是合法的 HTTP(S) 地址。',
            )

        if not self.session:
            await self.init_session()

        existing_rows = self._same_domain_csv_rows(detail_url)
        existing_results: List[tuple[SelectorSuggestionResult, int]] = []
        for row in existing_rows:
            evaluated = await self._evaluate_existing_selector_row(detail_url, row)
            if evaluated:
                existing_results.append(evaluated)

        detail_fetch = await self._fetch_page_for_selector_analysis(detail_url)
        detail_html = detail_fetch.get('content')
        if not detail_html:
            return SelectorSuggestionResult(
                detail_url=detail_url,
                classification=detail_fetch.get('failure_category') or 'fetch_failed',
                message=detail_fetch.get('error_message') or '详情页抓取失败，暂时无法反推选择器。',
            )

        detail_soup = BeautifulSoup(detail_html, 'lxml')
        hospital_name = self._infer_hospital_name(detail_soup, detail_html, existing_rows)
        candidate_list_urls = self._build_candidate_list_urls(detail_url, detail_soup, existing_rows)

        if existing_results:
            existing_results.sort(key=lambda item: item[1], reverse=True)
            top_result, _ = existing_results[0]
            top_result.candidate_list_urls = candidate_list_urls or [top_result.selected_list_url]
            if len(existing_results) == 1 or any(
                self._job_matches_detail_family(job.get('url', ''), detail_url)
                for job in top_result.jobs
            ):
                top_result.hospital_name = hospital_name or top_result.hospital_name
                return top_result

        if not candidate_list_urls:
            return SelectorSuggestionResult(
                hospital_name=hospital_name,
                detail_url=detail_url,
                classification='list_url_not_found',
                message='未能从详情页推导出候选列表页 URL，请改用列表页调试脚本人工确认。',
            )

        best_result: Optional[SelectorSuggestionResult] = None
        best_score = -1
        best_failure: Optional[SelectorSuggestionResult] = None

        for index, candidate_list_url in enumerate(candidate_list_urls):
            fetch_result = await self._fetch_page_for_selector_analysis(candidate_list_url)
            content = fetch_result.get('content')
            if not content:
                if not best_failure:
                    best_failure = SelectorSuggestionResult(
                        hospital_name=hospital_name,
                        detail_url=detail_url,
                        candidate_list_urls=candidate_list_urls,
                        selected_list_url=candidate_list_url,
                        base_url=self._absolute_base_url(candidate_list_url),
                        classification=fetch_result.get('failure_category') or 'fetch_failed',
                        message=fetch_result.get('error_message') or '候选列表页抓取失败。',
                    )
                continue

            inferred = self._infer_selector_row_from_html(content, candidate_list_url, detail_url)
            if not inferred:
                if not best_failure:
                    best_failure = SelectorSuggestionResult(
                        hospital_name=hospital_name,
                        detail_url=detail_url,
                        candidate_list_urls=candidate_list_urls,
                        selected_list_url=candidate_list_url,
                        base_url=self._absolute_base_url(candidate_list_url),
                        classification=self._classify_list_page_content(
                            content,
                            self._build_probe_config(candidate_list_url),
                        ),
                        message='候选列表页未能稳定推断出静态选择器，请改用现有列表页诊断流程人工确认。',
                    )
                continue

            base_url = self._absolute_base_url(candidate_list_url)
            validation_config = self._build_selector_validation_config(
                hospital_name=hospital_name or '待确认医院',
                list_page_url=candidate_list_url,
                base_url=base_url,
                listing_selector=inferred['listing_selector'],
                list_title_selector=inferred['list_title_selector'],
                link_selector=inferred['link_selector'],
            )
            jobs = await self.parse_page(content, validation_config)
            if not jobs:
                if not best_failure:
                    best_failure = SelectorSuggestionResult(
                        hospital_name=hospital_name,
                        detail_url=detail_url,
                        candidate_list_urls=candidate_list_urls,
                        selected_list_url=candidate_list_url,
                        base_url=base_url,
                        listing_selector=inferred['listing_selector'],
                        list_title_selector=inferred['list_title_selector'],
                        link_selector=inferred['link_selector'],
                        classification=self._classify_list_page_content(content, validation_config),
                        message='候选列表页已有选择器猜测，但二次验证未提取到稳定公告项。',
                    )
                continue

            detail_match = any((job.get('url') or '').strip() == detail_url for job in jobs)
            family_match = any(self._job_matches_detail_family(job.get('url', ''), detail_url) for job in jobs)
            source_bonus = max(0, len(candidate_list_urls) - index)
            score = len(jobs) + source_bonus + (8 if detail_match else 0) + (4 if family_match else 0)
            duplicate_row = self._find_existing_selector_duplicate(hospital_name, candidate_list_url)
            write_allowed = len(jobs) >= 2 and duplicate_row is None

            result = SelectorSuggestionResult(
                hospital_name=hospital_name,
                detail_url=detail_url,
                candidate_list_urls=candidate_list_urls,
                selected_list_url=candidate_list_url,
                base_url=base_url,
                listing_selector=inferred['listing_selector'],
                list_title_selector=inferred['list_title_selector'],
                link_selector=inferred['link_selector'],
                classification='suggestion_ready' if write_allowed else 'duplicate_or_needs_confirmation',
                confidence=min(0.99, 0.42 + min(len(jobs), 8) * 0.05 + (0.16 if family_match else 0) + (0.12 if detail_match else 0)),
                matched_existing_row=False,
                write_allowed=write_allowed,
                message=(
                    '已生成候选 CSV 行，可在确认后写入 selector.csv。'
                    if write_allowed
                    else '已找到候选配置，但当前更像重复记录或仍需人工确认，暂不自动写入。'
                ),
                jobs=jobs[:5],
                matched_hospital_name=(duplicate_row.get('hospital_name') or '') if duplicate_row else '',
                matched_list_page_url=(duplicate_row.get('list_page_url') or '') if duplicate_row else '',
            )

            if score > best_score:
                best_result = result
                best_score = score

        if best_result:
            return best_result
        if best_failure:
            return best_failure

        return SelectorSuggestionResult(
            hospital_name=hospital_name,
            detail_url=detail_url,
            candidate_list_urls=candidate_list_urls,
            classification='suggestion_failed',
            message='未找到可静态验证的候选列表页，请改用列表页诊断脚本继续排查。',
        )

    def append_selector_row_from_suggestion(self, suggestion: SelectorSuggestionResult) -> Dict[str, Any]:
        if not suggestion.write_allowed:
            return {'written': False, 'reason': 'write_not_allowed', 'row': suggestion.to_csv_row()}

        csv_path = self.selectors_csv_path
        fieldnames = [
            'hospital_name',
            'list_page_url',
            'base_url',
            'listing_selector',
            'list_title_selector',
            'link_selector',
        ]
        row = suggestion.to_csv_row()
        existing_duplicate = self._find_existing_selector_duplicate(
            row['hospital_name'],
            row['list_page_url'],
        )
        if existing_duplicate:
            return {'written': False, 'reason': 'duplicate', 'row': row}

        file_exists = os.path.exists(csv_path)
        file_has_content = file_exists and os.path.getsize(csv_path) > 0

        with open(csv_path, 'a', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if not file_has_content:
                writer.writeheader()
            writer.writerow(row)

        self.csv_rows.append(dict(row))
        return {'written': True, 'reason': 'appended', 'row': row}

    @staticmethod
    def _looks_like_fetch_blocked_page(content: str) -> bool:
        if not content:
            return False
        normalized = content.lower()
        if _looks_like_upstream_error_page(content):
            return True
        markers = (
            'captcha',
            'robot check',
            'access denied',
            'verify you are human',
            '人机验证',
            '访问受限',
            'cloudflare',
            'challenge-form',
        )
        return any(marker in normalized for marker in markers)

    @staticmethod
    def _supports_dynamic_fallback_without_selector(list_page_url: str) -> bool:
        """允许无选择器站点继续进入动态接口兜底流程"""
        if not list_page_url:
            return False

        parsed = urlparse(list_page_url)
        host = (parsed.netloc or '').lower()
        query = parse_qs(parsed.query)
        return (
            host.endswith('cqgwzx.com')
            and parsed.path.rstrip('/') == '/rsrc'
            and bool(query.get('tabDataId'))
            and bool(query.get('tabInfocusId'))
        )

    @staticmethod
    def _slugify_site_name(hospital_name: str, row_index: int) -> str:
        """生成稳定站点ID"""
        slug = re.sub(r'[^a-zA-Z0-9]+', '_', hospital_name.strip().lower()).strip('_')
        if not slug:
            slug = f"site_{row_index}"
        return f"{slug}_{row_index}"

    async def init_session(self):
        """初始化HTTP会话"""
        timeout = ClientTimeout(total=self.global_config.get('crawler', {}).get('request_timeout', 30))
        self.session = ClientSession(
            timeout=timeout,
            headers={
                'User-Agent': self.global_config.get('crawler', {}).get(
                    'user_agent',
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                )
            }
        )

    async def close_session(self):
        """关闭HTTP会话"""
        if self.session:
            await self.session.close()
            self.session = None

    @staticmethod
    def _build_waf_tag_cookie_value() -> str:
        """生成站点滑块验证页使用的 Waf_Tag Cookie 值"""
        return str(int(datetime.now().timestamp() * 1000))

    @staticmethod
    def _needs_waf_tag_cookie(content: str) -> bool:
        """检测是否命中需要补 Waf_Tag 的滑块验证页"""
        if not content:
            return False

        markers = ('人机身份验证', 'Waf_Tag=', 'slider-thumb')
        return all(marker in content for marker in markers)

    def _fetch_with_cloudscraper(
        self,
        url: str,
        config: CrawlerConfig,
        cookies: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """使用cloudscraper兜底获取页面（用于被拦截场景）"""
        try:
            scraper = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
            )
            request_cookies = dict(cookies or {})
            response = scraper.get(
                url,
                timeout=config.request_timeout,
                verify=config.ssl_verify,
                headers={'User-Agent': config.user_agent},
                cookies=request_cookies or None,
            )
            if response.status_code == 200:
                content = _decode_response_body(response.content, response.encoding)
                if self._needs_visited_cookie(content) and 'visited' not in request_cookies:
                    retry_cookies = {**request_cookies, 'visited': '1'}
                    return self._fetch_with_cloudscraper(url, config, retry_cookies)
                if self._needs_waf_tag_cookie(content) and 'Waf_Tag' not in request_cookies:
                    retry_cookies = {
                        **request_cookies,
                        'visited': '1',
                        'Waf_Tag': self._build_waf_tag_cookie_value(),
                    }
                    return self._fetch_with_cloudscraper(url, config, retry_cookies)
                return content

            logger.warning(f"cloudscraper获取失败: {url}, 状态码: {response.status_code}")
            return None
        except Exception as e:
            logger.error(f"cloudscraper获取异常 {url}: {e}")
            return None

    def _fetch_with_curl(
        self,
        url: str,
        config: CrawlerConfig,
        cookies: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """使用系统 curl 兜底获取页面，处理 Python DNS/TLS 异常站点"""
        try:
            cmd = [
                'curl',
                '-L',
                '--compressed',
                '--silent',
                '--show-error',
                '--max-time',
                str(max(int(config.request_timeout), 10)),
                '-A',
                config.user_agent,
            ]
            if not config.ssl_verify:
                cmd.append('-k')

            request_cookies = dict(cookies or {})
            if request_cookies:
                cookie_header = '; '.join(
                    f"{key}={value}" for key, value in request_cookies.items() if key and value is not None
                )
                if cookie_header:
                    cmd.extend(['-b', cookie_header])

            cmd.append(url)
            result = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
                timeout=max(int(config.request_timeout), 10) + 5,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode('utf-8', errors='ignore').strip()
                logger.warning(f"curl兜底失败: {url}, exit={result.returncode}, stderr={stderr}")
                return None

            content = _decode_response_body(result.stdout, None)
            if self._needs_visited_cookie(content) and 'visited' not in request_cookies:
                retry_cookies = {**request_cookies, 'visited': '1'}
                return self._fetch_with_curl(url, config, retry_cookies)
            if self._needs_waf_tag_cookie(content) and 'Waf_Tag' not in request_cookies:
                retry_cookies = {
                    **request_cookies,
                    'visited': '1',
                    'Waf_Tag': self._build_waf_tag_cookie_value(),
                }
                return self._fetch_with_curl(url, config, retry_cookies)
            return content
        except Exception as e:
            logger.error(f"curl兜底异常 {url}: {e}")
            return None

    @staticmethod
    def _needs_visited_cookie(content: str) -> bool:
        """检测是否命中访问引导页（需要visited=1 Cookie）"""
        if not content:
            return False
        markers = ('visited=1', 'document', 'cookie', 'reload')
        return all(marker in content for marker in markers)

    @staticmethod
    def _looks_like_ruifox_dynamic_list(content: str, url: str) -> bool:
        """识别 Ruifox CMS 的空壳列表页"""
        if not content or not url:
            return False

        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        has_list_query = bool(query.get('mId')) and bool(query.get('page'))
        page_shell_markers = (
            'id="pagerAppender"',
            'indexJS/page/list.js',
            'Ruifox Inc.',
        )

        return has_list_query and all(marker in content for marker in page_shell_markers)

    @staticmethod
    def _build_ruifox_api_url(list_url: str) -> tuple[Optional[str], str]:
        """从列表页URL推导 Ruifox 列表接口地址"""
        parsed = urlparse(list_url)
        query = parse_qs(parsed.query)
        m_id = (query.get('mId') or [None])[0]
        page = (query.get('page') or ['1'])[0]
        if not m_id:
            return None, page

        api_url = f"{parsed.scheme}://{parsed.netloc}/api/pageIndex/menuList?page={page}&mId={m_id}"
        return api_url, page

    @staticmethod
    def _looks_like_ruifox_recruitment_notice_shell(content: str, url: str) -> bool:
        """识别 Ruifox 招聘系统的前端公告空壳页"""
        if not content or not url:
            return False

        parsed = urlparse(url)
        path = parsed.path.rstrip('/')
        shell_markers = (
            '<div id="app"></div>',
            '/recruitment/js/app.',
            '/recruitment/js/chunk-vendors.',
            '<title>招聘系统</title>',
        )

        return path.endswith('/recruitment/recruitment/user/notice') and all(
            marker in content for marker in shell_markers
        )

    @staticmethod
    def _build_ruifox_recruitment_notice_api_url(list_url: str) -> Optional[str]:
        """构造 Ruifox 招聘系统公告列表接口"""
        parsed = urlparse(list_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}/api/recruitment/notice/selectPageFront"

    @staticmethod
    def _build_ruifox_recruitment_notice_detail_url(list_url: str, notice_id: Any) -> Optional[str]:
        """根据公告ID拼接前台公告详情页URL"""
        if notice_id in (None, ''):
            return None

        parsed = urlparse(list_url)
        if not parsed.scheme or not parsed.netloc:
            return None

        detail_query = urlencode({'id': str(notice_id)})
        return f"{parsed.scheme}://{parsed.netloc}/recruitment/recruitment/user/noticeDetail?{detail_query}"

    @staticmethod
    def _extract_hash_route_info(url: str) -> tuple[str, Dict[str, List[str]]]:
        """提取hash路由路径与查询参数"""
        fragment = urlparse(url).fragment or ''
        if not fragment:
            return '', {}

        route, _, query = fragment.partition('?')
        route = f"/{route.lstrip('/')}" if route else ''
        return route, parse_qs(query, keep_blank_values=True)

    @classmethod
    def _looks_like_sns120_dynamic_list(cls, content: str, url: str) -> bool:
        """识别遂宁市中心医院的Vue列表页"""
        if not content or not url:
            return False

        route, query = cls._extract_hash_route_info(url)
        shell_markers = (
            '<div id="app">',
            '/assets/index.',
            '<title>遂宁市中心医院</title>',
        )
        return route == '/column-list' and bool(query.get('columnId')) and all(
            marker in content for marker in shell_markers
        )

    @staticmethod
    def _looks_like_666120_jobs_shell(content: str, url: str) -> bool:
        """识别宜宾市第三人民医院招聘信息前端壳页面"""
        if not content or not url:
            return False

        parsed = urlparse(url)
        path = parsed.path.rstrip('/') or '/'
        shell_markers = (
            '<div id="app"></div>',
            '/assets/index-',
            '<title>宜宾市第三人民医院</title>',
        )
        return path == '/zpxx' and all(marker in content for marker in shell_markers)

    @staticmethod
    def _looks_like_cqgwzx_dynamic_list(content: str, url: str) -> bool:
        """识别重庆市公共卫生医疗救治中心的人事人才前端壳页面"""
        if not content or not url:
            return False

        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        shell_markers = (
            '<div id="app"></div>',
            '/assets/index-',
            '重庆市公共卫生医疗',
        )
        return (
            parsed.netloc.lower().endswith('cqgwzx.com')
            and parsed.path.rstrip('/') == '/rsrc'
            and bool(query.get('tabDataId'))
            and bool(query.get('tabInfocusId'))
            and all(marker in content for marker in shell_markers)
        )

    @staticmethod
    def _resolve_cqgwzx_article_type(list_url: str) -> Optional[str]:
        """将路由参数 tabInfocusId 解析为接口 type"""
        query = parse_qs(urlparse(list_url).query)
        tab_infocus_id = (query.get('tabInfocusId') or [None])[0]
        article_type_map = {
            '62': 'zpgg',
            '63': 'bsh',
            '64': 'jxjy',
            '65': 'jxxx',
        }
        return article_type_map.get(str(tab_infocus_id)) if tab_infocus_id is not None else None

    @staticmethod
    def _build_cqgwzx_api_headers(referer: str) -> Dict[str, str]:
        """构造 cqgwzx 前端接口访问头"""
        parsed = urlparse(referer)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else 'https://www.cqgwzx.com'
        return {
            'Accept': 'application/json, text/plain, */*',
            'Origin': origin,
            'Referer': referer,
            'fingerprint': 'codex-crawler',
        }

    @staticmethod
    def _build_cqgwzx_detail_url(list_url: str, article_id: Any) -> Optional[str]:
        """根据文章ID拼接 cqgwzx 详情页URL"""
        if article_id in (None, ''):
            return None

        parsed = urlparse(list_url)
        if not parsed.scheme or not parsed.netloc:
            return None

        return f"{parsed.scheme}://{parsed.netloc}/article-detail?{urlencode({'id': str(article_id)})}"

    async def _fetch_cqgwzx_dynamic_jobs(self, config: CrawlerConfig) -> List[Dict[str, Any]]:
        """通过 cqgwzx 的文章接口抓取前端渲染列表"""
        if not self.session:
            await self.init_session()

        article_type = self._resolve_cqgwzx_article_type(config.start_url)
        if not article_type:
            logger.warning(f"cqgwzx动态页未识别到栏目type: {config.start_url}")
            return []

        parsed = urlparse(config.start_url)
        api_url = f"{parsed.scheme}://{parsed.netloc}/prod-api/api/article/getPageListWeb"
        headers = self._build_cqgwzx_api_headers(config.start_url)
        params = {
            'current': 1,
            'size': 20,
            'type': article_type,
        }

        try:
            async with self.session.get(
                api_url,
                params=params,
                headers=headers,
                ssl=config.ssl_verify,
            ) as response:
                if response.status != 200:
                    logger.warning(f"cqgwzx接口请求失败: {api_url}, 状态码: {response.status}")
                    return []

                body = await response.read()
                content = _decode_response_body(body, response.charset)
        except Exception as e:
            logger.warning(f"cqgwzx接口请求异常: {api_url}, 错误: {e}")
            return []

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"cqgwzx接口返回非JSON: {e}")
            return []

        if payload.get('code') != 200:
            logger.warning(
                "cqgwzx接口返回失败: "
                f"code={payload.get('code')}, msg={payload.get('msg')}, type={article_type}"
            )
            return []

        data = payload.get('data') or {}
        records = data.get('list')
        if not isinstance(records, list):
            logger.warning("cqgwzx接口未返回 list 列表")
            return []

        hospital = (
            config.parsing.get('list_page', {})
            .get('fields', {})
            .get('hospital', {})
            .get('default')
        )

        jobs_data: List[Dict[str, Any]] = []
        for item in records:
            if not isinstance(item, dict):
                continue

            title = str(item.get('title') or '').strip()
            detail_url = self._build_cqgwzx_detail_url(config.start_url, item.get('id'))
            if not title or not detail_url:
                continue

            jobs_data.append({
                'title': title,
                'url': detail_url,
                'publish_date': item.get('publishTime') or item.get('createTime'),
                'hospital': hospital,
                'source_site': config.site_name,
                'crawl_time': datetime.now(),
            })

        logger.info(
            f"cqgwzx接口兜底成功: site={config.site_name}, type={article_type}, jobs={len(jobs_data)}"
        )
        return jobs_data

    async def _fetch_666120_dynamic_jobs(self, config: CrawlerConfig) -> List[Dict[str, Any]]:
        """使用浏览器渲染宜宾市第三人民医院招聘页并提取详情链接"""
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait
        except Exception as e:
            logger.warning(f"666120动态浏览器依赖不可用: {e}")
            return []

        hospital = (
            config.parsing.get('list_page', {})
            .get('fields', {})
            .get('hospital', {})
            .get('default')
        )

        try:
            if not self.waf_detector.driver:
                await self.waf_detector._init_browser_driver()

            driver = self.waf_detector.driver
            if not driver:
                return []

            driver.get(config.start_url)
            WebDriverWait(driver, 15).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[href^="/zpxx_detail?id="]'))
            )

            anchors = driver.find_elements(By.CSS_SELECTOR, 'a[href^="/zpxx_detail?id="]')
            jobs_data: List[Dict[str, Any]] = []
            seen_urls: set[str] = set()

            for anchor in anchors:
                href = (anchor.get_attribute('href') or '').strip()
                title = (anchor.text or '').replace('\u200b', '').strip()
                if not href:
                    raw_href = (anchor.get_attribute('href') or '').strip()
                    href = urljoin(config.base_url, raw_href)
                if not title or not href or href in seen_urls:
                    continue

                seen_urls.add(href)
                jobs_data.append({
                    'title': title,
                    'url': href,
                    'hospital': hospital,
                    'source_site': config.site_name,
                    'crawl_time': datetime.now(),
                })

            if jobs_data:
                logger.info(f"666120动态浏览器兜底成功: site={config.site_name}, jobs={len(jobs_data)}")
            return jobs_data
        except Exception as e:
            logger.warning(f"666120动态浏览器兜底失败: site={config.site_name}, error={e}")
            return []
        finally:
            await self.waf_detector.cleanup()

    async def _fetch_sns120_dynamic_jobs(self, config: CrawlerConfig) -> List[Dict[str, Any]]:
        """使用真实浏览器点击列表项，恢复无href的详情URL"""
        try:
            from selenium.common.exceptions import TimeoutException
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait
        except Exception as e:
            logger.warning(f"sns120动态浏览器依赖不可用: {e}")
            return []

        expected_route, expected_query = self._extract_hash_route_info(config.start_url)
        expected_column_id = (expected_query.get('columnId') or [None])[0]

        try:
            if not self.waf_detector.driver:
                await self.waf_detector._init_browser_driver()

            driver = self.waf_detector.driver
            if not driver:
                return []

            driver.get(config.start_url)
            WebDriverWait(driver, 12).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, 'div.doc-item') or d.current_url != config.start_url
            )

            loaded_route, loaded_query = self._extract_hash_route_info(driver.current_url)
            loaded_column_id = (loaded_query.get('columnId') or [None])[0]
            if (
                expected_route == '/column-list'
                and expected_column_id
                and loaded_route == '/column-list'
                and loaded_column_id
                and loaded_column_id != expected_column_id
            ):
                logger.warning(
                    "sns120动态页已跳转到其他栏目，放弃提取以避免误抓: "
                    f"expected_column_id={expected_column_id}, loaded_column_id={loaded_column_id}"
                )
                return []

            hospital = (
                config.parsing.get('list_page', {})
                .get('fields', {})
                .get('hospital', {})
                .get('default')
            )

            jobs_data: List[Dict[str, Any]] = []
            seen_urls: set[str] = set()

            initial_items = driver.find_elements(By.CSS_SELECTOR, 'div.doc-item')
            for index in range(min(len(initial_items), 20)):
                WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'div.doc-item'))
                )
                items = driver.find_elements(By.CSS_SELECTOR, 'div.doc-item')
                if index >= len(items):
                    break

                item = items[index]
                title = ''
                publish_date = None

                try:
                    title = item.find_element(By.CSS_SELECTOR, 'span.desc').text.strip()
                except Exception:
                    title = item.text.strip().splitlines()[0] if item.text.strip() else ''

                try:
                    publish_date = item.find_element(By.CSS_SELECTOR, 'span.time').text.strip()
                except Exception:
                    publish_date = None

                if not title:
                    continue

                before_url = driver.current_url
                driver.execute_script("arguments[0].click();", item)

                try:
                    WebDriverWait(driver, 10).until(
                        lambda d: d.current_url != before_url and 'detailsId=' in d.current_url
                    )
                except TimeoutException:
                    logger.warning(f"sns120列表项点击后未进入详情页: title={title}")
                    continue

                detail_url = driver.current_url
                if detail_url not in seen_urls:
                    seen_urls.add(detail_url)
                    jobs_data.append({
                        'title': title,
                        'url': detail_url,
                        'publish_date': publish_date,
                        'hospital': hospital,
                        'source_site': config.site_name,
                        'crawl_time': datetime.now(),
                    })

                driver.back()
                WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'div.doc-item'))
                )

            if jobs_data:
                logger.info(f"sns120动态浏览器兜底成功: site={config.site_name}, jobs={len(jobs_data)}")
            return jobs_data
        except Exception as e:
            logger.warning(f"sns120动态浏览器兜底失败: site={config.site_name}, error={e}")
            return []
        finally:
            await self.waf_detector.cleanup()

    async def _fetch_ruifox_recruitment_group_id(self, config: CrawlerConfig) -> str:
        """尽量获取招聘系统 groupId，失败时回退到默认值 1"""
        parsed = urlparse(config.start_url)
        host_candidates = []
        if parsed.hostname:
            host_candidates.append(parsed.hostname)
            normalized = parsed.hostname.removeprefix('www.')
            if normalized != parsed.hostname:
                host_candidates.append(normalized)

        for title in host_candidates:
            api_url = f"{parsed.scheme}://{parsed.netloc}/api/auth/indexSetting/selectByTitle?{urlencode({'title': title})}"
            content = await self.fetch_page(api_url, config)
            if not content:
                continue

            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue

            data = payload.get('data') or {}
            group_id = data.get('groupId')
            if group_id not in (None, ''):
                return str(group_id)

        return '1'

    @staticmethod
    def _extract_ruifox_recruitment_notice_records(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从常见分页JSON结构里提取公告记录列表"""
        if not isinstance(payload, dict):
            return []

        candidates: List[Any] = []
        data = payload.get('data')
        if data is not None:
            candidates.append(data)

        candidates.append(payload)

        for candidate in candidates:
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]

            if not isinstance(candidate, dict):
                continue

            for key in ('records', 'rows', 'list', 'data', 'items'):
                value = candidate.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]

        return []

    async def _post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        config: CrawlerConfig,
    ) -> Optional[Dict[str, Any]]:
        """发送JSON POST请求并解析JSON响应"""
        if not self.session:
            await self.init_session()

        try:
            async with self.session.post(
                url,
                ssl=config.ssl_verify,
                json=payload,
                headers={'Content-Type': 'application/json'},
            ) as response:
                if response.status != 200:
                    logger.warning(f"POST接口请求失败: {url}, 状态码: {response.status}")
                    return None

                body = await response.read()
                content = _decode_response_body(body, response.charset)
                return json.loads(content)
        except Exception as e:
            logger.warning(f"POST接口请求异常: {url}, 错误: {e}")
            return None

    async def _fetch_ruifox_recruitment_notice_jobs(self, config: CrawlerConfig) -> List[Dict[str, Any]]:
        """抓取 Ruifox 招聘系统前台公告接口"""
        api_url = self._build_ruifox_recruitment_notice_api_url(config.start_url)
        if not api_url:
            return []

        group_id = await self._fetch_ruifox_recruitment_group_id(config)
        payload = {
            'currentPage': 1,
            'pageSize': 20,
            'likeParams': {},
            'groupId': group_id,
            'noticeTypeId': '',
        }

        logger.info(f"尝试Ruifox招聘公告接口兜底: {api_url}, groupId={group_id}")
        result = await self._post_json(api_url, payload, config)
        if not result:
            return []

        records = self._extract_ruifox_recruitment_notice_records(result)
        hospital = (
            config.parsing.get('list_page', {})
            .get('fields', {})
            .get('hospital', {})
            .get('default')
        )

        jobs_data: List[Dict[str, Any]] = []
        for item in records:
            title = str(item.get('title') or '').strip()
            detail_url = self._build_ruifox_recruitment_notice_detail_url(
                config.start_url,
                item.get('id'),
            )
            if not title or not detail_url:
                continue

            jobs_data.append({
                'title': title,
                'url': detail_url,
                'publish_date': item.get('publish_time'),
                'hospital': hospital,
                'source_site': config.site_name,
                'crawl_time': datetime.now(),
            })

        logger.info(f"Ruifox招聘公告接口兜底成功: site={config.site_name}, jobs={len(jobs_data)}")
        return jobs_data

    async def _fetch_ruifox_jobs(self, config: CrawlerConfig) -> List[Dict[str, Any]]:
        """抓取并解析 Ruifox CMS 的列表接口"""
        api_url, fallback_page = self._build_ruifox_api_url(config.start_url)
        if not api_url:
            return []

        logger.info(f"尝试Ruifox接口兜底: {api_url}")
        api_content = await self.fetch_page(api_url, config)
        if not api_content:
            return []

        try:
            payload = json.loads(api_content)
        except json.JSONDecodeError as e:
            logger.warning(f"Ruifox接口返回非JSON: {e}")
            return []

        index_main = payload.get('indexMain')
        if not isinstance(index_main, list):
            logger.warning("Ruifox接口未返回 indexMain 列表")
            return []

        page = str(payload.get('page') or fallback_page or '1')
        hospital = (
            config.parsing.get('list_page', {})
            .get('fields', {})
            .get('hospital', {})
            .get('default')
        )

        jobs_data: List[Dict[str, Any]] = []
        for item in index_main:
            title = str(item.get('artTitle') or '').strip()
            art_id = item.get('artId')
            if not title or art_id in (None, ''):
                continue

            jobs_data.append({
                'title': title,
                'url': f"{config.base_url.rstrip('/')}/pageInfoArt.html?artId={art_id}&page={page}",
                'hospital': hospital,
                'source_site': config.site_name,
                'crawl_time': datetime.now(),
            })

        logger.info(f"Ruifox接口兜底成功: site={config.site_name}, jobs={len(jobs_data)}")
        return jobs_data

    async def _fetch_page_impl(
        self,
        url: str,
        config: CrawlerConfig,
        retry_count: int = 0,
        visited_cookie: bool = False,
        waf_tag_cookie: bool = False,
        extra_cookies: Optional[Dict[str, str]] = None,
        yunsuo_verified: bool = False,
        safeline_verified: bool = False,
    ) -> Dict[str, Any]:
        """获取页面内容并返回结构化抓取元数据"""
        if not self.session:
            await self.init_session()

        result: Dict[str, Any] = {
            'success': False,
            'content': None,
            'fetch_backend': 'aiohttp',
            'failure_category': '',
            'error_message': '',
            'status_code': None,
            'waf_detected': False,
            'waf_type': None,
            'retry_count': retry_count,
            'final_url': url,
        }
        cookies: Dict[str, str] = dict(extra_cookies or {})

        try:
            delay = config.delay_between_requests * (1 + random.random() * 0.5)
            await asyncio.sleep(delay)

            waf_status = await self.waf_detector.detect_waf(url, self.session, ssl=config.ssl_verify)
            result['waf_detected'] = bool(waf_status.get('has_waf'))
            result['waf_type'] = waf_status.get('waf_type')
            if waf_status['has_waf']:
                logger.warning(f"检测到WAF: {waf_status['waf_type']} for {url}")
                bypass_result = await self.waf_detector.apply_bypass_strategy(
                    url, waf_status, config.waf_strategy
                )
                if bypass_result:
                    url = bypass_result.get('url', url)
                    result['final_url'] = url
                    if bypass_result.get('html'):
                        result.update({
                            'success': True,
                            'content': bypass_result.get('html'),
                            'fetch_backend': bypass_result.get('method') or 'dynamic_browser',
                            'failure_category': '',
                            'error_message': '',
                        })
                        return result

            if visited_cookie:
                cookies['visited'] = '1'
            if waf_tag_cookie:
                cookies['visited'] = '1'
                cookies['Waf_Tag'] = self._build_waf_tag_cookie_value()

            async with self.session.get(url, ssl=config.ssl_verify, cookies=cookies) as response:
                result['status_code'] = response.status
                if response.status == 200:
                    body = await response.read()
                    content = _decode_response_body(body, response.charset)
                    if _looks_like_upstream_error_page(content):
                        logger.warning(f"检测到上游错误页: {url}")
                        if retry_count < config.max_retries:
                            logger.info(f"重试 {retry_count + 1}/{config.max_retries}")
                            return await self._fetch_page_impl(
                                url,
                                config,
                                retry_count + 1,
                                visited_cookie=visited_cookie,
                                waf_tag_cookie=waf_tag_cookie,
                                extra_cookies=cookies,
                                yunsuo_verified=yunsuo_verified,
                                safeline_verified=safeline_verified,
                            )
                        result['failure_category'] = 'upstream_error'
                        result['error_message'] = '检测到上游错误页'
                        return result
                    if _looks_like_yunsuo_verify_page(content) and not yunsuo_verified:
                        verify_url = _build_yunsuo_verify_url(url)
                        verify_cookies = {
                            **cookies,
                            'srcurl': _string_to_hex(url),
                        }
                        logger.info(f"检测到云锁验证页，携带校验参数重试: {verify_url}")
                        return await self._fetch_page_impl(
                            verify_url,
                            config,
                            retry_count=retry_count,
                            visited_cookie=visited_cookie,
                            waf_tag_cookie=waf_tag_cookie,
                            extra_cookies=verify_cookies,
                            yunsuo_verified=True,
                            safeline_verified=safeline_verified,
                        )
                    if _looks_like_safeline_js_challenge(content) and not safeline_verified:
                        challenge_cookie = None
                        response_challenge_cookie = response.cookies.get('safeline_bot_challenge')
                        if response_challenge_cookie is not None:
                            challenge_cookie = response_challenge_cookie.value

                        if not challenge_cookie:
                            challenge_cookie = cookies.get('safeline_bot_challenge')

                        if not challenge_cookie and self.session:
                            jar_cookies = self.session.cookie_jar.filter_cookies(response.url)
                            jar_cookie = jar_cookies.get('safeline_bot_challenge')
                            if jar_cookie is not None:
                                challenge_cookie = jar_cookie.value

                        prefix, leading_zero_bit = _extract_safeline_js_challenge(content)
                        suffix = (
                            _solve_safeline_js_challenge(prefix, leading_zero_bit)
                            if prefix and leading_zero_bit is not None and challenge_cookie
                            else None
                        )
                        if suffix:
                            verify_cookies = {
                                **cookies,
                                'safeline_bot_challenge': challenge_cookie,
                                'safeline_bot_challenge_ans': f"{challenge_cookie}{suffix}",
                            }
                            logger.info(
                                "检测到SafeLine JS Challenge，自动求解后重试: "
                                f"url={url}, leading_zero_bit={leading_zero_bit}, suffix={suffix}"
                            )
                            return await self._fetch_page_impl(
                                url,
                                config,
                                retry_count=retry_count,
                                visited_cookie=visited_cookie,
                                waf_tag_cookie=waf_tag_cookie,
                                extra_cookies=verify_cookies,
                                yunsuo_verified=yunsuo_verified,
                                safeline_verified=True,
                            )

                        logger.warning(
                            "SafeLine JS Challenge 求解失败: "
                            f"url={url}, has_prefix={bool(prefix)}, has_cookie={bool(challenge_cookie)}"
                        )
                    if self._needs_visited_cookie(content) and not visited_cookie:
                        logger.info(f"检测到访问引导页，携带visited=1 Cookie重试: {url}")
                        return await self._fetch_page_impl(
                            url,
                            config,
                            retry_count=retry_count,
                            visited_cookie=True,
                            waf_tag_cookie=waf_tag_cookie,
                            extra_cookies=cookies,
                            yunsuo_verified=yunsuo_verified,
                            safeline_verified=safeline_verified,
                        )
                    if self._needs_waf_tag_cookie(content) and not waf_tag_cookie:
                        logger.info(f"检测到滑块验证页，携带Waf_Tag Cookie重试: {url}")
                        return await self._fetch_page_impl(
                            url,
                            config,
                            retry_count=retry_count,
                            visited_cookie=True,
                            waf_tag_cookie=True,
                            extra_cookies=cookies,
                            yunsuo_verified=yunsuo_verified,
                            safeline_verified=safeline_verified,
                        )
                    logger.debug(f"成功获取页面: {url}")
                    result.update({
                        'success': True,
                        'content': content,
                        'fetch_backend': 'aiohttp',
                        'failure_category': '',
                        'error_message': '',
                    })
                    return result

                if response.status == 403:
                    logger.warning(f"aiohttp请求被拦截，尝试cloudscraper兜底: {url}")
                    fallback_content = await asyncio.to_thread(
                        self._fetch_with_cloudscraper,
                        url,
                        config,
                        cookies,
                    )
                    if fallback_content:
                        logger.info(f"cloudscraper兜底成功: {url}")
                        result.update({
                            'success': True,
                            'content': fallback_content,
                            'fetch_backend': 'cloudscraper',
                            'failure_category': '',
                            'error_message': '',
                        })
                        return result
                    logger.warning(f"cloudscraper兜底失败，尝试curl兜底: {url}")
                    curl_content = await asyncio.to_thread(
                        self._fetch_with_curl,
                        url,
                        config,
                        cookies,
                    )
                    if curl_content:
                        logger.info(f"curl兜底成功: {url}")
                        result.update({
                            'success': True,
                            'content': curl_content,
                            'fetch_backend': 'curl',
                            'failure_category': '',
                            'error_message': '',
                        })
                        return result

                logger.warning(f"获取页面失败: {url}, 状态码: {response.status}")
                if retry_count < config.max_retries:
                    logger.info(f"重试 {retry_count + 1}/{config.max_retries}")
                    return await self._fetch_page_impl(
                        url,
                        config,
                        retry_count + 1,
                        visited_cookie=visited_cookie,
                        waf_tag_cookie=waf_tag_cookie,
                        extra_cookies=cookies,
                        yunsuo_verified=yunsuo_verified,
                        safeline_verified=safeline_verified,
                    )
                result['failure_category'] = self._classify_fetch_failure('', status_code=response.status)
                result['error_message'] = f'HTTP {response.status}'
                return result

        except ClientError as e:
            logger.error(f"网络错误获取页面 {url}: {e}")
            if retry_count < config.max_retries:
                await asyncio.sleep(2 ** retry_count)
                return await self._fetch_page_impl(
                    url,
                    config,
                    retry_count + 1,
                    visited_cookie=visited_cookie,
                    waf_tag_cookie=waf_tag_cookie,
                    extra_cookies=cookies,
                    yunsuo_verified=yunsuo_verified,
                    safeline_verified=safeline_verified,
                )
            logger.warning(f"aiohttp网络错误，尝试cloudscraper兜底: {url}")
            fallback_content = await asyncio.to_thread(
                self._fetch_with_cloudscraper,
                url,
                config,
                cookies,
            )
            if fallback_content:
                logger.info(f"cloudscraper网络错误兜底成功: {url}")
                result.update({
                    'success': True,
                    'content': fallback_content,
                    'fetch_backend': 'cloudscraper',
                    'failure_category': '',
                    'error_message': '',
                })
                return result
            logger.warning(f"cloudscraper网络错误兜底失败，尝试curl兜底: {url}")
            curl_content = await asyncio.to_thread(
                self._fetch_with_curl,
                url,
                config,
                cookies,
            )
            if curl_content:
                logger.info(f"curl网络错误兜底成功: {url}")
                result.update({
                    'success': True,
                    'content': curl_content,
                    'fetch_backend': 'curl',
                    'failure_category': '',
                    'error_message': '',
                })
                return result
            result['failure_category'] = self._classify_fetch_failure(str(e))
            result['error_message'] = str(e)
            return result
        except Exception as e:
            logger.error(f"获取页面异常 {url}: {e}")
            result['failure_category'] = self._classify_fetch_failure(str(e))
            result['error_message'] = str(e)
            return result

    async def fetch_page_with_metadata(
        self,
        url: str,
        config: CrawlerConfig,
    ) -> Dict[str, Any]:
        """结构化抓取页面，返回内容与抓取元信息"""
        return await self._fetch_page_impl(url, config)

    async def fetch_page(
        self,
        url: str,
        config: CrawlerConfig,
        retry_count: int = 0,
        visited_cookie: bool = False,
        waf_tag_cookie: bool = False,
        extra_cookies: Optional[Dict[str, str]] = None,
        yunsuo_verified: bool = False,
        safeline_verified: bool = False,
    ) -> Optional[str]:
        """兼容旧调用方式，仅返回页面内容"""
        result = await self._fetch_page_impl(
            url,
            config,
            retry_count=retry_count,
            visited_cookie=visited_cookie,
            waf_tag_cookie=waf_tag_cookie,
            extra_cookies=extra_cookies,
            yunsuo_verified=yunsuo_verified,
            safeline_verified=safeline_verified,
        )
        return result.get('content')

    async def crawl_site(self, site_name: str, incremental: bool = True) -> Dict[str, Any]:
        """爬取单个网站"""
        if site_name not in self.site_configs:
            logger.error(f"网站配置不存在: {site_name}")
            return {'success': False, 'error': f'Config not found for {site_name}'}

        config = self.site_configs[site_name]
        if not config.enabled:
            logger.info(f"网站已禁用: {site_name}")
            return {'success': False, 'error': f'Site {site_name} is disabled'}

        logger.info(f"开始爬取网站: {site_name}")

        try:
            if not self.session:
                await self.init_session()

            fetch_result = await self.fetch_page_with_metadata(config.start_url, config)
            content = fetch_result.get('content')
            if not content:
                failure_category = fetch_result.get('failure_category') or 'fetch_failed'
                error_message = fetch_result.get('error_message') or 'Failed to fetch page'
                await db_manager.update_crawl_status(site_name, 'failed', error_message)
                return {
                    'success': False,
                    'error': error_message,
                    'failure_category': failure_category,
                    'fetch_backend': fetch_result.get('fetch_backend'),
                }

            jobs_data = await self.parse_page(content, config)
            jobs_data, filtered_count = self.filter_jobs(jobs_data, site_name)
            if not jobs_data:
                logger.warning(f"网站 {site_name} 未解析到职位数据")
                await db_manager.update_crawl_status(site_name, 'success', new_jobs=0)
                return {
                    'success': True,
                    'jobs_count': 0,
                    'new_jobs': 0,
                    'updated_jobs': 0,
                    'filtered_jobs': filtered_count,
                    'site_name': site_name,
                    'jobs': [],
                    'new_jobs_data': [],
                }

            new_jobs_count, new_jobs_data, updated_jobs_count = await self.process_and_save_jobs(
                jobs_data,
                site_name,
                incremental=incremental,
            )

            await db_manager.update_crawl_status(site_name, 'success', new_jobs=new_jobs_count)

            logger.info(
                "网站爬取完成: %s, 解析=%s, 过滤=%s, 新职位=%s, 更新=%s",
                site_name,
                len(jobs_data),
                filtered_count,
                new_jobs_count,
                updated_jobs_count,
            )
            return {
                'success': True,
                'jobs_count': len(jobs_data),
                'new_jobs': new_jobs_count,
                'updated_jobs': updated_jobs_count,
                'filtered_jobs': filtered_count,
                'site_name': site_name,
                'fetch_backend': fetch_result.get('fetch_backend'),
                'jobs': jobs_data[:5],
                'new_jobs_data': new_jobs_data,
            }

        except Exception as e:
            logger.error(f"爬取网站 {site_name} 失败: {e}")
            await db_manager.update_crawl_status(site_name, 'failed', str(e))
            return {'success': False, 'error': str(e), 'site_name': site_name}

    async def parse_page(self, content: str, config: CrawlerConfig) -> List[Dict[str, Any]]:
        """解析页面内容（仅提取title+url）"""
        try:
            spider_config = {
                'site_name': config.site_name,
                'base_url': config.base_url,
                'parsing': config.parsing,
                'validation': config.validation,
            }
            spider = BaseSpider(spider_config)
            parsed_jobs = spider.parse_list_page(content)

            jobs_data: List[Dict[str, Any]] = []
            for job in parsed_jobs:
                title = (job.get('title') or '').strip()
                url = (job.get('url') or '').strip()
                if not title or not url:
                    continue

                jobs_data.append({
                    'title': title,
                    'url': url,
                    'hospital': job.get('hospital'),
                    'source_site': config.site_name,
                    'crawl_time': datetime.now(),
                })

            if not jobs_data and self._looks_like_ruifox_dynamic_list(content, config.start_url):
                jobs_data = await self._fetch_ruifox_jobs(config)

            if not jobs_data and self._looks_like_ruifox_recruitment_notice_shell(content, config.start_url):
                jobs_data = await self._fetch_ruifox_recruitment_notice_jobs(config)

            if not jobs_data and self._looks_like_sns120_dynamic_list(content, config.start_url):
                jobs_data = await self._fetch_sns120_dynamic_jobs(config)

            if not jobs_data and self._looks_like_cqgwzx_dynamic_list(content, config.start_url):
                jobs_data = await self._fetch_cqgwzx_dynamic_jobs(config)

            if not jobs_data and self._looks_like_666120_jobs_shell(content, config.start_url):
                jobs_data = await self._fetch_666120_dynamic_jobs(config)

            logger.info(f"页面解析完成: site={config.site_name}, jobs={len(jobs_data)}")
            return jobs_data

        except Exception as e:
            logger.error(f"解析页面失败: {e}")
            return []

    def _title_matches_keywords(self, title: str, keywords: List[str]) -> bool:
        """判断标题是否命中任一关键词"""
        normalized_title = (title or '').strip().lower()
        return any(keyword and keyword.lower() in normalized_title for keyword in keywords)

    def _should_keep_job(self, job: Dict[str, Any]) -> bool:
        """按招聘过滤规则判断是否保留职位"""
        title = (job.get('title') or '').strip()
        if not title or not self.recruitment_filter_enabled:
            return bool(title)

        if self._title_matches_keywords(title, self.recruitment_exclude_keywords):
            return False

        if self._title_matches_keywords(title, self.recruitment_keep_keywords):
            return True

        return True

    def filter_jobs(
        self,
        jobs_data: List[Dict[str, Any]],
        site_name: Optional[str] = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """对提取结果应用招聘过滤规则"""
        if not self.recruitment_filter_enabled:
            return jobs_data, 0

        filtered_jobs: List[Dict[str, Any]] = []
        filtered_count = 0
        for job_data in jobs_data:
            if self._should_keep_job(job_data):
                filtered_jobs.append(job_data)
            else:
                filtered_count += 1

        if filtered_count:
            logger.info(
                "招聘过滤已生效: site=%s, kept=%s, filtered=%s",
                site_name or 'unknown',
                len(filtered_jobs),
                filtered_count,
            )

        return filtered_jobs, filtered_count

    async def process_and_save_jobs(
        self,
        jobs_data: List[Dict[str, Any]],
        site_name: str,
        incremental: bool = True,
    ) -> tuple[int, List[Dict[str, Any]], int]:
        """处理并保存职位数据，返回(新增数, 新增职位列表, 更新数)"""
        new_jobs_count = 0
        new_jobs_data: List[Dict[str, Any]] = []
        updated_jobs_count = 0

        for job_data in jobs_data:
            job_data['source_site'] = site_name

            required_fields = ['title', 'url']
            if not all(field in job_data and job_data[field] for field in required_fields):
                logger.warning(f"职位数据缺少必要字段: {job_data.get('title', 'Unknown')}")
                continue

            saved_job, is_new = await db_manager.save_or_update_job(
                job_data,
                update_existing=not incremental,
            )
            if not saved_job:
                continue

            if is_new:
                new_jobs_count += 1
                new_jobs_data.append(saved_job.to_dict())
            elif not incremental:
                updated_jobs_count += 1

        return new_jobs_count, new_jobs_data, updated_jobs_count

    async def crawl_all_sites(
        self,
        incremental: bool = True,
        force: bool = False,
    ) -> Dict[str, Any]:
        """爬取所有启用的网站"""
        results = {
            'total_sites': len(self.site_configs),
            'successful': 0,
            'failed': 0,
            'skipped': 0,
            'total_new_jobs': 0,
            'total_updated_jobs': 0,
            'total_filtered_jobs': 0,
            'site_results': {},
            'new_jobs_data': [],
        }

        enabled_sites = [
            (config.priority, site_name, config)
            for site_name, config in self.site_configs.items()
            if config.enabled
        ]
        enabled_sites.sort(key=lambda x: x[0])

        if not self.session:
            await self.init_session()

        incremental_mode = False if force else incremental
        semaphore = asyncio.Semaphore(self.concurrent_sites)

        async def crawl_with_limit(priority: int, site_name: str) -> tuple[str, Dict[str, Any]]:
            async with semaphore:
                logger.info(f"处理网站: {site_name} (优先级: {priority})")
                return site_name, await self.crawl_site(site_name, incremental_mode)

        tasks = [
            asyncio.create_task(crawl_with_limit(priority, site_name))
            for priority, site_name, _ in enabled_sites
        ]

        for task in asyncio.as_completed(tasks):
            site_name, result = await task
            results['site_results'][site_name] = result

            if result.get('success'):
                if result.get('skipped'):
                    results['skipped'] += 1
                else:
                    results['successful'] += 1
                    results['total_new_jobs'] += result.get('new_jobs', 0)
                    results['total_updated_jobs'] += result.get('updated_jobs', 0)
                    results['total_filtered_jobs'] += result.get('filtered_jobs', 0)
                    results['new_jobs_data'].extend(result.get('new_jobs_data', []))
            else:
                results['failed'] += 1

        logger.info(
            "所有网站爬取完成: 成功 %s, 失败 %s, 跳过 %s, 新职位 %s, 更新 %s, 过滤 %s",
            results['successful'],
            results['failed'],
            results['skipped'],
            results['total_new_jobs'],
            results['total_updated_jobs'],
            results['total_filtered_jobs'],
        )
        return results

    async def cleanup(self):
        """清理资源"""
        await self.close_session()
        await self.waf_detector.cleanup()
