"""Construction des paquets .deb CORRUX — TECH-043.

Layout d'installation : `/opt/corrux/` (chemin déjà référencé par les
unités systemd, ops/corrux-backup.service et corrux-cert-check.service,
TECH-009/010 : `WorkingDirectory=/opt/corrux`,
`ExecStart=/opt/corrux/.venv/bin/python manage.py ...`).

Installation sur un système existant (DEPLOY-001) — remplace
l'installation par ISO comme mode principal : CORRUX s'installe par apt
(ou par le script `install/install.sh`, qui ne fait qu'ajouter le dépôt
apt signé) sur Debian 12/13, Ubuntu 22.04/24.04/26.04 LTS et Proxmox VE 8/9.

Conséquences sur les paquets :
- Environnement Python embarqué : les dépendances Python (Django…) ne
  viennent plus des paquets `python3-*` de la distribution — leurs
  versions divergent trop d'une distribution à l'autre (Django 3.2 à
  5.2). corrux-core livre des wheels figés pour CPython 3.10 à 3.14
  (`packaging/wheelhouse.py`) et son postinst construit
  `/opt/corrux/.venv` hors ligne (`pip --no-index`). Le paquet est donc
  `amd64` (wheels binaires x86_64).
- postinst de provisionnement (`packaging/debian/`) : compte système,
  venv, `/etc/corrux/core.env` (secrets aléatoires), base PostgreSQL
  locale, migrations. Revient sur la décision « postinst minimal » de
  l'audit Phase 1, qui supposait l'ISO préparant le système : une
  installation par apt doit aboutir à un système cohérent. Reste
  inchangé : aucun service n'est démarré par le paquet ; identité,
  compte administrateur, certificat et support de sauvegarde restent
  le rôle de corrux-setup (`/usr/sbin/corrux-setup`).
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
class MaintainerScript:
    """Script de maintenance dpkg (postinst, prerm, postrm...) fourni
    par un fichier source du dépôt."""

    name: str
    source_relative_path: str


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
    maintainer_scripts: tuple[MaintainerScript, ...] = ()
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


# postinst par défaut, pour un paquet sans script dédié (méta-paquet).
_POSTINST_CONTENT = """#!/bin/sh
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
    for script in spec.maintainer_scripts:
        source = project_root / script.source_relative_path
        if not source.is_file():
            raise PackageBuildError(f"Script de maintenance introuvable : {source}")
        dest = debian_dir / script.name
        shutil.copy(source, dest)
        dest.chmod(0o755)

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

    # Tout fichier livré sous /etc est un fichier de configuration dpkg :
    # jamais écrasé silencieusement s'il a été modifié localement.
    etc_root = build_root / "etc"
    if etc_root.is_dir():
        conffiles = sorted(
            "/" + str(path.relative_to(build_root))
            for path in etc_root.rglob("*") if path.is_file()
        )
        (debian_dir / "conffiles").write_text("\n".join(conffiles) + "\n")

    deb_path = output_dir / f"{spec.name}_{spec.version}_{spec.architecture}.deb"
    result = subprocess.run(
        ["dpkg-deb", "--build", "--root-owner-group", str(build_root), str(deb_path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise PackageBuildError(f"dpkg-deb a échoué : {result.stderr.strip()}")

    shutil.rmtree(build_root)
    return deb_path


# --- Spécifications des paquets CORRUX (TECH-043, DEPLOY-001) -------------------
# Dépendances : uniquement des paquets présents sous le même nom dans
# Debian 12/13, Ubuntu 22.04/24.04/26.04 et Proxmox VE 8/9 (base Debian). Les
# dépendances Python sont embarquées (wheels), cf. docstring de module.
# Python >= 3.10 : minimum de Django 5.2 (Ubuntu 22.04 fournit 3.10).

CORRUX_CORE_DEPENDS = (
    "python3 (>= 3.10)",
    "python3-venv",
    "postgresql (>= 14)",
    "nginx",
    "gnupg",
    "openssl",
    "adduser",
    "e2fsprogs",
)

CORRUX_PACKAGE_DIR = "packaging/debian"
WHEELHOUSE_DEST = f"{INSTALL_PREFIX}/wheels"


def build_corrux_core_spec(version: str, wheelhouse_dir: Path | None = None) -> PackageSpec:
    """`wheelhouse_dir` : wheels produits par packaging/wheelhouse.py,
    embarqués dans /opt/corrux/wheels. Sans eux, le paquet se construit
    (tests de structure) mais son postinst ne peut pas créer le venv."""
    wheel_mappings = ()
    if wheelhouse_dir is not None:
        # Chemin absolu : `project_root / chemin_absolu` le conserve tel quel.
        wheel_mappings = (DirectoryMapping(str(wheelhouse_dir.resolve()), WHEELHOUSE_DEST),)
    return PackageSpec(
        name="corrux-core",
        version=version,
        depends=CORRUX_CORE_DEPENDS,
        description="CORRUX — Platform Core (identité, permissions, stockage, modules).",
        architecture="amd64",
        directory_mappings=(
            DirectoryMapping("corrux_core", f"{INSTALL_PREFIX}/corrux_core"),
            DirectoryMapping("core", f"{INSTALL_PREFIX}/core"),
            DirectoryMapping("ui", f"{INSTALL_PREFIX}/ui"),
            *wheel_mappings,
        ),
        file_mappings=(
            FileMapping("manage.py", f"{INSTALL_PREFIX}/manage.py"),
            FileMapping("requirements.txt", f"{INSTALL_PREFIX}/requirements.txt"),
            FileMapping("modules/__init__.py", f"{INSTALL_PREFIX}/modules/__init__.py"),
            FileMapping(f"{CORRUX_PACKAGE_DIR}/common.sh", "/usr/lib/corrux/common.sh"),
            FileMapping(f"{CORRUX_PACKAGE_DIR}/corrux-setup", "/usr/sbin/corrux-setup"),
            FileMapping(f"{CORRUX_PACKAGE_DIR}/corrux-manage", "/usr/sbin/corrux-manage"),
        ),
        maintainer_scripts=(
            MaintainerScript("postinst", f"{CORRUX_PACKAGE_DIR}/corrux-core.postinst"),
            MaintainerScript("prerm", f"{CORRUX_PACKAGE_DIR}/corrux-core.prerm"),
            MaintainerScript("postrm", f"{CORRUX_PACKAGE_DIR}/corrux-core.postrm"),
        ),
        python_package_mappings=(
            PythonPackageMapping("ops", f"{INSTALL_PREFIX}/ops"),
        ),
        system_unit_mappings=(
            SystemUnitMapping(
                "ops/corrux-core.service", "/lib/systemd/system/corrux-core.service"
            ),
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
        maintainer_scripts=(
            MaintainerScript("postinst", f"{CORRUX_PACKAGE_DIR}/corrux-module.postinst"),
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
        maintainer_scripts=(
            MaintainerScript("postinst", f"{CORRUX_PACKAGE_DIR}/corrux-module.postinst"),
        ),
    )


def build_corrux_metapackage_spec(version: str) -> PackageSpec:
    """Méta-paquet `corrux` : `apt install corrux` installe la plateforme
    et tous les modules V1 (déclarés dans INSTALLED_APPS, donc tous
    nécessaires au démarrage)."""
    return PackageSpec(
        name="corrux",
        version=version,
        depends=(
            f"corrux-core (= {version})",
            f"corrux-module-documentation (= {version})",
            f"corrux-module-rh (= {version})",
        ),
        description="CORRUX — système d'information local pour PME/TPE (installation complète).",
    )


ALL_PACKAGE_SPEC_BUILDERS = (
    build_corrux_core_spec,
    build_corrux_module_documentation_spec,
    build_corrux_module_rh_spec,
    build_corrux_metapackage_spec,
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
    version: str,
    project_root: Path,
    output_dir: Path,
    *,
    release_gnupg_home: Path,
    wheelhouse_dir: Path | None = None,
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
        if spec_builder is build_corrux_core_spec:
            spec = spec_builder(version, wheelhouse_dir)
        else:
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
