"""Tests du service CA/certificat — TECH-010 (§10).

« Test d'intégration émission + expiration simulée » (contrat explicite
du ticket) : vraie invocation OpenSSL (jamais de mock), expiration
"simulée" via une validité réellement courte (pas une manipulation de
l'horloge système, impraticable et non sûre en test) — un certificat
émis avec une validité de 1 jour est réellement, littéralement proche
de l'expiration au moment du test.
"""

import os
import subprocess

import pytest
from django.core.management import call_command, get_commands
from django.utils import timezone

from core.audit.models import AuditLog
from core.certs.openssl import CertificateError, get_certificate_expiry
from core.certs.service import (
    CertificatePaths,
    check_certificate_expiry,
    initialize_ca_and_server_certificate,
    renew_server_certificate,
)
from core.identity.models import User


@pytest.fixture
def paths(tmp_path) -> CertificatePaths:
    return CertificatePaths(
        ca_key=tmp_path / "ca.key",
        ca_cert=tmp_path / "ca.crt",
        server_key=tmp_path / "server.key",
        server_cert=tmp_path / "server.crt",
    )


@pytest.fixture
def actor(db):
    return User.objects.create(username="technicien_tech010", full_name="Technicien")


def _verify_chain(ca_cert_path, server_cert_path) -> bool:
    result = subprocess.run(
        ["openssl", "verify", "-CAfile", str(ca_cert_path), str(server_cert_path)],
        capture_output=True, text=True,
    )
    return result.returncode == 0


# --- A. Émission — CA + premier certificat ---------------------------------------


@pytest.mark.django_db
class TestInitializeCaAndServerCertificate:
    def test_generates_all_four_files(self, paths, actor):
        initialize_ca_and_server_certificate(paths, common_name="corrux.local", actor=actor)

        assert paths.ca_key.exists()
        assert paths.ca_cert.exists()
        assert paths.server_key.exists()
        assert paths.server_cert.exists()

    def test_server_certificate_chain_verifies_against_the_ca(self, paths, actor):
        """Le certificat serveur est réellement signé par la CA
        générée — vérifié par openssl verify, pas supposé."""
        initialize_ca_and_server_certificate(paths, common_name="corrux.local", actor=actor)

        assert _verify_chain(paths.ca_cert, paths.server_cert)

    def test_private_keys_are_not_world_readable(self, paths, actor):
        initialize_ca_and_server_certificate(paths, common_name="corrux.local", actor=actor)

        assert oct(paths.ca_key.stat().st_mode)[-3:] == "600"
        assert oct(paths.server_key.stat().st_mode)[-3:] == "600"

    def test_records_an_audit_event(self, paths, actor):
        initialize_ca_and_server_certificate(paths, common_name="corrux.local", actor=actor)

        assert AuditLog.objects.filter(
            actor_user=actor, action="certificate.ca_initialized", target="corrux.local"
        ).exists()

    def test_no_leftover_csr_file(self, paths, actor, tmp_path):
        initialize_ca_and_server_certificate(paths, common_name="corrux.local", actor=actor)

        assert list(tmp_path.glob("*.csr")) == []

    def test_respects_custom_validity_days(self, paths, actor):
        initialize_ca_and_server_certificate(
            paths, common_name="corrux.local", actor=actor,
            ca_validity_days=100, server_validity_days=10,
        )

        expiry = get_certificate_expiry(paths.server_cert)
        days_remaining = (expiry - timezone.now()).days
        assert 8 <= days_remaining <= 10


# --- B. Renouvellement ------------------------------------------------------------


@pytest.mark.django_db
class TestRenewServerCertificate:
    def test_renewal_issues_a_different_certificate(self, paths, actor):
        initialize_ca_and_server_certificate(paths, common_name="corrux.local", actor=actor)
        original_cert_bytes = paths.server_cert.read_bytes()

        renew_server_certificate(paths, common_name="corrux.local", actor=actor)

        assert paths.server_cert.read_bytes() != original_cert_bytes

    def test_renewed_certificate_still_chains_to_the_same_ca(self, paths, actor):
        initialize_ca_and_server_certificate(paths, common_name="corrux.local", actor=actor)
        renew_server_certificate(paths, common_name="corrux.local", actor=actor)

        assert _verify_chain(paths.ca_cert, paths.server_cert)

    def test_renewal_never_regenerates_the_ca(self, paths, actor):
        """§10 : renouvellement du certificat serveur uniquement,
        jamais une nouvelle CA."""
        initialize_ca_and_server_certificate(paths, common_name="corrux.local", actor=actor)
        original_ca_bytes = paths.ca_cert.read_bytes()

        renew_server_certificate(paths, common_name="corrux.local", actor=actor)

        assert paths.ca_cert.read_bytes() == original_ca_bytes

    def test_records_an_audit_event(self, paths, actor):
        initialize_ca_and_server_certificate(paths, common_name="corrux.local", actor=actor)
        renew_server_certificate(paths, common_name="corrux.local", actor=actor)

        assert AuditLog.objects.filter(
            actor_user=actor, action="certificate.renewed", target="corrux.local"
        ).exists()

    def test_renewal_fails_without_an_existing_ca(self, paths, actor):
        with pytest.raises(CertificateError):
            renew_server_certificate(paths, common_name="corrux.local", actor=actor)


