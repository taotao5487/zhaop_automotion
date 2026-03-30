# WeChat Default Cover Design

## Goal

When a crawler review package has no reusable images, still produce a valid `cover` so the article can be pushed to the WeChat official draft flow.

## Current Behavior

- `build_article_row_from_review_package()` relies on `_select_cover()`.
- `_select_cover()` only accepts kept body images, other kept images, or rendered attachment images.
- Text-only packages therefore raise `OfficialWechatDraftError` before draft creation starts.

## Chosen Approach

Generate a local fallback cover image inside the review package directory when `_select_cover()` cannot find an existing asset.

## Why This Approach

- It keeps the WeChat API publishing flow unchanged.
- It fixes both single-draft and shared-draft-series paths because both use the same adapter.
- It avoids scraping the original site again during push time.

## Design Details

- Add a small helper in `utils/crawler_review_package_draft.py` that generates a PNG cover under `assets/generated/default_cover.png`.
- Render a simplified cover using Pillow with minimal text and no explanatory watermark text.
- Default to showing only the hospital name in a larger size.
- If the hospital name is too long for the layout, fall back to a short fixed label: `招聘公告`.
- Regenerate the same deterministic output file on repeat runs so style updates can take effect immediately.
- Keep the existing cover-selection order; only fall back to generation when no existing asset is available.

## Success Criteria

- A review package with `images=[]` and no rendered attachments still returns a non-empty `cover` path.
- The returned cover file exists on disk.
- The generated cover does not include the article title.
- The generated cover does not include descriptive helper text such as `自动生成封面`.
- Existing packages that already have a usable image continue to use that image first.
