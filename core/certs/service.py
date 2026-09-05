"""Orchestration CA/certificat + alerte d'expiration — TECH-010 (§10).

Cf. core/certs/openssl.py pour les invocations OpenSSL brutes (aucune
décision d'orchestration). Ce module ajoute la traçabilité
(core.audit_log) et les décisions d'orchestration.

Valeurs par défaut — aucune source ne les impose littéralement (§10 dit
« ex. 2 ans » pour le certificat serveur, aucun chiffre pour la durée
de vie de la CA ni pour le seuil d'alerte avant expiration) :
paramètres techniques purs, sans implication de modèle de données ni de
règle métier — décidés ici avec une justification explicite plutôt
qu'escaladés (contrairement aux décisions de champ/schéma des tickets
RH, qui engageaient une vraie sémantique produit).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.utils import timezone

from core.audit.service import record_audit_event
from core.certs.openssl import generate_ca, get_certificate_expiry, issue_server_certificate
from core.identity.models import User

DEFAULT_CA_VALIDITY_DAYS = 3650  # 10 ans — pratique courante pour une CA racine.
DEFAULT_SERVER_CERT_VALIDITY_DAYS = 730  # 2 ans — valeur donnée explicitement par §10.
DEFAULT_ALERT_THRESHOLD_DAYS = 30  # pratique courante pour une alerte d'expiration TLS.


@dataclass(frozen=True)
class CertificatePaths:
    """Chemins des 4 fichiers manipulés — jamais codés en dur ailleurs,
    toujours fournis explicitement par l'appelant (corrux-setup,
    TECH-012, ou un test)."""

    ca_key: Path
    ca_cert: Path
    server_key: Path
    server_cert: Path


def initialize_ca_and_server_certificate(
    paths: CertificatePaths,
    *,
    common_name: str,
    actor: User | None,
    ca_validity_days: int = DEFAULT_CA_VALIDITY_DAYS,
    server_validity_days: int = DEFAULT_SERVER_CERT_VALIDITY_DAYS,
) -> None:
    """Génère la CA interne CORRUX ET émet le premier certificat
    serveur — étape d'installation initiale.

    Primitive que `corrux-setup` (TECH-012, hors périmètre de ce
    ticket) appellera ; non orchestrée ici au-delà de cet appel unique.
    """
    generate_ca(paths.ca_key, paths.ca_cert, validity_days=ca_validity_days)
    issue_server_certificate(
        paths.server_key, paths.server_cert, paths.ca_key, paths.ca_cert,
        common_name=common_name, validity_days=server_validity_days,
    )
    record_audit_event(
        actor=actor,
        action="certificate.ca_initialized",
        target=common_name,
        metadata={
            "ca_validity_days": ca_validity_days,
            "server_validity_days": server_validity_days,
        },
    )


def renew_server_certificate(
    paths: CertificatePaths,
    *,
    common_name: str,
    actor: User | None,
    validity_days: int = DEFAULT_SERVER_CERT_VALIDITY_DAYS,
) -> None:
    """Renouvelle le certificat serveur — signé par la CA déjà
    existante, jamais une nouvelle CA (`corrux-cert renew`, §10 :
    « renouvellement déclenché manuellement par le technicien »)."""
    issue_server_certificate(
        paths.server_key, paths.server_cert, paths.ca_key, paths.ca_cert,
        common_name=common_name, validity_days=validity_days,
    )
    record_audit_event(
        actor=actor, action="certificate.renewed", target=common_name, metadata={},
    )


def check_certificate_expiry(
    cert_path: Path,
    *,
    actor: User | None = None,
    alert_threshold_days: int = DEFAULT_ALERT_THRESHOLD_DAYS,
) -> bool:
    """Vérifie l'expiration du certificat serveur —
    `corrux-cert-check.timer` (§10 : « alerte (log + entrée audit_log)
    avant expiration »).

    Retourne True si une alerte a été déclenchée (expire dans moins de
    `alert_threshold_days`) — une entrée audit_log est alors créée.
    Ne déclenche JAMAIS de renouvellement automatique (§10 :
    renouvellement toujours manuel, `corrux-cert renew` — « pas
    d'automatisation réseau requise »).

    `actor=None` par défaut : ce contrôle est déclenché
    automatiquement par systemd, sans utilisateur interactif — même
    convention que `run_backup()` (TECH-009).
    """
    expiry = get_certificate_expiry(cert_path)
    days_remaining = (expiry - timezone.now()).days

    if days_remaining > alert_threshold_days:
        return False

    record_audit_event(
        actor=actor,
        action="certificate.expiry_alert",
        target=str(cert_path),
        metadata={"expires_at": expiry.isoformat(), "days_remaining": days_remaining},
    )
    return True
