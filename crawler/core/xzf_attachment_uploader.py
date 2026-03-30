from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

XZF_APPID = "wx5c9674e6177b0f6a"
XZF_BASE_URL = "https://bohao.wsqytec.com"
XZF_FILE_CENTER_URL = f"{XZF_BASE_URL}/file.html"
XZF_FILE_CENTER_PATH = "pages/file/cloudFile/index"


class AttachmentUploadError(Exception):
    """Raised when the XZF attachment flow cannot continue."""


@dataclass(frozen=True)
class LocalAttachment:
    title: str
    local_path: Path
    upload_path: Path
    source_url: str = ""

    @property
    def search_names(self) -> List[str]:
        candidates = [self.upload_path.name, self.upload_path.stem]
        if self.local_path.name not in candidates:
            candidates.append(self.local_path.name)
        if self.local_path.stem not in candidates:
            candidates.append(self.local_path.stem)
        return [item for item in candidates if item]


@dataclass(frozen=True)
class XzfRemoteFile:
    file_id: str
    name: str
    create_time: str
    insert_link: str
    mini_program_path: str
    quick_paste_html: str
    auto_reply_html: str
    raw: Dict[str, Any]


@dataclass(frozen=True)
class XzfAttachmentUploadResult:
    title: str
    local_path: str
    upload_path: str
    upload_status: str
    insert_link: str = ""
    uploaded_at: str = ""
    upload_error: str = ""
    source_url: str = ""
    remote_file_id: str = ""
    remote_file_name: str = ""
    mini_program_path: str = ""
    quick_paste_html: str = ""
    auto_reply_html: str = ""


def sanitize_upload_filename(title: str, fallback_name: str) -> str:
    candidate = (title or "").strip() or fallback_name
    candidate = candidate.replace("/", "_").replace("\\", "_")
    candidate = candidate.replace("：", "_").replace(":", "_")
    candidate = candidate.replace("\n", " ").replace("\r", " ")
    candidate = re.sub(r"\s+", " ", candidate).strip(" .")
    if not candidate:
        candidate = fallback_name

    fallback_suffix = Path(fallback_name).suffix
    if fallback_suffix and Path(candidate).suffix.lower() != fallback_suffix.lower():
        candidate = f"{candidate}{fallback_suffix}"
    return candidate


def build_local_attachments_from_review_dir(review_dir: Path) -> List[LocalAttachment]:
    package_path = review_dir / "package.json"
    if not package_path.exists():
        raise AttachmentUploadError(f"未找到待审包文件: {package_path}")

    import json

    payload = json.loads(package_path.read_text(encoding="utf-8"))
    attachments = payload.get("attachments") or []
    if not attachments:
        return []

    staging_dir = review_dir / "attachment_upload_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    prepared: List[LocalAttachment] = []
    used_names = set()
    for item in attachments:
        relative_path = (item.get("local_path") or "").strip()
        if not relative_path:
            continue
        local_path = (review_dir / relative_path).resolve()
        if not local_path.exists():
            raise AttachmentUploadError(f"附件文件不存在: {local_path}")

        staged_name = sanitize_upload_filename(
            str(item.get("title") or ""),
            local_path.name,
        )
        staged_name = _dedupe_filename(staged_name, used_names)
        used_names.add(staged_name)

        staged_path = staging_dir / staged_name
        shutil.copy2(local_path, staged_path)
        prepared.append(
            LocalAttachment(
                title=str(item.get("title") or local_path.name),
                local_path=local_path,
                upload_path=staged_path,
                source_url=str(item.get("source_url") or ""),
            )
        )
    return prepared


def _dedupe_filename(filename: str, used_names: set[str]) -> str:
    if filename not in used_names:
        return filename

    path = Path(filename)
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = f"{stem}_{index}{suffix}"
        if candidate not in used_names:
            return candidate
    raise AttachmentUploadError(f"无法创建唯一上传文件名: {filename}")


