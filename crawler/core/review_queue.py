import asyncio
import json
import logging
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web

from crawler.core.database import db_manager
from wechat_service.utils.recruitment_keyword_sets import (
    POST_RECRUITMENT_EXCLUDE_KEYWORDS,
)

logger = logging.getLogger(__name__)

DEFAULT_REVIEW_QUEUE_CONFIG = {
    "enabled": True,
    "recent_days": 30,
    "auto_open_browser": True,
    "hide_previously_discarded": True,
    "hide_old_without_keep": True,
}

DEFAULT_NON_RECRUITMENT_KEYWORDS = list(POST_RECRUITMENT_EXCLUDE_KEYWORDS)


@dataclass
class ReviewQueueConfig:
    enabled: bool = True
    recent_days: int = 30
    auto_open_browser: bool = True
    hide_previously_discarded: bool = True
    hide_old_without_keep: bool = True


@dataclass
class ReviewCandidate:
    candidate_id: str
    index: int
    job: Dict[str, Any]
    title: str
    url: str
    hospital: str
    source_site: str
    publish_date: Optional[datetime]
    publish_date_display: str
    recent: bool
    old: bool
    recruitment_likely: bool
    non_recruitment_likely: bool
    missing_publish_date: bool
    previously_kept: bool
    previously_discarded: bool
    default_hidden: bool
    hidden_reason: Optional[str]
    default_action: str
    sort_bucket: int
    auto_keep: bool

    def to_dict(self, recent_days: int) -> Dict[str, Any]:
        tags = []
        if self.previously_kept:
            tags.append({"key": "previously_kept", "label": "历史保留"})
        if self.previously_discarded:
            tags.append({"key": "previously_discarded", "label": "历史丢弃"})
        if self.recent:
            tags.append({"key": "recent", "label": f"最近{recent_days}天"})
        if self.old:
            tags.append({"key": "old", "label": "旧公告"})
        if self.recruitment_likely:
            tags.append({"key": "recruitment_likely", "label": "招聘倾向"})
        if self.non_recruitment_likely:
            tags.append({"key": "non_recruitment_likely", "label": "疑似非招聘"})
        if self.missing_publish_date:
            tags.append({"key": "missing_publish_date", "label": "缺少日期"})

        return {
            "candidate_id": self.candidate_id,
            "index": self.index,
            "title": self.title,
            "url": self.url,
            "hospital": self.hospital,
            "source_site": self.source_site,
            "publish_date": self.publish_date.isoformat() if self.publish_date else None,
            "publish_date_display": self.publish_date_display,
            "recent": self.recent,
            "old": self.old,
            "recruitment_likely": self.recruitment_likely,
            "non_recruitment_likely": self.non_recruitment_likely,
            "missing_publish_date": self.missing_publish_date,
            "previously_kept": self.previously_kept,
            "previously_discarded": self.previously_discarded,
            "default_hidden": self.default_hidden,
            "hidden_reason": self.hidden_reason,
            "default_action": self.default_action,
            "sort_bucket": self.sort_bucket,
            "auto_keep": self.auto_keep,
            "tags": tags,
        }


def load_review_queue_config(raw_config: Optional[Dict[str, Any]]) -> ReviewQueueConfig:
    payload = dict(DEFAULT_REVIEW_QUEUE_CONFIG)
    if isinstance(raw_config, dict):
        payload.update(raw_config)

    recent_days = payload.get("recent_days", DEFAULT_REVIEW_QUEUE_CONFIG["recent_days"])
    try:
        recent_days = int(recent_days)
    except (TypeError, ValueError):
        recent_days = DEFAULT_REVIEW_QUEUE_CONFIG["recent_days"]

    return ReviewQueueConfig(
        enabled=bool(payload.get("enabled", True)),
        recent_days=max(1, recent_days),
        auto_open_browser=bool(payload.get("auto_open_browser", True)),
        hide_previously_discarded=bool(payload.get("hide_previously_discarded", True)),
        hide_old_without_keep=bool(payload.get("hide_old_without_keep", True)),
    )


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if value in {None, ""}:
        return None

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def _title_matches_keywords(title: str, keywords: List[str]) -> bool:
    normalized_title = (title or "").strip().lower()
    return any(keyword and keyword.lower() in normalized_title for keyword in keywords)


