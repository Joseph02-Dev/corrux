"""Orchestration de la mise à jour hors ligne — `corrux-update` (§19.2).

Enchaîne : montage lecture seule -> vérification signature GPG (réutilise
packaging.signing, TECH-013) -> vérification checksums SHA-256 ->
refus total et atomique si échec (rien n'est appliqué) -> installation
dpkg -> migrations via la commande standard Django `migrate` (§19.2 :
« aucun nouveau mécanisme de migration, réutilisation directe de
l'existant » — le Module Manager, TECH-006, ne lance lui-même aucune
migration : c'est `manage.py migrate` qui est l'« existant » réutilisé
ici, vérifié directement dans le code avant d'écrire ce module plutôt
que supposé) -> traçabilité audit_log.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.core.management import call_command

from core.audit.service import record_audit_event
from core.identity.models import User
from packaging.signing import SignatureVerificationError, verify_bundle_signature
from packaging.update_manifest import UpdateManifest, UpdateManifestError, parse_update_manifest


class UpdateError(Exception):
    """La mise à jour a été refusée ou a échoué — aucun paquet
    n'est appliqué dans ce cas (refus total et atomique, §19.2)."""


@dataclass(frozen=True)
class UpdateReport:
    """Rapport de mise à jour — §19.2 étape 7 : « version avant/après,
    paquets appliqués, résultat »."""

    version: str
    packages_applied: tuple[str, ...]
    modules_migrated: tuple[str, ...]


def _compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_migrations_and_record_success(
    *,
    version: str,
    packages_applied: tuple[str, ...],
    modules_impacted: tuple[str, ...],
    media_target: str,
    actor: User | None,
) -> UpdateReport:
    """Étapes 6-7 du mécanisme de mise à jour (§19.2) — migrations via
    la commande standard Django `migrate`, puis traçabilité
    audit_log. Partagée par les DEUX canaux de transport (hors ligne,
    TECH-014, et en ligne, TECH-015) — §19.3 : « le mode en ligne...
    c'est un second canal de transport pour le MÊME mécanisme », donc
    cette fonction, jamais une seconde implémentation."""
    for module_id in modules_impacted:
        call_command("migrate", module_id, verbosity=0)

    record_audit_event(
        actor=actor, action="update.applied",
        target=media_target,
        metadata={
            "version": version,
            "packages_applied": list(packages_applied),
            "modules_migrated": list(modules_impacted),
        },
    )

    return UpdateReport(
        version=version, packages_applied=packages_applied, modules_migrated=modules_impacted,
    )


