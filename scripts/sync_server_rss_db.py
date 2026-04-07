#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_DB_PATH = PROJECT_ROOT / "data" / "rss.db"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "data" / "backups" / "rss_db"
DEFAULT_TMP_DIR = PROJECT_ROOT / ".tmp"
DEFAULT_PUBLISH_SCRIPT = PROJECT_ROOT / "scripts" / "publish_recruitment_static_site.py"
REQUIRED_TABLES = {"articles", "subscriptions"}


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class RssDbValidation:
    article_count: int
    tables: set[str]


@dataclass(frozen=True)
class SyncConfig:
    server: str
    remote_path: str
    local_db_path: Path = DEFAULT_LOCAL_DB_PATH
    backup_dir: Path = DEFAULT_BACKUP_DIR
    tmp_dir: Path = DEFAULT_TMP_DIR
    project_root: Path = PROJECT_ROOT
    publish: bool = False
    publish_script: Path = DEFAULT_PUBLISH_SCRIPT
    ssh_port: int | None = None
    timestamp_label: str | None = None


@dataclass(frozen=True)
class SyncOutcome:
    status: str
    article_count: int
    local_db_path: Path
    backup_path: Path | None
    staged_db_path: Path
    published: bool


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


def validate_rss_db(db_path: Path) -> RssDbValidation:
    if not db_path.exists():
        raise FileNotFoundError(f"rss db does not exist: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        quick_check = conn.execute("PRAGMA quick_check").fetchone()
        if not quick_check or quick_check[0] != "ok":
            raise ValueError(f"sqlite quick_check failed: {quick_check}")

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = REQUIRED_TABLES - tables
        if missing:
            raise ValueError(
                "missing required table(s): " + ", ".join(sorted(missing))
            )

        article_count = int(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
    finally:
        conn.close()

    return RssDbValidation(
        article_count=article_count,
        tables=tables & REQUIRED_TABLES,
    )


def replace_local_rss_db(
    *,
    staged_db_path: Path,
    local_db_path: Path,
    backup_dir: Path,
    timestamp_label: str | None = None,
) -> Path | None:
    validate_rss_db(staged_db_path)
    local_db_path.parent.mkdir(parents=True, exist_ok=True)

    backup_path = None
    if local_db_path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        label = timestamp_label or datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"rss_{label}.db"
        shutil.copy2(local_db_path, backup_path)

    replacement_path = local_db_path.with_suffix(local_db_path.suffix + ".new")
    shutil.copy2(staged_db_path, replacement_path)
    os.replace(replacement_path, local_db_path)
    return backup_path


def fetch_remote_rss_db(
    config: SyncConfig,
    *,
    destination: Path,
    runner: CommandRunner = run_command,
) -> None:
    if not config.server.strip():
        raise ValueError("server is required; pass --server or set SERVER_RSS_HOST")
    if not config.remote_path.strip():
        raise ValueError("remote_path is required; pass --remote-path or set SERVER_RSS_DB_PATH")

    destination.parent.mkdir(parents=True, exist_ok=True)
    command = ["scp"]
    if config.ssh_port is not None:
        command.extend(["-P", str(config.ssh_port)])
    command.extend([f"{config.server}:{config.remote_path}", str(destination)])
    runner(command, cwd=config.project_root)


def publish_static_site(
    config: SyncConfig,
    *,
    runner: CommandRunner = run_command,
) -> None:
    runner(
        [
            sys.executable,
            str(config.publish_script),
            "--db-path",
            str(config.local_db_path),
        ],
        cwd=config.project_root,
    )


def sync_server_rss_db(
    config: SyncConfig,
    *,
    runner: CommandRunner = run_command,
) -> SyncOutcome:
    label = config.timestamp_label or datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_dir = (
        config.project_root / ".tmp"
        if config.tmp_dir == DEFAULT_TMP_DIR and config.project_root != PROJECT_ROOT
        else config.tmp_dir
    )
    staged_db_path = tmp_dir / f"server_rss_{label}.db"

    fetch_remote_rss_db(config, destination=staged_db_path, runner=runner)
    validation = validate_rss_db(staged_db_path)
    backup_path = replace_local_rss_db(
        staged_db_path=staged_db_path,
        local_db_path=config.local_db_path,
        backup_dir=config.backup_dir,
        timestamp_label=label,
    )

    if config.publish:
        publish_static_site(config, runner=runner)

    return SyncOutcome(
        status="synced",
        article_count=validation.article_count,
        local_db_path=config.local_db_path,
        backup_path=backup_path,
        staged_db_path=staged_db_path,
        published=config.publish,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch the authoritative server rss.db, replace the local rss.db, and optionally publish the static site."
    )
    parser.add_argument("--server", default=os.getenv("SERVER_RSS_HOST", ""))
    parser.add_argument("--remote-path", default=os.getenv("SERVER_RSS_DB_PATH", ""))
    parser.add_argument("--ssh-port", type=int, default=os.getenv("SERVER_RSS_SSH_PORT"))
    parser.add_argument("--local-db-path", type=Path, default=DEFAULT_LOCAL_DB_PATH)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--tmp-dir", type=Path, default=DEFAULT_TMP_DIR)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--publish-script", type=Path, default=DEFAULT_PUBLISH_SCRIPT)
    parser.add_argument("--publish", action="store_true", help="Run publish_recruitment_static_site.py after replacing rss.db.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = SyncConfig(
        server=args.server,
        remote_path=args.remote_path,
        local_db_path=args.local_db_path,
        backup_dir=args.backup_dir,
        tmp_dir=args.tmp_dir,
        project_root=args.project_root,
        publish=args.publish,
        publish_script=args.publish_script,
        ssh_port=args.ssh_port,
    )

    try:
        outcome = sync_server_rss_db(config)
    except (FileNotFoundError, ValueError, sqlite3.DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(exc.stdout or "", end="", file=sys.stderr)
        print(exc.stderr or str(exc), end="", file=sys.stderr)
        return exc.returncode or 1

    print(
        json.dumps(
            {
                "status": outcome.status,
                "article_count": outcome.article_count,
                "local_db_path": str(outcome.local_db_path),
                "backup_path": str(outcome.backup_path) if outcome.backup_path else None,
                "staged_db_path": str(outcome.staged_db_path),
                "published": outcome.published,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