def build_review_candidates(
    jobs_data: List[Dict[str, Any]],
    existing_decisions: Dict[str, Any],
    config: ReviewQueueConfig,
    recruitment_keywords: List[str],
    non_recruitment_keywords: Optional[List[str]] = None,
) -> List[ReviewCandidate]:
    current_time = datetime.now()
    recent_cutoff = current_time - timedelta(days=config.recent_days)
    candidates: List[ReviewCandidate] = []
    non_recruitment_markers = list(non_recruitment_keywords or DEFAULT_NON_RECRUITMENT_KEYWORDS)

    for index, job in enumerate(jobs_data, start=1):
        url = str(job.get("url") or "").strip()
        title = str(job.get("title") or "未命名公告").strip()
        hospital = str(job.get("hospital") or "未知医院").strip()
        source_site = str(job.get("source_site") or "").strip()
        publish_date = _coerce_datetime(job.get("publish_date"))
        missing_publish_date = publish_date is None
        recent = bool(publish_date and publish_date >= recent_cutoff)
        old = bool(publish_date and publish_date < recent_cutoff)
        recruitment_likely = _title_matches_keywords(title, recruitment_keywords)
        non_recruitment_likely = _title_matches_keywords(title, non_recruitment_markers)

        history = existing_decisions.get(url)
        history_decision = str(getattr(history, "decision", "") or "").strip()
        previously_kept = history_decision == "keep"
        previously_discarded = history_decision == "discard"

        auto_keep = previously_kept
        hidden_reason = None
        if previously_discarded and config.hide_previously_discarded:
            hidden_reason = "previously_discarded"
        elif old and not previously_kept and config.hide_old_without_keep:
            hidden_reason = "old"

        default_hidden = hidden_reason is not None
        default_action = "keep" if previously_kept else ("discard" if previously_discarded else "later")

        if previously_kept:
            sort_bucket = 0
        elif recent and not non_recruitment_likely:
            sort_bucket = 1
        elif missing_publish_date:
            sort_bucket = 2
        elif non_recruitment_likely:
            sort_bucket = 3
        else:
            sort_bucket = 4

        candidates.append(
            ReviewCandidate(
                candidate_id=f"candidate-{index}",
                index=index,
                job=dict(job),
                title=title,
                url=url,
                hospital=hospital,
                source_site=source_site,
                publish_date=publish_date,
                publish_date_display=publish_date.strftime("%Y-%m-%d") if publish_date else "未解析",
                recent=recent,
                old=old,
                recruitment_likely=recruitment_likely,
                non_recruitment_likely=non_recruitment_likely,
                missing_publish_date=missing_publish_date,
                previously_kept=previously_kept,
                previously_discarded=previously_discarded,
                default_hidden=default_hidden,
                hidden_reason=hidden_reason,
                default_action=default_action,
                sort_bucket=sort_bucket,
                auto_keep=auto_keep,
            )
        )

    def _sort_key(candidate: ReviewCandidate) -> tuple:
        publish_sort = candidate.publish_date or datetime.min
        return (candidate.sort_bucket, -publish_sort.timestamp() if candidate.publish_date else float("inf"), candidate.index)

    return sorted(candidates, key=_sort_key)


def build_review_summary(candidates: List[ReviewCandidate]) -> Dict[str, int]:
    reviewable = [item for item in candidates if not item.auto_keep]
    visible = [item for item in reviewable if not item.default_hidden]
    return {
        "candidate_total": len(candidates),
        "auto_kept_count": sum(1 for item in candidates if item.auto_keep),
        "visible_count": len(visible),
        "reviewable_count": len(reviewable),
        "hidden_old_count": sum(1 for item in reviewable if item.hidden_reason == "old"),
        "hidden_discarded_count": sum(1 for item in reviewable if item.hidden_reason == "previously_discarded"),
        "non_recruitment_likely_count": sum(1 for item in candidates if item.non_recruitment_likely),
        "missing_publish_date_count": sum(1 for item in candidates if item.missing_publish_date),
    }


