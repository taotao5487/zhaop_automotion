#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

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
from wechat_service.utils.crawler_review_package_draft import (
    review_dir_uses_wechat_article_source,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


DEFAULT_DETAIL_OUTPUT_ROOT = PROJECT_ROOT / "data" / "exports" / "details"
push_review_package_to_shared_draft_series = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="自动处理最新一轮审核结果：有附件先上传回填，再批量推送到微信官方草稿箱。"
    )
    parser.add_argument(
        "--summary-path",
        default="",
        help="可选：显式指定某一轮 details/summary.json；不传则自动使用最新一轮。",
    )
    parser.add_argument(
        "--service",
        default="idocx",
        choices=["idocx", "xzf"],
        help="附件服务，默认使用 idocx。",
    )
    parser.add_argument(
        "--profile-dir",
        default=None,
        help="Playwright 持久化浏览器目录；默认按服务使用 data/browser_profiles/<service>。",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="以无头模式运行浏览器。默认关闭，便于首次扫码登录。",
    )
    parser.add_argument(
        "--skip-manual-upload",
        action="store_true",
        help="仅对 xzf 生效：缺失文件时不引导电脑微信手工上传。",
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="处理附件后不自动复制结果到系统剪贴板。",
    )
    parser.add_argument(
        "--skip-attachments",
        action="store_true",
        help="即使检测到附件也跳过上传回填，直接推送草稿。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="共享草稿串模式下允许重复推送已按 URL 记录过的文章。",
    )
    return parser.parse_args(argv)


def find_latest_summary_path(root: Path | str = DEFAULT_DETAIL_OUTPUT_ROOT) -> Path:
    details_root = Path(root).expanduser().resolve()
    if not details_root.exists():
        raise FileNotFoundError(f"详情输出目录不存在: {details_root}")

    candidates = [path / "summary.json" for path in details_root.iterdir() if path.is_dir() and (path / "summary.json").exists()]
    if not candidates:
        raise FileNotFoundError(f"未找到任何 summary.json: {details_root}")

    candidates.sort(key=lambda item: item.parent.name)
    return candidates[-1]


def resolve_summary_path(summary_path: str = "", root: Path | str = DEFAULT_DETAIL_OUTPUT_ROOT) -> Path:
    latest_summary_path = find_latest_summary_path(root)
    if not summary_path:
        return latest_summary_path

    explicit_summary_path = Path(summary_path).expanduser().resolve()
    if explicit_summary_path != latest_summary_path:
        print(
            "warning | summary_path_is_not_latest | "
            f"provided={explicit_summary_path} | latest={latest_summary_path}"
        )
    return explicit_summary_path


