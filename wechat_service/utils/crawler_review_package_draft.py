from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from bs4 import BeautifulSoup

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - Pillow is expected in runtime deps
    Image = None
    ImageDraw = None
    ImageFont = None

try:
    from crawler.core.detail_pipeline import DetailPipeline
except Exception:  # pragma: no cover - optional cross-project dependency
    DetailPipeline = None

try:
    from wechat_service.utils.official_wechat_draft import OfficialWechatDraftError
except Exception:  # pragma: no cover - keep build path usable without push deps
    class OfficialWechatDraftError(RuntimeError):
        pass


def _load_official_draft_pushers():
    from wechat_service.utils.official_wechat_draft import (
        append_article_row_to_draft,
        push_article_row_to_shared_draft_series,
        push_article_row_to_draft,
    )

    return (
        push_article_row_to_draft,
        push_article_row_to_shared_draft_series,
        append_article_row_to_draft,
    )


class _NoopCrawler:
    site_configs: dict[str, Any] = {}
    session = None


def _render_attachment_preview(
    review_dir: Path,
    attachment: Dict[str, Any],
) -> tuple[list[str], str]:
    local_path = _resolve_asset_path(review_dir, str(attachment.get("local_path") or ""))
    if local_path is None or DetailPipeline is None:
        return [], str(attachment.get("render_status") or "skipped")

    renders_dir = review_dir / "assets" / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    pipeline = DetailPipeline(_NoopCrawler())
    ext = local_path.suffix.lower()
    if ext == ".pdf":
        render_paths, render_status = pipeline._render_pdf(local_path, renders_dir)
    elif ext == ".xlsx":
        render_paths, render_status = pipeline._render_xlsx(local_path, renders_dir)
    elif ext == ".docx":
        render_paths, render_status = pipeline._render_docx(local_path, renders_dir)
    else:
        return [], str(attachment.get("render_status") or "unsupported")

    return [path.relative_to(review_dir).as_posix() for path in render_paths], render_status


def _classify_article_type(package: Dict[str, Any]) -> str:
    images = package.get("images") or []
    attachments = package.get("attachments") or []

    if any(item.get("keep") and item.get("category") == "qr_in_body" for item in images):
        return "qr_in_body"
    if any(item.get("keep") and item.get("category") == "position_image" for item in images):
        return "position_image"
    if any(
        item.get("category") == "position_table" and item.get("rendered_images")
        for item in attachments
    ):
        return "attachment_driven"
    return "text_only"


def _build_position_gallery_section(position_rendered_images: list[str]) -> str:
    gallery_items = "\n".join(
        f'<figure class="wechat-review-image"><img src="{escape(path, quote=True)}" alt="岗位信息图" /></figure>'
        for path in position_rendered_images
    )
    return (
        '<section class="wechat-review-section">'
        "<h2>招聘岗位</h2>"
        "<p>岗位名称、人数及条件以以下岗位信息图为准。</p>"
        f"{gallery_items}"
        "</section>"
    )


def _upsert_position_gallery(content_html: str, position_rendered_images: list[str]) -> str:
    soup = BeautifulSoup(content_html or "", "html.parser")
    article = soup.find("article") or soup

    for section in list(article.find_all("section")):
        heading = section.find("h2")
        if heading and heading.get_text(" ", strip=True) == "招聘岗位":
            section.decompose()

    if position_rendered_images:
        fragment = BeautifulSoup(_build_position_gallery_section(position_rendered_images), "html.parser")
        gallery_section = fragment.find("section")
        attachment_section = None
        for section in article.find_all("section"):
            heading = section.find("h2")
            if heading and heading.get_text(" ", strip=True) == "附件说明":
                attachment_section = section
                break
        if gallery_section is not None:
            if attachment_section is not None:
                attachment_section.insert_before(gallery_section)
            else:
                article.append(gallery_section)

    return str(soup)


