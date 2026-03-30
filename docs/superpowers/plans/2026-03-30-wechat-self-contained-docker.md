# WeChat Self-Contained Docker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/Users/xiongtao/Documents/zhaop_automotion` build and run the WeChat API container independently from the legacy folder.

**Architecture:** Add container build/run files in the current project root, mount only the current project's `.env` and `data/`, and provide a migration script to copy legacy runtime state into the current project before switching containers.

**Tech Stack:** Docker, Docker Compose, FastAPI, SQLite, shell migration script, pytest

---

### Task 1: Lock self-contained Docker expectations

**Files:**
- Create: `tests/test_docker_self_contained.py`

- [ ] **Step 1: Write the failing test**

```python
def test_compose_uses_local_env_and_data_mounts():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_docker_self_contained.py`
Expected: FAIL because `docker-compose.yml` and `Dockerfile` do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create project-root `Dockerfile` and `docker-compose.yml` that build from the current directory and mount `./data` and `./.env`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_docker_self_contained.py`
Expected: PASS

### Task 2: Add migration and run commands

**Files:**
- Create: `scripts/migrate_legacy_wechat_runtime.sh`
- Modify: `README.wechat.md`
- Modify: `WeChat推送到草稿箱新手操作指南.md`

- [ ] **Step 1: Write the failing test**

Add assertions in `tests/test_docker_self_contained.py` for the migration script path and required commands.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_docker_self_contained.py`
Expected: FAIL because the migration script is missing.

- [ ] **Step 3: Write minimal implementation**

Add a shell script that:
1. Backs up current `data/rss.db` if present
2. Copies legacy `data/` and `.env` into the current project
3. Stops/removes the legacy `wechat-download-api` container
4. Starts the current project container with compose

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_docker_self_contained.py`
Expected: PASS

### Task 3: Switch runtime and verify endpoints

**Files:**
- Modify: current runtime via Docker commands only

- [ ] **Step 1: Run syntax and targeted tests**

Run: `python -m py_compile app.py wechat_service/routes/recruitment.py wechat_service/utils/rss_store.py wechat_service/utils/recruitment_filter.py`

- [ ] **Step 2: Run Docker migration script**

Run: `bash scripts/migrate_legacy_wechat_runtime.sh`
Expected: new container is started from the current project.

- [ ] **Step 3: Verify runtime endpoints**

Run:

```bash
curl http://127.0.0.1:5001/api/health
curl http://127.0.0.1:5001/api/rss/status
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=5&profile=title_url"
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push"
```

Expected: all return 200-level responses and no dependency on the legacy folder remains.
