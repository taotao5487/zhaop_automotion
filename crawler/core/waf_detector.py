import asyncio
import logging
import re
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse
import aiohttp
from aiohttp import ClientSession
import cloudscraper
import selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

try:
    import undetected_chromedriver as uc
except Exception:  # pragma: no cover - optional runtime dependency on Python 3.12+
    uc = None

logger = logging.getLogger(__name__)


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


class WAFDetector:
    """WAF检测和绕过器"""

    def __init__(self):
        self.cloudflare_patterns = [
            r'cloudflare',
            r'cf-ray',
            r'__cfduid',
            r'challenge-form',
            r'jschl_vc',
            r'jschl_answer'
        ]
        self.akamai_patterns = [
            r'akamai',
            r'ak-baidu'
        ]
        self.imperva_patterns = [
            r'incap_ses',
            r'visid_incap'
        ]
        self.driver: Optional[webdriver.Chrome] = None

    async def detect_waf(self,
                        url: str,
                        session: ClientSession,
                        ssl: bool = True) -> Dict[str, Any]:
        """检测WAF"""
        waf_info = {
            'has_waf': False,
            'waf_type': None,
            'confidence': 0,
            'indicators': []
        }

        try:
            async with session.get(url, ssl=ssl) as response:
                headers = response.headers
                body = await response.read()
                html = _decode_response_body(body, response.charset)

                # 检查响应头
                for header_name, header_value in headers.items():
                    header_lower = header_name.lower()
                    header_value_lower = str(header_value).lower()

                    # Cloudflare检测
                    if 'cloudflare' in header_value_lower or 'cf-ray' in header_lower:
                        waf_info['has_waf'] = True
                        waf_info['waf_type'] = 'cloudflare'
                        waf_info['confidence'] = 90
                        waf_info['indicators'].append(f'Header: {header_name}={header_value}')
                        break

                    # Akamai检测
                    if 'akamai' in header_value_lower:
                        waf_info['has_waf'] = True
                        waf_info['waf_type'] = 'akamai'
                        waf_info['confidence'] = 85
                        waf_info['indicators'].append(f'Header: {header_name}={header_value}')
                        break

                # 检查HTML内容
                if not waf_info['has_waf']:
                    html_lower = html.lower()

                    # SafeLine JS Challenge
                    if 'safeline_bot_challenge' in html_lower and 'leading_zero_bit' in html_lower:
                        waf_info['has_waf'] = True
                        waf_info['waf_type'] = 'safeline_js_challenge'
                        waf_info['confidence'] = 95
                        waf_info['indicators'].append('HTML contains safeline js challenge')

                    # Cloudflare挑战页面
                    if 'cloudflare' in html_lower or 'cf-browser-verification' in html_lower:
                        waf_info['has_waf'] = True
                        waf_info['waf_type'] = 'cloudflare'
                        waf_info['confidence'] = 95
                        waf_info['indicators'].append('HTML contains cloudflare')

                    # 验证码检测
                    if 'captcha' in html_lower or 'recaptcha' in html_lower:
                        waf_info['has_waf'] = True
                        if not waf_info['waf_type']:
                            waf_info['waf_type'] = 'captcha'
                        waf_info['confidence'] = 80
                        waf_info['indicators'].append('HTML contains captcha')

                    # 挑战表单检测
                    if 'challenge-form' in html_lower:
                        waf_info['has_waf'] = True
                        waf_info['waf_type'] = 'challenge'
                        waf_info['confidence'] = 90
                        waf_info['indicators'].append('HTML contains challenge form')

                # 检查状态码
                if response.status in [403, 429, 503]:
                    if not waf_info['has_waf']:
                        waf_info['has_waf'] = True
                        waf_info['waf_type'] = 'rate_limit'
                        waf_info['confidence'] = 70
                        waf_info['indicators'].append(f'Status code: {response.status}')

                logger.debug(f"WAF检测结果: {waf_info}")
                return waf_info

        except Exception as e:
            logger.error(f"WAF检测失败 {url}: {e}")
            return waf_info

    async def apply_bypass_strategy(self,
                                  url: str,
                                  waf_info: Dict[str, Any],
                                  strategy_config: Dict[str, Any]) -> Dict[str, Any]:
        """应用WAF绕过策略"""
        if not waf_info['has_waf']:
            return {'bypassed': False, 'message': 'No WAF detected'}

        waf_type = waf_info['waf_type']
        result = {
            'bypassed': False,
            'method': None,
            'message': '',
            'url': url
        }

        try:
            # 根据WAF类型选择策略
            if waf_type == 'cloudflare':
                # 使用cloudscraper绕过Cloudflare
                bypass_result = await self._bypass_cloudflare(url, strategy_config)
                if bypass_result['success']:
                    result.update({
                        'bypassed': True,
                        'method': 'cloudscraper',
                        'message': 'Cloudflare bypassed using cloudscraper'
                    })
                else:
                    # 降级到动态模式
                    dynamic_result = await self._use_dynamic_browser(url, strategy_config)
                    if dynamic_result['success']:
                        result.update({
                            'bypassed': True,
                            'method': 'dynamic_browser',
                            'message': 'Cloudflare bypassed using dynamic browser',
                            'html': dynamic_result.get('html')
                        })

            elif waf_type == 'captcha':
                # 对于验证码，使用动态浏览器
                dynamic_result = await self._use_dynamic_browser(url, strategy_config)
                if dynamic_result['success']:
                    result.update({
                        'bypassed': True,
                        'method': 'dynamic_browser',
                        'message': 'Captcha bypassed using dynamic browser',
                        'html': dynamic_result.get('html')
                    })

            elif waf_type == 'safeline_js_challenge':
                # 这类挑战在 crawler.fetch_page 中通过自动求解 Cookie 处理
                result.update({
                    'bypassed': True,
                    'method': 'cookie_challenge',
                    'message': 'SafeLine JS challenge will be handled in fetch_page'
                })

            elif waf_type in ['akamai', 'imperva', 'rate_limit']:
                # 使用请求头伪装和延迟
                stealth_result = await self._use_stealth_mode(url, strategy_config)
                if stealth_result['success']:
                    result.update({
                        'bypassed': True,
                        'method': 'stealth_mode',
                        'message': f'{waf_type} bypassed using stealth mode'
                    })

            else:
                # 通用绕过策略
                generic_result = await self._use_generic_bypass(url, strategy_config)
                if generic_result['success']:
                    result.update({
                        'bypassed': True,
                        'method': 'generic',
                        'message': 'WAF bypassed using generic method'
                    })

            if not result['bypassed']:
                result['message'] = f'Failed to bypass {waf_type} WAF'

            logger.info(f"WAF绕过结果: {result}")
            return result

        except Exception as e:
            logger.error(f"应用WAF绕过策略失败: {e}")
            result['message'] = f'Error: {str(e)}'
            return result

    async def _bypass_cloudflare(self,
                               url: str,
                               strategy_config: Dict[str, Any]) -> Dict[str, Any]:
        """使用cloudscraper绕过Cloudflare"""
        try:
            # 创建cloudscraper实例
            scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'mobile': False
                }
            )

            # 发送请求
            response = scraper.get(url, timeout=30)

            if response.status_code == 200:
                return {
                    'success': True,
                    'html': response.text,
                    'cookies': dict(response.cookies)
                }
            else:
                return {
                    'success': False,
                    'error': f'Status code: {response.status_code}'
                }

        except Exception as e:
            logger.error(f"cloudscraper绕过失败: {e}")
            return {'success': False, 'error': str(e)}

    async def _use_stealth_mode(self,
                              url: str,
                              strategy_config: Dict[str, Any]) -> Dict[str, Any]:
        """使用隐身模式（请求头伪装）"""
        # 这里应该在crawler.py的fetch_page方法中实现
        # 通过修改请求头、添加延迟等方式
        return {'success': True, 'message': 'Stealth mode applied'}

    async def _use_dynamic_browser(self,
                                 url: str,
                                 strategy_config: Dict[str, Any]) -> Dict[str, Any]:
        """使用动态浏览器（Selenium/undetected-chromedriver）"""
        try:
            if not self.driver:
                await self._init_browser_driver()

            self.driver.get(url)

            # 等待页面加载
            await asyncio.sleep(5)

            # 检查是否有验证码或挑战
            page_source = self.driver.page_source
            if 'captcha' in page_source.lower() or 'challenge' in page_source.lower():
                logger.warning(f"动态浏览器仍然遇到验证码: {url}")
                return {'success': False, 'error': 'Captcha still present'}

            return {
                'success': True,
                'html': page_source,
                'cookies': self.driver.get_cookies()
            }

        except Exception as e:
            logger.error(f"动态浏览器绕过失败: {e}")
            return {'success': False, 'error': str(e)}

    async def _init_browser_driver(self):
        """初始化浏览器驱动"""
        if uc is None:
            raise RuntimeError(
                "undetected_chromedriver 当前不可用，请改用 selenium 或在兼容环境中启用动态浏览器"
            )
        try:
            options = uc.ChromeOptions()
            options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-blink-features=AutomationControlled')

            self.driver = uc.Chrome(options=options)

            # 执行CDP命令隐藏自动化特征
            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                '''
            })

            logger.info("浏览器驱动初始化成功")

        except Exception as e:
            logger.error(f"浏览器驱动初始化失败: {e}")
            self.driver = None
            raise

    async def _use_generic_bypass(self,
                                url: str,
                                strategy_config: Dict[str, Any]) -> Dict[str, Any]:
        """通用绕过策略"""
        # 实现通用的绕过策略，如：
        # 1. 修改User-Agent
        # 2. 添加Referer
        # 3. 使用代理
        # 4. 添加延迟
        # 5. 轮换IP
        return {'success': True, 'message': 'Generic bypass applied'}

    async def cleanup(self):
        """清理资源"""
        if self.driver:
            try:
                self.driver.quit()
                self.driver = None
                logger.info("浏览器驱动已清理")
            except Exception as e:
                logger.error(f"清理浏览器驱动失败: {e}")

    def get_recommended_strategy(self, waf_type: str) -> Dict[str, Any]:
        """获取推荐的绕过策略"""
        strategies = {
            'cloudflare': {
                'primary': 'cloudscraper',
                'fallback': 'dynamic_browser',
                'timeout': 30,
                'retries': 2
            },
            'captcha': {
                'primary': 'dynamic_browser',
                'fallback': None,
                'timeout': 60,
                'retries': 1
            },
            'akamai': {
                'primary': 'stealth_mode',
                'fallback': 'dynamic_browser',
                'timeout': 20,
                'retries': 3
            },
            'imperva': {
                'primary': 'stealth_mode',
                'fallback': 'dynamic_browser',
                'timeout': 20,
                'retries': 3
            },
            'rate_limit': {
                'primary': 'stealth_mode',
                'fallback': 'generic',
                'timeout': 15,
                'retries': 5
            }
        }

        return strategies.get(waf_type, {
            'primary': 'generic',
            'fallback': None,
            'timeout': 15,
            'retries': 3
        })