def _persist_review_package(review_dir: Path, package: Dict[str, Any]) -> None:
    package_path = review_dir / "package.json"
    package_path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    content_html = str(package.get("content_html") or "")
    article_html_path = review_dir / "article.html"
    if article_html_path.exists() and content_html:
        html_soup = BeautifulSoup(article_html_path.read_text(encoding="utf-8"), "html.parser")
        body = html_soup.body
        fragment = BeautifulSoup(content_html, "html.parser")
        source_root = fragment.body or fragment
        if body is not None:
            body.clear()
            for child in list(source_root.contents):
                body.append(child)
            article_html_path.write_text(str(html_soup), encoding="utf-8")
        else:
            article_html_path.write_text(content_html, encoding="utf-8")

    article_txt_path = review_dir / "article.txt"
    if article_txt_path.exists():
        article_txt_path.write_text(str(package.get("plain_text") or ""), encoding="utf-8")


def _ensure_rendered_position_tables(review_dir: Path, package: Dict[str, Any]) -> Dict[str, Any]:
    attachments = package.get("attachments") or []
    if not attachments:
        return package

    changed = False
    for attachment in attachments:
        if attachment.get("category") != "position_table":
            continue

        valid_rendered_images = []
        for raw_path in attachment.get("rendered_images") or []:
            resolved = _resolve_asset_path(review_dir, str(raw_path or ""))
            if resolved is not None:
                valid_rendered_images.append(Path(raw_path).as_posix())

        if valid_rendered_images != list(attachment.get("rendered_images") or []):
            attachment["rendered_images"] = valid_rendered_images
            changed = True

        if attachment.get("rendered_images"):
            continue

        rendered_images, render_status = _render_attachment_preview(review_dir, attachment)
        if rendered_images:
            attachment["rendered_images"] = rendered_images
            attachment["render_status"] = render_status
            changed = True
        elif render_status != attachment.get("render_status"):
            attachment["render_status"] = render_status
            changed = True

    position_rendered_images = [
        str(path)
        for attachment in attachments
        if attachment.get("category") == "position_table"
        for path in attachment.get("rendered_images") or []
    ]
    article_type = _classify_article_type(package)
    updated_content_html = _upsert_position_gallery(
        str(package.get("content_html") or ""),
        position_rendered_images,
    )
    updated_plain_text = BeautifulSoup(updated_content_html, "html.parser").get_text("\n", strip=True)

    if article_type != package.get("article_type"):
        package["article_type"] = article_type
        changed = True
    if updated_content_html != str(package.get("content_html") or ""):
        package["content_html"] = updated_content_html
        changed = True
    if updated_plain_text != str(package.get("plain_text") or "").strip():
        package["plain_text"] = updated_plain_text
        changed = True

    if changed:
        _persist_review_package(review_dir, package)
    return package


def _load_review_package(review_dir: Path) -> Dict[str, Any]:
    package_path = review_dir / "package.json"
    if not package_path.exists():
        raise OfficialWechatDraftError(f"未找到 review package: {package_path}")
    return json.loads(package_path.read_text(encoding="utf-8"))


def is_wechat_article_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    hostname = (parsed.hostname or "").lower()
    return hostname == "mp.weixin.qq.com"


def is_wechat_origin_package(package: Dict[str, Any]) -> bool:
    return is_wechat_article_url(str(package.get("source_url") or ""))


def review_dir_uses_wechat_article_source(review_dir: str | Path) -> bool:
    package_path = Path(review_dir).expanduser().resolve() / "package.json"
    if not package_path.exists():
        return False

    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return is_wechat_origin_package(package)


def _resolve_asset_path(review_dir: Path, raw_path: str) -> Path | None:
    candidate = (raw_path or "").strip()
    if not candidate:
        return None

    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = (review_dir / candidate).resolve()
    else:
        path = path.resolve()

    if path.exists():
        return path
    return None


