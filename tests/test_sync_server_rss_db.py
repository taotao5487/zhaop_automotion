#!/usr/bin/env python3

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from scripts.sync_server_rss_db import (  # noqa: E402
    SyncConfig,
    replace_local_rss_db,
    sync_server_rss_db,
    validate_rss_db,
)


def _write_rss_db(db_path: Path, title: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fakeid TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            link TEXT NOT NULL DEFAULT '',
            publish_time INTEGER NOT NULL DEFAULT 0,
            is_recruitment INTEGER NOT NULL DEFAULT 0,
            review_status TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE subscriptions (
            fakeid TEXT PRIMARY KEY,
            nickname TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.execute(
        """
        INSERT INTO articles
            (fakeid, title, link, publish_time, is_recruitment, review_status)
        VALUES ('f1', ?, 'https://example.com/a', 1775536800, 1, 'confirmed')
        """,
        (title,),
    )
    conn.execute("INSERT INTO subscriptions (fakeid, nickname) VALUES ('f1', '测试公众号')")
    conn.commit()
    conn.close()


def _read_first_title(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT title FROM articles ORDER BY id LIMIT 1").fetchone()
    finally:
        conn.close()
    return row[0]


def test_validate_rss_db_accepts_valid_server_database(tmp_path: Path):
    db_path = tmp_path / "rss.db"
    _write_rss_db(db_path, "服务器实时招聘")

    result = validate_rss_db(db_path)

    assert result.article_count == 1
    assert result.tables == {"articles", "subscriptions"}


def test_validate_rss_db_rejects_database_without_required_tables(tmp_path: Path):
    db_path = tmp_path / "rss.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    try:
        validate_rss_db(db_path)
    except ValueError as exc:
        assert "missing required table" in str(exc)
    else:
        raise AssertionError("expected invalid rss db to be rejected")


def test_replace_local_rss_db_backs_up_local_and_uses_server_as_truth(tmp_path: Path):
    server_db = tmp_path / "server" / "rss.db"
    local_db = tmp_path / "local" / "rss.db"
    backup_dir = tmp_path / "backups"
    _write_rss_db(server_db, "服务器实时招聘")
    _write_rss_db(local_db, "本地旧招聘")

    backup_path = replace_local_rss_db(
        staged_db_path=server_db,
        local_db_path=local_db,
        backup_dir=backup_dir,
        timestamp_label="20260407_143000",
    )

    assert backup_path == backup_dir / "rss_20260407_143000.db"
    assert _read_first_title(local_db) == "服务器实时招聘"
    assert _read_first_title(backup_path) == "本地旧招聘"


def test_sync_server_rss_db_fetches_replaces_and_publishes(tmp_path: Path):
    server_db = tmp_path / "remote" / "rss.db"
    local_db = tmp_path / "data" / "rss.db"
    backup_dir = tmp_path / "backups"
    _write_rss_db(server_db, "服务器实时招聘")
    _write_rss_db(local_db, "本地旧招聘")
    commands: list[list[str]] = []

    def fake_runner(command, *, cwd=None, check=True):
        commands.append([str(part) for part in command])
        if command[0] == "scp":
            destination = Path(command[-1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(server_db.read_bytes())
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    config = SyncConfig(
        server="user@example.com",
        remote_path="/srv/zhaop_automotion/data/rss.db",
        local_db_path=local_db,
        backup_dir=backup_dir,
        project_root=tmp_path,
        publish=True,
        publish_script=tmp_path / "scripts" / "publish_recruitment_static_site.py",
        timestamp_label="20260407_143000",
    )

    outcome = sync_server_rss_db(config, runner=fake_runner)

    assert outcome.status == "synced"
    assert outcome.article_count == 1
    assert outcome.published is True
    assert _read_first_title(local_db) == "服务器实时招聘"
    assert commands[0] == [
        "scp",
        "user@example.com:/srv/zhaop_automotion/data/rss.db",
        str(tmp_path / ".tmp" / "server_rss_20260407_143000.db"),
    ]
    assert commands[-1] == [
        sys.executable,
        str(tmp_path / "scripts" / "publish_recruitment_static_site.py"),
        "--db-path",
        str(local_db),
    ]