class XzfAttachmentUploader:
    def __init__(
        self,
        *,
        profile_dir: Path,
        headless: bool = False,
        base_url: str = XZF_BASE_URL,
        file_center_url: str = XZF_FILE_CENTER_URL,
        login_timeout_seconds: int = 180,
        lookup_timeout_seconds: int = 120,
        poll_interval_seconds: int = 5,
    ):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.base_url = base_url.rstrip("/")
        self.file_center_url = file_center_url
        self.login_timeout_seconds = login_timeout_seconds
        self.lookup_timeout_seconds = lookup_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def upload_attachments(
        self,
        attachments: Sequence[LocalAttachment],
        *,
        allow_manual_upload: bool = True,
        prompt_fn: Optional[Callable[[str], str]] = input,
    ) -> List[XzfAttachmentUploadResult]:
        if not attachments:
            return []

        results: Dict[str, XzfAttachmentUploadResult] = {}
        missing: List[LocalAttachment] = []

        with self._browser_session() as page:
            self._ensure_logged_in(page, prompt_fn=prompt_fn)

            for attachment in attachments:
                try:
                    remote = self._find_remote_file(page, attachment)
                except Exception as exc:
                    results[str(attachment.local_path)] = XzfAttachmentUploadResult(
                        title=attachment.title,
                        local_path=str(attachment.local_path),
                        upload_path=str(attachment.upload_path),
                        upload_status="error",
                        upload_error=str(exc),
                        source_url=attachment.source_url,
                    )
                    continue

                if remote:
                    results[str(attachment.local_path)] = self._build_success_result(
                        attachment,
                        remote,
                        upload_status="linked",
                    )
                else:
                    missing.append(attachment)

            if missing and allow_manual_upload:
                self._assist_manual_upload(missing, prompt_fn=prompt_fn)
                for attachment in missing:
                    if str(attachment.local_path) in results:
                        continue
                    try:
                        remote = self._wait_for_remote_file(page, attachment)
                    except Exception as exc:
                        results[str(attachment.local_path)] = XzfAttachmentUploadResult(
                            title=attachment.title,
                            local_path=str(attachment.local_path),
                            upload_path=str(attachment.upload_path),
                            upload_status="error",
                            upload_error=str(exc),
                            source_url=attachment.source_url,
                        )
                        continue

                    if remote:
                        results[str(attachment.local_path)] = self._build_success_result(
                            attachment,
                            remote,
                            upload_status="uploaded",
                        )
                    else:
                        results[str(attachment.local_path)] = XzfAttachmentUploadResult(
                            title=attachment.title,
                            local_path=str(attachment.local_path),
                            upload_path=str(attachment.upload_path),
                            upload_status="missing",
                            upload_error="在小正方中未检索到对应附件，请确认已在电脑微信小程序中上传。",
                            source_url=attachment.source_url,
                        )
            else:
                for attachment in missing:
                    results[str(attachment.local_path)] = XzfAttachmentUploadResult(
                        title=attachment.title,
                        local_path=str(attachment.local_path),
                        upload_path=str(attachment.upload_path),
                        upload_status="missing",
                        upload_error="未命中小正方现有文件，且当前未启用人工上传协同。",
                        source_url=attachment.source_url,
                    )

        ordered_results = [results[str(item.local_path)] for item in attachments]
        return ordered_results

    def _build_success_result(
        self,
        attachment: LocalAttachment,
        remote: XzfRemoteFile,
        *,
        upload_status: str,
    ) -> XzfAttachmentUploadResult:
        return XzfAttachmentUploadResult(
            title=attachment.title,
            local_path=str(attachment.local_path),
            upload_path=str(attachment.upload_path),
            upload_status=upload_status,
            insert_link=remote.insert_link,
            uploaded_at=datetime.now().isoformat(timespec="seconds"),
            source_url=attachment.source_url,
            remote_file_id=remote.file_id,
            remote_file_name=remote.name,
            mini_program_path=remote.mini_program_path,
            quick_paste_html=remote.quick_paste_html,
            auto_reply_html=remote.auto_reply_html,
        )

    def _assist_manual_upload(
        self,
        attachments: Sequence[LocalAttachment],
        *,
        prompt_fn: Optional[Callable[[str], str]],
    ) -> None:
        if not attachments:
            return

        upload_dir = attachments[0].upload_path.parent
        self._open_path(upload_dir)
        self._launch_pc_wechat()

        if prompt_fn is None:
            return

        names = "\n".join(f"- {item.upload_path.name}" for item in attachments)
        prompt_fn(
            "以下附件尚未在小正方中找到。\n"
            "我已经帮你打开了上传目录，并尝试唤起电脑微信里的小正方助手。\n"
            "请在小程序“云上文件”中上传这些文件，完成后回到终端按回车继续检索：\n"
            f"{names}\n"
        )

    def _wait_for_remote_file(self, page: Any, attachment: LocalAttachment) -> Optional[XzfRemoteFile]:
        deadline = time.time() + self.lookup_timeout_seconds
        while time.time() < deadline:
            remote = self._find_remote_file(page, attachment)
            if remote:
                return remote
            self._sleep(self.poll_interval_seconds)
        return None

    def _find_remote_file(self, page: Any, attachment: LocalAttachment) -> Optional[XzfRemoteFile]:
        for search_key in attachment.search_names:
            records = self._fetch_remote_rows(page, search_key)
            matched = self._pick_best_match(records, attachment)
            if matched:
                return matched
        return None

    def _pick_best_match(
        self,
        records: Sequence[XzfRemoteFile],
        attachment: LocalAttachment,
    ) -> Optional[XzfRemoteFile]:
        expected_names = {
            _normalize_name(attachment.upload_path.name),
            _normalize_name(attachment.upload_path.stem),
            _normalize_name(attachment.local_path.name),
            _normalize_name(attachment.local_path.stem),
        }

        for record in records:
            record_names = {_normalize_name(record.name), _normalize_name(Path(record.name).stem)}
            if expected_names & record_names:
                return record
        return records[0] if len(records) == 1 else None

    def _fetch_remote_rows(self, page: Any, search_key: str) -> List[XzfRemoteFile]:
        payload = page.evaluate(
            """
            async ({ searchKey }) => {
                const base = window.location.origin;
                const url = `${base}/api/zhushou/center/data?type=1&pageNum=0&searchKey=${encodeURIComponent(searchKey)}`;
                const response = await fetch(url, {
                    credentials: "include",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest"
                    }
                });
                return await response.json();
            }
            """,
            {"searchKey": search_key},
        )
        rows = payload.get("data") or []
        return [self._remote_file_from_row(row) for row in rows if row.get("_id")]

    def _remote_file_from_row(self, row: Dict[str, Any]) -> XzfRemoteFile:
        file_id = str(row.get("_id") or "")
        name = str(row.get("name") or row.get("title") or file_id)
        path = f"/file/down?id={file_id}"
        insert_link = f"{self.base_url}/link.html?type=f&id={file_id}"
        quick_paste_html = (
            f'<a class="weapp_text_link js_weapp_entry" '
            f'data-miniprogram-appid="{XZF_APPID}" '
            f'data-miniprogram-path="{path}" '
            f'data-miniprogram-applink '
            f'data-miniprogram-nickname="小正方助手" '
            f'href="{insert_link}" '
            f'data-miniprogram-type="text" '
            f'data-miniprogram-servicetype _href>{name}</a><span>&nbsp;</span>'
        )
        auto_reply_html = (
            f'<a data-miniprogram-appid="{XZF_APPID}" '
            f'data-miniprogram-path="{path}" '
            f'data-miniprogram-type="text" href="{insert_link}">{name}</a>'
        )
        return XzfRemoteFile(
            file_id=file_id,
            name=name,
            create_time=str(row.get("createTime") or ""),
            insert_link=insert_link,
            mini_program_path=path,
            quick_paste_html=quick_paste_html,
            auto_reply_html=auto_reply_html,
            raw=dict(row),
        )

    def _ensure_logged_in(
        self,
        page: Any,
        *,
        prompt_fn: Optional[Callable[[str], str]],
    ) -> None:
        page.goto(self.file_center_url, wait_until="domcontentloaded")
        if self._is_logged_in(page):
            return

        try:
            if page.locator("#loginDiv a").is_visible():
                page.locator("#loginDiv a").click()
        except Exception:
            logger.info("未能自动点击小正方登录入口，继续等待人工扫码。")

        if prompt_fn is not None:
            prompt_fn(
                "请在弹出的页面里扫码登录小正方网页个人中心，完成后回到终端按回车继续。"
            )

        deadline = time.time() + self.login_timeout_seconds
        while time.time() < deadline:
            page.goto(self.file_center_url, wait_until="domcontentloaded")
            if self._is_logged_in(page):
                return
            self._sleep(2)

        raise XzfAttachmentUploadError("等待小正方网页登录超时。")

    @staticmethod
    def _is_logged_in(page: Any) -> bool:
        try:
            return page.locator("#logoutDiv").is_visible()
        except Exception:
            return False

    def _launch_pc_wechat(self) -> None:
        scheme = f"weixin://dl/business/?appid={XZF_APPID}&path={XZF_FILE_CENTER_PATH}"
        try:
            subprocess.run(["open", scheme], check=False, capture_output=True)
        except Exception as exc:
            logger.warning("尝试唤起电脑微信失败: %s", exc)

    @staticmethod
    def _open_path(path: Path) -> None:
        try:
            subprocess.run(["open", str(path)], check=False, capture_output=True)
        except Exception as exc:
            logger.warning("尝试打开目录失败: %s", exc)

    @staticmethod
    def _sleep(seconds: int) -> None:
        time.sleep(seconds)

    def _browser_session(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - dependency failure
            raise AttachmentUploadError(
                "未安装 Playwright，无法运行小正方附件流程。请先执行 `pip install playwright` 并安装浏览器。"
            ) from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)

        class _Session:
            def __init__(self, outer: XzfAttachmentUploader):
                self.outer = outer
                self._playwright = None
                self._context = None
                self._page = None

            def __enter__(self):
                self._playwright = sync_playwright().start()
                self._context = self._playwright.chromium.launch_persistent_context(
                    str(self.outer.profile_dir),
                    headless=self.outer.headless,
                    viewport={"width": 1440, "height": 960},
                )
                self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
                return self._page

            def __exit__(self, exc_type, exc, tb):
                if self._context is not None:
                    self._context.close()
                if self._playwright is not None:
                    self._playwright.stop()
                return False

        return _Session(self)


def _normalize_name(value: str) -> str:
    normalized = (value or "").strip().lower()
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


XzfAttachmentUploadError = AttachmentUploadError