def _has_meaningful_review_body(content_html: str) -> bool:
    soup = BeautifulSoup(content_html or "", "html.parser")
    article = soup.find("article") or soup

    for header in list(article.find_all("header")):
        header.decompose()

    for section in list(article.find_all("section")):
        heading = section.find(["h1", "h2", "h3"])
        heading_text = heading.get_text(" ", strip=True) if heading else ""
        if heading_text in {"附件说明", "招聘岗位", "原文链接"}:
            section.decompose()

    text = article.get_text("\n", strip=True)
    if text:
        return True

    return article.find(["img", "table", "figure", "ul", "ol"]) is not None


def _rewrite_local_images(review_dir: Path, content_html: str) -> str:
    soup = BeautifulSoup(content_html or "", "html.parser")
    for img in soup.find_all("img"):
        drop_image = False
        for attr in ("src", "data-src", "data-original", "data-croporisrc"):
            raw = (img.get(attr) or "").strip()
            if not raw:
                continue
            if raw.startswith(("http://", "https://")):
                drop_image = True
                break
            if raw.startswith(("file://", "data:")):
                continue
            resolved = _resolve_asset_path(review_dir, raw)
            if resolved is not None:
                img[attr] = str(resolved)
                continue
            drop_image = True
            break
        if drop_image:
            img.decompose()
    return str(soup)


def _build_digest(plain_text: str, limit: int = 120) -> str:
    normalized = " ".join((plain_text or "").split()).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _normalize_cover_text(text: str, fallback: str) -> str:
    normalized = " ".join((text or "").split()).strip()
    return normalized or fallback


def _build_cover_display_text(hospital_name: str) -> str:
    normalized = _normalize_cover_text(hospital_name, "招聘公告")
    if len(normalized) > 16:
        return "招聘公告"
    return normalized


