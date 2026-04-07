#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from scripts.publish_recruitment_static_site import (  # noqa: E402
    PublishConfig,
    publish_static_site,
    sync_site_files,
)


def _write_source_site(site_dir: Path) -> None:
    assets_dir = site_dir / "assets"
    assets_dir.mkdir(parents=True)
    (site_dir / "index.html").write_text("<!DOCTYPE html>", encoding="utf-8")
    (site_dir / "recruitment.json").write_text('{"count":1}', encoding="utf-8")
    (assets_dir / "site.css").write_text("body{}", encoding="utf-8")
    (assets_dir / "site.js").write_text("console.log('ok')", encoding="utf-8")


def test_sync_site_files_updates_generated_files_and_preserves_repo_metadata(tmp_path: Path):
    source_site = tmp_path / "site"
    target_repo = tmp_path / "zhaopin"
    _write_source_site(source_site)
    (target_repo / ".git").mkdir(parents=True)
    (target_repo / ".github" / "workflows").mkdir(parents=True)
    (target_repo / ".github" / "workflows" / "pages.yml").write_text("name: pages", encoding="utf-8")
    (target_repo / "README.md").write_text("keep me", encoding="utf-8")
    (target_repo / "assets").mkdir()
    (target_repo / "assets" / "old.css").write_text("stale", encoding="utf-8")

    sync_site_files(source_site, target_repo)

    assert (target_repo / ".git").exists()
    assert (target_repo / ".github" / "workflows" / "pages.yml").exists()
    assert (target_repo / "README.md").read_text(encoding="utf-8") == "keep me"
    assert (target_repo / "index.html").read_text(encoding="utf-8") == "<!DOCTYPE html>"
    assert (target_repo / "recruitment.json").read_text(encoding="utf-8") == '{"count":1}'
    assert (target_repo / "assets" / "site.css").exists()
    assert not (target_repo / "assets" / "old.css").exists()


def test_publish_static_site_clones_and_pushes_when_site_changed(tmp_path: Path):
    source_site = tmp_path / "site"
    target_repo = tmp_path / "zhaopin"
    _write_source_site(source_site)
    commands: list[list[str]] = []

    def fake_runner(command, *, cwd=None, check=True):
        commands.append([str(part) for part in command])
        if command[:2] == ["git", "clone"]:
            (target_repo / ".git").mkdir(parents=True)
        if command[:3] == ["git", "status", "--short"]:
            return subprocess.CompletedProcess(command, 0, stdout=" M recruitment.json\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    config = PublishConfig(
        project_root=tmp_path,
        site_dir=source_site,
        target_dir=target_repo,
        repo_url="https://github.com/taotao5487/zhaopin.git",
        db_path=tmp_path / "rss.db",
        jobs_db_path=tmp_path / "jobs.db",
        skip_export=True,
    )

    outcome = publish_static_site(config, runner=fake_runner)

    assert outcome.status == "published"
    assert ["git", "clone", "https://github.com/taotao5487/zhaopin.git", str(target_repo)] in commands
    assert ["git", "add", "index.html", "recruitment.json", "assets"] in commands
    assert ["git", "commit", "-m", "update recruitment static site"] in commands
    assert ["git", "push"] in commands


def test_publish_static_site_skips_commit_when_no_generated_changes(tmp_path: Path):
    source_site = tmp_path / "site"
    target_repo = tmp_path / "zhaopin"
    _write_source_site(source_site)
    (target_repo / ".git").mkdir(parents=True)
    commands: list[list[str]] = []

    def fake_runner(command, *, cwd=None, check=True):
        commands.append([str(part) for part in command])
        if command[:3] == ["git", "status", "--short"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    config = PublishConfig(
        project_root=tmp_path,
        site_dir=source_site,
        target_dir=target_repo,
        repo_url="https://github.com/taotao5487/zhaopin.git",
        db_path=tmp_path / "rss.db",
        jobs_db_path=tmp_path / "jobs.db",
        skip_export=True,
    )

    outcome = publish_static_site(config, runner=fake_runner)

    assert outcome.status == "no_changes"
    assert ["git", "pull", "--ff-only"] in commands
    assert ["git", "commit", "-m", "update recruitment static site"] not in commands
    assert ["git", "push"] not in commands


def test_publish_static_site_runs_export_before_syncing(tmp_path: Path):
    source_site = tmp_path / "site"
    target_repo = tmp_path / "zhaopin"
    _write_source_site(source_site)
    (target_repo / ".git").mkdir(parents=True)
    commands: list[list[str]] = []

    def fake_runner(command, *, cwd=None, check=True):
        commands.append([str(part) for part in command])
        if command[:3] == ["git", "status", "--short"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    config = PublishConfig(
        project_root=tmp_path,
        site_dir=source_site,
        target_dir=target_repo,
        repo_url="https://github.com/taotao5487/zhaopin.git",
        db_path=tmp_path / "rss.db",
        jobs_db_path=tmp_path / "jobs.db",
    )

    publish_static_site(config, runner=fake_runner)

    assert commands[0] == [
        sys.executable,
        str(tmp_path / "scripts" / "export_recruitment_static_site.py"),
        "--db-path",
        str(tmp_path / "rss.db"),
        "--jobs-db-path",
        str(tmp_path / "jobs.db"),
        "--output-dir",
        str(source_site),
        "--days",
        "30",
    ]
