from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"
CONFIG_DIR = ROOT_DIR / "config"
STATIC_DIR = ROOT_DIR / "static"
ASSETS_DIR = ROOT_DIR / "assets"
SCRIPTS_DIR = ROOT_DIR / "scripts"
ENV_FILE = ROOT_DIR / ".env"


def ensure_runtime_dirs() -> None:
    for path in (DATA_DIR, LOGS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def load_root_env(*, override: bool = False) -> bool:
    if ENV_FILE.exists():
        return load_dotenv(ENV_FILE, override=override)
    return False


def resolve_from_root(path_str: str, *, default_parent: Path | None = None) -> Path:
    path = Path(path_str).expanduser()
    if path.is_absolute():
        return path.resolve()
    base = default_parent or ROOT_DIR
    return (base / path).resolve()
