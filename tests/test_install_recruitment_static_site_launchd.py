#!/usr/bin/env python3

from __future__ import annotations

import plistlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from scripts.install_recruitment_static_site_launchd import (  # noqa: E402
    build_launch_agent_plist,
)


def test_build_launch_agent_plist_runs_publish_script_daily_at_0830(tmp_path: Path):
    plist_bytes = build_launch_agent_plist(
        label="com.xiongtao.recruitment-static-site.publish",
        python_bin=tmp_path / ".venv" / "bin" / "python",
        publish_script=tmp_path / "scripts" / "publish_recruitment_static_site.py",
        project_root=tmp_path,
        target_dir=tmp_path / "zhaopin",
        repo_url="https://github.com/taotao5487/zhaopin.git",
        hour=8,
        minute=30,
    )

    payload = plistlib.loads(plist_bytes)

    assert payload["Label"] == "com.xiongtao.recruitment-static-site.publish"
    assert payload["StartCalendarInterval"] == {"Hour": 8, "Minute": 30}
    assert payload["WorkingDirectory"] == str(tmp_path)
    assert payload["ProgramArguments"] == [
        str(tmp_path / ".venv" / "bin" / "python"),
        str(tmp_path / "scripts" / "publish_recruitment_static_site.py"),
        "--target-dir",
        str(tmp_path / "zhaopin"),
        "--repo-url",
        "https://github.com/taotao5487/zhaopin.git",
    ]
    assert "logs/recruitment_static_site_publish.out.log" in payload["StandardOutPath"]
    assert "logs/recruitment_static_site_publish.err.log" in payload["StandardErrorPath"]
