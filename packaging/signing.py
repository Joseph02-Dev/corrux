"""Signature et vérification de release CORRUX — TECH-013 (§19.1).

GPG via subprocess — même patron déjà établi pour le chiffrement des
sauvegardes (core/backup/encryption.py, TECH-009) et les certificats
(core/certs/openssl.py, TECH-010) : un outil système, jamais une
dépendance Python de cryptographie ajoutée pour ce besoin, cohérent
avec le principe d'auto-suffisance offline (§3).

Aucune dépendance Django — module pur, réutilisable directement par
corrux-update (TECH-014/015, hors périmètre de ce ticket) sans
présupposer un contexte applicatif particulier.

Principe de confiance central : la vérification importe la clé
publique de confiance dans un trousseau GPG ISOLÉ et ÉPHÉMÈRE, créé
pour CETTE seule vérification — jamais le trousseau GPG ambiant/
partagé de la machine. Un bundle signé par une clé absente de ce
trousseau isolé est rejeté (GPG : « Can't check signature: No public
key », code retour non nul) — c'est le mécanisme même par lequel
« une clé inconnue » est rejetée (critère d'acceptation explicite du
ticket), vérifié manuellement en ligne de commande avant d'écrire le
moindre code.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class SignatureVerificationError(Exception):
    """La signature n'a pas pu être produite ou vérifiée comme valide.

    Un seul type d'erreur pour les trois cas distincts (signature
    absente, bundle altéré, clé signataire inconnue) — le critère
    d'acceptation du ticket les traite tous comme un rejet, pas trois
    diagnostics différents à distinguer côté appelant.
    """


def sign_bundle(
    bundle_path: Path,
    signature_path: Path,
    *,
    gnupg_home: Path,
    runner=subprocess.run,
) -> None:
    """Produit une signature GPG détachée du bundle, avec la clé
    privée présente dans `gnupg_home` — jamais présente sur les
    machines clientes (§19.1). Utilise la clé secrète par défaut du
    trousseau fourni ; ne s'applique qu'à un trousseau ne contenant
    qu'une seule clé de signature (le trousseau de release CORRUX,
    jamais un trousseau partagé)."""
    result = runner(
        [
            "gpg", "--homedir", str(gnupg_home), "--batch", "--yes",
            "--detach-sign", "--armor", "-o", str(signature_path), str(bundle_path),
        ],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise SignatureVerificationError(f"Signature échouée : {result.stderr.strip()}")


def verify_bundle_signature(
    bundle_path: Path,
    signature_path: Path,
    *,
    trusted_public_key_path: Path,
    runner=subprocess.run,
) -> None:
    """Vérifie la signature détachée d'un bundle contre UNE SEULE clé
    de confiance — jamais le trousseau GPG ambiant de la machine.

    Lève SignatureVerificationError si la signature est absente,
    invalide (bundle altéré après signature) ou produite par une clé
    différente de `trusted_public_key_path` — les trois cas sont
    volontairement traités de façon identique (rejet), conformément au
    critère d'acceptation explicite du ticket.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        verifier_home = Path(tmp_dir) / "gnupg-verifier"
        verifier_home.mkdir(mode=0o700)

        import_result = runner(
            ["gpg", "--homedir", str(verifier_home), "--import", str(trusted_public_key_path)],
            capture_output=True, text=True, check=False,
        )
        if import_result.returncode != 0:
            raise SignatureVerificationError(
                f"Import de la clé de confiance échoué : {import_result.stderr.strip()}"
            )

        verify_result = runner(
            [
                "gpg", "--homedir", str(verifier_home),
                "--verify", str(signature_path), str(bundle_path),
            ],
            capture_output=True, text=True, check=False,
        )
        if verify_result.returncode != 0:
            raise SignatureVerificationError(
                f"Signature invalide ou clé signataire inconnue : "
                f"{verify_result.stderr.strip()}"
            )


# --- Rotation de clé (§19.1 : « livrée comme un paquet spécial signé
# par la clé précédente ») --------------------------------------------------------


def create_rotation_package(
    new_public_key_path: Path,
    rotation_signature_path: Path,
    *,
    previous_gnupg_home: Path,
    runner=subprocess.run,
) -> None:
    """Signe la nouvelle clé publique avec l'ancienne clé privée —
    chaîne de confiance (§19.1), jamais un remplacement manuel non
    vérifié. Produit exactement le même type d'artefact qu'un bundle
    de mise à jour normal (contenu + signature détachée) : la nouvelle
    clé publique EST le contenu signé."""
    sign_bundle(
        new_public_key_path, rotation_signature_path,
        gnupg_home=previous_gnupg_home, runner=runner,
    )


def rotate_trusted_key(
    new_public_key_path: Path,
    rotation_signature_path: Path,
    *,
    current_trusted_public_key_path: Path,
    runner=subprocess.run,
) -> None:
    """Adopte une nouvelle clé de confiance — uniquement si le paquet
    de rotation est authentifié par la clé ACTUELLEMENT approuvée
    (chaîne de confiance, §19.1). Lève SignatureVerificationError et ne
    modifie rien si la vérification échoue — la clé actuellement
    approuvée reste en vigueur tant que la rotation n'est pas prouvée
    légitime."""
    verify_bundle_signature(
        new_public_key_path, rotation_signature_path,
        trusted_public_key_path=current_trusted_public_key_path, runner=runner,
    )
    current_trusted_public_key_path.write_bytes(new_public_key_path.read_bytes())
