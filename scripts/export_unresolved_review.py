#!/usr/bin/env python3
"""
Build a review CSV for unresolved account names from the import report.

The script queries the local search API with both the original author name and a
normalized variant, then exports candidate accounts for manual review.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from urllib import parse, request


SUFFIX_PATTERNS = [
    r"订阅号$",
    r"服务号$",
    r"健康服务号$",
    r"公众号$",
    r"官微$",
    r"医疗次中心$",
]


def normalize_text(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"\s+", "", value)
    value = value.replace("\u200b", "")
    return value


def simplify_query(value: str) -> str:
    simplified = (value or "").strip()
    for pattern in SUFFIX_PATTERNS:
        simplified = re.sub(pattern, "", simplified)
    simplified = re.sub(r"\s+", "", simplified)
    return simplified.strip()


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def search_accounts(base_url: str, query: str) -> list[dict]:
    if not query:
        return []
    url = f"{base_url.rstrip('/')}/api/public/searchbiz?query={parse.quote(query)}"
    with request.urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("data", {}).get("list", []) if data.get("success") else []


def confidence_for(author: str, simplified: str, candidate_name: str) -> str:
    author_norm = normalize_text(author)
    simplified_norm = normalize_text(simplified)
    candidate_norm = normalize_text(candidate_name)

    if candidate_norm == author_norm:
        return "exact_original"
    if simplified_norm and candidate_norm == simplified_norm:
        return "exact_simplified"
    if simplified_norm and simplified_norm in candidate_norm:
        return "partial_simplified"
    if author_norm and author_norm in candidate_norm:
        return "partial_original"
    return "low"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export unresolved subscriptions review CSV")
    parser.add_argument(
        "--report",
        default="data/import_subscriptions_report.full.json",
        help="Input JSON report path",
    )
    parser.add_argument(
        "--output",
        default="data/unresolved_subscriptions_review.csv",
        help="Output CSV path",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:5001",
        help="Local API base URL",
    )
    args = parser.parse_args()

    report_path = Path(args.report).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    report = load_report(report_path)
    unresolved = report.get("unresolved_accounts", [])

    rows = []
    for item in unresolved:
        author = item.get("author", "")
        article_count = item.get("article_count", 0)
        simplified = simplify_query(author)

        original_results = search_accounts(args.base_url, author)
        simplified_results = search_accounts(args.base_url, simplified) if simplified and simplified != author else []

        candidates = []
        seen = set()
        for source, results in (("original", original_results), ("simplified", simplified_results)):
            for account in results:
                fakeid = account.get("fakeid", "")
                if not fakeid or fakeid in seen:
                    continue
                seen.add(fakeid)
                candidates.append({
                    "source": source,
                    "nickname": account.get("nickname", ""),
                    "fakeid": fakeid,
                    "alias": account.get("alias", ""),
                    "confidence": confidence_for(author, simplified, account.get("nickname", "")),
                })

        note = ""
        if candidates:
            note = candidates[0]["confidence"]
            if len(candidates) > 1:
                note += f"; {len(candidates)} candidates"
        else:
            note = "no_candidates"

        row = {
            "author": author,
            "article_count": article_count,
            "suggested_query": simplified,
            "note": note,
        }

        for index in range(3):
            candidate = candidates[index] if index < len(candidates) else {}
            col = index + 1
            row[f"candidate_{col}_source"] = candidate.get("source", "")
            row[f"candidate_{col}_confidence"] = candidate.get("confidence", "")
            row[f"candidate_{col}_nickname"] = candidate.get("nickname", "")
            row[f"candidate_{col}_fakeid"] = candidate.get("fakeid", "")
            row[f"candidate_{col}_alias"] = candidate.get("alias", "")

        rows.append(row)

    fieldnames = [
        "author",
        "article_count",
        "suggested_query",
        "note",
        "candidate_1_source",
        "candidate_1_confidence",
        "candidate_1_nickname",
        "candidate_1_fakeid",
        "candidate_1_alias",
        "candidate_2_source",
        "candidate_2_confidence",
        "candidate_2_nickname",
        "candidate_2_fakeid",
        "candidate_2_alias",
        "candidate_3_source",
        "candidate_3_confidence",
        "candidate_3_nickname",
        "candidate_3_fakeid",
        "candidate_3_alias",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows)} rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
