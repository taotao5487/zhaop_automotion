#!/usr/bin/env python3

from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LABEL = "com.xiongtao.server-rss-static-site.sync"
DEFAULT_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"
DEFAULT_HOUR = 8
DEFAULT_MINUTE = 30


def default_python_bin(project_root: Path = PROJECT_ROOT) -> Path:
    venv_python = project_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python
    primary_workspace_python = Path.home() / "Documents" / "zhaop_automotion" / ".venv" / "bin" / "python"
    if primary_workspace_python.exists():
        return primary_workspace_python
    return Path(sys.executable)


def build_launch_agent_plist(
    *,
    label: str,
    python_bin: Path,
    sync_script: Path,
    project_root: Path,
    server: str,
    remote_path: str,
    hour: int,
    minute: int,
    ssh_port: int | None = None,
) -> bytes:
    logs_dir = project_root / "logs"
    program_arguments = [
        str(python_bin),
        str(sync_script),
        "--server",
        server,
        "--remote-path",
        remote_path,
    ]
    if ssh_port is not None:
        program_arguments.extend(["--ssh-port", str(ssh_port)])
    program_arguments.append("--publish")

    payload = {
        "Label": label,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(project_root),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(logs_dir / "server_rss_static_site_sync.out.log"),
        "StandardErrorPath": str(logs_dir / "server_rss_static_site_sync.err.log"),
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
        description="Install a macOS launchd job that syncs server rss.db and publishes the static site."
    )
    parser.add_argument("--server", required=True, help="SSH target, for example user@example.com")
    parser.add_argument("--remote-path", required=True, help="rss.db path on the server")
    parser.add_argument("--ssh-port", type=int, default=None)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--sync-script", type=Path, default=PROJECT_ROOT / "scripts" / "sync_server_rss_db.py")
    parser.add_argument("--python-bin", type=Path, default=None)
    parser.add_argument("--plist-path", type=Path, default=DEFAULT_PLIST_PATH)
    parser.add_argument("--hour", type=int, default=DEFAULT_HOUR)
    parser.add_argument("--minute", type=int, default=DEFAULT_MINUTE)
    parser.add_argument("--load", action="store_true", help="Load the LaunchAgent immediately after writing it.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    python_bin = args.python_bin or default_python_bin(args.project_root)
    plist_bytes = build_launch_agent_plist(
        label=args.label,
        python_bin=python_bin,
        sync_script=args.sync_script,
        project_root=args.project_root,
        server=args.server,
        remote_path=args.remote_path,
        hour=args.hour,
        minute=args.minute,
        ssh_port=args.ssh_port,
    )
    plist_path = install_launch_agent(
        plist_path=args.plist_path,
        plist_bytes=plist_bytes,
        project_root=args.project_root,
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
