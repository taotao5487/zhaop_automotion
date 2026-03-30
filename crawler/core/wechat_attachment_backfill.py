from __future__ import annotations

import json
import logging
import subprocess
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def load_review_package(review_dir: Path) -> Dict[str, Any]:
    package_path = review_dir / "package.json"
    if not package_path.exists():
        raise FileNotFoundError(f"未找到待审包文件: {package_path}")
    return json.loads(package_path.read_text(encoding="utf-8"))


def backfill_review_dir(
    review_dir: Path,
    upload_results: Iterable[Mapping[str, Any]],
    *,
    service: str = "idocx",
) -> Dict[str, Any]:
    review_dir = Path(review_dir).resolve()
    package_path = review_dir / "package.json"
    article_html_path = review_dir / "article.html"

    package = load_review_package(review_dir)
    result_by_local_path = {
        _normalize_local_path(Path(item["local_path"])): dict(item)
        for item in upload_results
    }

    updated_attachments: List[Dict[str, Any]] = []
    for attachment in package.get("attachments") or []:
        local_path = _normalize_local_path(review_dir / str(attachment.get("local_path") or ""))
        upload = result_by_local_path.get(local_path, {})
        updated = dict(attachment)
        updated["upload_service"] = service
        updated["upload_status"] = str(upload.get("upload_status") or "pending")
        updated["insert_link"] = str(upload.get("insert_link") or "")
        updated["uploaded_at"] = str(upload.get("uploaded_at") or "")
        updated["upload_error"] = str(upload.get("upload_error") or "")
        updated["original_source_url"] = str(attachment.get("source_url") or "")
        updated["source_url"] = str(upload.get("insert_link") or attachment.get("source_url") or "")
        updated["remote_file_id"] = str(upload.get("remote_file_id") or "")
        updated["remote_file_name"] = str(upload.get("remote_file_name") or "")
        updated["mini_program_path"] = str(upload.get("mini_program_path") or "")
        updated["quick_paste_html"] = str(upload.get("quick_paste_html") or "")
        updated["auto_reply_html"] = str(upload.get("auto_reply_html") or "")
        updated_attachments.append(updated)

    package["attachments"] = updated_attachments
    package["attachment_link_service"] = service
    package["attachment_links_generated_at"] = datetime.now().isoformat(timespec="seconds")

    content_html = str(package.get("content_html") or "")
    if content_html:
        package["content_html"] = _rewrite_attachment_links_html(content_html, updated_attachments)

    package_path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if article_html_path.exists():
        html_text = article_html_path.read_text(encoding="utf-8")
        article_html_path.write_text(
            _rewrite_attachment_links_html(html_text, updated_attachments),
            encoding="utf-8",
        )

    links_payload = {
        "service": service,
        "generated_at": package["attachment_links_generated_at"],
        "review_dir": str(review_dir),
        "success_count": sum(1 for item in updated_attachments if item.get("insert_link")),
        "failed_count": sum(1 for item in updated_attachments if not item.get("insert_link")),
        "attachments": updated_attachments,
    }
    links_json_path = review_dir / "attachment_links.json"
    links_json_path.write_text(
        json.dumps(links_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    clipboard_text = build_attachment_clipboard_text(updated_attachments)
    links_markdown_path = review_dir / "attachment_links.md"
    links_markdown_path.write_text(
        _build_attachment_links_markdown(links_payload, clipboard_text),
        encoding="utf-8",
    )

    return {
        "review_dir": str(review_dir),
        "package_path": str(package_path),
        "article_html_path": str(article_html_path),
        "attachment_links_json_path": str(links_json_path),
        "attachment_links_markdown_path": str(links_markdown_path),
        "clipboard_text": clipboard_text,
        "attachments": updated_attachments,
        "success_count": links_payload["success_count"],
        "failed_count": links_payload["failed_count"],
    }


def build_attachment_clipboard_text(attachments: Iterable[Mapping[str, Any]]) -> str:
    normalized = [dict(item) for item in attachments]
    if len(normalized) == 1:
        quick_html = str(normalized[0].get("quick_paste_html") or "")
        if quick_html:
            return quick_html
        linked = [item for item in normalized if item.get("insert_link")]
        if linked:
            return str(linked[0]["insert_link"])

    quick_html_items = [str(item.get("quick_paste_html") or "") for item in normalized if item.get("quick_paste_html")]
    if quick_html_items:
        return "<br/>\n".join(quick_html_items)

    lines: List[str] = []
    for item in normalized:
        title = str(item.get("title") or "未命名附件")
        link = str(item.get("insert_link") or "")
        if link:
            lines.append(f"- {title}: {link}")
        else:
            fallback = str(item.get("original_source_url") or item.get("source_url") or "")
            if fallback:
                lines.append(f"- {title}: 未生成新链接（原始链接：{fallback}）")
            else:
                lines.append(f"- {title}: 未生成新链接")
    return "\n".join(lines)


def copy_text_to_clipboard(text: str) -> bool:
    if not text.strip():
        return False
    try:
        subprocess.run(
            ["pbcopy"],
            input=text.encode("utf-8"),
            check=True,
            capture_output=True,
        )
        return True
    except Exception as exc:
        logger.warning("复制到剪贴板失败: %s", exc)
        return False


def copy_html_to_clipboard(html_text: str, plain_text: str = "") -> bool:
    if not html_text.strip():
        return False

    script = f"""
ObjC.import('AppKit');
const pb = $.NSPasteboard.generalPasteboard;
pb.clearContents;
pb.setStringForType($({json.dumps(plain_text or html_text)}), $.NSPasteboardTypeString);
pb.setStringForType($({json.dumps(html_text)}), $.NSPasteboardTypeHTML);
"""
    try:
        subprocess.run(
            ["osascript", "-l", "JavaScript"],
            input=script.encode("utf-8"),
            check=True,
            capture_output=True,
        )
        return True
    except Exception as exc:
        logger.warning("复制 HTML 到剪贴板失败: %s", exc)
        return False


def _build_attachment_links_markdown(payload: Mapping[str, Any], clipboard_text: str) -> str:
    attachment_lines: List[str] = []
    for item in payload["attachments"]:
        title = str(item.get("title") or "未命名附件")
        attachment_lines.append(f"### {title}")
        if item.get("mini_program_path"):
            attachment_lines.append(f"- 小程序路径：`{item['mini_program_path']}`")
        if item.get("insert_link"):
            attachment_lines.append(f"- 插入字段：`{item['insert_link']}`")
        if item.get("quick_paste_html"):
            attachment_lines.append("- 直达路径 HTML：")
            attachment_lines.append("```html")
            attachment_lines.append(str(item["quick_paste_html"]))
            attachment_lines.append("```")
        if item.get("upload_error"):
            attachment_lines.append(f"- 错误：{item['upload_error']}")
        attachment_lines.append("")

    return (
        "# 附件链接清单\n\n"
        f"- 服务：{payload['service']}\n"
        f"- 生成时间：{payload['generated_at']}\n"
        f"- 成功：{payload['success_count']}\n"
        f"- 失败：{payload['failed_count']}\n\n"
        "## 可复制内容\n\n"
        f"{clipboard_text}\n\n"
        "## 附件明细\n\n"
        f"{chr(10).join(attachment_lines)}"
    )


def _rewrite_attachment_links_html(html_text: str, attachments: List[Mapping[str, Any]]) -> str:
    has_doctype = html_text.lstrip().lower().startswith("<!doctype html>")
    soup = BeautifulSoup(html_text, "lxml")

    attachment_by_original = {
        str(item.get("original_source_url") or ""): item
        for item in attachments
        if item.get("original_source_url")
    }
    attachment_by_title = {
        str(item.get("title") or ""): item
        for item in attachments
        if item.get("title")
    }

    for section in soup.find_all("section"):
        heading = section.find("h2")
        if not heading or heading.get_text(" ", strip=True) != "附件说明":
            continue
        for link in section.find_all("a", href=True):
            title = link.get_text(" ", strip=True)
            original_href = (link.get("href") or "").strip()
            attachment = attachment_by_original.get(original_href) or attachment_by_title.get(title)
            if not attachment:
                continue
            quick_paste_html = str(attachment.get("quick_paste_html") or "")
            if quick_paste_html:
                fragment = BeautifulSoup(quick_paste_html, "lxml")
                replacement = fragment.body.contents[0] if fragment.body and fragment.body.contents else None
                if replacement is not None:
                    link.replace_with(replacement)
                    continue
            insert_link = str(attachment.get("insert_link") or "")
            if insert_link:
                link["href"] = insert_link

    serialized = str(soup)
    if has_doctype and not serialized.lstrip().lower().startswith("<!doctype html>"):
        serialized = f"<!DOCTYPE html>\n{serialized}"
    return serialized


def _normalize_local_path(path: Path) -> str:
    return path.resolve().as_posix()
