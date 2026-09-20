"""Config must stay lazy: importing it generates no keys and runs no VCS."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from micronote import config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_lazy_aliases_delegate_to_accessors():
    assert config.VERSION == config.version()
    assert config.KEY is config.key()
    assert config.ME is config.me()


def test_unknown_attribute_raises():
    with pytest.raises(AttributeError):
        _ = config.does_not_exist


def test_importing_config_writes_no_key_material(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(REPO_ROOT / "tests" / "fixtures" / "me.yml", config_dir / "me.yml")

    env = {**os.environ, "MICRONOTE_CONFIG_DIR": str(config_dir)}
    result = subprocess.run(
        [sys.executable, "-c", "import micronote.config"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert sorted(os.listdir(config_dir)) == ["me.yml"]


def test_importing_config_runs_no_vcs_subprocess(tmp_path):
    marker = tmp_path / "hg-called"
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    fake_hg = fakebin / "hg"
    fake_hg.write_text(f'#!/bin/sh\necho called > "{marker}"\nexit 1\n')
    fake_hg.chmod(0o755)

    env = {**os.environ, "PATH": f"{fakebin}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(
        [sys.executable, "-c", "import micronote.config"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