def _load_cover_font(size: int):
    if ImageFont is None:
        return None

    candidates = [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _measure_text(draw, text: str, font) -> int:
    if not text:
        return 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return max(0, bbox[2] - bbox[0])


def _select_cover_font(draw, text: str, max_width: int):
    for size in (96, 88, 80, 72, 64, 58):
        font = _load_cover_font(size)
        if _measure_text(draw, text, font) <= max_width:
            return font
    return _load_cover_font(52)


def _generate_default_cover(package: Dict[str, Any], review_dir: Path) -> str:
    if Image is None or ImageDraw is None:
        raise OfficialWechatDraftError("Pillow 未安装，无法为纯文字公告生成默认封面")

    generated_dir = review_dir / "assets" / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    output_path = generated_dir / "default_cover.png"

    width, height = 900, 500
    image = Image.new("RGB", (width, height), "#114b5f")
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / max(1, height - 1)
        r = int(17 + (25 - 17) * ratio)
        g = int(75 + (99 - 75) * ratio)
        b = int(95 + (112 - 95) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    draw.ellipse((width - 280, -80, width + 120, 260), fill="#2a7d87")
    draw.ellipse((-120, height - 180, 220, height + 140), fill="#195564")
    draw.rounded_rectangle((54, 54, width - 54, height - 54), radius=36, outline="#d9efe6", width=2)
    draw.rounded_rectangle((88, 96, 160, height - 96), radius=28, fill="#d9efe6")

    display_text = _build_cover_display_text(str(package.get("hospital") or ""))
    font = _select_cover_font(draw, display_text, max_width=560)
    bbox = draw.textbbox((0, 0), display_text, font=font)
    text_width = max(0, bbox[2] - bbox[0])
    text_height = max(0, bbox[3] - bbox[1])
    text_x = 210 + max(0, (width - 260 - text_width) / 2)
    text_y = (height - text_height) / 2 - 10
    draw.text((text_x, text_y), display_text, fill="#f9fffc", font=font)

    image.save(output_path, format="PNG", optimize=True)
    return str(output_path.resolve())


def _validate_attachment_backfill(package: Dict[str, Any]) -> None:
    attachments = package.get("attachments") or []
    if not attachments:
        return
    if is_wechat_origin_package(package):
        return

    missing_titles = []
    for attachment in attachments:
        if attachment.get("category") not in {"position_table", "application_form"}:
            continue
        if attachment.get("quick_paste_html") or attachment.get("insert_link"):
            continue
        missing_titles.append(str(attachment.get("title") or "未命名附件"))

    if missing_titles:
        raise OfficialWechatDraftError(
            "以下附件尚未回填公众号可用链接，请先运行附件上传回填流程: "
            + "；".join(missing_titles)
        )


def _select_cover(package: Dict[str, Any], review_dir: Path) -> str:
    for image in package.get("images") or []:
        if not image.get("keep"):
            continue
        if image.get("category") not in {"content_image", "position_image"}:
            continue
        resolved = _resolve_asset_path(review_dir, str(image.get("src") or ""))
        if resolved is not None:
            return str(resolved)

    # Some notice/publicity packages only preserve a QR or logo-like inline image.
    # If that's the only available local asset, it is still better than failing
    # before the WeChat draft flow even starts.
    for image in package.get("images") or []:
        if not image.get("keep"):
            continue
        resolved = _resolve_asset_path(review_dir, str(image.get("src") or ""))
        if resolved is not None:
            return str(resolved)

    for attachment in package.get("attachments") or []:
        if attachment.get("category") != "position_table":
            continue
        for image_path in attachment.get("rendered_images") or []:
            resolved = _resolve_asset_path(review_dir, str(image_path or ""))
            if resolved is not None:
                return str(resolved)

    return _generate_default_cover(package, review_dir)


def build_article_row_from_review_package(review_dir: str | Path) -> Dict[str, Any]:
    resolved_review_dir = Path(review_dir).expanduser().resolve()
    if not resolved_review_dir.exists():
        raise OfficialWechatDraftError(f"review_dir 不存在: {resolved_review_dir}")

    package = _ensure_rendered_position_tables(
        resolved_review_dir,
        _load_review_package(resolved_review_dir),
    )
    _validate_attachment_backfill(package)

    content_html = str(package.get("content_html") or "").strip()
    if not content_html:
        raise OfficialWechatDraftError("review package 缺少 content_html，无法生成草稿")
    if not _has_meaningful_review_body(content_html):
        raise OfficialWechatDraftError("review package 正文为空，已阻止生成只有标题的草稿")

    plain_text = str(package.get("plain_text") or "").strip()
    if not plain_text:
        plain_text = BeautifulSoup(content_html, "html.parser").get_text("\n", strip=True)

    return {
        "title": str(package.get("title") or "").strip(),
        "link": str(package.get("source_url") or "").strip(),
        "cover": _select_cover(package, resolved_review_dir),
        "content": _rewrite_local_images(resolved_review_dir, content_html),
        "plain_content": plain_text,
        "digest": _build_digest(plain_text),
        "author": str(package.get("hospital") or "").strip(),
        "hospital": str(package.get("hospital") or "").strip(),
        "account_name": str(package.get("hospital") or "").strip(),
    }


async def push_review_package_to_draft(review_dir: str | Path) -> Dict[str, Any]:
    push_article_row_to_draft, _, _ = _load_official_draft_pushers()
    row = build_article_row_from_review_package(review_dir)
    result = await push_article_row_to_draft(row)
    return {
        **result,
        "review_dir": str(Path(review_dir).expanduser().resolve()),
    }


async def push_review_package_to_shared_draft_series(
    review_dir: str | Path,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    _, push_article_row_to_shared_draft_series, _ = _load_official_draft_pushers()
    row = build_article_row_from_review_package(review_dir)
    result = await push_article_row_to_shared_draft_series(
        row,
        source_type="crawler_review",
        force=force,
    )
    return {
        **result,
        "review_dir": str(Path(review_dir).expanduser().resolve()),
    }


async def append_review_package_to_draft(
    review_dir: str | Path,
    draft_media_id: str,
) -> Dict[str, Any]:
    _, _, append_article_row_to_draft = _load_official_draft_pushers()
    row = build_article_row_from_review_package(review_dir)
    result = await append_article_row_to_draft(draft_media_id=draft_media_id, row=row)
    return {
        **result,
        "review_dir": str(Path(review_dir).expanduser().resolve()),
    }
