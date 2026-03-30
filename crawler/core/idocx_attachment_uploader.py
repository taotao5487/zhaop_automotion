from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from crawler.core.xzf_attachment_uploader import AttachmentUploadError, LocalAttachment, _normalize_name

logger = logging.getLogger(__name__)

IDOCX_BASE_URL = "https://idocx.cc"
IDOCX_HOME_URL = f"{IDOCX_BASE_URL}/"


@dataclass(frozen=True)
class IdocxRemoteFile:
    file_id: str
    uid: str
    name: str
    upload_time: str
    insert_link: str
    mini_program_path: str
    quick_paste_html: str
    auto_reply_html: str
    raw: Dict[str, Any]


@dataclass(frozen=True)
class IdocxAttachmentUploadResult:
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


class IdocxAttachmentUploader:
    def __init__(
        self,
        *,
        profile_dir: Path,
        headless: bool = False,
        home_url: str = IDOCX_HOME_URL,
        login_timeout_seconds: int = 180,
        upload_timeout_seconds: int = 180,
        poll_interval_seconds: int = 2,
    ):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.home_url = home_url
        self.login_timeout_seconds = login_timeout_seconds
        self.upload_timeout_seconds = upload_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def upload_attachments(
        self,
        attachments: Sequence[LocalAttachment],
        *,
        prompt_fn: Optional[Callable[[str], str]] = input,
    ) -> List[IdocxAttachmentUploadResult]:
        if not attachments:
            return []

        results: List[IdocxAttachmentUploadResult] = []
        with self._browser_session() as page:
            page.goto(self.home_url, wait_until="domcontentloaded")
            self._ensure_logged_in(page, prompt_fn=prompt_fn)
            xcx_info = self._load_xcx_info(page)

            for attachment in attachments:
                try:
                    existing = self._find_remote_file(page, attachment, xcx_info)
                    if existing:
                        results.append(self._build_success_result(attachment, existing, "linked"))
                        continue

                    before_ids = set(self._list_remote_file_ids(page))
                    self._set_input_file(page, attachment.upload_path)
                    remote = self._wait_for_uploaded_file(
                        page,
                        attachment,
                        xcx_info,
                        before_ids=before_ids,
                    )
                    results.append(self._build_success_result(attachment, remote, "uploaded"))
                except Exception as exc:
                    results.append(
                        IdocxAttachmentUploadResult(
                            title=attachment.title,
                            local_path=str(attachment.local_path),
                            upload_path=str(attachment.upload_path),
                            upload_status="error",
                            upload_error=str(exc),
                            source_url=attachment.source_url,
                        )
                    )

        return results

    def copy_results_to_clipboard(self, results: Sequence[IdocxAttachmentUploadResult]) -> bool:
        html_fragments = [item.quick_paste_html for item in results if item.quick_paste_html]
        if not html_fragments:
            return False

        html_text = "<br/>".join(html_fragments)
        plain_lines: List[str] = []
        for item in results:
            if item.mini_program_path:
                plain_lines.append(f"- {item.title}: {item.mini_program_path}")
            elif item.insert_link:
                plain_lines.append(f"- {item.title}: {item.insert_link}")
            elif item.upload_error:
                plain_lines.append(f"- {item.title}: {item.upload_error}")
        plain_text = "\n".join(plain_lines)

        with self._browser_session() as page:
            page.goto(self.home_url, wait_until="domcontentloaded")
            try:
                page.context.grant_permissions(
                    ["clipboard-read", "clipboard-write"],
                    origin=IDOCX_BASE_URL,
                )
            except Exception:
                logger.info("未能显式授予 clipboard 权限，继续尝试。")

            try:
                page.evaluate(
                    """
                    async ({ html, text }) => {
                        const item = new ClipboardItem({
                            "text/html": new Blob([html], { type: "text/html" }),
                            "text/plain": new Blob([text], { type: "text/plain" })
                        });
                        await navigator.clipboard.write([item]);
                        return true;
                    }
                    """,
                    {"html": html_text, "text": plain_text},
                )
                return True
            except Exception as exc:
                logger.warning("写入 idocx 富文本剪贴板失败: %s", exc)
                return False

    def _ensure_logged_in(
        self,
        page: Any,
        *,
        prompt_fn: Optional[Callable[[str], str]],
    ) -> None:
        user_info = self._load_user_info(page)
        if str(user_info.get("uid") or "").strip():
            return

        try:
            page.locator("a").filter(has_text="微信登录").first.click()
        except Exception:
            logger.info("未能自动打开 idocx 登录弹层，请手动点击页面上的微信登录。")

        if prompt_fn is not None:
            prompt_fn("请扫码登录 idocx，登录成功后回到终端按回车继续。")

        deadline = time.time() + self.login_timeout_seconds
        while time.time() < deadline:
            page.goto(self.home_url, wait_until="domcontentloaded")
            user_info = self._load_user_info(page)
            if str(user_info.get("uid") or "").strip():
                return
            time.sleep(2)

        raise AttachmentUploadError("等待 idocx 登录超时。")

    def _set_input_file(self, page: Any, upload_path: Path) -> None:
        page.locator("input.upload_input").first.set_input_files(str(upload_path))

    def _wait_for_uploaded_file(
        self,
        page: Any,
        attachment: LocalAttachment,
        xcx_info: Dict[str, Any],
        *,
        before_ids: set[str],
    ) -> IdocxRemoteFile:
        deadline = time.time() + self.upload_timeout_seconds
        while time.time() < deadline:
            remote = self._find_remote_file(page, attachment, xcx_info, exclude_ids=before_ids)
            if remote:
                return remote
            time.sleep(self.poll_interval_seconds)
        raise AttachmentUploadError(f"idocx 上传后未在列表中找到附件: {attachment.upload_path.name}")

    def _find_remote_file(
        self,
        page: Any,
        attachment: LocalAttachment,
        xcx_info: Dict[str, Any],
        *,
        exclude_ids: Optional[set[str]] = None,
    ) -> Optional[IdocxRemoteFile]:
        rows = self._load_attach_list(page)
        expected_names = {
            _normalize_name(attachment.upload_path.name),
            _normalize_name(attachment.upload_path.stem),
            _normalize_name(attachment.local_path.name),
            _normalize_name(attachment.local_path.stem),
        }

        matched: List[IdocxRemoteFile] = []
        for row in rows:
            fid = str(row.get("fid") or "")
            if exclude_ids and fid in exclude_ids:
                continue
            record = self._remote_file_from_row(row, xcx_info)
            record_names = {_normalize_name(record.name), _normalize_name(Path(record.name).stem)}
            if expected_names & record_names:
                matched.append(record)

        if not matched:
            return None
        return sorted(matched, key=lambda item: item.upload_time or "", reverse=True)[0]

    def _list_remote_file_ids(self, page: Any) -> List[str]:
        return [str(item.get("fid") or "") for item in self._load_attach_list(page)]

    def _load_attach_list(self, page: Any) -> List[Dict[str, Any]]:
        payload = page.evaluate(
            """
            async () => {
                const response = await fetch('/wfj/web/upload/list', {
                    method: 'POST',
                    credentials: 'include',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
                    },
                    body: ''
                });
                return await response.json();
            }
            """
        )
        return [item for item in (payload.get("data") or []) if item.get("fid")]

    def _load_user_info(self, page: Any) -> Dict[str, Any]:
        payload = page.evaluate(
            """
            async () => {
                const response = await fetch('/wfj/web/user/get', {
                    method: 'GET',
                    credentials: 'include'
                });
                return await response.json();
            }
            """
        )
        return dict(payload.get("data") or {})

    def _load_xcx_info(self, page: Any) -> Dict[str, Any]:
        payload = page.evaluate(
            """
            async () => {
                const response = await fetch('/wfj/web/xcx/get?web=idocx', {
                    method: 'GET',
                    credentials: 'include'
                });
                return await response.json();
            }
            """
        )
        data = dict(payload.get("data") or {})
        if not data.get("appid2") or not data.get("page2"):
            raise AttachmentUploadError("未能从 idocx 获取小程序配置信息。")
        return data

    def _remote_file_from_row(self, row: Dict[str, Any], xcx_info: Dict[str, Any]) -> IdocxRemoteFile:
        file_id = str(row.get("fid") or "")
        uid = str(row.get("uid") or "")
        name = str(row.get("name") or file_id)
        path = f"{xcx_info['page2']}?fid={file_id}&sid={uid}"
        escaped_name = html.escape(name, quote=False)
        quick_paste_html = (
            f'<a class="weapp_text_link" '
            f'data-miniprogram-appid="{xcx_info["appid2"]}" '
            f'data-miniprogram-path="{path}" '
            f'data-miniprogram-nickname="{html.escape(str(xcx_info["name"]), quote=True)}" '
            f'href="" data-miniprogram-type="text" '
            f'data-miniprogram-servicetype style="white-space: normal;">{escaped_name}</a>'
        )
        auto_reply_html = (
            f'<a data-miniprogram-appid="{xcx_info["appid2"]}" '
            f'data-miniprogram-path="{path}" data-miniprogram-type="text" '
            f'href="">{escaped_name}</a>'
        )
        return IdocxRemoteFile(
            file_id=file_id,
            uid=uid,
            name=name,
            upload_time=str(row.get("createTime") or ""),
            insert_link=path,
            mini_program_path=path,
            quick_paste_html=quick_paste_html,
            auto_reply_html=auto_reply_html,
            raw=dict(row),
        )

    def _build_success_result(
        self,
        attachment: LocalAttachment,
        remote: IdocxRemoteFile,
        upload_status: str,
    ) -> IdocxAttachmentUploadResult:
        return IdocxAttachmentUploadResult(
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

    def _browser_session(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            raise AttachmentUploadError(
                "未安装 Playwright，无法运行 idocx 自动上传。请先安装 playwright 并下载浏览器。"
            ) from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)

        class _Session:
            def __init__(self, outer: IdocxAttachmentUploader):
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
                try:
                    self._context.grant_permissions(
                        ["clipboard-read", "clipboard-write"],
                        origin=IDOCX_BASE_URL,
                    )
                except Exception:
                    pass
                self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
                return self._page

            def __exit__(self, exc_type, exc, tb):
                if self._context is not None:
                    self._context.close()
                if self._playwright is not None:
                    self._playwright.stop()
                return False

        return _Session(self)
