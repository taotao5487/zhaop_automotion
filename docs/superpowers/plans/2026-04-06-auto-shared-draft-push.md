# Auto Shared Draft Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cron-friendly automation entrypoint that batch-pushes recruitment articles into the shared WeChat draft series at scheduled times and sends webhook notifications for success, no-op, and failure outcomes.

**Architecture:** Keep scheduling outside the FastAPI service. Add a standalone Python script under `scripts/` that reads `.env`, calls the existing shared-draft batch API, classifies the API result, and posts a concise markdown message to the configured enterprise WeCom webhook. Update user-facing docs with server setup and cron examples.

**Tech Stack:** Python 3, httpx, python-dotenv, pytest, cron, existing FastAPI batch push API

---

### Task 1: Add failing tests for result classification and notification formatting

**Files:**
- Create: `tests/test_auto_push_shared_draft.py`
- Test: `tests/test_auto_push_shared_draft.py`

- [ ] **Step 1: Write the failing test**

```python
from scripts.auto_push_shared_draft import (
    classify_push_response,
    build_wecom_markdown_message,
)


def test_classify_push_response_success():
    payload = {"success": True, "data": {"processed_count": 3, "draft_media_ids": ["draft-1"]}}
    outcome = classify_push_response(payload)
    assert outcome.kind == "success"


def test_classify_push_response_no_articles_from_api_error():
    payload = {"success": False, "error": "当前没有未推送到共享草稿串的 confirmed 招聘文章"}
    outcome = classify_push_response(payload)
    assert outcome.kind == "no_articles"


def test_build_wecom_markdown_message_for_success_contains_counts():
    message = build_wecom_markdown_message(
        "success",
        {
            "processed_count": 4,
            "created_draft_count": 1,
            "draft_media_ids": ["draft-1"],
            "status_counts": {"pushed": 1, "appended": 3},
        },
        run_label="10:00",
    )
    assert "自动推送成功" in message
    assert "4" in message
    assert "draft-1" in message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auto_push_shared_draft.py -q`
Expected: FAIL with `ModuleNotFoundError` or missing symbol errors because the script does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create a new module at `scripts/auto_push_shared_draft.py` with importable helper functions and placeholders for the command-line entrypoint.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auto_push_shared_draft.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_auto_push_shared_draft.py scripts/auto_push_shared_draft.py
git commit -m "test: cover auto shared draft push outcome handling"
```

### Task 2: Implement the cron-friendly auto-push script

**Files:**
- Modify: `scripts/auto_push_shared_draft.py`
- Test: `tests/test_auto_push_shared_draft.py`

- [ ] **Step 1: Write the failing test**

```python
def test_classify_push_response_failure_uses_error_message():
    payload = {"success": False, "error": "推送共享草稿串失败: timeout"}
    outcome = classify_push_response(payload)
    assert outcome.kind == "failure"
    assert "timeout" in outcome.summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auto_push_shared_draft.py -q`
Expected: FAIL because failure classification is not complete yet.

- [ ] **Step 3: Write minimal implementation**

Implement:

- environment loading from `.env`
- API POST to `/api/recruitment/official-draft/series/push?all=true`
- no-article detection
- webhook markdown POST using `WEBHOOK_URL`
- CLI exit codes (`0` for success/no-articles, non-zero for failure)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auto_push_shared_draft.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_push_shared_draft.py tests/test_auto_push_shared_draft.py
git commit -m "feat: add shared draft auto-push script"
```

### Task 3: Document server setup and cron usage

**Files:**
- Modify: `docs/操作指南-常用命令速查.md`

- [ ] **Step 1: Write the failing test**

No automated doc test. Use a manual checklist instead:

```text
Checklist:
- Includes direct one-off manual run command
- Includes vim/.env variables if needed
- Includes cron entries for 10:00 and 16:30
- Explains "no articles" notification behavior
```

- [ ] **Step 2: Run verification to confirm the gap**

Run: `rg -n "16:30|auto_push_shared_draft|crontab|共享草稿串" docs/操作指南-常用命令速查.md`
Expected: Missing the new automation instructions before editing.

- [ ] **Step 3: Write minimal implementation**

Add a short section covering:

- manual script execution
- recommended cron lines
- expected webhook notifications
- reminder that manual API push remains available

- [ ] **Step 4: Run verification**

Run: `rg -n "16:30|auto_push_shared_draft|crontab|共享草稿串" docs/操作指南-常用命令速查.md`
Expected: Matches the new instructions.

- [ ] **Step 5: Commit**

```bash
git add docs/操作指南-常用命令速查.md
git commit -m "docs: add shared draft auto-push setup guide"
```

### Task 4: Final verification

**Files:**
- Modify: `scripts/auto_push_shared_draft.py`
- Modify: `tests/test_auto_push_shared_draft.py`
- Modify: `docs/操作指南-常用命令速查.md`

- [ ] **Step 1: Run targeted tests**

Run: `pytest tests/test_auto_push_shared_draft.py tests/test_official_draft_series.py -q`
Expected: PASS

- [ ] **Step 2: Run lightweight script help verification**

Run: `python scripts/auto_push_shared_draft.py --help`
Expected: Shows usage without traceback.

- [ ] **Step 3: Review changed files**

Run: `git diff -- scripts/auto_push_shared_draft.py tests/test_auto_push_shared_draft.py docs/操作指南-常用命令速查.md`
Expected: Only the automation script, its tests, and the doc updates are included.

- [ ] **Step 4: Commit**

```bash
git add scripts/auto_push_shared_draft.py tests/test_auto_push_shared_draft.py docs/操作指南-常用命令速查.md
git commit -m "feat: automate shared draft batch push"
```
