from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_project_has_self_contained_docker_files():
    assert (ROOT / "Dockerfile").exists()
    assert (ROOT / "docker-compose.yml").exists()


def test_compose_uses_local_env_and_data_mounts():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["wechat-api"]

    assert service["image"] == "tmwgsicp/wechat-download-api:latest"
    assert service["command"][:2] == ["sh", "-lc"]
    assert "import PIL" in service["command"][2]
    assert "pip install --no-cache-dir 'Pillow>=10.0.0'" in service["command"][2]
    assert ".:/app" in service["volumes"]
    assert "5001:5000" in service["ports"]
    assert "./data:/app/data" in service["volumes"]
    assert "./.env:/app/.env" in service["volumes"]


def test_project_has_migration_script():
    script_path = ROOT / "scripts" / "migrate_legacy_wechat_runtime.sh"
    assert script_path.exists()
    content = script_path.read_text(encoding="utf-8")
    assert "docker compose up -d" in content
    assert "docker rm -f wechat-download-api" in content
