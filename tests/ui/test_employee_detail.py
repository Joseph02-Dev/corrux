"""Tests de la Fiche employé — en-tête + onglet Informations — UI-402.

Rendu HTTP réel, permissions réelles.
"""

from datetime import date

import pytest
from django.test import Client

from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from modules.rh.services import create_employee, deactivate_employee


def _detail_url(employee_id: int) -> str:
    return f"/employes/{employee_id}/"


@pytest.fixture
def rh_activated(db):
    return Module.objects.create(
        id="rh", name="Ressources Humaines", version="1.0.0",
        state=Module.State.ACTIVATED, manifest_snapshot={},
    )


def _authenticated_client(user) -> Client:
    client = Client()
    session = client.session
    session[auth.SESSION_USER_ID_KEY] = user.id
    session.save()
    client.cookies["sessionid"] = session.session_key
    return client


def _grant(user, module_id, resource, action):
    role = Role.objects.create(name=f"role-{user.username}-{resource}-{action}")
    permission, _ = Permission.objects.get_or_create(
        module_id=module_id, resource=resource, action=action
    )
    RolePermission.objects.create(role=role, permission=permission)
    UserRole.objects.create(user=user, role=role)


@pytest.fixture
def reader(db):
    user = User.objects.create(username="lecteur_fiche", full_name="Lecteur")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "employee", "read")
    return user


@pytest.fixture
def writer(db):
    user = User.objects.create(username="rh_admin_fiche", full_name="Admin RH")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "rh", "employee", "read")
    _grant(user, "rh", "employee", "write")
    return user


@pytest.fixture
def employee(rh_activated):
    return create_employee(
        first_name="Jean", last_name="Dupont", email="jean.dupont@example.com",
        position="Développeur", hire_date=date(2026, 3, 15),
    )


# --- A. Accès ------------------------------------------------------------------


@pytest.mark.django_db
class TestAccess:
    def test_reader_sees_the_page(self, employee, reader):
        client = _authenticated_client(reader)
        assert client.get(_detail_url(employee.id)).status_code == 200

    def test_unauthorized_user_sees_permission_denied(self, employee, db):
        user = User.objects.create(username="sans_droit_fiche", full_name="Sans Droit")
        auth.set_user_password(user, "Password123!")
        user.save()
        client = _authenticated_client(user)
        response = client.get(_detail_url(employee.id))
        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()

    def test_anonymous_user_is_redirected_to_login(self, employee):
        client = Client()
        response = client.get(_detail_url(employee.id))
        assert response.status_code == 302

    def test_deactivated_rh_module_is_refused(self, db, reader):
        employee = create_employee(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1)
        )
        client = _authenticated_client(reader)
        response = client.get(_detail_url(employee.id))
        assert response.status_code == 403

    def test_nonexistent_employee_returns_404(self, rh_activated, reader):
        client = _authenticated_client(reader)
        response = client.get(_detail_url(999999))
        assert response.status_code == 404

    def test_post_is_rejected(self, employee, reader):
        client = _authenticated_client(reader)
        response = client.post(_detail_url(employee.id))
        assert response.status_code == 405


# --- B. En-tête ---------------------------------------------------------------


@pytest.mark.django_db
class TestHeader:
    def test_displays_full_name(self, employee, reader):
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()
        assert "Jean Dupont" in content

    def test_displays_position_as_subtitle(self, employee, reader):
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()
        assert "Développeur" in content

    def test_displays_avatar_initial(self, employee, reader):
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()
        assert "corrux-record-header__avatar" in content
        assert ">J<" in content

    def test_displays_active_status_badge(self, employee, reader):
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()
        assert "Actif" in content

    def test_displays_inactive_status_badge(self, employee, reader):
        deactivate_employee(employee=employee)
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()
        assert "Inactif" in content

    def test_modify_button_visible_with_write_permission(self, employee, writer):
        client = _authenticated_client(writer)
        content = client.get(_detail_url(employee.id)).content.decode()
        assert "Modifier" in content
        assert f"/employes/{employee.id}/modifier/" in content

    def test_modify_button_not_visible_for_reader_only(self, employee, reader):
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()
        assert f"/employes/{employee.id}/modifier/" not in content


# --- C. Onglets — navigation testée explicitement --------------------------------


@pytest.mark.django_db
class TestTabs:
    def test_informations_tab_is_active(self, employee, reader):
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()
        assert 'aria-selected="true"' in content

    def test_tabs_are_specific_to_the_viewed_employee(self, employee, reader, rh_activated):
        """Navigation entre onglets — cas explicitement requis par le
        contrat du ticket : les onglets pointent vers CET employé
        précis, pas un employé fixe/codé en dur (vérifié en comparant
        deux fiches différentes)."""
        other_employee = create_employee(
            first_name="Awa", last_name="Sow", position="RH", hire_date=date(2026, 1, 1)
        )
        client = _authenticated_client(reader)

        first_content = client.get(_detail_url(employee.id)).content.decode()
        second_content = client.get(_detail_url(other_employee.id)).content.decode()

        assert "Jean Dupont" in first_content
        assert "Jean Dupont" not in second_content
        assert "Awa Sow" in second_content

    def test_other_tabs_are_rendered_disabled(self, employee, reader):
        """UI-404/405 non construits — jamais un lien mort. Documents
        est désormais un lien réel (UI-403) : mise à jour nécessaire de
        ce test, pas une régression — même situation que les
        précédentes lorsqu'un onglet devient réellement fonctionnel."""
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()
        tabs_html = content.split('class="corrux-tabs"')[1].split("</div>")[0]
        assert tabs_html.count('aria-disabled="true"') == 2
        assert f"/employes/{employee.id}/documents/" in tabs_html
        for label in ("Documents", "Contrats", "Congés"):
            assert label in tabs_html


# --- D. Contenu de l'onglet Informations ------------------------------------------


@pytest.mark.django_db
class TestInformationsContent:
    def test_displays_all_fields_read_only(self, employee, reader):
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()

        assert "Jean" in content
        assert "Dupont" in content
        assert "jean.dupont@example.com" in content
        assert "Développeur" in content
        assert "15/03/2026" in content

    def test_missing_email_shows_placeholder(self, rh_activated, reader):
        employee = create_employee(
            first_name="X", last_name="Y", position="Z", hire_date=date(2026, 1, 1)
        )
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()
        assert "—" in content

    def test_no_form_fields_are_editable_on_this_page(self, employee, reader):
        """Onglet Informations en lecture seule (comportement attendu
        explicite du ticket) — aucun <input> dans la zone de contenu
        Informations elle-même (la Topbar, présente sur toute page,
        contient légitimement son propre champ de recherche désactivé,
        sans rapport avec cette assertion)."""
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()
        info_section = content.split('class="corrux-definition-list"')[1]
        assert "<input" not in info_section


# --- E. Sécurité --------------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_name_is_html_escaped(self, rh_activated, reader):
        employee = create_employee(
            first_name="<script>alert(1)</script>", last_name="Y", position="Z",
            hire_date=date(2026, 1, 1),
        )
        client = _authenticated_client(reader)
        content = client.get(_detail_url(employee.id)).content.decode()
        assert "<script>alert(1)</script>" not in content
