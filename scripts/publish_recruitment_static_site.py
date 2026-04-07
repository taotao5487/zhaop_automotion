#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO_URL = "https://github.com/taotao5487/zhaopin.git"
DEFAULT_TARGET_DIR = Path.home() / "Documents" / "zhaopin"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site"
DEFAULT_DAYS = 30
DEFAULT_COMMIT_MESSAGE = "update recruitment static site"


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class PublishConfig:
    project_root: Path = PROJECT_ROOT
    site_dir: Path = DEFAULT_SITE_DIR
    target_dir: Path = DEFAULT_TARGET_DIR
    repo_url: str = DEFAULT_REPO_URL
    db_path: Path = PROJECT_ROOT / "data" / "rss.db"
    jobs_db_path: Path = PROJECT_ROOT / "data" / "jobs.db"
    branch: str | None = None
    days: int = DEFAULT_DAYS
    message: str = DEFAULT_COMMIT_MESSAGE
    skip_export: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class PublishOutcome:
    status: str
    target_dir: Path
    changed: bool
    summary: str


def _existing_default_data_path(filename: str) -> Path:
    project_path = PROJECT_ROOT / "data" / filename
    if project_path.exists():
        return project_path

    primary_workspace_path = Path.home() / "Documents" / "zhaop_automotion" / "data" / filename
    if primary_workspace_path.exists():
        return primary_workspace_path

    return project_path


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=check,
    )


def run_export(config: PublishConfig, runner: CommandRunner = run_command) -> None:
    export_script = config.project_root / "scripts" / "export_recruitment_static_site.py"
    runner(
        [
            sys.executable,
            str(export_script),
            "--db-path",
            str(config.db_path),
            "--jobs-db-path",
            str(config.jobs_db_path),
            "--output-dir",
            str(config.site_dir),
            "--days",
            str(config.days),
        ],
        cwd=config.project_root,
    )


def ensure_target_repository(config: PublishConfig, runner: CommandRunner = run_command) -> None:
    if (config.target_dir / ".git").exists():
        if config.branch:
            runner(["git", "checkout", config.branch], cwd=config.target_dir)
            runner(["git", "pull", "--ff-only", "origin", config.branch], cwd=config.target_dir)
        else:
            runner(["git", "pull", "--ff-only"], cwd=config.target_dir)
        return

    config.target_dir.parent.mkdir(parents=True, exist_ok=True)
    runner(["git", "clone", config.repo_url, str(config.target_dir)])
    if config.branch:
        runner(["git", "checkout", config.branch], cwd=config.target_dir)


def sync_site_files(source_site_dir: Path, target_repo_dir: Path) -> None:
    required_files = ("index.html", "recruitment.json")
    missing = [name for name in required_files if not (source_site_dir / name).exists()]
    if missing:
        raise FileNotFoundError("site output is incomplete: missing " + ", ".join(missing))

    source_assets_dir = source_site_dir / "assets"
    if not source_assets_dir.exists():
        raise FileNotFoundError(f"site output is incomplete: missing {source_assets_dir}")

    target_repo_dir.mkdir(parents=True, exist_ok=True)
    for filename in required_files:
        shutil.copy2(source_site_dir / filename, target_repo_dir / filename)

    target_assets_dir = target_repo_dir / "assets"
    if target_assets_dir.exists():
        shutil.rmtree(target_assets_dir)
    shutil.copytree(source_assets_dir, target_assets_dir)


def _git_status_short(target_dir: Path, runner: CommandRunner = run_command) -> str:
    result = runner(["git", "status", "--short"], cwd=target_dir)
    return (result.stdout or "").strip()


def commit_and_push(config: PublishConfig, runner: CommandRunner = run_command) -> PublishOutcome:
    status = _git_status_short(config.target_dir, runner=runner)
    if not status:
        return PublishOutcome(
            status="no_changes",
            target_dir=config.target_dir,
            changed=False,
            summary="No generated static site changes to publish.",
        )

    if config.dry_run:
        return PublishOutcome(
            status="dry_run",
            target_dir=config.target_dir,
            changed=True,
            summary="Generated static site has changes, but dry-run skipped commit and push.",
        )

    runner(["git", "add", "index.html", "recruitment.json", "assets"], cwd=config.target_dir)
    runner(["git", "commit", "-m", config.message], cwd=config.target_dir)
    if config.branch:
        runner(["git", "push", "origin", config.branch], cwd=config.target_dir)
    else:
        runner(["git", "push"], cwd=config.target_dir)

    return PublishOutcome(
        status="published",
        target_dir=config.target_dir,
        changed=True,
        summary=f"Published generated static site to {config.repo_url}.",
    )


def publish_static_site(
    config: PublishConfig,
    *,
    runner: CommandRunner = run_command,
) -> PublishOutcome:
    if not config.skip_export:
        run_export(config, runner=runner)

    ensure_target_repository(config, runner=runner)
    sync_site_files(config.site_dir, config.target_dir)
    return commit_and_push(config, runner=runner)


def _parse_branch(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export and publish the recruitment static site to the zhaopin GitHub repo."
    )
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    parser.add_argument("--target-dir", type=Path, default=DEFAULT_TARGET_DIR)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument("--db-path", type=Path, default=_existing_default_data_path("rss.db"))
    parser.add_argument("--jobs-db-path", type=Path, default=_existing_default_data_path("jobs.db"))
    parser.add_argument("--branch", default=None, help="Optional publish branch; omit to use the repo's current branch.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--message", default=DEFAULT_COMMIT_MESSAGE)
    parser.add_argument("--skip-export", action="store_true", help="Copy the existing site/ output without regenerating it.")
    parser.add_argument("--dry-run", action="store_true", help="Run export and sync checks, but skip commit and push.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = PublishConfig(
        project_root=args.project_root,
        site_dir=args.site_dir,
        target_dir=args.target_dir,
        repo_url=args.repo_url,
        db_path=args.db_path,
        jobs_db_path=args.jobs_db_path,
        branch=_parse_branch(args.branch),
        days=args.days,
        message=args.message,
        skip_export=args.skip_export,
        dry_run=args.dry_run,
    )

    try:
        outcome = publish_static_site(config)
    except subprocess.CalledProcessError as exc:
        print(exc.stdout or "", end="", file=sys.stderr)
        print(exc.stderr or str(exc), end="", file=sys.stderr)
        return exc.returncode or 1

    print(
        json.dumps(
            {
                "status": outcome.status,
                "changed": outcome.changed,
                "summary": outcome.summary,
                "target_dir": str(outcome.target_dir),
                "repo_url": config.repo_url,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
