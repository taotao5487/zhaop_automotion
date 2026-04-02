#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from wechat_service.utils.crawler_review_package_draft import (  # noqa: E402
    append_review_package_to_draft,
    push_review_package_to_shared_draft_series,
)
from wechat_service.utils.official_wechat_draft import OfficialWechatDraftError  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
DEFAULT_DETAIL_OUTPUT_ROOT = PROJECT_ROOT / "data" / "exports" / "details"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将 crawler 生成的 wechat_review/review_dir 推送到微信官方草稿箱。"
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--review-dir",
        help="wechat_review 待审包目录绝对路径",
    )
    source_group.add_argument(
        "--summary-path",
        help="detail_pipeline 生成的 summary.json 路径，会按顺序推送其中的 review_dir",
    )
    parser.add_argument(
        "--draft-media-id",
        default="",
        help="可选：已有草稿 media_id，传入后走追加模式。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="共享草稿串模式下允许重复推送已按 URL 记录过的文章",
    )
    return parser.parse_args(argv)


def _resolve_review_dir(review_dir: str) -> Path:
    path = Path(review_dir).expanduser().resolve()
    if not path.exists():
        raise OfficialWechatDraftError(f"review_dir 不存在: {path}")
    return path


def _load_review_dirs_from_summary(summary_path: Path) -> list[Path]:
    if not summary_path.exists():
        raise OfficialWechatDraftError(f"summary_path 不存在: {summary_path}")
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    review_dirs: list[Path] = []
    for item in payload.get("results") or []:
        review_dir = str((item or {}).get("review_dir") or "").strip()
        if not review_dir:
            continue
        review_dirs.append(_resolve_review_dir(review_dir))
    return review_dirs


def find_latest_summary_path(root: Path | str = DEFAULT_DETAIL_OUTPUT_ROOT) -> Path:
    details_root = Path(root).expanduser().resolve()
    if not details_root.exists():
        raise OfficialWechatDraftError(f"详情输出目录不存在: {details_root}")

    candidates = [
        path / "summary.json"
        for path in details_root.iterdir()
        if path.is_dir() and (path / "summary.json").exists()
    ]
    if not candidates:
        raise OfficialWechatDraftError(f"未找到任何 summary.json: {details_root}")

    candidates.sort(key=lambda item: item.parent.name)
    return candidates[-1]


def resolve_summary_path(summary_path: str, root: Path | str = DEFAULT_DETAIL_OUTPUT_ROOT) -> Path:
    explicit_summary_path = Path(summary_path).expanduser().resolve()
    latest_summary_path = find_latest_summary_path(root)
    if explicit_summary_path != latest_summary_path:
        print(
            "warning | summary_path_is_not_latest | "
            f"provided={explicit_summary_path} | latest={latest_summary_path}"
        )
    return explicit_summary_path


def _print_result(status: str, review_dir: Path, result: dict | None = None, error: str = "") -> None:
    parts = [status, str(review_dir)]
    if result:
        if result.get("draft_media_id"):
            parts.append(f"draft_media_id={result['draft_media_id']}")
        if result.get("batch_index"):
            parts.append(f"batch_index={result['batch_index']}")
        if result.get("append_mode"):
            parts.append(f"append_mode={result['append_mode']}")
    if error:
        parts.append(f"error={error}")
    print(" | ".join(parts))


async def _push_one_review_dir(review_dir: Path, args: argparse.Namespace) -> tuple[str, dict]:
    if not review_dir.exists():
        raise OfficialWechatDraftError(f"review_dir 不存在: {review_dir}")

    if args.draft_media_id:
        result = await append_review_package_to_draft(
            review_dir=review_dir,
            draft_media_id=args.draft_media_id,
        )
        return "appended", result

    result = await push_review_package_to_shared_draft_series(
        review_dir=review_dir,
        force=args.force,
    )
    return str(result.get("status") or "pushed"), result


async def _run(args: argparse.Namespace) -> int:
    if args.summary_path and args.draft_media_id:
        raise OfficialWechatDraftError("--summary-path 模式不支持 --draft-media-id，请使用共享草稿串自动分配")

    if args.summary_path:
        review_dirs = _load_review_dirs_from_summary(
            resolve_summary_path(args.summary_path)
        )
    else:
        review_dirs = [_resolve_review_dir(args.review_dir)]

    has_error = False
    for review_dir in review_dirs:
        try:
            status, result = await _push_one_review_dir(review_dir, args)
            _print_result(status, review_dir, result=result)
        except Exception as exc:
            has_error = True
            _print_result("error", review_dir, error=str(exc))

    return 1 if has_error else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OfficialWechatDraftError as exc:
        logger.error(str(exc))
        sys.exit(1)
