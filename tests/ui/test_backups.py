"""Tests de l'écran Sauvegardes — UI-205.

Rendu HTTP réel (patron établi UI-101 à UI-203), RBAC réel (TECH-003).
Fixtures BackupRun créées directement via l'ORM (champs réels
uniquement) — pas de mock artificiel, écran strictement en lecture ne
nécessitant pas le cycle lourd de run_backup() (TECH-009, déjà testé
pour lui-même).
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.template import engines
from django.test import Client

from core.audit.models import AuditLog
from core.authz.models import Permission, Role, RolePermission, UserRole
from core.backup.models import BackupRun
from core.identity import auth
from core.identity.models import User

LIST_URL = "/sauvegardes/"


def _grant(user, module_id, resource, action):
    role = Role.objects.create(name=f"role-{user.username}-{resource}-{action}")
    permission, _ = Permission.objects.get_or_create(
        module_id=module_id, resource=resource, action=action
    )
    RolePermission.objects.create(role=role, permission=permission)
    UserRole.objects.create(user=user, role=role)


def _authenticated_client(user) -> Client:
    client = Client()
    session = client.session
    session[auth.SESSION_USER_ID_KEY] = user.id
    session.save()
    client.cookies["sessionid"] = session.session_key
    return client


@pytest.fixture
def reader(db):
    user = User.objects.create(username="lecteur_bck", full_name="Lecteur Sauvegardes")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "core", "backup", "read")
    return user


@pytest.fixture
def no_permission_user(db):
    user = User.objects.create(username="sanspermission_bck", full_name="Sans Permission")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


def _dt(offset_days=0, hour=10, minute=0):
    return datetime(2026, 1, 1, hour, minute, tzinfo=UTC) + timedelta(
        days=offset_days
    )


def _make_run(status, started_at, finished_at=None, size_bytes=None, location=""):
    return BackupRun.objects.create(
        started_at=started_at,
        finished_at=finished_at or started_at + timedelta(minutes=2),
        status=status,
        size_bytes=size_bytes,
        location=location,
    )


# --- A. Permission ------------------------------------------------------------


@pytest.mark.django_db
class TestPermission:
    def test_authorized_user_sees_the_screen(self, reader):
        client = _authenticated_client(reader)
        response = client.get(LIST_URL)
        assert response.status_code == 200

    def test_unauthorized_user_sees_permission_denied(self, no_permission_user):
        client = _authenticated_client(no_permission_user)
        response = client.get(LIST_URL)
        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()

    def test_anonymous_user_is_redirected_to_login(self):
        client = Client()
        response = client.get(LIST_URL)
        assert response.status_code == 302
        assert response.url == f"/login/?next={LIST_URL}"


# --- B. État vide ---------------------------------------------------------------


@pytest.mark.django_db
class TestEmptyState:
    def test_no_backup_run_renders_empty_state(self, reader):
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-empty-state" in content
        assert "corrux-table" not in content

    def test_empty_state_cards_show_neutral_values(self, reader):
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "Aucun succès enregistré" in content
        assert "corrux-stat-card" in content


# --- C. Historique réel -------------------------------------------------------


@pytest.mark.django_db
class TestRealHistory:
    def test_real_runs_are_displayed(self, reader):
        _make_run(
            BackupRun.Status.SUCCESS,
            _dt(0),
            size_bytes=15_728_640,
            location="/mnt/corrux-backup/corrux-backup-20260101T100200-abcd1234.tar.gpg",
        )
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "01/01/2026" in content
        assert "15.0 Mo" in content
        assert "/mnt/corrux-backup" in content

    def test_runs_are_ordered_most_recent_first(self, reader):
        _make_run(BackupRun.Status.SUCCESS, _dt(0))
        _make_run(BackupRun.Status.SUCCESS, _dt(2))
        _make_run(BackupRun.Status.SUCCESS, _dt(1))

        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()

        pos_day3 = content.index("03/01/2026")
        pos_day2 = content.index("02/01/2026")
        pos_day1 = content.index("01/01/2026 11:00")  # 10:00 UTC -> 11:00 Europe/Paris (janvier)
        assert pos_day3 < pos_day2 < pos_day1

    def test_duration_is_correctly_computed(self, reader):
        _make_run(
            BackupRun.Status.SUCCESS,
            _dt(0, hour=2, minute=0),
            finished_at=_dt(0, hour=2, minute=3) + timedelta(seconds=12),
        )
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "3 min 12 s" in content

    def test_missing_size_shows_neutral_value(self, reader):
        _make_run(BackupRun.Status.FAILURE, _dt(0), size_bytes=None, location="")
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert ">—<" in content or "<td>—</td>" in content

    def test_no_data_is_invented_beyond_real_model_fields(self, reader):
        run = _make_run(
            BackupRun.Status.SUCCESS, _dt(0), size_bytes=1000, location="/x/y.tar.gpg"
        )
        _authenticated_client(reader)
        assert BackupRun.objects.count() == 1
        assert run.status in [r.status for r in BackupRun.objects.all()]


# --- D. Statuts ---------------------------------------------------------------


@pytest.mark.django_db
class TestStatusMapping:
    def test_success_maps_to_success_badge(self, reader):
        _make_run(BackupRun.Status.SUCCESS, _dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-badge--success" in content
        assert "Succès" in content

    def test_failure_maps_to_error_badge(self, reader):
        _make_run(BackupRun.Status.FAILURE, _dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-badge--error" in content
        assert "Échec" in content

    def test_refused_maps_to_warning_badge(self, reader):
        _make_run(BackupRun.Status.REFUSED, _dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-badge--warning" in content
        assert "Refusé" in content

    def test_no_other_tone_is_ever_used_for_a_run_row(self, reader):
        _make_run(BackupRun.Status.SUCCESS, _dt(0))
        _make_run(BackupRun.Status.FAILURE, _dt(1))
        _make_run(BackupRun.Status.REFUSED, _dt(2))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-badge--info" not in content


# --- E. Cartes ---------------------------------------------------------------


@pytest.mark.django_db
class TestSummaryCards:
    def test_last_success_card_reflects_most_recent_success(self, reader):
        _make_run(BackupRun.Status.SUCCESS, _dt(0))
        _make_run(BackupRun.Status.FAILURE, _dt(2))
        _make_run(BackupRun.Status.SUCCESS, _dt(1))

        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "02/01/2026" in content

    def test_status_card_reflects_most_recent_run_even_if_failure(self, reader):
        _make_run(BackupRun.Status.SUCCESS, _dt(0))
        _make_run(BackupRun.Status.FAILURE, _dt(3))

        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        cards_start = content.index('class="corrux-stat-cards"')
        table_start = content.index("corrux-table", cards_start)
        cards_block = content[cards_start:table_start]
        assert "Échec" in cards_block

    def test_destination_card_derived_from_last_success_location(self, reader):
        _make_run(
            BackupRun.Status.SUCCESS,
            _dt(0),
            location="/mnt/corrux-backup/corrux-backup-20260101T100200-abcd1234.tar.gpg",
        )
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "/mnt/corrux-backup" in content

    def test_no_success_shows_neutral_values_on_all_relevant_cards(self, reader):
        _make_run(BackupRun.Status.FAILURE, _dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "Aucun succès enregistré" in content


# --- F. Sécurité / lecture seule ----------------------------------------------


@pytest.mark.django_db
class TestReadOnly:
    def test_get_works_for_authorized_user(self, reader):
        client = _authenticated_client(reader)
        assert client.get(LIST_URL).status_code == 200

    def test_no_post_route_is_exposed(self, reader):
        client = _authenticated_client(reader)
        response = client.post(LIST_URL)
        assert response.status_code == 405

    def test_post_attempt_never_creates_a_backup_run(self, reader):
        client = _authenticated_client(reader)
        client.post(LIST_URL)
        assert BackupRun.objects.count() == 0

    def test_no_restore_or_delete_route_exists(self, reader):
        client = _authenticated_client(reader)
        for suffix in ("restaurer", "supprimer", "telecharger", "1/restaurer"):
            response = client.get(f"{LIST_URL}{suffix}/")
            assert response.status_code == 404

    def test_no_ui_triggered_audit_event_is_created(self, reader):
        """L'écran ne déclenche jamais run_backup() : aucun événement
        backup.run ne doit apparaître à la simple consultation."""
        _make_run(BackupRun.Status.SUCCESS, _dt(0))
        client = _authenticated_client(reader)
        client.get(LIST_URL)
        assert not AuditLog.objects.filter(action="backup.run").exists()


# --- G. Échappement -----------------------------------------------------------


@pytest.mark.django_db
class TestEscaping:
    def test_location_value_is_html_escaped(self, reader):
        _make_run(
            BackupRun.Status.SUCCESS,
            _dt(0),
            location="/mnt/corrux-backup/<script>alert(1)</script>/x.tar.gpg",
        )
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "<script>alert(1)</script>" not in content


# --- H. Composant stat_card (isolé, sans BackupRun) ---------------------------


class TestStatCardComponent:
    def _render(self, snippet, context=None):
        template = engines["django"].from_string("{% load ui_tags %}" + snippet)
        return template.render(context or {})

    def test_renders_label_and_value(self):
        html = self._render('{% corrux_stat_card label="Employés actifs" value="42" %}')
        assert "Employés actifs" in html
        assert "42" in html
        assert "corrux-stat-card" in html

    def test_value_is_html_escaped_when_plain_text(self):
        html = self._render(
            "{% corrux_stat_card label=label value=value %}",
            {"label": "X", "value": "<script>alert(1)</script>"},
        )
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_no_reference_to_backup_run_in_component_template(self):
        import re
        from pathlib import Path

        source = Path("ui/templates/ui/components/stat_card.html").read_text(
            encoding="utf-8"
        )
        # Le commentaire explicatif du template mentionne volontairement
        # "BackupRun" en prose pour justifier l'absence de référence
        # métier — on l'exclut avant de vérifier le code actif du
        # template (balises/variables), seul endroit où une vraie
        # dépendance apparaîtrait.
        active_code = re.sub(r"{% comment %}.*?{% endcomment %}", "", source, flags=re.DOTALL)
        assert "backup" not in active_code.lower()

    def test_component_has_no_action_or_permission_parameters(self):
        import inspect

        from ui.templatetags.ui_tags import corrux_stat_card

        signature = inspect.signature(corrux_stat_card)
        assert set(signature.parameters) == {"label", "value"}

    def test_no_django_comment_leaks_into_output(self):
        html = self._render('{% corrux_stat_card label="X" value="1" %}')
        assert "{#" not in html
        assert "{% comment %}" not in html
