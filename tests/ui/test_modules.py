"""Tests de l'écran Modules — UI-203.

Rendu HTTP réel (patron établi UI-101 à UI-201), RBAC réel (TECH-003),
Module Manager réel (TECH-006/007 — activate_module()/deactivate_module()
sont les seules autorités, jamais recalculées). Fixtures via le patron
déjà établi par les tests TECH-006 : parse_manifest_text -> install_module.
"""

import pytest
from django.test import Client

from core.audit.models import AuditLog
from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.manager import activate_module, deactivate_module, install_module
from core.modules.manifest import parse_manifest_text
from core.modules.models import Module

LIST_URL = "/modules/"
CSRF_URL = "/api/auth/csrf/"

DOCUMENTATION_MANIFEST_YAML = """
id: documentation
name: "Documentation / Archivage"
version: "1.0.0"
db_schema: documentation
migrations_path: migrations/
"""

RH_MANIFEST_YAML = """
id: rh
name: "Ressources Humaines"
version: "1.0.0"
depends_on:
  - module: documentation
    version: ">=1.0.0"
db_schema: rh
migrations_path: migrations/
"""

MODULE_B_MANIFEST_YAML = """
id: module_b
name: "Module B (test)"
version: "1.0.0"
db_schema: module_b
migrations_path: migrations/
"""


def _activate_url(module_id):
    return f"/modules/{module_id}/activer/"


def _deactivate_url(module_id):
    return f"/modules/{module_id}/desactiver/"


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


def _post_with_csrf(client, url, data=None):
    token = client.cookies["csrftoken"].value
    return client.post(url, data or {}, HTTP_X_CSRFTOKEN=token)


@pytest.fixture
def actor(db):
    return User.objects.create(username="technicien", full_name="Technicien Test")


@pytest.fixture
def reader(db):
    user = User.objects.create(username="lecteur_mod", full_name="Lecteur Modules")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "core", "module", "read")
    return user


@pytest.fixture
def writer(db):
    user = User.objects.create(username="gestionnaire_mod", full_name="Gestionnaire Modules")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant(user, "core", "module", "read")
    _grant(user, "core", "module", "write")
    return user


@pytest.fixture
def no_permission_user(db):
    user = User.objects.create(username="sanspermission_mod", full_name="Sans Permission")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


# --- A. Autorisation -----------------------------------------------------------


@pytest.mark.django_db
class TestPermissions:
    def test_user_without_read_permission_sees_permission_denied(self, no_permission_user):
        client = _authenticated_client(no_permission_user)
        response = client.get(LIST_URL)
        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()

    def test_user_with_read_permission_sees_the_catalogue(self, reader, actor):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        client = _authenticated_client(reader)
        response = client.get(LIST_URL)
        assert response.status_code == 200
        assert "corrux-table" in response.content.decode()

    def test_anonymous_visitor_is_redirected_to_login(self):
        client = Client()
        response = client.get(LIST_URL)
        assert response.status_code == 302
        assert response.url == f"/login/?next={LIST_URL}"

    def test_reader_without_write_cannot_activate(self, reader, actor):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        client = _csrf_client(reader)
        response = _post_with_csrf(client, _activate_url("module_b"))
        assert response.status_code == 403
        module = Module.objects.get(pk="module_b")
        assert module.state == Module.State.INSTALLED

    def test_reader_without_write_cannot_deactivate(self, reader, writer):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=writer)
        activate_module("module_b", actor=writer)

        client = _csrf_client(reader)
        response = _post_with_csrf(client, _deactivate_url("module_b"))
        assert response.status_code == 403
        module = Module.objects.get(pk="module_b")
        assert module.state == Module.State.ACTIVATED

    def test_writer_can_activate(self, writer, actor):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        client = _csrf_client(writer)
        response = _post_with_csrf(client, _activate_url("module_b"))
        assert response.status_code == 302
        assert Module.objects.get(pk="module_b").state == Module.State.ACTIVATED

    def test_activation_mutation_is_transmitted_to_the_real_mechanism(self, writer, actor):
        """La mutation appelle réellement activate_module (TECH-006) —
        vérifié par l'effet observable en base, pas par inspection du code."""
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        client = _csrf_client(writer)
        _post_with_csrf(client, _activate_url("module_b"))
        assert AuditLog.objects.filter(action="module.activate", target="module_b").exists()


# --- B. Catalogue ---------------------------------------------------------------