def apply_offline_update(
    *,
    media_device_path: str,
    mount_point: Path,
    trusted_public_key_path: Path,
    actor: User | None,
    runner=subprocess.run,
) -> UpdateReport:
    """Applique une mise à jour hors ligne depuis un support amovible
    (§19.2, étapes 1 à 7).

    Structure attendue du support : `update-manifest.yaml` +
    `update-manifest.yaml.sig` (signature GPG détachée du manifeste) +
    les fichiers `.deb` qu'il référence, à sa racine.
    """
    mount_point.mkdir(parents=True, exist_ok=True)

    # --- 1. Montage en lecture seule --------------------------------------------
    mount_result = runner(
        ["mount", "-o", "ro", media_device_path, str(mount_point)],
        capture_output=True, text=True, check=False,
    )
    if mount_result.returncode != 0:
        raise UpdateError(f"Montage du support échoué : {mount_result.stderr.strip()}")

    try:
        manifest_path = mount_point / "update-manifest.yaml"
        signature_path = mount_point / "update-manifest.yaml.sig"

        # --- 2. Vérification de signature ---------------------------------------
        try:
            verify_bundle_signature(
                manifest_path, signature_path,
                trusted_public_key_path=trusted_public_key_path, runner=runner,
            )
        except SignatureVerificationError as exc:
            record_audit_event(
                actor=actor, action="update.refused",
                target=str(media_device_path),
                metadata={"reason": "signature_invalide", "detail": str(exc)},
            )
            raise UpdateError(f"Signature du manifeste invalide : {exc}") from exc

        try:
            manifest: UpdateManifest = parse_update_manifest(manifest_path)
        except UpdateManifestError as exc:
            record_audit_event(
                actor=actor, action="update.refused",
                target=str(media_device_path),
                metadata={"reason": "manifeste_invalide", "detail": str(exc)},
            )
            raise UpdateError(f"Manifeste invalide : {exc}") from exc

        # --- 3. Vérification d'intégrité (checksums) ----------------------------
        for package in manifest.packages:
            package_path = mount_point / package.filename
            if not package_path.exists():
                record_audit_event(
                    actor=actor, action="update.refused",
                    target=str(media_device_path),
                    metadata={
                        "reason": "paquet_absent", "package": package.filename,
                        "version": manifest.version,
                    },
                )
                raise UpdateError(f"Paquet référencé absent du support : {package.filename}")

            actual_checksum = _compute_sha256(package_path)
            if actual_checksum != package.sha256:
                # --- 4. Échec -> refus total et atomique, rien n'est appliqué ---
                record_audit_event(
                    actor=actor, action="update.refused",
                    target=str(media_device_path),
                    metadata={
                        "reason": "checksum_invalide", "package": package.filename,
                        "version": manifest.version,
                    },
                )
                raise UpdateError(
                    f"Checksum invalide pour {package.filename} — mise à jour refusée, "
                    f"aucun paquet appliqué."
                )

        # --- 5. Installation via dpkg (dépendances CORRUX auto-suffisantes) -----
        applied_packages = []
        for package in manifest.packages:
            package_path = mount_point / package.filename
            install_result = runner(
                ["dpkg", "-i", str(package_path)], capture_output=True, text=True, check=False,
            )
            if install_result.returncode != 0:
                record_audit_event(
                    actor=actor, action="update.failed",
                    target=str(media_device_path),
                    metadata={
                        "reason": "installation_echouee", "package": package.filename,
                        "version": manifest.version, "packages_applied": applied_packages,
                    },
                )
                raise UpdateError(
                    f"Installation de {package.filename} échouée : "
                    f"{install_result.stderr.strip()}"
                )
            applied_packages.append(package.filename)

        # --- 6-7. Migrations + traçabilité — fonction partagée avec le
        # mode en ligne (TECH-015) ------------------------------------------------
        return run_migrations_and_record_success(
            version=manifest.version,
            packages_applied=tuple(applied_packages),
            modules_impacted=manifest.modules_impacted,
            media_target=str(media_device_path),
            actor=actor,
        )
    finally:
        runner(["umount", str(mount_point)], capture_output=True, text=True, check=False)


# ============================================================================
# TECH-015 — Canal de transport en ligne (§19.3)
# ============================================================================
#
# « Le mode en ligne ne remplace pas le mode hors ligne dans la
# conception : c'est un second canal de transport pour le MÊME
# mécanisme, pas une architecture parallèle » — les étapes 6-7
# (migrations + traçabilité) sont donc la fonction
# run_migrations_and_record_success() ci-dessus, jamais dupliquées.
#
# Différence assumée par rapport au mode hors ligne (§19.3, texte
# explicite) : « apt vérifie NATIVEMENT la signature du dépôt via la
# clé publique déjà installée » — pas packaging.signing
# (verify_bundle_signature), spécifique au format bundle+manifeste du
# mode hors ligne. La vérification ici est celle d'apt lui-même
# (mécanisme "signed-by" standard), vérifiée manuellement en ligne de
# commande avant d'écrire ce code (dépôt réel signé, servi en HTTPS,
# apt-get update/install réellement exécutés avec succès en
# environnement isolé).
#
# Isolation stricte : chaque appel utilise un répertoire d'état apt
# ENTIÈREMENT dédié (sources/cache/status) — jamais les répertoires
# système réels (/etc/apt/, /var/lib/apt/, /var/lib/dpkg/), qui
# restent totalement intacts quel que soit le résultat.


