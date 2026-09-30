"""Tests du wheelhouse embarqué dans corrux-core — DEPLOY-001.

Le téléchargement réel (PyPI) est exercé par la CI de publication
(packaging/build_apt_site.sh) ; ici, la construction des commandes pip
et la gestion d'erreur, sans réseau.
"""

import subprocess

import pytest

from packaging.wheelhouse import (
    SUPPORTED_PYTHON_VERSIONS,
    WheelhouseError,
    build_wheelhouse,
    pip_download_command,
)


def test_supported_python_versions_cover_all_target_distributions():
    # Ubuntu 22.04, Debian 12/Proxmox 8, Ubuntu 24.04, Debian 13/Proxmox 9, Ubuntu 26.04.
    assert SUPPORTED_PYTHON_VERSIONS == ("3.10", "3.11", "3.12", "3.13", "3.14")


def test_pip_command_downloads_binary_wheels_only_for_the_target_python(tmp_path):
    command = pip_download_command(tmp_path / "requirements.txt", tmp_path / "out", "3.10")

    assert command[1:4] == ["-m", "pip", "download"]
    assert "--only-binary=:all:" in command
    assert command[command.index("--python-version") + 1] == "3.10"
    assert command[command.index("--implementation") + 1] == "cp"
    assert "manylinux2014_x86_64" in command


def test_builds_one_download_per_python_version(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("Django==5.2.*\n")
    dest = tmp_path / "wheels"
    calls = []

    def fake_runner(command, **kwargs):
        calls.append(command)
        (dest / "django-5.2-py3-none-any.whl").write_bytes(b"")
        return subprocess.CompletedProcess(command, 0, "", "")

    wheels = build_wheelhouse(requirements, dest, runner=fake_runner)

    versions = [c[c.index("--python-version") + 1] for c in calls]
    assert versions == list(SUPPORTED_PYTHON_VERSIONS)
    assert [w.name for w in wheels] == ["django-5.2-py3-none-any.whl"]


def test_pip_failure_is_reported_with_the_python_version(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("Django==5.2.*\n")

    def failing_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "No matching distribution")

    with pytest.raises(WheelhouseError, match=r"Python 3\.10.*No matching distribution"):
        build_wheelhouse(requirements, tmp_path / "wheels", runner=failing_runner)


def test_missing_requirements_file_fails_explicitly(tmp_path):
    with pytest.raises(WheelhouseError):
        build_wheelhouse(tmp_path / "absent.txt", tmp_path / "wheels")