# --- C. Expiration simulée — cas explicitement requis par le contrat -----------


@pytest.mark.django_db
class TestCheckCertificateExpiry:
    def test_far_expiry_does_not_alert(self, paths, actor):
        initialize_ca_and_server_certificate(
            paths, common_name="corrux.local", actor=actor,
            server_validity_days=730,
        )

        alerted = check_certificate_expiry(
            paths.server_cert, actor=actor, alert_threshold_days=30
        )

        assert alerted is False
        assert not AuditLog.objects.filter(action="certificate.expiry_alert").exists()

    def test_near_expiry_alerts(self, paths, actor):
        """Expiration « simulée » : un certificat réellement émis avec
        une validité de 1 jour est réellement proche de l'expiration —
        pas un mock, pas une horloge système manipulée."""
        initialize_ca_and_server_certificate(
            paths, common_name="corrux.local", actor=actor,
            server_validity_days=1,
        )

        alerted = check_certificate_expiry(
            paths.server_cert, actor=actor, alert_threshold_days=30
        )

        assert alerted is True

    def test_alert_records_an_audit_event_with_expiry_details(self, paths, actor):
        initialize_ca_and_server_certificate(
            paths, common_name="corrux.local", actor=actor, server_validity_days=1,
        )

        check_certificate_expiry(paths.server_cert, actor=actor, alert_threshold_days=30)

        entry = AuditLog.objects.get(action="certificate.expiry_alert")
        assert entry.actor_user == actor
        assert "expires_at" in entry.metadata
        assert "days_remaining" in entry.metadata

    def test_no_actor_records_event_without_actor_user(self, paths):
        """`actor=None` — exécution automatisée par systemd, même
        convention que run_backup() (TECH-009)."""
        technician = User.objects.create(username="tech_auto_010", full_name="Auto")
        initialize_ca_and_server_certificate(
            paths, common_name="corrux.local", actor=technician, server_validity_days=1,
        )

        check_certificate_expiry(paths.server_cert, actor=None, alert_threshold_days=30)

        entry = AuditLog.objects.get(action="certificate.expiry_alert")
        assert entry.actor_user is None

    def test_custom_threshold_is_respected(self, paths, actor):
        initialize_ca_and_server_certificate(
            paths, common_name="corrux.local", actor=actor, server_validity_days=10,
        )

        assert check_certificate_expiry(
            paths.server_cert, actor=actor, alert_threshold_days=5
        ) is False
        assert check_certificate_expiry(
            paths.server_cert, actor=actor, alert_threshold_days=15
        ) is True


# --- D. Commandes de management réellement invocables ---------------------------
# Régression explicite : ces 3 commandes (TECH-010) ET run_backup
# (TECH-009, gap pré-existant découvert et corrigé à l'occasion de ce
# ticket) n'étaient PAS découvrables par Django avant la correction de
# INSTALLED_APPS (core.backup/core.certs jamais enregistrés comme apps
# séparées) — un `manage.py run_backup` échouait silencieusement avec
# "Unknown command", ce qui aurait rendu le mécanisme de sauvegarde
# planifiée totalement inopérant en production malgré tous les tests
# de service déjà verts (ils testent run_backup() la fonction
# directement, jamais la commande manage.py elle-même). Corrigé dans
# corrux_core/settings.py — vérifié ici pour ne plus jamais régresser
# silencieusement.


@pytest.mark.django_db
class TestManagementCommandsAreDiscoverable:
    def test_run_backup_is_a_real_discoverable_command(self):
        """Régression pour un bug pré-existant de TECH-009, découvert
        et corrigé à l'occasion de ce ticket (cf. commentaire de
        classe) — jamais testé jusqu'ici."""
        assert get_commands().get("run_backup") == "core.backup"

    def test_check_certificate_expiry_is_a_real_discoverable_command(self):
        assert get_commands().get("check_certificate_expiry") == "core.certs"

    def test_renew_certificate_is_a_real_discoverable_command(self):
        assert get_commands().get("renew_certificate") == "core.certs"

    def test_initialize_ca_is_a_real_discoverable_command(self):
        assert get_commands().get("initialize_ca") == "core.certs"

    def test_check_certificate_expiry_command_invokable_end_to_end(self, paths, actor):
        """Invocation réelle via call_command (pas juste un appel direct
        à la fonction de service) — couvre la commande elle-même,
        variables d'environnement comprises."""
        initialize_ca_and_server_certificate(
            paths, common_name="corrux.local", actor=actor, server_validity_days=1,
        )

        os.environ["CORRUX_CERT_SERVER_CERT_PATH"] = str(paths.server_cert)
        try:
            call_command("check_certificate_expiry")
        finally:
            del os.environ["CORRUX_CERT_SERVER_CERT_PATH"]

        assert AuditLog.objects.filter(action="certificate.expiry_alert").exists()
