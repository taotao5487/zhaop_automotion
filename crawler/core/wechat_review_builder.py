import json
import re
import shutil
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None


REVIEW_NOISE_SELECTORS = [
    ".path",
    ".breadcrumb",
    ".breadcrumbs",
    ".current_Location",
    ".ctrl",
    ".share",
    ".shares",
    ".fenxiang",
    ".toolbar",
    ".aside",
    ".aside-container",
    ".aside-btn",
    ".qr-container",
    ".qr-content",
    ".pagination",
    ".pager",
    ".footer",
    ".header",
    ".sidebar",
    ".second-nav-wrap",
    ".second-nav",
    ".site-error-wrap",
    ".announcement_fh",
    ".yun_new_top",
    ".hp_head",
    ".navbg",
    ".hs-topbg",
    ".zicobox",
    ".ewm-box2",
    ".news-info",
    ".content-mark",
    ".article-info",
    ".info",
    ".meta",
    ".article-meta",
    ".share-box",
    ".bdsharebuttonbox",
]
REVIEW_NOISE_TEXT_PATTERNS = (
    r"^您现在的位置",
    r"^扫一扫[，,\s]*手机端浏览$",
    r"^上一篇",
    r"^下一篇",
    r"^返回顶部$",
    r"^字号[:：]?$",
    r"^日期[:：]?",
    r"^发布日期[:：]?",
    r"^发布时间[:：]?",
    r"^发布于[:：]?",
    r"^来源[:：]?",
    r"^本文来源[:：]?",
    r"^发布人[:：]?",
    r"^作者[:：]?",
    r"^编辑[:：]?",
    r"^审核[:：]?",
    r"^点击量[:：]?",
    r"^浏览[:：]?",
    r"^浏览量[:：]?",
    r"^阅读量[:：]?",
    r"^分享到[:：]?",
)
POSITION_KEYWORDS = (
    "岗位",
    "职位",
    "一览表",
    "需求表",
    "岗位表",
    "职位表",
    "招聘计划",
    "招录计划",
)
FORM_KEYWORDS = (
    "报名表",
    "登记表",
    "申请表",
    "信息收集表",
    "信息表",
    "汇总表",
    "扫码表",
)
QR_KEYWORDS = ("二维码", "扫码", "报名码", "填报", "报名", "应聘")
NOISE_IMAGE_KEYWORDS = (
    "icon",
    "ico",
    "share",
    "logo",
    "arrow",
    "xls",
    "xlsx",
    "doc",
    "docx",
    "pdf",
    "print",
)
SECTION_PATTERNS = (
    re.compile(r"^[一二三四五六七八九十]+、"),
    re.compile(r"^[(（]?[一二三四五六七八九十]+[)）]"),
    re.compile(r"^\d+[\.、]"),
)
INLINE_SECTION_SPLIT_PATTERNS = (
    re.compile(r"^(?P<prefix>.+?[。！？；])\s*(?P<heading>[一二三四五六七八九十]+\s*、\s*[^。！？；]{2,24})$"),
    re.compile(r"^(?P<prefix>.+?[。！？；])\s*(?P<heading>\d+\s*[\.、]\s*[^。！？；]{2,24})$"),
)


