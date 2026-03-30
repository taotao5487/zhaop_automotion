# WeChat RSS Draft Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure confirmed recruitment articles have full body content before WeChat draft push, keep the QR code at the end of the article, and restore container network access required for live WeChat API writes.

**Architecture:** Fix the content hydration gap at two layers: RSS polling for future articles and on-demand hydration in the official draft push path for already-stored articles. Keep the WeChat publishing logic centralized in `wechat_service/utils/official_wechat_draft.py`, and use a small Docker compose DNS change to stabilize outbound access from the running container.

**Tech Stack:** Python, FastAPI, pytest, SQLite, Docker Compose, WeChat Official Account draft APIs

---

### Task 1: Lock in failing behavior with focused tests

**Files:**
- Modify: `tests/test_official_draft_series.py`
- Modify: `tests/test_rss_env_compat.py`

- [ ] Add a failing test proving the draft builder hydrates missing article body before building payload.
- [ ] Add a failing test proving the QR card image survives HTML rewriting and stays inside the final content.
- [ ] Add a failing test proving title-confirmed articles are included in the full-content fetch candidate set.

### Task 2: Implement minimal content hydration fixes

**Files:**
- Modify: `wechat_service/utils/rss_poller.py`
- Modify: `wechat_service/utils/official_wechat_draft.py`

- [ ] Expand RSS full-content candidate selection so `title_confirmed` recruitment articles also fetch body content.
- [ ] Add an on-demand hydration helper in the official draft path to backfill `content`/`plain_content` when the DB row is still empty.
- [ ] Reject draft creation when article HTML is still empty after hydration.
- [ ] Preserve the QR code image by resolving local or relative QR paths before image rewriting.

### Task 3: Stabilize container DNS for live WeChat calls

**Files:**
- Modify: `docker-compose.yml`

- [ ] Add explicit DNS resolvers for the runtime container.
- [ ] Restart the container and verify in-container resolution for `api.weixin.qq.com` and WeChat image domains.

### Task 4: Verify end-to-end behavior

**Files:**
- No code changes required

- [ ] Run focused pytest targets and syntax checks.
- [ ] Restart the runtime container.
- [ ] Perform one real shared-draft push using an existing confirmed unpushed article.
- [ ] Read the resulting WeChat draft back via `draft/get` and verify the content contains actual article HTML plus the QR image tail.
