#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def test_importing_app_keeps_launch_env_overrides():
    env = os.environ.copy()
    env["PORT"] = "5002"
    env["SITE_URL"] = "http://127.0.0.1:5002"

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                "print('before', os.getenv('PORT'), os.getenv('SITE_URL')); "
                "import app; "
                "print('after', os.getenv('PORT'), os.getenv('SITE_URL'))"
            ),
        ],
        cwd=str(ROOT_DIR),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "before 5002 http://127.0.0.1:5002" in result.stdout
    assert "after 5002 http://127.0.0.1:5002" in result.stdout