def load_review_dirs_from_summary(summary_path: Path | str) -> list[Path]:
    path = Path(summary_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"summary.json 不存在: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    review_dirs: list[Path] = []
    for item in payload.get("results") or []:
        review_dir = str((item or {}).get("review_dir") or "").strip()
        if not review_dir:
            continue
        review_dirs.append(Path(review_dir).expanduser().resolve())
    return review_dirs


def _resolve_profile_dir(service: str, profile_dir: str | None) -> Path:
    profile_raw = profile_dir or f"data/browser_profiles/{service}"
    resolved = Path(profile_raw).expanduser()
    if not resolved.is_absolute():
        resolved = (PROJECT_ROOT / resolved).resolve()
    return resolved


def _copy_attachment_results(upload_results: Sequence[object], service: str, no_clipboard: bool) -> bool:
    copied = False
    if no_clipboard:
        return copied

    if service == "idocx":
        html_fragments = [getattr(item, "quick_paste_html", "") for item in upload_results if getattr(item, "quick_paste_html", "")]
        if html_fragments:
            plain_lines = []
            for item in upload_results:
                mini_program_path = getattr(item, "mini_program_path", "")
                insert_link = getattr(item, "insert_link", "")
                title = getattr(item, "title", "未命名附件")
                if mini_program_path:
                    plain_lines.append(f"- {title}: {mini_program_path}")
                elif insert_link:
                    plain_lines.append(f"- {title}: {insert_link}")
            copied = copy_html_to_clipboard("<br/>".join(html_fragments), "\n".join(plain_lines))

    if not copied:
        lines = []
        for item in upload_results:
            title = getattr(item, "title", "未命名附件")
            insert_link = getattr(item, "insert_link", "")
            upload_error = getattr(item, "upload_error", "")
            if insert_link:
                lines.append(f"- {title}: {insert_link}")
            elif upload_error:
                lines.append(f"- {title}: {upload_error}")
        copied = copy_text_to_clipboard("\n".join(lines))

    return copied


def process_review_dir_attachments(
    review_dir: Path | str,
    *,
    service: str = "idocx",
    profile_dir: str | None = None,
    headless: bool = False,
    skip_manual_upload: bool = False,
    no_clipboard: bool = False,
    prepared_attachments: Iterable[object] | None = None,
) -> dict:
    review_path = Path(review_dir).expanduser().resolve()
    attachments = list(prepared_attachments) if prepared_attachments is not None else build_local_attachments_from_review_dir(review_path)
    if not attachments:
        return {
            "review_dir": str(review_path),
            "skipped": True,
            "reason": "no_attachments",
            "success_count": 0,
            "failed_count": 0,
        }

    resolved_profile_dir = _resolve_profile_dir(service, profile_dir)
    logger.info("检测到附件 %s 个，开始上传回填: %s", len(attachments), review_path)

    if service == "idocx":
        uploader = IdocxAttachmentUploader(
            profile_dir=resolved_profile_dir,
            headless=headless,
        )
        upload_results = uploader.upload_attachments(attachments)
    else:
        uploader = XzfAttachmentUploader(
            profile_dir=resolved_profile_dir,
            headless=headless,
        )
        upload_results = uploader.upload_attachments(
            attachments,
            allow_manual_upload=not skip_manual_upload,
        )

    backfill_result = backfill_review_dir(
        review_path,
        [item.__dict__ for item in upload_results],
        service=service,
    )
    copied = _copy_attachment_results(upload_results, service, no_clipboard)
    backfill_result["clipboard_copied"] = copied
    return backfill_result


def _parse_push_cli_output(output: str) -> dict:
    last_line = ""
    for line in reversed(output.splitlines()):
        if line.strip():
            last_line = line.strip()
            break
    if not last_line:
        return {"status": "pushed"}

    parts = [part.strip() for part in last_line.split("|")]
    payload = {"status": parts[0] if parts else "pushed"}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        payload[key.strip()] = value.strip()
    return payload


async def _push_via_fallback_cli(review_dir: Path, force: bool) -> dict:
    interpreter_candidates = [
        PROJECT_ROOT / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    interpreter = next((path for path in interpreter_candidates if path.exists()), None)
    if interpreter is None:
        raise RuntimeError("未找到可用的推送解释器，请先准备根目录 .venv")

    command = [
        str(interpreter),
        str(PROJECT_ROOT / "scripts" / "push_wechat_review_to_official_draft.py"),
        "--review-dir",
        str(review_dir),
    ]
    if force:
        command.append("--force")

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "push cli failed"
        raise RuntimeError(message)

    return _parse_push_cli_output(completed.stdout)


async def push_review_dir(review_dir: Path, force: bool = False) -> dict:
    if callable(push_review_package_to_shared_draft_series):
        try:
            return await push_review_package_to_shared_draft_series(review_dir=review_dir, force=force)
        except ModuleNotFoundError:
            return await _push_via_fallback_cli(review_dir, force)

    try:
        from wechat_service.utils.crawler_review_package_draft import push_review_package_to_shared_draft_series as push_impl
    except ModuleNotFoundError:
        return await _push_via_fallback_cli(review_dir, force)

    try:
        return await push_impl(review_dir=review_dir, force=force)
    except ModuleNotFoundError:
        return await _push_via_fallback_cli(review_dir, force)


async def _run(args: argparse.Namespace) -> int:
    summary_path = resolve_summary_path(args.summary_path)
    review_dirs = load_review_dirs_from_summary(summary_path)
    print(f"using summary: {summary_path}")

    if not review_dirs:
        print(f"error | {summary_path} | no review_dir found")
        return 1

    has_error = False
    for review_dir in review_dirs:
        try:
            attachments = build_local_attachments_from_review_dir(review_dir)
        except Exception as exc:
            print(f"error | {review_dir} | attachment_scan_failed={exc}")
            has_error = True
            continue

        if attachments and review_dir_uses_wechat_article_source(review_dir):
            print(f"attachments | {review_dir} | skip_attachment_backfill=wechat_article_source")
            attachments = []

        if attachments and not args.skip_attachments:
            try:
                attachment_result = await asyncio.to_thread(
                    process_review_dir_attachments,
                    review_dir,
                    service=args.service,
                    profile_dir=args.profile_dir,
                    headless=args.headless,
                    skip_manual_upload=args.skip_manual_upload,
                    no_clipboard=args.no_clipboard,
                    prepared_attachments=attachments,
                )
                if attachment_result.get("failed_count", 0) > 0:
                    raise AttachmentUploadError(
                        f"附件上传失败 {attachment_result['failed_count']} 个，请先处理后再推送"
                    )
                print(
                    f"attachments | {review_dir} | success={attachment_result.get('success_count', 0)} "
                    f"| failed={attachment_result.get('failed_count', 0)}"
                )
            except Exception as exc:
                print(f"error | {review_dir} | attachment_upload_failed={exc}")
                has_error = True
                continue

        try:
            result = await push_review_dir(review_dir=review_dir, force=args.force)
            print(
                f"{result.get('status', 'pushed')} | {review_dir} | "
                f"draft_media_id={result.get('draft_media_id', '')} | "
                f"batch_index={result.get('batch_index', '')}"
            )
        except Exception as exc:
            print(f"error | {review_dir} | push_failed={exc}")
            has_error = True

    return 1 if has_error else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        logger.error(str(exc))
        sys.exit(1)
