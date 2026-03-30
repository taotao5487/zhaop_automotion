# WeChat Default Cover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow text-only crawler review packages to generate a cleaner fallback cover image before pushing to the WeChat official draft flow.

**Architecture:** Keep the existing review-package adapter as the single normalization point. When normal image selection fails, generate one deterministic local PNG cover and return its absolute path as the article `cover`. The visual design should be minimal: show only the hospital name when it fits, otherwise fall back to `招聘公告`, with no title text or explanatory watermark.

**Tech Stack:** Python, Pillow, existing review-package adapter tests

---

### Task 1: Add the failing adapter test

**Files:**
- Modify: `test_basic.py`
- Test: `test_basic.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_review_package_draft_adapter_generates_default_cover():
    with TemporaryDirectory() as tmpdir:
        review_dir = Path(tmpdir) / "wechat_review" / "item_001"
        review_dir.mkdir(parents=True, exist_ok=True)

        package = {
            "title": "纯文字公告",
            "source_url": "https://example.com/source",
            "hospital": "测试医院",
            "plain_text": "这里只有正文，没有图片。",
            "content_html": "<article><p>这里只有正文，没有图片。</p></article>",
            "images": [],
            "attachments": [],
        }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python - <<'PY' ... await test_review_package_draft_adapter_generates_default_cover() ... PY`
Expected: FAIL because the adapter still raises `review package 缺少可用封面图`.

- [ ] **Step 3: Write minimal implementation**

```python
def _select_cover(package, review_dir):
    ...
    return _generate_default_cover(package, review_dir)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python - <<'PY' ... await test_review_package_draft_adapter_generates_default_cover() ... PY`
Expected: PASS and generated cover file exists.

### Task 2: Implement polished fallback cover generation

**Files:**
- Modify: `../Wechat_api/wechat-download-api/utils/crawler_review_package_draft.py`
- Test: `test_basic.py`

- [ ] **Step 1: Add deterministic cover generator helpers**

```python
def _generate_default_cover(package: Dict[str, Any], review_dir: Path) -> str:
    generated_dir = review_dir / "assets" / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 2: Render only the short headline into a PNG**

```python
image = Image.new("RGB", (900, 500), "#1f5c4a")
draw = ImageDraw.Draw(image)
```

- [ ] **Step 3: Apply the display-text rule**

```python
display_text = hospital_name if hospital_name fits else "招聘公告"
```

- [ ] **Step 4: Regenerate the same deterministic cover file on repeat runs**

```python
image.save(output_path, format="PNG", optimize=True)
```

- [ ] **Step 5: Keep existing selection priority and only use fallback last**

```python
for existing_source in ...:
    ...
return _generate_default_cover(package, review_dir)
```

### Task 3: Verify the focused flow

**Files:**
- Modify: `test_basic.py`
- Modify: `../Wechat_api/wechat-download-api/utils/crawler_review_package_draft.py`

- [ ] **Step 1: Run the focused test script**

Run: `python - <<'PY' ... await test_review_package_draft_adapter(); await test_review_package_draft_adapter_generates_default_cover() ... PY`
Expected: both tests return `True`.

- [ ] **Step 2: Run Python syntax verification**

Run: `python -m py_compile test_basic.py ../Wechat_api/wechat-download-api/utils/crawler_review_package_draft.py`
Expected: no output, exit code 0.
