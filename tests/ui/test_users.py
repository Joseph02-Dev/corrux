"""Tests de l'écran Utilisateurs & rôles — UI-201.

Rendu HTTP réel (patron établi UI-101 à UI-204), RBAC réel (TECH-003,
aucune permission simulée), audit réel (TECH-008). Couvre les sections
A à H du mandat UI-201.
"""

import pytest
from django.test import Client

from core.audit.models import AuditLog
from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User

LIST_URL = "/utilisateurs/"
CREATE_URL = "/utilisateurs/nouveau/"
CSRF_URL = "/api/auth/csrf/"


def _edit_url(user_id):
    return f"/utilisateurs/{user_id}/modifier/"


def _reset_url(user_id):
    return f"/utilisateurs/{user_id}/reinitialiser-mot-de-passe/"


def _deactivate_url(user_id):
    return f"/utilisateurs/{user_id}/desactiver/"


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


def _csrf_client(user=None):
    client = Client(enforce_csrf_checks=True)
    client.get(CSRF_URL)
    if user is not None:
        session = client.session
        session[auth.SESSION_USER_ID_KEY] = user.id
        session.save()
        client.cookies["sessionid"] = session.session_key
    return client


def _post_with_csrf(client, url, data):
    token = client.cookies["csrftoken"].value
    return client.post(url, data, HTTP_X_CSRFTOKEN=token)


@pytest.fixture
def reader(db):
    user = User.objects.create(username="lecteur", full_name="Lecteur Test")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "core", "user", "read")
    return user


@pytest.fixture
def writer(db):
    user = User.objects.create(username="gestionnaire", full_name="Gestionnaire Test")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "core", "user", "read")
    _grant(user, "core", "user", "write")
    return user


@pytest.fixture
def no_permission_user(db):
    user = User.objects.create(username="sanspermission", full_name="Sans Permission")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


@pytest.fixture
def role_employe(db):
    return Role.objects.get(name="Employé")


# --- A. Permissions ----------------------------------------------------------


@pytest.mark.django_db
class TestPermissions:
    def test_user_without_read_permission_sees_permission_denied(self, no_permission_user):
        client = _authenticated_client(no_permission_user)
        response = client.get(LIST_URL)

        assert response.status_code == 403
        content = response.content.decode()
        assert "corrux-permission-denied" in content
        assert "corrux-table" not in content

    def test_user_with_read_permission_sees_the_list(self, reader):
        client = _authenticated_client(reader)
        response = client.get(LIST_URL)
        assert response.status_code == 200
        assert "corrux-table" in response.content.decode()

    def test_anonymous_visitor_is_redirected_to_login(self):
        client = Client()
        response = client.get(LIST_URL)
        assert response.status_code == 302
        assert response.url == f"/login/?next={LIST_URL}"

    def test_reader_without_write_cannot_create(self, reader, role_employe):
        client = _csrf_client(reader)
        response = _post_with_csrf(
            client, CREATE_URL,
            {"full_name": "X", "username": "xnew", "role": str(role_employe.id)},
        )
        assert response.status_code == 403
        assert not User.objects.filter(username="xnew").exists()

    def test_reader_without_write_cannot_deactivate(self, reader, writer):
        client = _csrf_client(reader)
        response = _post_with_csrf(client, _deactivate_url(writer.id), {})
        assert response.status_code == 403
        writer.refresh_from_db()
        assert writer.status == User.Status.ACTIVE

    def test_writer_can_create(self, writer, role_employe):
        client = _csrf_client(writer)
        response = _post_with_csrf(
            client, CREATE_URL,
            {"full_name": "Nouveau", "username": "nouveau1", "role": str(role_employe.id)},
        )
        assert response.status_code == 200
        assert User.objects.filter(username="nouveau1").exists()

    def test_actions_are_hidden_from_read_only_user_in_rendered_html(self, reader):
        """La présence/absence d'actions dans l'UI n'est qu'une aide —
        vérifiée ici comme confort d'affichage, pas comme protection."""
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "create-user" not in content
        assert "Nouvel utilisateur" not in content


# --- B. Liste réelle -----------------------------------------------------------


