#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from crawler.core.idocx_attachment_uploader import IdocxAttachmentUploader
from crawler.core.wechat_attachment_backfill import (
    backfill_review_dir,
    copy_html_to_clipboard,
    copy_text_to_clipboard,
)
from crawler.core.xzf_attachment_uploader import (
    AttachmentUploadError,
    XzfAttachmentUploader,
    build_local_attachments_from_review_dir,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将 wechat_review 待审包中的附件上传到指定站点，并回填公众号可用链接/小程序路径。"
    )
    parser.add_argument(
        "--review-dir",
        required=True,
        help="待处理的 wechat_review 目录绝对路径",
    )
    parser.add_argument(
        "--service",
        default="idocx",
        choices=["idocx", "xzf"],
        help="附件服务，默认使用 idocx",
    )
    parser.add_argument(
        "--profile-dir",
        default=None,
        help="Playwright 持久化浏览器目录；默认按服务使用 data/browser_profiles/<service>",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="以无头模式运行浏览器。默认关闭，便于首次扫码登录。",
    )
    parser.add_argument(
        "--skip-manual-upload",
        action="store_true",
        help="只检索小正方现有文件，不在缺失时引导电脑微信手工上传。",
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="完成后不自动复制结果到系统剪贴板。",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    review_dir = Path(args.review_dir).expanduser().resolve()
    if not review_dir.is_absolute():
        raise AttachmentUploadError("--review-dir 必须是绝对路径")
    if not review_dir.exists():
        raise AttachmentUploadError(f"待审包目录不存在: {review_dir}")

    attachments = build_local_attachments_from_review_dir(review_dir)
    if not attachments:
        logger.info("当前待审包没有附件，无需上传。")
        return 0

    profile_raw = args.profile_dir or f"data/browser_profiles/{args.service}"
    profile_dir = Path(profile_raw).expanduser()
    if not profile_dir.is_absolute():
        profile_dir = (Path.cwd() / profile_dir).resolve()

    logger.info("读取到 %s 个附件，开始处理。", len(attachments))
    logger.info("待审包目录: %s", review_dir)
    logger.info("浏览器目录: %s", profile_dir)

    if args.service == "idocx":
        uploader = IdocxAttachmentUploader(
            profile_dir=profile_dir,
            headless=args.headless,
        )
        upload_results = uploader.upload_attachments(attachments)
    else:
        uploader = XzfAttachmentUploader(
            profile_dir=profile_dir,
            headless=args.headless,
        )
        upload_results = uploader.upload_attachments(
            attachments,
            allow_manual_upload=not args.skip_manual_upload,
        )

    backfill_result = backfill_review_dir(
        review_dir,
        [item.__dict__ for item in upload_results],
        service=args.service,
    )

    copied = False
    if not args.no_clipboard:
        if args.service == "idocx":
            html_fragments = [item.quick_paste_html for item in upload_results if item.quick_paste_html]
            if html_fragments:
                copied = copy_html_to_clipboard(
                    "<br/>".join(html_fragments),
                    backfill_result["clipboard_text"],
                )
        if not copied:
            copied = copy_text_to_clipboard(backfill_result["clipboard_text"])

    logger.info(
        "处理完成: 成功 %s，失败 %s",
        backfill_result["success_count"],
        backfill_result["failed_count"],
    )
    logger.info("package.json 已回填: %s", backfill_result["package_path"])
    logger.info("article.html 已更新: %s", backfill_result["article_html_path"])
    logger.info("链接清单 JSON: %s", backfill_result["attachment_links_json_path"])
    logger.info("链接清单 Markdown: %s", backfill_result["attachment_links_markdown_path"])
    logger.info(
        "如需继续推送官方草稿箱，可执行: python push_wechat_review_to_official_draft.py --review-dir %s",
        review_dir,
    )
    if copied:
        logger.info("链接内容已复制到系统剪贴板。")
    else:
        logger.info("本次未复制到系统剪贴板。")

    failed_count = backfill_result["failed_count"]
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AttachmentUploadError as exc:
        logger.error(str(exc))
        sys.exit(1)