def apply_review_decisions(
    candidates: List[ReviewCandidate],
    submitted_decisions: Dict[str, str],
) -> Dict[str, Any]:
    kept_jobs: List[Dict[str, Any]] = []
    persist_items: List[Dict[str, Any]] = []
    result_items: List[Dict[str, Any]] = []
    manual_keep_count = 0
    manual_discard_count = 0
    manual_later_count = 0

    for candidate in candidates:
        selected_action = submitted_decisions.get(candidate.candidate_id, candidate.default_action)
        if selected_action not in {"keep", "discard", "later"}:
            selected_action = candidate.default_action

        default_source = "history_keep" if candidate.auto_keep else (
            "history_discard" if candidate.previously_discarded and selected_action == candidate.default_action else (
                "hidden_old_default" if candidate.hidden_reason == "old" and selected_action == candidate.default_action else "manual"
            )
        )

        is_manual = (not candidate.auto_keep) and (not candidate.default_hidden or selected_action != candidate.default_action)
        if is_manual:
            if selected_action == "keep":
                manual_keep_count += 1
            elif selected_action == "discard":
                manual_discard_count += 1
            else:
                manual_later_count += 1

        if candidate.auto_keep or selected_action == "keep":
            kept_jobs.append(dict(candidate.job))

        if not candidate.auto_keep and selected_action in {"keep", "discard"}:
            persist_items.append(
                {
                    "url": candidate.url,
                    "decision": selected_action,
                    "source_site": candidate.source_site,
                    "title": candidate.title,
                    "hospital": candidate.hospital,
                    "publish_date": candidate.publish_date,
                }
            )

        result_items.append(
            {
                "candidate_id": candidate.candidate_id,
                "index": candidate.index,
                "title": candidate.title,
                "url": candidate.url,
                "final_action": "keep" if candidate.auto_keep else selected_action,
                "action_source": default_source if not is_manual else "manual",
                "default_hidden": candidate.default_hidden,
                "hidden_reason": candidate.hidden_reason,
                "previously_kept": candidate.previously_kept,
                "previously_discarded": candidate.previously_discarded,
            }
        )

    return {
        "kept_jobs": kept_jobs,
        "persist_items": persist_items,
        "result_items": result_items,
        "summary": {
            "manual_keep_count": manual_keep_count,
            "manual_discard_count": manual_discard_count,
            "manual_later_count": manual_later_count,
            "final_detail_count": len(kept_jobs),
            "auto_kept_count": sum(1 for item in candidates if item.auto_keep),
        },
    }


def _review_state_json(candidates: List[ReviewCandidate], config: ReviewQueueConfig, summary: Dict[str, int]) -> Dict[str, Any]:
    auto_kept = [item.to_dict(config.recent_days) for item in candidates if item.auto_keep]
    reviewable = [item.to_dict(config.recent_days) for item in candidates if not item.auto_keep]
    return {
        "generated_at": datetime.now().isoformat(),
        "config": {
            "recent_days": config.recent_days,
            "hide_previously_discarded": config.hide_previously_discarded,
            "hide_old_without_keep": config.hide_old_without_keep,
        },
        "summary": summary,
        "auto_kept": auto_kept,
        "reviewable": reviewable,
    }