@pytest.mark.django_db
class TestRealList:
    def test_users_are_fetched_from_the_real_database(self, writer, role_employe):
        third = User.objects.create(username="troisieme", full_name="Troisième Personne")
        UserRole.objects.create(user=third, role=role_employe)

        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()

        assert "Troisième Personne" in content
        assert "troisieme" in content

    def test_role_is_correctly_displayed(self, writer, role_employe):
        target = User.objects.create(username="avecrole", full_name="Avec Rôle")
        UserRole.objects.create(user=target, role=role_employe)

        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()
        assert "Employé" in content

    def test_status_is_correctly_displayed(self, writer):
        User.objects.create(
            username="inactif_test", full_name="Inactif Test", status=User.Status.INACTIVE
        )
        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()
        assert "Inactif" in content

    def test_empty_search_result_renders_empty_state(self, writer):
        client = _authenticated_client(writer)
        response = client.get(LIST_URL + "?q=zzzzzznotfound")
        content = response.content.decode()
        assert "corrux-empty-state" in content
        assert "corrux-table" not in content

    def test_search_filters_by_name_or_username(self, writer):
        User.objects.create(username="findme", full_name="Trouvable Personne")
        User.objects.create(username="other", full_name="Autre Personne")

        client = _authenticated_client(writer)
        content = client.get(LIST_URL + "?q=findme").content.decode()

        assert "Trouvable Personne" in content
        assert "Autre Personne" not in content


# --- C. Création ---------------------------------------------------------------


@pytest.mark.django_db
class TestCreate:
    def test_create_form_is_post_with_csrf(self, writer):
        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()
        assert 'action="/utilisateurs/nouveau/"' in content
        assert "csrfmiddlewaretoken" in content

    def test_creating_a_user_persists_it_in_database(self, writer, role_employe):
        client = _csrf_client(writer)
        _post_with_csrf(
            client, CREATE_URL,
            {"full_name": "Jean Test", "username": "jean_test", "role": str(role_employe.id)},
        )
        user = User.objects.get(username="jean_test")
        assert user.full_name == "Jean Test"

    def test_role_is_correctly_associated(self, writer, role_employe):
        client = _csrf_client(writer)
        _post_with_csrf(
            client, CREATE_URL,
            {"full_name": "Rôle Test", "username": "role_test", "role": str(role_employe.id)},
        )
        user = User.objects.get(username="role_test")
        assert user.user_roles.get().role == role_employe

    def test_password_is_stored_only_as_a_hash(self, writer, role_employe):
        client = _csrf_client(writer)
        response = _post_with_csrf(
            client, CREATE_URL,
            {"full_name": "Hash Test", "username": "hash_test", "role": str(role_employe.id)},
        )
        user = User.objects.get(username="hash_test")
        html = response.content.decode()

        temp_password = html.split('corrux-secret-reveal__value">')[1].split("</code>")[0]
        assert user.password_hash != temp_password
        assert user.password_hash.startswith("argon2$argon2id$")

    def test_temporary_password_is_absent_from_the_user_model_in_clear(
        self, writer, role_employe
    ):
        client = _csrf_client(writer)
        response = _post_with_csrf(
            client, CREATE_URL,
            {"full_name": "Clair Test", "username": "clair_test", "role": str(role_employe.id)},
        )
        html = response.content.decode()
        temp_password = html.split('corrux-secret-reveal__value">')[1].split("</code>")[0]

        user = User.objects.get(username="clair_test")
        assert temp_password not in user.password_hash

    def test_created_account_can_really_log_in(self, writer, role_employe):
        """Critère d'acceptation explicite : un compte créé peut se
        connecter — testé par un vrai flux d'authentification, pas
        seulement par inspection du hash."""
        client = _csrf_client(writer)
        response = _post_with_csrf(
            client, CREATE_URL,
            {"full_name": "Login Test", "username": "login_test", "role": str(role_employe.id)},
        )
        html = response.content.decode()
        temp_password = html.split('corrux-secret-reveal__value">')[1].split("</code>")[0]

        authenticated_user = auth.authenticate(username="login_test", raw_password=temp_password)
        assert authenticated_user.username == "login_test"

    def test_creation_is_audited_without_the_secret(self, writer, role_employe):
        client = _csrf_client(writer)
        response = _post_with_csrf(
            client, CREATE_URL,
            {"full_name": "Audit Test", "username": "audit_test", "role": str(role_employe.id)},
        )
        html = response.content.decode()
        temp_password = html.split('corrux-secret-reveal__value">')[1].split("</code>")[0]

        entry = AuditLog.objects.get(action="user.create", target="audit_test")
        assert entry.actor_user == writer
        assert temp_password not in str(entry.metadata)

    def test_secret_never_appears_in_a_later_response(self, writer, role_employe):
        client = _csrf_client(writer)
        response = _post_with_csrf(
            client, CREATE_URL,
            {"full_name": "Secret Test", "username": "secret_test", "role": str(role_employe.id)},
        )
        html = response.content.decode()
        temp_password = html.split('corrux-secret-reveal__value">')[1].split("</code>")[0]

        later_client = _authenticated_client(writer)
        later_content = later_client.get(LIST_URL).content.decode()
        assert temp_password not in later_content

    def test_duplicate_username_is_rejected(self, writer, role_employe):
        client = _csrf_client(writer)
        _post_with_csrf(
            client, CREATE_URL,
            {"full_name": "Premier", "username": "doublon", "role": str(role_employe.id)},
        )
        response = _post_with_csrf(
            client, CREATE_URL,
            {"full_name": "Second", "username": "doublon", "role": str(role_employe.id)},
        )
        assert response.status_code == 400
        assert "déjà utilisé" in response.content.decode()
        assert User.objects.filter(username="doublon").count() == 1

    def test_missing_required_fields_are_rejected_without_crashing(self, writer):
        client = _csrf_client(writer)
        response = _post_with_csrf(
            client, CREATE_URL, {"full_name": "", "username": "", "role": ""}
        )
        assert response.status_code == 400
        assert "requis" in response.content.decode()


