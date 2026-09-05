"""Invocations OpenSSL brutes — CA interne et certificat serveur (§10).

§10 nomme explicitement OpenSSL comme outil ("CA interne CORRUX générée
localement (OpenSSL)") — invoqué via subprocess, même patron déjà établi
pour gpg (core/backup/encryption.py, TECH-009) : jamais une dépendance
Python de cryptographie ajoutée pour ce besoin (cryptography n'est pas
installée dans ce projet), cohérent avec le principe d'auto-suffisance
offline déjà posé pour toute la stack (architecture-technique-v1.md §3 :
tout provient des dépôts Debian embarqués — openssl fait partie du
paquet base de toute installation Debian).

Ce module ne fait AUCUN choix d'orchestration (durées de validité,
seuils d'alerte, audit) — cf. core/certs/service.py pour cette couche.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path


class CertificateError(Exception):
    """Une opération OpenSSL a échoué. Ne contient jamais de clé privée."""


def _run(command: list[str], runner, description: str):
    result = runner(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise CertificateError(
            f"{description} échouée (code {result.returncode}) : {result.stderr.strip()}"
        )
    return result


def generate_ca(
    ca_key_path: Path,
    ca_cert_path: Path,
    *,
    common_name: str = "CORRUX Root CA",
    validity_days: int,
    runner=subprocess.run,
) -> None:
    """Génère une CA interne CORRUX — clé privée RSA 4096 bits +
    certificat racine auto-signé.

    Le technicien distribue/installe le certificat racine (`ca_cert_path`)
    sur les postes clients — procédure hors périmètre code (§10, décision
    explicite). La clé privée (`ca_key_path`) ne quitte jamais ce
    système.
    """
    _run(
        ["openssl", "genrsa", "-out", str(ca_key_path), "4096"],
        runner,
        "Génération de la clé privée de la CA",
    )
    _run(
        [
            "openssl", "req", "-x509", "-new", "-nodes",
            "-key", str(ca_key_path),
            "-sha256",
            "-days", str(validity_days),
            "-out", str(ca_cert_path),
            "-subj", f"/CN={common_name}/O=CORRUX",
        ],
        runner,
        "Génération du certificat de la CA",
    )
    ca_key_path.chmod(0o600)


def issue_server_certificate(
    server_key_path: Path,
    server_cert_path: Path,
    ca_key_path: Path,
    ca_cert_path: Path,
    *,
    common_name: str,
    validity_days: int,
    runner=subprocess.run,
) -> None:
    """Émet (ou renouvelle) un certificat serveur signé par la CA
    interne déjà existante — jamais une nouvelle CA."""
    if not ca_key_path.exists() or not ca_cert_path.exists():
        raise CertificateError(
            f"CA introuvable — générez-la d'abord (générate_ca) : "
            f"{ca_key_path} / {ca_cert_path}"
        )

    _run(
        ["openssl", "genrsa", "-out", str(server_key_path), "2048"],
        runner,
        "Génération de la clé privée du serveur",
    )

    csr_path = server_cert_path.with_suffix(".csr")
    _run(
        [
            "openssl", "req", "-new",
            "-key", str(server_key_path),
            "-out", str(csr_path),
            "-subj", f"/CN={common_name}",
        ],
        runner,
        "Génération de la requête de signature (CSR)",
    )

    try:
        _run(
            [
                "openssl", "x509", "-req",
                "-in", str(csr_path),
                "-CA", str(ca_cert_path),
                "-CAkey", str(ca_key_path),
                "-CAcreateserial",
                "-out", str(server_cert_path),
                "-days", str(validity_days),
                "-sha256",
            ],
            runner,
            "Signature du certificat serveur",
        )
    finally:
        # La CSR est un artefact intermédiaire — jamais laissée sur
        # disque, réussite ou échec (même discipline que TECH-009 pour
        # les artefacts de sauvegarde en clair).
        csr_path.unlink(missing_ok=True)

    server_key_path.chmod(0o600)


def get_certificate_expiry(cert_path: Path, *, runner=subprocess.run) -> datetime:
    """Date d'expiration réelle d'un certificat — lue directement sur
    le fichier via `openssl x509 -enddate`, jamais recalculée à partir
    d'une durée de validité supposée (le fichier fait foi)."""
    result = _run(
        ["openssl", "x509", "-in", str(cert_path), "-noout", "-enddate"],
        runner,
        "Lecture de la date d'expiration",
    )
    _, _, raw_date = result.stdout.strip().partition("=")
    # OpenSSL rend toujours cette date en GMT (documenté, pas supposé) —
    # jamais %Z (le parsing de fuseau par strptime est peu fiable),
    # la mention finale est retirée puis UTC est attaché explicitement.
    naive = datetime.strptime(raw_date.strip().removesuffix(" GMT"), "%b %d %H:%M:%S %Y")
    return naive.replace(tzinfo=UTC)