def _json_for_script_tag(payload: Dict[str, Any]) -> str:
    """安全写入 <script>，避免 </script> 提前截断且保留可被 JSON.parse 的内容。"""
    return (
        json.dumps(payload, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def render_review_page(state: Dict[str, Any]) -> str:
    state_json = _json_for_script_tag(state)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>招聘公告审核队列</title>
  <style>
    :root {{
      --bg: #f5efe2;
      --paper: #fffdf7;
      --ink: #1f2933;
      --muted: #6b7280;
      --line: #d9cdb6;
      --accent: #004b5f;
      --accent-soft: #d8ede8;
      --warn: #9a3412;
      --warn-soft: #fde7dc;
      --good: #166534;
      --good-soft: #dcfce7;
      --bad: #991b1b;
      --bad-soft: #fee2e2;
      --chip: #efe7d5;
      --shadow: 0 12px 30px rgba(86, 69, 36, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(0, 75, 95, 0.14), transparent 30%),
        radial-gradient(circle at top right, rgba(154, 52, 18, 0.10), transparent 28%),
        linear-gradient(180deg, #f8f3e8 0%, var(--bg) 100%);
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
    }}
    .shell {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 28px 20px 80px;
    }}
    .hero {{
      background: rgba(255, 253, 247, 0.82);
      backdrop-filter: blur(10px);
      border: 1px solid rgba(217, 205, 182, 0.7);
      border-radius: 24px;
      padding: 24px;
      box-shadow: var(--shadow);
    }}
    .hero h1 {{
      margin: 0 0 10px;
      font-size: 30px;
      letter-spacing: 0.02em;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.7;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 14px;
      margin-top: 22px;
    }}
    .summary-card {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
    }}
    .summary-card strong {{
      display: block;
      font-size: 28px;
      margin-bottom: 6px;
    }}
    .summary-card span {{
      color: var(--muted);
      font-size: 14px;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      align-items: center;
      justify-content: space-between;
      margin: 24px 0 18px;
    }}
    .toolbar-left, .toolbar-right {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
    }}
    .toggle {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 12px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255, 253, 247, 0.76);
      color: var(--ink);
    }}
    .status {{
      color: var(--muted);
      font-size: 14px;
    }}
    .queue {{
      display: grid;
      gap: 16px;
    }}
    .card {{
      background: rgba(255, 253, 247, 0.92);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px;
      box-shadow: var(--shadow);
    }}
    .card.hidden-default {{
      border-style: dashed;
      opacity: 0.84;
    }}
    .card-head {{
      display: flex;
      gap: 14px;
      justify-content: space-between;
      align-items: flex-start;
    }}
    .card-index {{
      flex: none;
      width: 54px;
      height: 54px;
      border-radius: 16px;
      background: var(--accent);
      color: white;
      display: grid;
      place-items: center;
      font-weight: 700;
      font-size: 20px;
    }}
    .card-body {{
      flex: 1;
      min-width: 0;
    }}
    .card-body h2 {{
      margin: 0;
      font-size: 20px;
      line-height: 1.5;
    }}
    .meta {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 14px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .url {{
      margin-top: 10px;
      word-break: break-all;
      font-size: 14px;
    }}
    .url a {{
      color: var(--accent);
      text-decoration: none;
    }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 7px 10px;
      border-radius: 999px;
      background: var(--chip);
      color: var(--ink);
      font-size: 13px;
    }}
    .chip.recent, .chip.previously_kept, .chip.recruitment_likely {{
      background: var(--good-soft);
      color: var(--good);
    }}
    .chip.old, .chip.previously_discarded, .chip.non_recruitment_likely {{
      background: var(--warn-soft);
      color: var(--warn);
    }}
    .chip.missing_publish_date {{
      background: var(--accent-soft);
      color: var(--accent);
    }}
    .actions {{
      margin-top: 16px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .action-option {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: var(--paper);
      cursor: pointer;
    }}
    .action-option.keep {{
      border-color: #9fd7ac;
    }}
    .action-option.discard {{
      border-color: #ebb0b0;
    }}
    .action-option.later {{
      border-color: #b9ccd6;
    }}
    .card-note {{
      margin-top: 12px;
      color: var(--muted);
      font-size: 13px;
    }}
    .auto-keep {{
      margin: 22px 0 12px;
      padding: 16px 18px;
      border-radius: 20px;
      border: 1px solid var(--line);
      background: rgba(220, 252, 231, 0.72);
    }}
    .auto-keep h3 {{
      margin: 0 0 8px;
      font-size: 18px;
    }}
    .auto-keep ul {{
      margin: 0;
      padding-left: 20px;
      line-height: 1.7;
    }}
    .submit-bar {{
      position: sticky;
      bottom: 18px;
      margin-top: 26px;
      background: rgba(255, 253, 247, 0.94);
      border: 1px solid var(--line);
      border-radius: 22px;
      box-shadow: var(--shadow);
      padding: 16px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 14px;
      justify-content: space-between;
    }}
    .submit-bar button {{
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      background: var(--accent);
      color: white;
      font-size: 15px;
      cursor: pointer;
    }}
    .submit-bar button[disabled] {{
      opacity: 0.6;
      cursor: wait;
    }}
    .hidden {{
      display: none !important;
    }}
    @media (max-width: 720px) {{
      .shell {{ padding: 18px 14px 70px; }}
      .hero h1 {{ font-size: 24px; }}
      .card-head {{ flex-direction: column; }}
      .card-index {{ width: 48px; height: 48px; font-size: 18px; }}
      .submit-bar {{ bottom: 10px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h1>招聘公告人工审核队列</h1>
      <p>点开原文确认是否是最新招聘公告，再决定保留、丢弃或稍后。页面底部点击“完成并继续”后，命令行才会进入详情抓取。</p>
      <div class="summary-grid" id="summary-grid"></div>
    </section>

    <section class="auto-keep hidden" id="auto-keep-box">
      <h3>历史已保留，直接进入下一步</h3>
      <ul id="auto-keep-list"></ul>
    </section>

    <section class="toolbar">
      <div class="toolbar-left">
        <label class="toggle"><input type="checkbox" id="toggle-old" /> 显示默认隐藏的旧公告</label>
        <label class="toggle"><input type="checkbox" id="toggle-discarded" /> 显示历史已丢弃项</label>
      </div>
      <div class="toolbar-right">
        <span class="status" id="queue-status"></span>
      </div>
    </section>

    <form id="review-form">
      <section class="queue" id="queue"></section>
      <div class="submit-bar">
        <div class="status" id="submit-status">保留项会进入详情处理；丢弃项会记住，下次默认跳过。</div>
        <button type="submit" id="submit-btn">完成并继续</button>
      </div>
    </form>
  </div>

  <script id="review-state" type="application/json">{state_json}</script>
  <script>
    const state = JSON.parse(document.getElementById("review-state").textContent);
    const queueEl = document.getElementById("queue");
    const summaryEl = document.getElementById("summary-grid");
    const statusEl = document.getElementById("queue-status");
    const submitStatusEl = document.getElementById("submit-status");
    const submitBtn = document.getElementById("submit-btn");
    const formEl = document.getElementById("review-form");
    const showOldEl = document.getElementById("toggle-old");
    const showDiscardedEl = document.getElementById("toggle-discarded");
    const autoKeepBoxEl = document.getElementById("auto-keep-box");
    const autoKeepListEl = document.getElementById("auto-keep-list");

    function escapeHtml(value) {{
      return String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }}

    function renderSummary() {{
      const items = [
        ["候选总数", state.summary.candidate_total],
        ["可审核", state.summary.reviewable_count],
        ["自动保留", state.summary.auto_kept_count],
        ["默认隐藏旧公告", state.summary.hidden_old_count],
        ["历史已丢弃", state.summary.hidden_discarded_count],
        ["疑似非招聘", state.summary.non_recruitment_likely_count],
      ];
      summaryEl.innerHTML = items.map(([label, value]) => `
        <article class="summary-card">
          <strong>${{value}}</strong>
          <span>${{label}}</span>
        </article>
      `).join("");
    }}

    function renderAutoKeep() {{
      if (!state.auto_kept.length) {{
        autoKeepBoxEl.classList.add("hidden");
        return;
      }}

      autoKeepListEl.innerHTML = state.auto_kept.map(item => `
        <li>
          <strong>${{escapeHtml(item.title)}}</strong>
          <span> · ${{escapeHtml(item.hospital)}} · ${{escapeHtml(item.publish_date_display)}}</span>
          <a href="${{escapeHtml(item.url)}}" target="_blank" rel="noreferrer">打开原文</a>
        </li>
      `).join("");
      autoKeepBoxEl.classList.remove("hidden");
    }}

    function shouldShow(item) {{
      if (!item.default_hidden) {{
        return true;
      }}
      if (item.hidden_reason === "old") {{
        return showOldEl.checked;
      }}
      if (item.hidden_reason === "previously_discarded") {{
        return showDiscardedEl.checked;
      }}
      return true;
    }}

    function renderQueue() {{
      const visibleCount = state.reviewable.filter(shouldShow).length;
      statusEl.textContent = `当前显示 ${{visibleCount}} / ${{state.reviewable.length}} 条可审核候选`;

      queueEl.innerHTML = state.reviewable.filter(shouldShow).map(item => {{
        const tagHtml = item.tags.map(tag => `<span class="chip ${{tag.key}}">${{escapeHtml(tag.label)}}</span>`).join("");
        const note = item.hidden_reason === "old"
          ? "这条默认因为公告较旧而隐藏；如果你确认仍需抓取，可以直接改成保留。"
          : item.hidden_reason === "previously_discarded"
            ? "这条历史上已被丢弃；如果判断有误，可以重新改成保留。"
            : "打开原文确认后，再选择保留、丢弃或稍后。";

        return `
          <article class="card ${{item.default_hidden ? "hidden-default" : ""}}">
            <div class="card-head">
              <div class="card-index">${{item.index}}</div>
              <div class="card-body">
                <h2>${{escapeHtml(item.title)}}</h2>
                <div class="meta">
                  <span>医院：${{escapeHtml(item.hospital)}}</span>
                  <span>日期：${{escapeHtml(item.publish_date_display)}}</span>
                  <span>来源：${{escapeHtml(item.source_site || "未知站点")}}</span>
                </div>
                <div class="url">
                  <a href="${{escapeHtml(item.url)}}" target="_blank" rel="noreferrer">打开原文</a>
                  <span> · ${{escapeHtml(item.url)}}</span>
                </div>
                <div class="chips">${{tagHtml}}</div>
                <div class="actions">
                  <label class="action-option keep">
                    <input type="radio" name="${{item.candidate_id}}" value="keep" ${{item.default_action === "keep" ? "checked" : ""}} />
                    保留
                  </label>
                  <label class="action-option discard">
                    <input type="radio" name="${{item.candidate_id}}" value="discard" ${{item.default_action === "discard" ? "checked" : ""}} />
                    丢弃
                  </label>
                  <label class="action-option later">
                    <input type="radio" name="${{item.candidate_id}}" value="later" ${{item.default_action === "later" ? "checked" : ""}} />
                    稍后
                  </label>
                </div>
                <div class="card-note">${{escapeHtml(note)}}</div>
              </div>
            </div>
          </article>
        `;
      }}).join("");
    }}

    showOldEl.addEventListener("change", renderQueue);
    showDiscardedEl.addEventListener("change", renderQueue);

    formEl.addEventListener("submit", async (event) => {{
      event.preventDefault();
      submitBtn.disabled = true;
      submitStatusEl.textContent = "正在保存审核结果，请稍候...";

      const decisions = {{}};
      for (const item of state.reviewable) {{
        const selected = formEl.querySelector(`input[name="${{item.candidate_id}}"]:checked`);
        decisions[item.candidate_id] = selected ? selected.value : item.default_action;
      }}

      try {{
        const response = await fetch("/api/submit", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ decisions }}),
        }});

        if (!response.ok) {{
          throw new Error(`HTTP ${{response.status}}`);
        }}

        const payload = await response.json();
        submitStatusEl.textContent = payload.message || "审核结果已保存，命令行会继续执行。";
      }} catch (error) {{
        submitBtn.disabled = false;
        submitStatusEl.textContent = `提交失败：${{error.message}}`;
      }}
    }});

    renderSummary();
    renderAutoKeep();
    renderQueue();
  </script>