# --- D. Édition ------------------------------------------------------------------


@pytest.mark.django_db
class TestEdit:
    def test_edit_form_is_prefilled_with_real_values(self, writer, role_employe):
        target = User.objects.create(username="aeditertest", full_name="À Éditer")
        UserRole.objects.create(user=target, role=role_employe)

        client = _authenticated_client(writer)
        content = client.get(_edit_url(target.id)).content.decode()

        assert 'value="À Éditer"' in content
        assert 'value="aeditertest"' in content

    def test_edit_persists_changes(self, writer, role_employe):
        target = User.objects.create(username="modifiable", full_name="Ancien Nom")
        client = _csrf_client(writer)
        _post_with_csrf(
            client, _edit_url(target.id),
            {"full_name": "Nouveau Nom", "username": "modifiable", "role": str(role_employe.id)},
        )
        target.refresh_from_db()
        assert target.full_name == "Nouveau Nom"

    def test_edit_updates_role(self, writer, role_employe):
        target = User.objects.create(username="rolechange", full_name="X")
        autre_role = Role.objects.get(name="Valideur")
        UserRole.objects.create(user=target, role=role_employe)

        client = _csrf_client(writer)
        _post_with_csrf(
            client, _edit_url(target.id),
            {"full_name": "X", "username": "rolechange", "role": str(autre_role.id)},
        )
        assert target.user_roles.get().role == autre_role

    def test_edit_is_audited(self, writer, role_employe):
        target = User.objects.create(username="auditedit", full_name="X")
        client = _csrf_client(writer)
        _post_with_csrf(
            client, _edit_url(target.id),
            {"full_name": "Y", "username": "auditedit", "role": str(role_employe.id)},
        )
        assert AuditLog.objects.filter(action="user.update", target="auditedit").exists()

    def test_no_password_field_in_edit_drawer(self, writer, role_employe):
        target = User.objects.create(username="nopassword", full_name="X")
        UserRole.objects.create(user=target, role=role_employe)
        client = _authenticated_client(writer)
        content = client.get(_edit_url(target.id)).content.decode()

        edit_block = content.split(f'id="edit-user-{target.id}"')[1].split("</dialog>")[0]
        assert 'type="password"' not in edit_block


# --- E. Réinitialisation -------------------------------------------------------


@pytest.mark.django_db
class TestPasswordReset:
    def test_reset_requires_post_and_csrf(self, writer):
        target = User.objects.create(username="resettest", full_name="X")
        auth.set_user_password(target, "AncienMotDePasse1!")
        target.save()

        client = _authenticated_client(writer)
        response = client.get(_reset_url(target.id))
        assert response.status_code == 405

    def test_new_password_is_really_functional(self, writer):
        target = User.objects.create(username="resetfonctionnel", full_name="X")
        auth.set_user_password(target, "AncienMotDePasse1!")
        target.save()

        client = _csrf_client(writer)
        response = _post_with_csrf(client, _reset_url(target.id), {})
        html = response.content.decode()
        new_password = html.split('corrux-secret-reveal__value">')[1].split("</code>")[0]

        authenticated = auth.authenticate(
            username="resetfonctionnel", raw_password=new_password
        )
        assert authenticated.username == "resetfonctionnel"

    def test_old_password_is_invalidated(self, writer):
        target = User.objects.create(username="ancienrejete", full_name="X")
        auth.set_user_password(target, "AncienMotDePasse1!")
        target.save()

        client = _csrf_client(writer)
        _post_with_csrf(client, _reset_url(target.id), {})

        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username="ancienrejete", raw_password="AncienMotDePasse1!")

    def test_secret_absent_from_audit(self, writer):
        target = User.objects.create(username="resetaudit", full_name="X")
        client = _csrf_client(writer)
        response = _post_with_csrf(client, _reset_url(target.id), {})
        html = response.content.decode()
        new_password = html.split('corrux-secret-reveal__value">')[1].split("</code>")[0]

        entry = AuditLog.objects.get(action="user.password_reset", target="resetaudit")
        assert new_password not in str(entry.metadata)

    def test_reset_is_audited(self, writer):
        target = User.objects.create(username="resetaudit2", full_name="X")
        client = _csrf_client(writer)
        _post_with_csrf(client, _reset_url(target.id), {})
        assert AuditLog.objects.filter(
            action="user.password_reset", target="resetaudit2", actor_user=writer
        ).exists()


