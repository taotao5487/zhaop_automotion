#!/usr/bin/env python3
"""
导出招聘标题待审核表，并将人工审核结果写回招聘过滤规则。
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.paths import CONFIG_DIR, DATA_DIR
from wechat_service.utils.recruitment_filter import DEFAULT_RULES
from wechat_service.utils.recruitment_review_workflow import (
    classify_title_for_review,
    derive_rule_suggestions,
    merge_rules,
)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_jobs(db_path: Path) -> List[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select id, title, url, source_site, publish_date
            from medical_jobs
            order by publish_date desc, id desc
            """
        ).fetchall()
    return [dict(row) for row in rows]


def export_review(db_path: Path, output_path: Path, summary_path: Path) -> None:
    rows = _read_jobs(db_path)
    review_rows: List[dict] = []
    counts = {"keep": 0, "exclude": 0, "review": 0}

    for row in rows:
        decision = classify_title_for_review(row.get("title", ""))
        counts[decision.decision] += 1

        if decision.decision != "review":
            continue

        review_rows.append(
            {
                "id": row.get("id", ""),
                "title": row.get("title", ""),
                "url": row.get("url", ""),
                "source_site": row.get("source_site", ""),
                "publish_date": row.get("publish_date", ""),
                "auto_decision": decision.decision,
                "auto_reason": " | ".join(decision.reasons),
                "suggested_label": "",
                "manual_label": "",
                "manual_note": "",
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id",
                "title",
                "url",
                "source_site",
                "publish_date",
                "auto_decision",
                "auto_reason",
                "suggested_label",
                "manual_label",
                "manual_note",
            ],
        )
        writer.writeheader()
        writer.writerows(review_rows)

    summary = {
        "db_path": str(db_path),
        "generated_at": datetime.now().isoformat(),
        "counts": counts,
        "review_output": str(output_path),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _load_rules(path: Path) -> Dict[str, List[str]]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return dict(DEFAULT_RULES)


def apply_review(review_csv: Path, rules_output: Path) -> None:
    with review_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    suggestions = derive_rule_suggestions(rows)
    existing_rules = _load_rules(rules_output)
    merged = merge_rules(existing_rules, suggestions)

    rules_output.parent.mkdir(parents=True, exist_ok=True)
    rules_output.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary = {
        "review_csv": str(review_csv),
        "rules_output": str(rules_output),
        "suggested_title_exclude": suggestions["title_exclude"],
        "reviewed_rows": len(rows),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Recruitment review workflow tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export-review", help="Export review CSV")
    export_parser.add_argument("--db", default=str(DATA_DIR / "jobs.db"))
    export_parser.add_argument(
        "--output",
        default=str(DATA_DIR / "review_exports" / f"recruitment_title_review_{_timestamp()}.csv"),
    )
    export_parser.add_argument(
        "--summary",
        default=str(DATA_DIR / "review_exports" / f"recruitment_title_review_{_timestamp()}.summary.json"),
    )

    apply_parser = subparsers.add_parser("apply-review", help="Apply review CSV to rules")
    apply_parser.add_argument("--review-csv", required=True)
    apply_parser.add_argument(
        "--rules-output",
        default=str(CONFIG_DIR / "recruitment_rules.json"),
    )

    args = parser.parse_args()

    if args.command == "export-review":
        export_review(Path(args.db), Path(args.output), Path(args.summary))
        return 0

    apply_review(Path(args.review_csv), Path(args.rules_output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
