"""Construction des paquets .deb CORRUX — TECH-043.

Layout d'installation : `/opt/corrux/` (chemin déjà référencé par les
unités systemd, ops/corrux-backup.service et corrux-cert-check.service,
TECH-009/010 : `WorkingDirectory=/opt/corrux`,
`ExecStart=/opt/corrux/.venv/bin/python manage.py ...`).

`postinst` volontairement minimal (`systemctl daemon-reload` uniquement)
— décision confirmée (audit Phase 1) : la configuration du système
(identité, compte admin, certificat, support de sauvegarde, activation
des modules) reste exclusivement le rôle de corrux-setup (TECH-012,
déjà construit), jamais du paquet lui-même. Aucune création
d'utilisateur système, aucun démarrage de service, aucune modification
de PostgreSQL — le paquet dépose des fichiers, rien de plus.

Dépendances déclarées vers les paquets Debian officiels (§3 :
« toutes les briques techniques proviennent des dépôts officiels
Debian 13 »), pas vers un environnement virtuel pip — cohérent avec le
principe d'auto-suffisance offline déjà acté. Noms de paquets Debian
donnés au mieux (à revérifier à la date réelle de packaging, comme déjà
noté pour les versions de la stack, architecture-technique-v1.md §3) —
non vérifiables depuis cet environnement de développement (basé sur un
environnement virtuel pip, pas les paquets système Debian).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

INSTALL_PREFIX = "/opt/corrux"

# Ignorés lors de la copie — jamais empaquetés (artefacts de
# développement, jamais du code applicatif).
_IGNORE_PATTERNS = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".pytest_cache", "*.egg-info"
)


class PackageBuildError(Exception):
    """La construction du paquet a échoué."""


@dataclass(frozen=True)
class DirectoryMapping:
    """Copie récursive d'un répertoire source vers une destination
    absolue dans l'arborescence du paquet."""

    source_relative_path: str
    dest_absolute_path: str


@dataclass(frozen=True)
class FileMapping:
    """Copie d'un seul fichier."""

    source_relative_path: str
    dest_absolute_path: str


@dataclass(frozen=True)
class PythonPackageMapping:
    """Copie récursive d'un répertoire, en excluant les fichiers qui ne
    sont pas du code Python applicatif (unités systemd, configuration
    Nginx) — cas de `ops/`, qui mélange code Python (modules Django,
    commandes de management) et fichiers système destinés à des
    emplacements différents (cf. SystemUnitMapping ci-dessous)."""

    source_relative_path: str
    dest_absolute_path: str
    excluded_suffixes: tuple[str, ...] = (".timer", ".service", ".conf")


@dataclass(frozen=True)
class SystemUnitMapping:
    """Copie un fichier système (unité systemd, configuration Nginx)
    vers son emplacement final — jamais mélangé au code applicatif."""

    source_relative_path: str
    dest_absolute_path: str


@dataclass(frozen=True)
class PackageSpec:
    name: str
    version: str
    depends: tuple[str, ...]
    description: str
    directory_mappings: tuple[DirectoryMapping, ...] = ()
    file_mappings: tuple[FileMapping, ...] = ()
    python_package_mappings: tuple[PythonPackageMapping, ...] = ()
    system_unit_mappings: tuple[SystemUnitMapping, ...] = ()
    architecture: str = "all"
    maintainer: str = "CORRUX <packaging@corrux.local>"


def _control_file_content(spec: PackageSpec) -> str:
    depends_line = f"Depends: {', '.join(spec.depends)}\n" if spec.depends else ""
    return (
        f"Package: {spec.name}\n"
        f"Version: {spec.version}\n"
        f"Architecture: {spec.architecture}\n"
        f"{depends_line}"
        f"Maintainer: {spec.maintainer}\n"
        f"Description: {spec.description}\n"
    )


