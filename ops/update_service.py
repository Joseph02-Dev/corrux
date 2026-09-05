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

        # --- 6. Migrations — réutilisation directe de manage.py migrate --------
        for module_id in manifest.modules_impacted:
            call_command("migrate", module_id, verbosity=0)

        # --- 7. Traçabilité ------------------------------------------------------
        record_audit_event(
            actor=actor, action="update.applied",
            target=str(media_device_path),
            metadata={
                "version": manifest.version,
                "packages_applied": applied_packages,
                "modules_migrated": list(manifest.modules_impacted),
            },
        )

        return UpdateReport(
            version=manifest.version,
            packages_applied=tuple(applied_packages),
            modules_migrated=manifest.modules_impacted,
        )
    finally:
        runner(["umount", str(mount_point)], capture_output=True, text=True, check=False)