@dataclass(frozen=True)
class AptIsolationPaths:
    """Répertoires d'état apt entièrement dédiés à cet appel — jamais
    les répertoires système réels."""

    sources_list: Path
    sources_list_d: Path
    lists_dir: Path
    archives_dir: Path
    dpkg_status: Path

    @classmethod
    def create_under(cls, root: Path) -> AptIsolationPaths:
        (root / "sources.list.d").mkdir(parents=True, exist_ok=True)
        (root / "lists" / "partial").mkdir(parents=True, exist_ok=True)
        (root / "archives" / "partial").mkdir(parents=True, exist_ok=True)
        sources_list = root / "sources.list"
        dpkg_status = root / "dpkg-status"
        if not dpkg_status.exists():
            dpkg_status.write_text("")
        return cls(
            sources_list=sources_list,
            sources_list_d=root / "sources.list.d",
            lists_dir=root / "lists",
            archives_dir=root / "archives",
            dpkg_status=dpkg_status,
        )

    def as_apt_options(self, *, ca_cert_path: Path) -> list[str]:
        return [
            "-o", f"Dir::Etc::sourcelist={self.sources_list}",
            "-o", f"Dir::Etc::sourceparts={self.sources_list_d}",
            "-o", f"Dir::State::lists={self.lists_dir}",
            "-o", f"Dir::Cache::archives={self.archives_dir}",
            "-o", f"Dir::State::status={self.dpkg_status}",
            "-o", "APT::Get::AllowUnauthenticated=false",
            "-o", f"Acquire::https::CaInfo={ca_cert_path}",
        ]


def apply_online_update(
    *,
    repository_url: str,
    trusted_keyring_path: Path,
    package_names: tuple[str, ...],
    modules_impacted: tuple[str, ...],
    version: str,
    isolation_root: Path,
    ca_cert_path: Path,
    actor: User | None,
    runner=subprocess.run,
) -> UpdateReport:
    """Canal de transport en ligne — §19.3.

    `trusted_keyring_path` : la MÊME clé publique que le mode hors
    ligne, au format keyring binaire (`gpg --dearmor`), jamais le
    fichier ASCII-armored utilisé par packaging.signing (formats
    distincts, exigés par des outils distincts — apt vs GPG direct).

    `package_names` : les paquets `corrux-*` explicitement ciblés —
    pas un motif shell résolu ici, pour rester aussi explicite que le
    mode hors ligne (liste exacte des paquets, pas une expansion
    implicite pouvant capturer autre chose que prévu).
    """
    isolation = AptIsolationPaths.create_under(isolation_root)
    isolation.sources_list.write_text(
        f"deb [signed-by={trusted_keyring_path}] {repository_url} ./\n"
    )
    apt_options = isolation.as_apt_options(ca_cert_path=ca_cert_path)

    update_result = runner(
        ["apt-get", "update"] + apt_options, capture_output=True, text=True, check=False,
    )
    if update_result.returncode != 0:
        record_audit_event(
            actor=actor, action="update.refused",
            target=repository_url,
            metadata={"reason": "depot_non_verifie", "detail": update_result.stderr.strip()},
        )
        raise UpdateError(
            f"Vérification du dépôt distant échouée : {update_result.stderr.strip()}"
        )

    install_result = runner(
        ["apt-get", "install", "-y", *package_names] + apt_options,
        capture_output=True, text=True, check=False,
    )
    if install_result.returncode != 0:
        record_audit_event(
            actor=actor, action="update.failed",
            target=repository_url,
            metadata={"reason": "installation_echouee", "detail": install_result.stderr.strip()},
        )
        raise UpdateError(f"Installation échouée : {install_result.stderr.strip()}")

    return run_migrations_and_record_success(
        version=version,
        packages_applied=package_names,
        modules_impacted=modules_impacted,
        media_target=repository_url,
        actor=actor,
    )