_POSTINST_CONTENT = """#!/bin/sh
# postinst volontairement minimal (audit Phase 1, TECH-043) — la
# configuration du système reste exclusivement le rôle de corrux-setup
# (TECH-012). Ce script ne crée aucun utilisateur système, ne démarre
# aucun service, ne modifie jamais PostgreSQL.
set -e
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
fi
exit 0
"""


def build_deb_package(spec: PackageSpec, project_root: Path, output_dir: Path) -> Path:
    """Construit un paquet .deb réel à partir des correspondances de
    fichiers/répertoires déclarées. Retourne le chemin du fichier .deb
    produit."""
    build_root = output_dir / f"_build_{spec.name}"
    if build_root.exists():
        shutil.rmtree(build_root)

    debian_dir = build_root / "DEBIAN"
    debian_dir.mkdir(parents=True)
    (debian_dir / "control").write_text(_control_file_content(spec))
    postinst_path = debian_dir / "postinst"
    postinst_path.write_text(_POSTINST_CONTENT)
    postinst_path.chmod(0o755)

    for mapping in spec.directory_mappings:
        source = project_root / mapping.source_relative_path
        if not source.is_dir():
            raise PackageBuildError(f"Répertoire source introuvable : {source}")
        dest = build_root / mapping.dest_absolute_path.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest, ignore=_IGNORE_PATTERNS)

    for mapping in spec.file_mappings:
        source = project_root / mapping.source_relative_path
        if not source.is_file():
            raise PackageBuildError(f"Fichier source introuvable : {source}")
        dest = build_root / mapping.dest_absolute_path.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, dest)

    for py_mapping in spec.python_package_mappings:
        source = project_root / py_mapping.source_relative_path
        if not source.is_dir():
            raise PackageBuildError(f"Répertoire source introuvable : {source}")
        dest = build_root / py_mapping.dest_absolute_path.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)

        def _ignore_non_python(directory, names, _suffixes=py_mapping.excluded_suffixes):
            base_ignored = _IGNORE_PATTERNS(directory, names)
            suffix_ignored = {n for n in names if n.endswith(_suffixes)}
            return base_ignored | suffix_ignored

        shutil.copytree(source, dest, ignore=_ignore_non_python)

    for unit_mapping in spec.system_unit_mappings:
        source = project_root / unit_mapping.source_relative_path
        if not source.is_file():
            raise PackageBuildError(f"Fichier source introuvable : {source}")
        dest = build_root / unit_mapping.dest_absolute_path.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, dest)

    deb_path = output_dir / f"{spec.name}_{spec.version}_{spec.architecture}.deb"
    result = subprocess.run(
        ["dpkg-deb", "--build", "--root-owner-group", str(build_root), str(deb_path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise PackageBuildError(f"dpkg-deb a échoué : {result.stderr.strip()}")

    shutil.rmtree(build_root)
    return deb_path


# --- Spécifications des 3 paquets CORRUX (TECH-043) -----------------------------
# Noms de paquets Debian donnés au mieux — à revérifier au moment réel
# du packaging (cf. docstring de module).

CORRUX_CORE_DEPENDS = (
    "python3 (>= 3.13)",
    "python3-django (>= 5.2)",
    "python3-psycopg",
    "python3-yaml",
    "python3-argon2",
    "postgresql-client",
)


def build_corrux_core_spec(version: str) -> PackageSpec:
    return PackageSpec(
        name="corrux-core",
        version=version,
        depends=CORRUX_CORE_DEPENDS,
        description="CORRUX — Platform Core (identité, permissions, stockage, modules).",
        directory_mappings=(
            DirectoryMapping("corrux_core", f"{INSTALL_PREFIX}/corrux_core"),
            DirectoryMapping("core", f"{INSTALL_PREFIX}/core"),
            DirectoryMapping("ui", f"{INSTALL_PREFIX}/ui"),
        ),
        file_mappings=(
            FileMapping("manage.py", f"{INSTALL_PREFIX}/manage.py"),
            FileMapping("modules/__init__.py", f"{INSTALL_PREFIX}/modules/__init__.py"),
        ),
        python_package_mappings=(
            PythonPackageMapping("ops", f"{INSTALL_PREFIX}/ops"),
        ),
        system_unit_mappings=(
            SystemUnitMapping(
                "ops/corrux-backup.timer", "/lib/systemd/system/corrux-backup.timer"
            ),
            SystemUnitMapping(
                "ops/corrux-backup.service", "/lib/systemd/system/corrux-backup.service"
            ),
            SystemUnitMapping(
                "ops/corrux-cert-check.timer", "/lib/systemd/system/corrux-cert-check.timer"
            ),
            SystemUnitMapping(
                "ops/corrux-cert-check.service", "/lib/systemd/system/corrux-cert-check.service"
            ),
            SystemUnitMapping(
                "ops/nginx-corrux.conf", "/etc/nginx/sites-available/corrux.conf"
            ),
        ),
    )


def build_corrux_module_documentation_spec(version: str) -> PackageSpec:
    return PackageSpec(
        name="corrux-module-documentation",
        version=version,
        depends=(f"corrux-core (>= {version})",),
        description="CORRUX — module Documentation/Archivage.",
        directory_mappings=(
            DirectoryMapping(
                "modules/documentation", f"{INSTALL_PREFIX}/modules/documentation"
            ),
        ),
    )


def build_corrux_module_rh_spec(version: str) -> PackageSpec:
    return PackageSpec(
        name="corrux-module-rh",
        version=version,
        depends=(
            f"corrux-core (>= {version})",
            "corrux-module-documentation",
        ),
        description="CORRUX — module Ressources Humaines.",
        directory_mappings=(
            DirectoryMapping("modules/rh", f"{INSTALL_PREFIX}/modules/rh"),
        ),
    )


# --- Bundle de release signé — §19.1, consommé par corrux-update (TECH-014) ---
#
# Omis à tort dans une première passe de ce ticket : « empaquetés ET
# SIGNÉS » (comportement attendu explicite) suppose un bundle complet
# au format attendu par TECH-014 (update-manifest.yaml + signature GPG
# détachée + les .deb qu'il référence), pas seulement les 3 fichiers
# .deb isolés. Réutilise packaging.signing (TECH-013) et le format
# packaging.update_manifest (TECH-014) — jamais une seconde
# implémentation de la signature ou du format de manifeste.


def build_signed_release_bundle(
    version: str, project_root: Path, output_dir: Path, *, release_gnupg_home: Path
) -> Path:
    """Construit les 3 paquets, calcule leurs checksums SHA-256, écrit
    update-manifest.yaml (format §19.1/TECH-014) et le signe avec la
    clé de release — retourne le répertoire du bundle complet, prêt à
    être consommé tel quel par apply_offline_update() (TECH-014) ou
    publié comme dépôt apt (TECH-015)."""
    import hashlib

    from packaging.signing import sign_bundle

    bundle_dir = output_dir / "release_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    package_entries = []
    for spec_builder in (
        build_corrux_core_spec,
        build_corrux_module_documentation_spec,
        build_corrux_module_rh_spec,
    ):
        spec = spec_builder(version)
        deb_path = build_deb_package(spec, project_root, bundle_dir)
        checksum = hashlib.sha256(deb_path.read_bytes()).hexdigest()
        package_entries.append((deb_path.name, checksum))

    manifest_lines = [f'version: "{version}"', "packages:"]
    for filename, checksum in package_entries:
        manifest_lines.append(f"  - filename: {filename}")
        manifest_lines.append(f"    sha256: {checksum}")
    manifest_lines.append("modules_impacted:")
    manifest_lines.append("  - core")
    manifest_lines.append("  - documentation")
    manifest_lines.append("  - rh")

    manifest_path = bundle_dir / "update-manifest.yaml"
    manifest_path.write_text("\n".join(manifest_lines) + "\n")

    signature_path = bundle_dir / "update-manifest.yaml.sig"
    sign_bundle(manifest_path, signature_path, gnupg_home=release_gnupg_home)

    return bundle_dir
