#!/usr/bin/env python3

from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LABEL = "com.xiongtao.recruitment-static-site.publish"
DEFAULT_REPO_URL = "https://github.com/taotao5487/zhaopin.git"
DEFAULT_TARGET_DIR = Path.home() / "Documents" / "zhaopin"
DEFAULT_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"
DEFAULT_HOUR = 8
DEFAULT_MINUTE = 30


def default_python_bin(project_root: Path = PROJECT_ROOT) -> Path:
    venv_python = project_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


def build_launch_agent_plist(
    *,
    label: str,
    python_bin: Path,
    publish_script: Path,
    project_root: Path,
    target_dir: Path,
    repo_url: str,
    hour: int,
    minute: int,
) -> bytes:
    logs_dir = project_root / "logs"
    payload = {
        "Label": label,
        "ProgramArguments": [
            str(python_bin),
            str(publish_script),
            "--target-dir",
            str(target_dir),
            "--repo-url",
            repo_url,
        ],
        "WorkingDirectory": str(project_root),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(logs_dir / "recruitment_static_site_publish.out.log"),
        "StandardErrorPath": str(logs_dir / "recruitment_static_site_publish.err.log"),
        "RunAtLoad": False,
    }
    return plistlib.dumps(payload, sort_keys=False)


def install_launch_agent(
    *,
    plist_path: Path,
    plist_bytes: bytes,
    project_root: Path,
    load: bool,
) -> Path:
    (project_root / "logs").mkdir(parents=True, exist_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plist_bytes)

    if load:
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
        subprocess.run(["launchctl", "load", str(plist_path)], check=True)

    return plist_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install a macOS launchd job for publishing the recruitment static site daily."
    )
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    parser.add_argument("--target-dir", type=Path, default=DEFAULT_TARGET_DIR)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--publish-script", type=Path, default=PROJECT_ROOT / "scripts" / "publish_recruitment_static_site.py")
    parser.add_argument("--python-bin", type=Path, default=None)
    parser.add_argument("--plist-path", type=Path, default=DEFAULT_PLIST_PATH)
    parser.add_argument("--hour", type=int, default=DEFAULT_HOUR)
    parser.add_argument("--minute", type=int, default=DEFAULT_MINUTE)
    parser.add_argument("--load", action="store_true", help="Load the LaunchAgent immediately after writing it.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root
    python_bin = args.python_bin or default_python_bin(project_root)
    plist_bytes = build_launch_agent_plist(
        label=args.label,
        python_bin=python_bin,
        publish_script=args.publish_script,
        project_root=project_root,
        target_dir=args.target_dir,
        repo_url=args.repo_url,
        hour=args.hour,
        minute=args.minute,
    )
    plist_path = install_launch_agent(
        plist_path=args.plist_path,
        plist_bytes=plist_bytes,
        project_root=project_root,
        load=args.load,
    )
    print(f"Wrote LaunchAgent: {plist_path}")
    if args.load:
        print("Loaded LaunchAgent with launchctl.")
    else:
        print(f"To enable it now, run: launchctl load {plist_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
