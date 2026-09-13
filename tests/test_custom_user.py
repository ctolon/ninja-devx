"""AUTH_USER_MODEL is process-wide; test its contract in a separate Django process."""

import subprocess
import sys
from pathlib import Path


def test_custom_uuid_user_contract():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/custom_userapp/contract.py",
            "--ds=tests.custom_userapp.settings",
            "-q",
            "--tb=short",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