class WechatReviewBuilder:
    def __init__(self, output_root: str = "data/exports/wechat_review"):
        self.output_root = Path(output_root)

    def build_package(
        self,
        *,
        job: Dict[str, Any],
        title: str,
        localized_html: str,
        manifest: Dict[str, Any],
        run_time: datetime,
        article_dir: Path,
    ) -> Dict[str, Any]:
        run_id = run_time.strftime("%Y%m%d_%H%M%S")
        review_root = self.output_root / run_id
        review_root.mkdir(parents=True, exist_ok=True)
        review_dir = self._dedupe_directory(review_root / article_dir.name)
        self._copy_assets(article_dir, review_dir)

        soup = BeautifulSoup(localized_html or "", "lxml")
        root = soup.body or soup
        container = self._select_primary_container(root)
        self._prune_noise(container)
        self._normalize_sections(container)
        display_title = self._extract_display_title(container, title)
        self._dedupe_leading_title(container, display_title)

        image_decisions = self._filter_images(container, review_dir)
        attachments = [self._normalize_attachment(item) for item in manifest.get("attachments", [])]
        article_type = self._classify_article_type(image_decisions, attachments)
        position_rendered_images = [
            image_path
            for attachment in attachments
            if attachment["category"] == "position_table"
            for image_path in attachment.get("rendered_images", [])
        ]
        self._remove_attachment_links(container, attachments)

        content_html = self._build_content_html(
            job=job,
            title=display_title,
            container=container,
            article_type=article_type,
            attachments=attachments,
            position_rendered_images=position_rendered_images,
        )
        plain_text = self._build_plain_text(content_html)
        review_flags = self._build_review_flags(image_decisions, attachments)

        package = {
            "title": display_title,
            "source_url": job.get("url", ""),
            "hospital": job.get("hospital", ""),
            "article_type": article_type,
            "content_html": content_html,
            "plain_text": plain_text,
            "attachments": attachments,
            "images": image_decisions,
            "review_flags": review_flags,
            "review_generated_at": run_time.isoformat(),
            "review_dir": str(review_dir),
        }

        article_html = self._wrap_full_html(display_title, content_html)
        (review_dir / "article.html").write_text(article_html, encoding="utf-8")
        (review_dir / "article.txt").write_text(plain_text, encoding="utf-8")
        (review_dir / "package.json").write_text(
            json.dumps(package, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (review_dir / "review.md").write_text(
            self._build_review_markdown(package, position_rendered_images),
            encoding="utf-8",
        )

        return {
            "review_dir": str(review_dir),
            "article_html_path": str(review_dir / "article.html"),
            "article_text_path": str(review_dir / "article.txt"),
            "package_path": str(review_dir / "package.json"),
            "review_markdown_path": str(review_dir / "review.md"),
            "title": display_title,
            "article_type": article_type,
            "review_flags": review_flags,
        }

    def _select_primary_container(self, root: Tag) -> Tag:
        selectors = [
            "#Article",
            ".nrpage",
            "article.content",
            ".announcement_box",
            ".col-auto .content",
            ".col-auto #Article",
            ".main-c",
            ".mainbox",
            ".container-inner article.content",
            ".news-content",
            ".announcement_showbox",
            ".article-content",
            ".content-box",
            ".detail-content",
            ".article-detail",
            ".entry-content",
            ".post-content",
            ".main-content",
            ".container-inner",
            ".content",
        ]

        candidates: List[Tag] = []
        seen = set()
        for selector in selectors:
            try:
                nodes = root.select(selector)
            except Exception:
                continue
            for node in nodes:
                identifier = id(node)
                if identifier in seen:
                    continue
                seen.add(identifier)
                text = node.get_text(" ", strip=True)
                if len(re.sub(r"\s+", "", text)) < 60:
                    continue
                candidates.append(node)

        if not candidates:
            return root

        best_node = root
        best_score = -10**9
        for node in candidates:
            score = self._score_container(node)
            if score > best_score:
                best_score = score
                best_node = node
        return best_node

    def _score_container(self, node: Tag) -> int:
        text = node.get_text(" ", strip=True)
        text_len = len(re.sub(r"\s+", "", text))
        classes = " ".join(node.get("class", []))
        node_id = (node.get("id") or "").strip()
        links = len(node.find_all("a"))
        images = len(node.find_all("img"))
        score = min(text_len, 5000)
        if node.select_one("h1"):
            score += 500
        if node.select_one("h2"):
            score += 220
        if node_id == "Article":
            score += 900
        if any(key in classes.lower() for key in ("nrpage", "content", "announcement_box", "news-content", "article")):
            score += 240
        if any(key in classes.lower() for key in ("news-content", "announcement_box", "announcement_showbox", "main-c", "content")):
            score += 400
        nav_terms = ("您的位置", "网站导航", "医院首页", "新闻中心", "频道首页", "预约挂号", "医院概况", "新闻快报", "专家介绍")
        nav_hits = sum(text[:500].count(term) for term in nav_terms)
        score -= nav_hits * 260
        if text.startswith("欢迎访问"):
            score -= 800
        score -= links * 35
        score -= max(images - 3, 0) * 80
        return score

    def _extract_display_title(self, container: Tag, fallback_title: str) -> str:
        trusted_title = self._clean_title(fallback_title)
        if trusted_title:
            return trusted_title

        for selector in (
            ".content-title",
            ".announcement_show_tit",
            ".article-title",
            ".news-title",
            ".title",
            "h1",
            "h2",
        ):
            node = container.select_one(selector)
            if not node or not node.get_text(strip=True):
                continue
            candidate = self._clean_title(node.get_text(strip=True))
            if not candidate:
                continue
            if selector in {"h2", ".title"} and self._looks_like_section_heading(candidate):
                continue
            if selector == "h1" and self._looks_like_section_heading(candidate) and fallback_title:
                continue
            return candidate
        return trusted_title

    def _copy_assets(self, article_dir: Path, review_dir: Path) -> None:
        source_assets = article_dir / "assets"
        target_assets = review_dir / "assets"
        if source_assets.exists():
            shutil.copytree(source_assets, target_assets, dirs_exist_ok=True)

    def _prune_noise(self, container: Tag) -> None:
        for comment in container.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        for node in list(container.find_all(True)):
            if self._is_hidden_text_node(node):
                node.decompose()

        for selector in REVIEW_NOISE_SELECTORS:
            try:
                for node in container.select(selector):
                    node.decompose()
            except Exception:
                continue

        for node in list(container.find_all(True)):
            if not isinstance(node, Tag) or getattr(node, "attrs", None) is None:
                continue
            text = node.get_text(" ", strip=True)
            classes = " ".join(node.get("class", []))
            node_id = node.get("id", "")
            if re.search(r"(share|sidebar|aside|footer|header|toolbar|breadcrumb|pager|qr-container)", classes, re.I):
                node.decompose()
                continue
            if re.search(r"(share|sidebar|aside|footer|header|toolbar|breadcrumb|pager|qr)", node_id, re.I):
                node.decompose()
                continue
            if text and any(re.search(pattern, text) for pattern in REVIEW_NOISE_TEXT_PATTERNS):
                if not node.find("img"):
                    node.decompose()
                    continue
            if self._looks_like_meta_noise(text):
                node.decompose()
                continue
            if node.name in {"div", "section", "p", "span"} and not text and not node.find("img") and not node.find("a"):
                node.decompose()

    def _normalize_sections(self, container: Tag) -> None:
        should_promote = self._should_promote_blocks_to_headings(container)
        self._split_embedded_headings(container)
        for node in list(container.find_all(["p", "div", "strong"])):
            if not should_promote:
                break
            parent = node.parent
            if parent and isinstance(parent, Tag) and parent.name in {"li", "td", "th", "ul", "ol", "table"}:
                continue
            text = node.get_text(" ", strip=True)
            if not text or len(text) > 24:
                continue
            if node.find("img"):
                continue
            if node.find("a"):
                continue
            if not self._looks_like_section_heading(text):
                continue
            normalized_text = self._normalize_inline_text(text)
            heading_name = "h2" if re.match(r"^[一二三四五六七八九十]+、", normalized_text) else "h3"
            heading = BeautifulSoup("", "lxml").new_tag(heading_name)
            heading.string = normalized_text
            node.replace_with(heading)

        for node in list(container.find_all(["h1", "h2", "h3", "h4"])):
            if not self._normalize_inline_text(node.get_text(" ", strip=True)):
                node.decompose()

        for node in container.find_all(True):
            attrs_to_keep = {}
            for attr in ("href", "src", "alt"):
                value = node.get(attr)
                if value:
                    attrs_to_keep[attr] = value
            node.attrs = attrs_to_keep

    def _filter_images(self, container: Tag, review_dir: Path) -> List[Dict[str, Any]]:
        decisions: List[Dict[str, Any]] = []
        for img in list(container.find_all("img")):
            src = (img.get("src") or "").strip()
            image_meta = self._inspect_image(review_dir, src)
            context_text = self._collect_image_context(img)
            category = self._classify_image(src, context_text, image_meta)
            keep = category != "noise"
            decisions.append(
                {
                    "src": src,
                    "category": category,
                    "keep": keep,
                    "width": image_meta.get("width"),
                    "height": image_meta.get("height"),
                    "context": context_text[:120],
                }
            )
            if not keep:
                img.decompose()
                continue

            if category == "qr_in_body" and not self._has_qr_note_nearby(img):
                note = BeautifulSoup("", "lxml").new_tag("p")
                note.string = "请按公告要求扫描上方二维码报名或填写信息。"
                img.insert_after(note)

        return decisions

    def _inspect_image(self, review_dir: Path, src: str) -> Dict[str, Optional[int]]:
        if not src or src.startswith(("http://", "https://", "data:")):
            return {"width": None, "height": None, "is_image": None}
        image_path = review_dir / src
        if Image is None or not image_path.exists():
            return {"width": None, "height": None, "is_image": None}
        try:
            with Image.open(image_path) as image:
                return {"width": image.width, "height": image.height, "is_image": True}
        except Exception:
            return {"width": None, "height": None, "is_image": False}

    def _collect_image_context(self, img: Tag) -> str:
        fragments = []
        parent = img.parent
        if parent and isinstance(parent, Tag):
            fragments.append(parent.get_text(" ", strip=True))
            previous = parent.find_previous_sibling()
            if previous and isinstance(previous, Tag):
                fragments.append(previous.get_text(" ", strip=True))
            following = parent.find_next_sibling()
            if following and isinstance(following, Tag):
                fragments.append(following.get_text(" ", strip=True))
        return " ".join(item for item in fragments if item)

    def _classify_image(self, src: str, context_text: str, image_meta: Dict[str, Optional[int]]) -> str:
        lower_src = src.lower()
        lower_context = context_text.lower()
        width = image_meta.get("width") or 0
        height = image_meta.get("height") or 0
        max_side = max(width, height)
        min_side = min(width, height) if width and height else 0
        is_image = image_meta.get("is_image")

        if src and not src.startswith(("http://", "https://", "data:")) and is_image is False:
            return "noise"

        if any(keyword in lower_src for keyword in NOISE_IMAGE_KEYWORDS):
            if max_side and max_side <= 96:
                return "noise"
            if lower_src.endswith(".gif"):
                return "noise"
        if max_side and max_side <= 72:
            return "noise"

        if any(keyword in lower_context for keyword in QR_KEYWORDS) or "qrcode" in lower_src or "qr" in lower_src:
            if min_side >= 120 or max_side >= 180:
                return "qr_in_body"

        if any(keyword in lower_context for keyword in POSITION_KEYWORDS):
            return "position_image"
        if any(keyword in lower_src for keyword in ("job", "post", "岗位", "职位")):
            return "position_image"
        if max_side >= 420 and min_side >= 180 and lower_src.endswith((".png", ".jpg", ".jpeg", ".webp")):
            return "content_image"

        return "noise" if lower_src.endswith(".gif") else "content_image"

    def _has_qr_note_nearby(self, img: Tag) -> bool:
        parent = img.parent
        if not parent or not isinstance(parent, Tag):
            return False
        candidates = [parent.get_text(" ", strip=True)]
        previous = parent.find_previous_sibling()
        if previous and isinstance(previous, Tag):
            candidates.append(previous.get_text(" ", strip=True))
        following = parent.find_next_sibling()
        if following and isinstance(following, Tag):
            candidates.append(following.get_text(" ", strip=True))
        nearby_text = " ".join(item for item in candidates if item)
        return any(keyword in nearby_text for keyword in QR_KEYWORDS)

    def _normalize_attachment(self, attachment: Dict[str, Any]) -> Dict[str, Any]:
        title = attachment.get("title") or ""
        category = attachment.get("category") or self._classify_attachment(title)
        return {
            "title": title,
            "source_url": attachment.get("source_url", ""),
            "content_type": attachment.get("content_type"),
            "local_path": attachment.get("local_path"),
            "render_status": attachment.get("render_status"),
            "rendered_images": attachment.get("rendered_images", []),
            "category": category,
            "description": self._attachment_description(category),
        }

    def _classify_attachment(self, title: str) -> str:
        normalized = (title or "").lower()
        if any(keyword in normalized for keyword in POSITION_KEYWORDS):
            return "position_table"
        if any(keyword in normalized for keyword in FORM_KEYWORDS):
            return "application_form"
        return "other"

    def _attachment_description(self, category: str) -> str:
        if category == "position_table":
            return "岗位信息附件"
        if category == "application_form":
            return "报名或信息填报附件"
        return "补充附件"

    def _classify_article_type(self, images: List[Dict[str, Any]], attachments: List[Dict[str, Any]]) -> str:
        if any(item["keep"] and item["category"] == "qr_in_body" for item in images):
            return "qr_in_body"
        if any(item["keep"] and item["category"] == "position_image" for item in images):
            return "position_image"
        if any(
            attachment["category"] == "position_table" and attachment.get("rendered_images")
            for attachment in attachments
        ):
            return "attachment_driven"
        return "text_only"

    def _remove_attachment_links(self, container: Tag, attachments: List[Dict[str, Any]]) -> None:
        attachment_urls = {item.get("source_url") for item in attachments if item.get("source_url")}
        for link in list(container.find_all("a", href=True)):
            if not isinstance(link, Tag) or getattr(link, "attrs", None) is None:
                continue
            href = (link.get("href") or "").strip()
            if href not in attachment_urls:
                continue
            parent = link.parent
            if parent and isinstance(parent, Tag) and parent.name in {"p", "div", "li"}:
                text = parent.get_text(" ", strip=True)
                if len(text) <= 80:
                    parent.decompose()
                    continue
            link.decompose()

    def _build_content_html(
        self,
        *,
        job: Dict[str, Any],
        title: str,
        container: Tag,
        article_type: str,
        attachments: List[Dict[str, Any]],
        position_rendered_images: List[str],
    ) -> str:
        body_html = self._serialize_clean_body(container)
        rendered_gallery = ""
        if article_type == "attachment_driven" and position_rendered_images:
            gallery_items = "\n".join(
                f'<figure class="wechat-review-image"><img src="{escape(path, quote=True)}" alt="岗位信息图" /></figure>'
                for path in position_rendered_images
            )
            rendered_gallery = (
                '<section class="wechat-review-section">'
                "<h2>招聘岗位</h2>"
                "<p>岗位名称、人数及条件以以下岗位信息图为准。</p>"
                f"{gallery_items}"
                "</section>"
            )

        attachment_section = self._build_attachment_section(attachments)

        return (
            '<article class="wechat-review-article">'
            f"<header><h1>{escape(title)}</h1></header>"
            f'<section class="wechat-review-section">{body_html}</section>'
            f"{rendered_gallery}"
            f"{attachment_section}"
            "</article>"
        )

    def _serialize_clean_body(self, container: Tag) -> str:
        clone = BeautifulSoup(str(container), "lxml")
        root = clone.body or clone
        for tag in root.find_all(True):
            if tag.name in {"html", "body"}:
                continue
            attrs_to_keep = {}
            for attr in ("href", "src", "alt"):
                value = tag.get(attr)
                if value:
                    attrs_to_keep[attr] = value
            tag.attrs = attrs_to_keep
        inner_html = "".join(str(child) for child in root.children)
        return inner_html.strip()

    def _build_attachment_section(self, attachments: List[Dict[str, Any]]) -> str:
        link_items = []
        for attachment in attachments:
            title = escape(attachment["title"])
            source_url = escape(attachment.get("source_url") or "", quote=True)
            description = escape(attachment.get("description") or "")
            if source_url:
                link_items.append(
                    f'<li><a href="{source_url}">{title}</a><span>（{description}）</span></li>'
                )
            else:
                link_items.append(f"<li>{title}<span>（{description}）</span></li>")
        if not link_items:
            return ""
        return (
            '<section class="wechat-review-section">'
            "<h2>附件说明</h2>"
            "<ul class=\"wechat-review-attachments\">"
            f"{''.join(link_items)}"
            "</ul>"
            "</section>"
        )

    def _build_digest(self, container: Tag) -> str:
        paragraphs: List[str] = []
        for node in container.find_all(["p", "li"]):
            text = node.get_text(" ", strip=True)
            if not text:
                continue
            if any(keyword in text for keyword in ("附件", "原文链接")):
                continue
            if len(text) <= 6:
                continue
            paragraphs.append(text)
            if len(" ".join(paragraphs)) >= 180:
                break
        if not paragraphs:
            text = container.get_text(" ", strip=True)
            return self._truncate_text_nicely(text, 120)
        digest = " ".join(paragraphs[:2])
        return self._truncate_text_nicely(digest, 150)

    def _extract_deadline(self, text: str) -> str:
        for line in [item.strip() for item in text.splitlines() if item.strip()]:
            if "报名" not in line and "截止" not in line:
                continue
            if "年龄" in line:
                continue
            if "报名截止" in line or "截止时间" in line or "报名时间" in line:
                normalized = line
                normalized = re.sub(r"^(报名截止时间|报名时间|截止时间|报名截止)[：:\s]*", "", normalized)
                normalized = normalized.strip("：:，。； ")
                if 6 <= len(normalized) <= 32:
                    return normalized

        patterns = [
            r"(公告发布之日起至\d{4}年\d{1,2}月\d{1,2}日[^，。；\n]{0,12})",
            r"(至\d{4}年\d{1,2}月\d{1,2}日[^，。；\n]{0,12}截止)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip("：:，。； \n")
        return ""

    def _build_plain_text(self, content_html: str) -> str:
        soup = BeautifulSoup(content_html, "lxml")
        text = soup.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _should_promote_blocks_to_headings(self, container: Tag) -> bool:
        existing_headings = [
            node.get_text(" ", strip=True)
            for node in container.find_all(["h2", "h3", "h4"])
            if node.get_text(" ", strip=True)
        ]
        if len(existing_headings) >= 2:
            return False

        candidate_count = 0
        for node in container.find_all(["p", "div", "strong"]):
            text = node.get_text(" ", strip=True)
            if not text or len(text) > 24:
                continue
            if self._looks_like_section_heading(text):
                candidate_count += 1
        return candidate_count >= 2

    def _split_embedded_headings(self, container: Tag) -> None:
        for node in list(container.find_all(["p", "div"])):
            parent = node.parent
            if parent and isinstance(parent, Tag) and parent.name in {"li", "td", "th", "ul", "ol", "table"}:
                continue
            if node.find(["img", "table", "ul", "ol"]):
                continue
            if node.find("a"):
                continue

            text = self._normalize_inline_text(node.get_text(" ", strip=True))
            split_result = self._find_embedded_heading_split(text)
            if not split_result:
                continue

            prefix, heading_text = split_result
            prefix_node = BeautifulSoup("", "lxml").new_tag("p")
            prefix_node.string = prefix
            heading_name = "h2" if re.match(r"^[一二三四五六七八九十]+、", heading_text) else "h3"
            heading_node = BeautifulSoup("", "lxml").new_tag(heading_name)
            heading_node.string = heading_text

            node.insert_before(prefix_node)
            prefix_node.insert_after(heading_node)
            node.decompose()

    def _find_embedded_heading_split(self, text: str) -> Optional[Tuple[str, str]]:
        if not text or len(text) < 12:
            return None
        for pattern in INLINE_SECTION_SPLIT_PATTERNS:
            match = pattern.match(text)
            if not match:
                continue
            prefix = self._normalize_inline_text(match.group("prefix"))
            heading = self._normalize_inline_text(match.group("heading"))
            if len(prefix) < 8 or not self._looks_like_section_heading(heading):
                continue
            return prefix, heading
        return None

    def _looks_like_section_heading(self, text: str) -> bool:
        normalized = self._normalize_inline_text(text)
        if not normalized or re.search(r"[。；：:，,]$", normalized):
            return False

        first_level = re.match(r"^([一二三四五六七八九十]+)、(.+)$", normalized)
        if first_level:
            return len(first_level.group(2).strip()) >= 2

        bracket_level = re.match(r"^[(（]?[一二三四五六七八九十]+[)）]\s*(.+)$", normalized)
        if bracket_level:
            return len(bracket_level.group(1).strip()) >= 2

        digit_level = re.match(r"^\d+[\.、]\s*(.+)$", normalized)
        if digit_level:
            return len(digit_level.group(1).strip()) >= 2

        return any(pattern.search(normalized) for pattern in SECTION_PATTERNS)

    def _looks_like_meta_noise(self, text: str) -> bool:
        normalized = self._normalize_inline_text(text)
        if not normalized or len(normalized) > 80:
            return False
        meta_prefixes = (
            "日期",
            "发布日期",
            "发布时间",
            "发布于",
            "来源",
            "本文来源",
            "发布人",
            "作者",
            "编辑",
            "审核",
            "点击量",
            "浏览",
            "浏览量",
            "阅读量",
            "分享到",
            "分享至",
        )
        return any(normalized.startswith(prefix) for prefix in meta_prefixes)

    def _normalize_inline_text(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").strip())
        normalized = re.sub(r"([一二三四五六七八九十])\s+、", r"\1、", normalized)
        normalized = re.sub(r"(\d+)\s+([\.、])", r"\1\2", normalized)
        normalized = re.sub(r"\s+([、，。；：:])", r"\1", normalized)
        normalized = re.sub(r"([（(])\s+", r"\1", normalized)
        normalized = re.sub(r"\s+([)）])", r"\1", normalized)
        return normalized.strip()

    def _is_hidden_text_node(self, node: Tag) -> bool:
        attrs = getattr(node, "attrs", None)
        if not isinstance(attrs, dict):
            return False
        style = (attrs.get("style") or "").lower()
        text = re.sub(r"\s+", "", node.get_text(" ", strip=True))
        if not text:
            return False
        if node.find(["img", "table"]):
            return False
        if "display:none" in style or "visibility:hidden" in style or "opacity:0" in style or "mso-hide:all" in style:
            return True
        if not self._has_white_text_style(style):
            return False
        font_size = self._extract_font_size(style)
        if font_size is not None and font_size <= 14:
            return True
        return len(text) <= 8

    def _has_white_text_style(self, style: str) -> bool:
        return any(
            marker in style
            for marker in (
                "color:#fff",
                "color: #fff",
                "color:#ffffff",
                "color: #ffffff",
                "color:white",
                "color: white",
                "color:rgb(255,255,255)",
                "color: rgb(255,255,255)",
            )
        )

    def _extract_font_size(self, style: str) -> Optional[float]:
        match = re.search(r"font-size\s*:\s*([0-9.]+)\s*(px|pt)?", style)
        if not match:
            return None
        size = float(match.group(1))
        unit = (match.group(2) or "px").lower()
        if unit == "pt":
            return size
        return size * 0.75

    def _dedupe_leading_title(self, container: Tag, display_title: str) -> None:
        normalized_title = self._clean_title(display_title)
        for node in container.find_all(["h1", "h2"], limit=2):
            text = self._clean_title(node.get_text(" ", strip=True))
            if text and text == normalized_title:
                node.decompose()
                break

    def _truncate_text_nicely(self, text: str, limit: int) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").strip())
        if len(normalized) <= limit:
            return normalized
        punctuation_positions = [normalized.rfind(mark, 0, limit) for mark in "。！？；"]
        best = max(punctuation_positions)
        if best >= int(limit * 0.55):
            return normalized[: best + 1].strip()
        comma_best = max(normalized.rfind(mark, 0, limit) for mark in "，、")
        if comma_best >= int(limit * 0.65):
            return normalized[: comma_best].strip()
        return normalized[:limit].rstrip()

    def _clean_title(self, value: str) -> str:
        title = re.sub(r"\s+", " ", (value or "").strip())
        if not title:
            return ""
        title = re.sub(r"返回列表\s*>{0,2}", "", title).strip()
        title = re.sub(r"返回列表\s*≫*", "", title).strip()
        suffix_keywords = ("官方网站", "医院公告", "招贤纳士", "新闻中心", "人事招聘", "招聘公告", "通知公告")
        if "-" in title:
            parts = [part.strip() for part in title.split("-") if part.strip()]
            if len(parts) >= 2 and (
                len(parts) >= 3 or any(any(keyword in part for keyword in suffix_keywords) for part in parts[1:])
            ):
                title = parts[0]
            elif len(parts) >= 2 and all(len(part) <= 24 for part in parts[1:]):
                title = parts[0]
        for separator in (" - ", "——", "|", "__"):
            if separator in title:
                parts = [part.strip() for part in title.split(separator) if part.strip()]
                if parts:
                    return parts[0]
        return title

    def _build_review_flags(
        self,
        images: List[Dict[str, Any]],
        attachments: List[Dict[str, Any]],
    ) -> List[str]:
        flags = []
        if any(item["category"] == "noise" for item in images):
            flags.append("removed_noise_images")
        for attachment in attachments:
            status = attachment.get("render_status")
            if status in {"download_failed", "render_failed", "missing_dependency"}:
                flags.append(f"attachment_issue:{attachment['title']}:{status}")
        return flags

    def _wrap_full_html(self, title: str, content_html: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #f3f1ea;
      --paper: #fffdf8;
      --ink: #24342b;
      --muted: #5f6f65;
      --line: #dfe7df;
      --accent: #2f6f57;
      --accent-soft: #edf7f1;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 24px;
      background:
        radial-gradient(circle at top left, #efe8d4 0, transparent 28%),
        linear-gradient(180deg, #f4f1e7 0%, #eef4ee 100%);
      color: var(--ink);
      font: 16px/1.8 "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    }}
    .wechat-review-article {{
      max-width: 860px;
      margin: 0 auto;
      background: var(--paper);
      border: 1px solid rgba(47, 111, 87, 0.12);
      border-radius: 24px;
      padding: 32px 28px 40px;
      box-shadow: 0 20px 60px rgba(36, 52, 43, 0.08);
    }}
    h1, h2 {{ line-height: 1.4; }}
    h1 {{ margin: 0 0 16px; font-size: 34px; }}
    h2 {{
      margin: 28px 0 12px;
      font-size: 24px;
      border-left: 5px solid var(--accent);
      padding-left: 12px;
    }}
    p, li {{ color: var(--ink); }}
    a {{ color: var(--accent); word-break: break-all; }}
    img {{
      display: block;
      max-width: 100%;
      height: auto;
      margin: 16px auto;
      border-radius: 16px;
    }}
    .wechat-review-summary {{
      margin: 16px 0 28px;
      padding: 20px 22px;
      border-radius: 18px;
      background: var(--accent-soft);
      border: 1px solid rgba(47, 111, 87, 0.12);
    }}
    .wechat-review-summary ul,
    .wechat-review-attachments {{
      margin: 10px 0 0;
      padding-left: 20px;
    }}
    .wechat-review-attachments span {{
      color: var(--muted);
      margin-left: 6px;
    }}
    .wechat-review-section {{
      margin-top: 24px;
    }}
  </style>
</head>
<body>
{content_html}
</body>
</html>
"""

    def _build_review_markdown(self, package: Dict[str, Any], position_rendered_images: List[str]) -> str:
        attachment_lines = []
        for item in package["attachments"]:
            attachment_lines.append(
                f"- {item['title']} | {item['category']} | {item.get('render_status')}"
            )
        rendered_lines = "\n".join(f"- {path}" for path in position_rendered_images) or "- 无"
        flag_lines = "\n".join(f"- {flag}" for flag in package["review_flags"]) or "- 无"
        return (
            f"# {package['title']}\n\n"
            f"- 医院：{package['hospital']}\n"
            f"- 来源：{package['source_url']}\n"
            f"- 文章类型：{package['article_type']}\n\n"
            "## 转图岗位图\n\n"
            f"{rendered_lines}\n\n"
            "## 附件清单\n\n"
            f"{chr(10).join(attachment_lines) if attachment_lines else '- 无'}\n\n"
            "## 审核标记\n\n"
            f"{flag_lines}\n"
        )

    @staticmethod
    def _dedupe_directory(path: Path) -> Path:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            return path
        for suffix in range(2, 1000):
            candidate = path.with_name(f"{path.name}_{suffix}")
            if not candidate.exists():
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
        raise RuntimeError(f"无法创建唯一目录: {path}")