</body>
</html>"""


class _ReviewQueueServer:
    def __init__(self, state: Dict[str, Any], html: str):
        self.state = state
        self.html = html
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.BaseSite] = None
        self._submitted_future: Optional[asyncio.Future] = None
        self.base_url = ""

    async def start(self) -> str:
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/state", self._handle_state)
        app.router.add_post("/api/submit", self._handle_submit)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        sockets = getattr(getattr(self._site, "_server", None), "sockets", None) or []
        if not sockets:
            raise RuntimeError("审核队列服务启动失败：未获取到监听端口")

        port = sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"
        self._submitted_future = asyncio.get_running_loop().create_future()
        return self.base_url

    async def wait_for_submission(self) -> Dict[str, str]:
        if not self._submitted_future:
            raise RuntimeError("审核队列服务尚未启动")
        return await self._submitted_future

    async def close(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=self.html, content_type="text/html", charset="utf-8")

    async def _handle_state(self, request: web.Request) -> web.Response:
        return web.json_response(self.state)

    async def _handle_submit(self, request: web.Request) -> web.Response:
        payload = await request.json()
        decisions = payload.get("decisions") or {}
        if not isinstance(decisions, dict):
            return web.json_response({"message": "提交格式错误"}, status=400)

        valid_ids = {item["candidate_id"] for item in self.state.get("reviewable", [])}
        normalized = {
            key: value
            for key, value in decisions.items()
            if key in valid_ids and value in {"keep", "discard", "later"}
        }
        if self._submitted_future and not self._submitted_future.done():
            self._submitted_future.set_result(normalized)
        return web.json_response({"message": "审核结果已提交，命令行会继续执行。"})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def review_jobs_for_detail_processing(
    crawler: Any,
    jobs_data: List[Dict[str, Any]],
    logger_instance: Optional[logging.Logger] = None,
) -> List[Dict[str, Any]]:
    active_logger = logger_instance or logger
    if not jobs_data:
        return []

    config = load_review_queue_config(getattr(crawler, "global_config", {}).get("review_queue"))
    if not config.enabled:
        active_logger.info("审核队列配置已关闭，详情处理直接消费本次新增URL")
        return jobs_data

    urls = [str(item.get("url") or "").strip() for item in jobs_data if str(item.get("url") or "").strip()]
    await db_manager.touch_review_decisions(jobs_data)
    existing_decisions = await db_manager.get_review_decisions(urls)
    candidates = build_review_candidates(
        jobs_data,
        existing_decisions,
        config,
        recruitment_keywords=list(getattr(crawler, "recruitment_keep_keywords", []) or []),
    )
    summary = build_review_summary(candidates)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    review_root = Path(getattr(crawler, "export_directory", "data/exports")) / "detail_review" / run_id
    review_root.mkdir(parents=True, exist_ok=True)

    state = _review_state_json(candidates, config, summary)
    _write_json(review_root / "candidates.json", state)
    html = render_review_page(state)
    (review_root / "index.html").write_text(html, encoding="utf-8")

    active_logger.info(
        "详情审核候选汇总: 总数=%s, 自动保留=%s, 默认隐藏旧公告=%s, 历史已丢弃=%s, 疑似非招聘=%s, 输出目录=%s",
        summary["candidate_total"],
        summary["auto_kept_count"],
        summary["hidden_old_count"],
        summary["hidden_discarded_count"],
        summary["non_recruitment_likely_count"],
        review_root,
    )

    reviewable = [item for item in candidates if not item.auto_keep]
    if not reviewable:
        outcome = apply_review_decisions(candidates, {})
        await db_manager.save_review_decisions(outcome["persist_items"])
        review_payload = {
            "submitted_at": datetime.now().isoformat(),
            "summary": {**summary, **outcome["summary"]},
            "results": outcome["result_items"],
        }
        _write_json(review_root / "review_results.json", review_payload)
        active_logger.info("所有候选均已在历史审核中保留，本次直接进入详情处理 %s 条", len(outcome["kept_jobs"]))
        return outcome["kept_jobs"]

    server = _ReviewQueueServer(state, html)
    base_url = await server.start()
    active_logger.info("审核页面已启动: %s", base_url)
    active_logger.info("请在浏览器中审核后点击“完成并继续”，命令行会自动恢复。")

    if config.auto_open_browser:
        try:
            webbrowser.open(base_url)
        except Exception as exc:
            active_logger.warning("自动打开浏览器失败，请手动访问 %s，错误: %s", base_url, exc)

    try:
        submitted_decisions = await server.wait_for_submission()
    finally:
        await server.close()

    outcome = apply_review_decisions(candidates, submitted_decisions)
    await db_manager.save_review_decisions(outcome["persist_items"])

    review_payload = {
        "submitted_at": datetime.now().isoformat(),
        "summary": {**summary, **outcome["summary"]},
        "results": outcome["result_items"],
        "submitted_decisions": submitted_decisions,
    }
    _write_json(review_root / "review_results.json", review_payload)

    active_logger.info(
        "详情审核已完成: 人工保留=%s, 人工丢弃=%s, 人工稍后=%s, 最终进入详情=%s, 结果文件=%s",
        outcome["summary"]["manual_keep_count"],
        outcome["summary"]["manual_discard_count"],
        outcome["summary"]["manual_later_count"],
        outcome["summary"]["final_detail_count"],
        review_root / "review_results.json",
    )
    return outcome["kept_jobs"]
