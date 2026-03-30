"""Compatibility package for legacy `utils.*` imports."""

from importlib import import_module
import sys

_SUBMODULES = [
    "article_fetcher",
    "auth_manager",
    "content_processor",
    "crawler_review_package_draft",
    "helpers",
    "http_client",
    "image_proxy",
    "official_wechat_draft",
    "official_wx_preview",
    "proxy_pool",
    "rate_limiter",
    "recruitment_filter",
    "rss_poller",
    "rss_store",
    "webhook",
]

for _name in _SUBMODULES:
    _module = import_module(f"wechat_service.utils.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

from wechat_service.utils import rss_store  # noqa: E402,F401
