import sys

from scripts import publish_latest_wechat_batch as _impl

sys.modules[__name__] = _impl