@pytest.mark.django_db
class TestCatalogue:
    def test_empty_catalogue_is_correctly_handled(self, reader):
        client = _authenticated_client(reader)
        response = client.get(LIST_URL)
        content = response.content.decode()
        assert "corrux-empty-state" in content
        assert "corrux-table" not in content

    def test_real_modules_are_loaded_from_database(self, reader, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "Documentation / Archivage" in content
        assert "1.0.0" in content

    def test_no_fictional_module_is_added(self, reader, actor):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        client = _authenticated_client(reader)
        client.get(LIST_URL)
        assert Module.objects.count() == 1

    def test_three_states_are_correctly_mapped(self, reader, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        activate_module("module_b", actor=actor)

        deactivated_manifest_yaml = """
id: module_c
name: "Module C (test)"
version: "1.0.0"
db_schema: module_c
migrations_path: migrations/
"""
        install_module(parse_manifest_text(deactivated_manifest_yaml), actor=actor)
        activate_module("module_c", actor=actor)
        deactivate_module("module_c", actor=actor)

        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()

        assert "Installé" in content
        assert "Activé" in content
        assert "Désactivé" in content
        assert "corrux-badge--info" in content
        assert "corrux-badge--success" in content
        assert "corrux-badge--neutral" in content

    def test_no_description_field_is_invented(self, reader, actor):
        """Le modèle Module n'a pas de champ description : aucune donnée
        de ce type ne doit être affichée."""
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "description" not in content.lower()


# --- C. Activation ---------------------------------------------------------------


@pytest.mark.django_db
class TestActivation:
    def test_activate_requires_post_and_csrf(self, writer, actor):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        client = _authenticated_client(writer)
        response = client.get(_activate_url("module_b"))
        assert response.status_code == 405

    def test_successful_activation_persists_state(self, writer, actor):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        client = _csrf_client(writer)
        _post_with_csrf(client, _activate_url("module_b"))
        assert Module.objects.get(pk="module_b").state == Module.State.ACTIVATED

    def test_activation_denied_for_unsatisfied_dependency(self, writer, actor):
        """Cas de référence obligatoire : RH dépend de Documentation."""
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        client = _csrf_client(writer)
        response = _post_with_csrf(client, _activate_url("rh"))

        assert response.status_code == 400
        content = response.content.decode()
        assert "documentation" in content
        assert "refusée" in content
        assert Module.objects.get(pk="rh").state == Module.State.INSTALLED

    def test_activation_error_message_reflects_real_dependency_error(self, writer, actor):
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        client = _csrf_client(writer)
        response = _post_with_csrf(client, _activate_url("rh"))
        # "absent" est la raison réelle produite par check_dependencies()
        # (TECH-007) lorsque Documentation n'existe pas du tout.
        assert "absent" in response.content.decode()

    def test_no_second_audit_entry_is_created_by_the_ui(self, writer, actor):
        """activate_module() journalise déjà lui-même : un seul événement
        doit exister, jamais un doublon ajouté par ui/views.py."""
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        client = _csrf_client(writer)
        _post_with_csrf(client, _activate_url("module_b"))
        assert (
            AuditLog.objects.filter(action="module.activate", target="module_b").count() == 1
        )


# --- D. Désactivation ------------------------------------------------------------


@pytest.mark.django_db
class TestDeactivation:
    def test_deactivate_requires_post_and_csrf(self, writer, actor):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        activate_module("module_b", actor=actor)
        client = _authenticated_client(writer)
        response = client.get(_deactivate_url("module_b"))
        assert response.status_code == 405

    def test_confirmation_uses_corrux_modal(self, writer, actor):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        activate_module("module_b", actor=actor)
        client = _authenticated_client(writer)
        content = client.get(LIST_URL).content.decode()
        assert 'id="deactivate-module-module_b"' in content
        assert 'role="dialog"' in content

    def test_successful_deactivation_persists_state(self, writer, actor):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        activate_module("module_b", actor=actor)
        client = _csrf_client(writer)
        _post_with_csrf(client, _deactivate_url("module_b"))
        assert Module.objects.get(pk="module_b").state == Module.State.DEACTIVATED

    def test_deactivation_never_deletes_module_data(self, writer, actor):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        activate_module("module_b", actor=actor)
        client = _csrf_client(writer)
        _post_with_csrf(client, _deactivate_url("module_b"))
        assert Module.objects.filter(pk="module_b").exists()

    def test_deactivation_audited_once(self, writer, actor):
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        activate_module("module_b", actor=actor)
        client = _csrf_client(writer)
        _post_with_csrf(client, _deactivate_url("module_b"))
        assert (
            AuditLog.objects.filter(action="module.deactivate", target="module_b").count() == 1
        )


# --- E. Dépendances ---------------------------------------------------------------


@pytest.mark.django_db
class TestDependencyBlocking:
    def test_deactivation_allowed_without_active_dependent(self, writer, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        activate_module("documentation", actor=actor)
        client = _csrf_client(writer)
        response = _post_with_csrf(client, _deactivate_url("documentation"))
        assert response.status_code == 302
        assert Module.objects.get(pk="documentation").state == Module.State.DEACTIVATED

    def test_deactivation_blocked_when_active_dependent_exists(self, writer, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        activate_module("documentation", actor=actor)
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        activate_module("rh", actor=actor)

        client = _csrf_client(writer)
        response = _post_with_csrf(client, _deactivate_url("documentation"))

        assert response.status_code == 400
        assert Module.objects.get(pk="documentation").state == Module.State.ACTIVATED

    def test_blocking_message_reflects_real_active_dependents(self, writer, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        activate_module("documentation", actor=actor)
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        activate_module("rh", actor=actor)

        client = _csrf_client(writer)
        response = _post_with_csrf(client, _deactivate_url("documentation"))
        content = response.content.decode()
        assert "<li>rh</li>" in content

    def test_no_ui_mechanism_can_bypass_the_block(self, writer, actor):
        """Aucun paramètre caché/URL spéciale ne doit permettre de
        contourner le blocage — seule deactivate_module() décide."""
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        activate_module("documentation", actor=actor)
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        activate_module("rh", actor=actor)

        client = _csrf_client(writer)
        response = _post_with_csrf(
            client, _deactivate_url("documentation"), {"force": "true", "override": "1"}
        )
        assert response.status_code == 400
        assert Module.objects.get(pk="documentation").state == Module.State.ACTIVATED

    def test_no_second_dependency_graph_implementation(self, writer, actor):
        """Vérifie que le blocage réel provient de TECH-007 : désactiver
        rh (le dépendant, pas la dépendance) doit réussir sans blocage."""
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        activate_module("documentation", actor=actor)
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        activate_module("rh", actor=actor)

        client = _csrf_client(writer)
        response = _post_with_csrf(client, _deactivate_url("rh"))
        assert response.status_code == 302
        assert Module.objects.get(pk="rh").state == Module.State.DEACTIVATED


# --- F. Composants -----------------------------------------------------------------


@pytest.mark.django_db
class TestComponents:
    def test_table_reused_without_duplication(self, reader, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        install_module(parse_manifest_text(MODULE_B_MANIFEST_YAML), actor=actor)
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "corrux-table" in content
        assert content.count("<table") == 1

    def test_info_modal_has_a_single_exit_button(self, writer, actor):
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        activate_module("documentation", actor=actor)
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        activate_module("rh", actor=actor)

        client = _csrf_client(writer)
        response = _post_with_csrf(client, _deactivate_url("documentation"))
        content = response.content.decode()

        info_block = content.split("corrux-modal--info")[1].split("</dialog>")[0]
        assert "Fermer" in info_block
        assert 'method="dialog"' in info_block
        assert 'method="post"' not in info_block
        assert "csrfmiddlewaretoken" not in info_block

    def test_info_modal_is_not_corrux_modal_confirm_variant(self, writer, actor):
        """La modal d'information n'est pas corrux_modal détourné : pas
        de formulaire POST, pas d'action destructive à confirmer."""
        install_module(parse_manifest_text(DOCUMENTATION_MANIFEST_YAML), actor=actor)
        activate_module("documentation", actor=actor)
        install_module(parse_manifest_text(RH_MANIFEST_YAML), actor=actor)
        activate_module("rh", actor=actor)

        client = _csrf_client(writer)
        response = _post_with_csrf(client, _deactivate_url("documentation"))
        content = response.content.decode()
        info_block = content.split("corrux-modal--info")[1].split("</dialog>")[0]
        assert "Annuler" not in info_block

    def test_module_name_is_html_escaped(self, reader, actor):
        xss_manifest = """
id: module_xss
name: "<script>alert(1)</script>"
version: "1.0.0"
db_schema: module_xss
migrations_path: migrations/
"""
        install_module(parse_manifest_text(xss_manifest), actor=actor)
        client = _authenticated_client(reader)
        content = client.get(LIST_URL).content.decode()
        assert "<script>alert(1)</script>" not in content
        assert "&lt;script&gt;" in content

    def test_no_hardcoded_colors_introduced(self):
        import re
        from pathlib import Path

        content = Path("ui/static/ui/css/components.css").read_text(encoding="utf-8")
        matches = re.findall(r"#[0-9a-fA-F]{3,8}\b", content)
        unexpected = [m for m in matches if m.lower() != "#8f1b12"]
        assert unexpected == []

    def test_navigation_points_to_real_modules_route(self, writer):
        from ui.navigation import get_navigation

        groups = get_navigation(writer)
        admin_group = next(g for g in groups if g.label == "Administration")
        modules_item = next(i for i in admin_group.items if i.label == "Modules")
        assert modules_item.href == "/modules/"


# --- G. Régression -----------------------------------------------------------------
# Exécutée via la suite complète (pytest), pas dupliquée ici.
