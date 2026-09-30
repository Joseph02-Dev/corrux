"""Wheelhouse embarqué dans corrux-core — DEPLOY-001.

Télécharge, pour chaque version de CPython fournie par les
distributions supportées, les wheels binaires x86_64 de
`requirements.txt`. Le postinst de corrux-core construit ensuite
`/opt/corrux/.venv` à partir de ces seuls fichiers (`pip --no-index`) :
l'installation ne dépend jamais de PyPI, ni d'Internet.

  Ubuntu 22.04 -> 3.10 · Debian 12 / Proxmox VE 8 -> 3.11
  Ubuntu 24.04 -> 3.12 · Debian 13 / Proxmox VE 9 -> 3.13
  Ubuntu 26.04 -> 3.14

`--only-binary=:all:` : aucune compilation sur la machine cliente (pas
de compilateur requis). Plateformes manylinux jusqu'à glibc 2.28 :
glibc 2.35 minimum sur les distributions supportées (Ubuntu 22.04).

Usage (machine de build, accès réseau requis) :
    python -m packaging.wheelhouse <requirements.txt> <répertoire_de_sortie>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SUPPORTED_PYTHON_VERSIONS = ("3.10", "3.11", "3.12", "3.13", "3.14")
MANYLINUX_PLATFORMS = (
    "manylinux2014_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux_2_28_x86_64",
)


class WheelhouseError(Exception):
    """Le téléchargement des wheels a échoué."""


def pip_download_command(requirements: Path, dest: Path, python_version: str) -> list[str]:
    command = [
        sys.executable, "-m", "pip", "download",
        "--quiet", "--disable-pip-version-check",
        "--only-binary=:all:",
        "--implementation", "cp",
        "--python-version", python_version,
        "--requirement", str(requirements),
        "--dest", str(dest),
    ]
    for platform in MANYLINUX_PLATFORMS:
        command += ["--platform", platform]
    return command


def build_wheelhouse(
    requirements: Path,
    dest: Path,
    *,
    python_versions: tuple[str, ...] = SUPPORTED_PYTHON_VERSIONS,
    runner=subprocess.run,
) -> list[Path]:
    """Télécharge les wheels pour chaque version de Python ; retourne la
    liste triée des wheels présents dans `dest`."""
    if not requirements.is_file():
        raise WheelhouseError(f"Fichier de dépendances introuvable : {requirements}")
    dest.mkdir(parents=True, exist_ok=True)
    for version in python_versions:
        result = runner(
            pip_download_command(requirements, dest, version),
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise WheelhouseError(
                f"pip download (Python {version}) a échoué : {result.stderr.strip()}"
            )
    wheels = sorted(dest.glob("*.whl"))
    if not wheels:
        raise WheelhouseError(f"Aucun wheel produit dans {dest}")
    return wheels


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    wheels = build_wheelhouse(Path(argv[0]), Path(argv[1]))
    print(f"{len(wheels)} wheels dans {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
