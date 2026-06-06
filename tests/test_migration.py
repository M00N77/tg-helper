"""Проверка, что миграция актуальна (alembic check)."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_alembic_current_returns_no_error():
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, f"alembic current failed: {result.stderr}"


def test_alembic_check_no_new_migrations():
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, f"alembic check failed: {result.stderr}"