# --- F. Désactivation ----------------------------------------------------------


@pytest.mark.django_db
class TestDeactivate:
    def test_deactivate_requires_post_and_csrf(self, writer):
        target = User.objects.create(username="desacttest", full_name="X")
        client = _authenticated_client(writer)
        response = client.get(_deactivate_url(target.id))
        assert response.status_code == 405

    def test_confirmation_uses_corrux_modal(self, writer):
        target = User.objects.create(username="desactconfirm", full_name="X")
        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()

        assert f'id="deactivate-user-{target.id}"' in content
        assert 'role="dialog"' in content

    def test_deactivation_sets_status_to_inactive(self, writer):
        target = User.objects.create(username="devientinactif", full_name="X")
        client = _csrf_client(writer)
        _post_with_csrf(client, _deactivate_url(target.id), {})
        target.refresh_from_db()
        assert target.status == User.Status.INACTIVE

    def test_deactivation_never_deletes_the_user(self, writer):
        target = User.objects.create(username="jamaissupprime", full_name="X")
        client = _csrf_client(writer)
        _post_with_csrf(client, _deactivate_url(target.id), {})
        assert User.objects.filter(pk=target.id).exists()

    def test_deactivation_preserves_roles_and_associated_data(self, writer, role_employe):
        target = User.objects.create(username="rolespreserves", full_name="X")
        UserRole.objects.create(user=target, role=role_employe)

        client = _csrf_client(writer)
        _post_with_csrf(client, _deactivate_url(target.id), {})

        assert UserRole.objects.filter(user=target, role=role_employe).exists()

    def test_deactivation_is_audited(self, writer):
        target = User.objects.create(username="desactaudit", full_name="X")
        client = _csrf_client(writer)
        _post_with_csrf(client, _deactivate_url(target.id), {})
        assert AuditLog.objects.filter(
            action="user.deactivate", target="desactaudit", actor_user=writer
        ).exists()

    def test_deactivated_user_cannot_authenticate(self, writer):
        target = User.objects.create(username="plusdeconnexion", full_name="X")
        auth.set_user_password(target, "MotDePasse123!")
        target.save()

        client = _csrf_client(writer)
        _post_with_csrf(client, _deactivate_url(target.id), {})

        with pytest.raises(auth.AuthenticationError):
            auth.authenticate(username="plusdeconnexion", raw_password="MotDePasse123!")


# --- G. Composants -----------------------------------------------------------


@pytest.mark.django_db
class TestComponents:
    def test_table_renders_with_two_different_datasets(self, writer):
        User.objects.create(username="tbla", full_name="Table Un")
        User.objects.create(username="tblb", full_name="Table Deux")
        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()
        assert "Table Un" in content
        assert "Table Deux" in content

    def test_no_duplicate_template_for_create_and_edit_drawer(self, writer):
        target = User.objects.create(username="drawertest", full_name="X")
        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()
        assert content.count("corrux-drawer") >= 2
        assert 'id="create-user"' in content
        assert f'id="edit-user-{target.id}"' in content

    def test_select_field_used_for_role(self, writer):
        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()
        assert "<select" in content

    def test_tabs_present_with_roles_disabled(self, writer):
        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()
        assert 'role="tab"' in content
        assert "Rôles" in content
        assert 'aria-disabled="true"' in content

    def test_user_data_is_html_escaped_in_table(self, writer):
        User.objects.create(
            username="xsstest", full_name="<script>alert(1)</script>"
        )
        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()
        assert "<script>alert(1)</script>" not in content
        assert "&lt;script&gt;" in content

    def test_no_hardcoded_colors_in_components_css(self):
        import re
        from pathlib import Path

        content = Path("ui/static/ui/css/components.css").read_text(encoding="utf-8")
        matches = re.findall(r"#[0-9a-fA-F]{3,8}\b", content)
        unexpected = [m for m in matches if m.lower() != "#8f1b12"]
        assert unexpected == []


# --- H. Régression -------------------------------------------------------------
# Exécutée via la suite complète (pytest), pas dupliquée ici.
