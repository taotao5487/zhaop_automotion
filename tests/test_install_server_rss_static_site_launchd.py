#!/usr/bin/env python3

from __future__ import annotations

import plistlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from scripts.install_server_rss_static_site_launchd import (  # noqa: E402
    build_launch_agent_plist,
)


def test_build_launch_agent_plist_syncs_server_rss_and_publishes_daily(tmp_path: Path):
    plist_bytes = build_launch_agent_plist(
        label="com.xiongtao.server-rss-static-site.sync",
        python_bin=tmp_path / ".venv" / "bin" / "python",
        sync_script=tmp_path / "scripts" / "sync_server_rss_db.py",
        project_root=tmp_path,
        server="user@example.com",
        remote_path="/srv/zhaop_automotion/data/rss.db",
        hour=8,
        minute=30,
        ssh_port=22,
    )

    payload = plistlib.loads(plist_bytes)

    assert payload["Label"] == "com.xiongtao.server-rss-static-site.sync"
    assert payload["WorkingDirectory"] == str(tmp_path)
    assert payload["StartCalendarInterval"] == {"Hour": 8, "Minute": 30}
    assert payload["ProgramArguments"] == [
        str(tmp_path / ".venv" / "bin" / "python"),
        str(tmp_path / "scripts" / "sync_server_rss_db.py"),
        "--server",
        "user@example.com",
        "--remote-path",
        "/srv/zhaop_automotion/data/rss.db",
        "--ssh-port",
        "22",
        "--publish",
    ]
    assert "logs/server_rss_static_site_sync.out.log" in payload["StandardOutPath"]
    assert "logs/server_rss_static_site_sync.err.log" in payload["StandardErrorPath"]
