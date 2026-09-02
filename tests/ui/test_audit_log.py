"""Tests du Journal d'audit — UI-206.

Rendu HTTP réel (patron établi UI-101 à UI-205), RBAC réel (TECH-003).
Fixtures AuditLog créées directement via l'ORM (champs réels
uniquement), cohérentes avec les événements réellement produits par le
code (core/modules/manager.py, core/backup/service.py,
core/identity/auth.py, ui/views.py).
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client

from core.audit.models import AuditLog
from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User

LIST_URL = "/journal-audit/"


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
    user = User.objects.create(username="lecteur_audit", full_name="Lecteur Audit")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "core", "audit", "read")
    return user


@pytest.fixture
def no_permission_user(db):
    user = User.objects.create(username="sanspermission_audit", full_name="Sans Permission")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


def _dt(offset_seconds=0):
    return datetime(2026, 3, 1, 12, 0, tzinfo=UTC) + timedelta(
        seconds=offset_seconds
    )


def _make_entry(action, target, actor=None, status=None, timestamp=None, extra=None):
    metadata = dict(extra or {})
    if status is not None:
        metadata["status"] = status
    entry = AuditLog.objects.create(
        actor_user=actor, action=action, target=target, metadata=metadata
    )
    if timestamp is not None:
        AuditLog.objects.filter(pk=entry.pk).update(timestamp=timestamp)
        entry.refresh_from_db()
    return entry


# --- A. Permission ------------------------------------------------------------


@pytest.mark.django_db
class TestPermission:
    def test_authorized_user_gets_200(self, reader):
        client = _authenticated_client(reader)
        assert client.get(LIST_URL).status_code == 200

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


# --- B. Affichage réel ----------------------------------------------------------


@pytest.mark.django_db
class TestRealDisplay:
    def test_multiple_real_events_are_displayed(self, reader):
        _make_entry("auth.login", "jdupont", status="success", timestamp=_dt(0))
        _make_entry("module.activate", "documentation", status="success", timestamp=_dt(1))

        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()

        assert "auth.login" in content
        assert "jdupont" in content
        assert "module.activate" in content
        assert "documentation" in content

    def test_five_columns_are_present(self, reader):
        _make_entry("auth.login", "jdupont", status="success", timestamp=_dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        for header in ("Date/heure", "Utilisateur", "Action", "Ressource", "Résultat"):
            assert header in content

    def test_real_actor_is_displayed_by_full_name(self, reader):
        actor = User.objects.create(username="jean_actor", full_name="Jean Acteur")
        _make_entry("user.update", "cible", actor=actor, timestamp=_dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "Jean Acteur" in content

    def test_none_actor_is_displayed_as_systeme(self, reader):
        _make_entry("backup.run", "/mnt/x", actor=None, status="success", timestamp=_dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "Système" in content


# --- C. Tri ---------------------------------------------------------------------


@pytest.mark.django_db
class TestOrdering:
    def test_entries_are_ordered_most_recent_first(self, reader):
        _make_entry("auth.login", "premier", status="success", timestamp=_dt(0))
        _make_entry("auth.login", "troisieme", status="success", timestamp=_dt(20))
        _make_entry("auth.login", "second", status="success", timestamp=_dt(10))

        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()

        pos_third = content.index("troisieme")
        pos_second = content.index("second")
        pos_first = content.index("premier")
        assert pos_third < pos_second < pos_first


# --- D. Résultat ------------------------------------------------------------------


@pytest.mark.django_db
class TestResultMapping:
    def test_success_maps_to_success_badge(self, reader):
        _make_entry("auth.login", "x", status="success", timestamp=_dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-badge--success" in content
        assert "Succès" in content

    def test_failure_maps_to_error_badge(self, reader):
        _make_entry("auth.login", "x", status="failure", timestamp=_dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-badge--error" in content
        assert "Échec" in content

    def test_denied_maps_to_warning_badge(self, reader):
        _make_entry("module.activate", "rh", status="denied", timestamp=_dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-badge--warning" in content
        assert "Refusé" in content

    def test_refused_maps_to_warning_badge(self, reader):
        _make_entry("backup.run", "destination-guard", status="refused", timestamp=_dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-badge--warning" in content
        assert "Refusé" in content

    def test_missing_status_defaults_to_success(self, reader):
        """Les 4 événements user.* n'écrivent aucun status — normalisé
        en Succès par défaut (règle de présentation, pas une donnée
        inventée en base)."""
        _make_entry("user.create", "nouveau_compte", timestamp=_dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-badge--success" in content
        assert "Succès" in content


# --- E. Filtrage ---------------------------------------------------------------


@pytest.mark.django_db
class TestFiltering:
    def test_filter_by_action(self, reader):
        _make_entry("auth.login", "a", status="success", timestamp=_dt(0))
        _make_entry("module.activate", "b", status="success", timestamp=_dt(1))

        client = _authenticated_client(reader)
        content = client.get(LIST_URL, {"action": "auth.login"}).content.decode()

        table_html = content.split("<table")[1]
        assert ">a<" in table_html or "<td>a</td>" in table_html
        assert "module.activate" not in table_html

    def test_filter_by_real_user(self, reader):
        actor_a = User.objects.create(username="actor_a", full_name="Acteur A")
        actor_b = User.objects.create(username="actor_b", full_name="Acteur B")
        _make_entry("user.update", "cible-a", actor=actor_a, timestamp=_dt(0))
        _make_entry("user.update", "cible-b", actor=actor_b, timestamp=_dt(1))

        client = _authenticated_client(reader)
        content = client.get(LIST_URL, {"actor": str(actor_a.id)}).content.decode()

        assert "cible-a" in content
        assert "cible-b" not in content

    def test_filter_by_system_actor(self, reader):
        real_user = User.objects.create(username="reel", full_name="Utilisateur Réel")
        _make_entry("backup.run", "auto", actor=None, status="success", timestamp=_dt(0))
        _make_entry("user.update", "manuel", actor=real_user, timestamp=_dt(1))

        client = _authenticated_client(reader)
        content = client.get(LIST_URL, {"actor": "system"}).content.decode()

        assert "auto" in content
        assert "manuel" not in content

    def test_filter_by_from_date(self, reader):
        _make_entry("auth.login", "ancien", status="success", timestamp=_dt(0))
        _make_entry(
            "auth.login", "recent", status="success",
            timestamp=datetime(2026, 3, 10, tzinfo=UTC),
        )

        client = _authenticated_client(reader)
        content = client.get(LIST_URL, {"from": "2026-03-05"}).content.decode()

        assert "recent" in content
        assert "ancien" not in content

    def test_filter_by_to_date(self, reader):
        _make_entry("auth.login", "ancien", status="success", timestamp=_dt(0))
        _make_entry(
            "auth.login", "recent", status="success",
            timestamp=datetime(2026, 3, 10, tzinfo=UTC),
        )

        client = _authenticated_client(reader)
        content = client.get(LIST_URL, {"to": "2026-03-05"}).content.decode()

        assert "ancien" in content
        assert "recent" not in content

    def test_combined_filters(self, reader):
        actor = User.objects.create(username="combine", full_name="Combine Test")
        _make_entry(
            "user.update", "match", actor=actor,
            timestamp=datetime(2026, 3, 5, tzinfo=UTC),
        )
        _make_entry(
            "auth.login", "wrong_action", actor=actor, status="success", timestamp=_dt(0)
        )
        _make_entry(
            "user.update", "wrong_actor",
            timestamp=datetime(2026, 3, 5, tzinfo=UTC),
        )

        client = _authenticated_client(reader)
        content = client.get(
            LIST_URL,
            {
                "action": "user.update",
                "actor": str(actor.id),
                "from": "2026-03-01",
                "to": "2026-03-10",
            },
        ).content.decode()

        assert "match" in content
        assert "wrong_action" not in content
        assert "wrong_actor" not in content

    def test_invalid_date_is_ignored_without_500(self, reader):
        _make_entry("auth.login", "x", status="success", timestamp=_dt(0))
        client = _authenticated_client(reader)
        response = client.get(LIST_URL, {"from": "not-a-date"})
        assert response.status_code == 200

    def test_invalid_actor_value_is_ignored_without_500(self, reader):
        _make_entry("auth.login", "x", status="success", timestamp=_dt(0))
        client = _authenticated_client(reader)
        response = client.get(LIST_URL, {"actor": "not-an-id"})
        assert response.status_code == 200

    def test_selected_filter_values_are_preserved_in_the_form(self, reader):
        actor = User.objects.create(username="preserve", full_name="Preserve Test")
        _make_entry("user.update", "x", actor=actor, timestamp=_dt(0))

        client = _authenticated_client(reader)
        content = client.get(
            LIST_URL, {"action": "user.update", "actor": str(actor.id)}
        ).content.decode()

        assert f'value="{actor.id}" selected' in content
        assert 'value="user.update" selected' in content


# --- F. État vide ---------------------------------------------------------------


@pytest.mark.django_db
class TestEmptyState:
    def test_no_events_at_all_renders_empty_state(self, reader):
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-empty-state" in content
        assert "corrux-table" not in content

    def test_no_results_after_filtering_renders_empty_state(self, reader):
        _make_entry("auth.login", "x", status="success", timestamp=_dt(0))
        client = _authenticated_client(reader)
        content = client.get(LIST_URL, {"action": "module.activate"}).content.decode()
        assert "corrux-empty-state" in content


# --- G. Lecture seule ----------------------------------------------------------


@pytest.mark.django_db
class TestReadOnly:
    def test_post_is_rejected(self, reader):
        client = _authenticated_client(reader)
        response = client.post(LIST_URL)
        assert response.status_code == 405

    def test_consulting_the_screen_creates_no_new_audit_log(self, reader):
        _make_entry("auth.login", "x", status="success", timestamp=_dt(0))
        before = AuditLog.objects.count()
        client = _authenticated_client(reader)
        client.get(LIST_URL)
        assert AuditLog.objects.count() == before

    def test_filtering_creates_no_mutation(self, reader):
        _make_entry("auth.login", "x", status="success", timestamp=_dt(0))
        before = list(AuditLog.objects.values_list("pk", "action", "target"))
        client = _authenticated_client(reader)
        client.get(LIST_URL, {"action": "auth.login"})
        after = list(AuditLog.objects.values_list("pk", "action", "target"))
        assert before == after


# --- H. Sécurité HTML --------------------------------------------------------------


@pytest.mark.django_db
class TestEscaping:
    def test_target_is_html_escaped(self, reader):
        _make_entry(
            "auth.login", "<script>alert(1)</script>", status="failure", timestamp=_dt(0)
        )
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "<script>alert(1)</script>" not in content
        assert "&lt;script&gt;" in content


# --- I. Filtre Action dynamique ------------------------------------------------


@pytest.mark.django_db
class TestActionFilterOptions:
    def test_options_come_from_real_database_actions_only(self, reader):
        _make_entry("auth.login", "x", status="success", timestamp=_dt(0))
        _make_entry("module.activate", "y", status="success", timestamp=_dt(1))

        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()

        filter_block = content.split('name="action"')[1].split("</select>")[0]
        assert "auth.login" in filter_block
        assert "module.activate" in filter_block
        # Aucune action inventée/non présente en base ne doit apparaître.
        assert "backup.run" not in filter_block
        assert "user.create" not in filter_block

    def test_no_static_action_list_is_hardcoded_in_views(self):
        from pathlib import Path

        source = Path("ui/views.py").read_text(encoding="utf-8")
        audit_section = source[source.index("UI-206") :]
        # La liste d'actions du filtre doit provenir d'une requête ORM,
        # jamais d'une liste Python codée en dur (ex. ["auth.login", ...]).
        assert '["auth.login"' not in audit_section
        assert "'auth.login'" not in audit_section
