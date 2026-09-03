"""Tests des permissions document/dossier (Modal) — UI-304.

Composant générique réutilisant intégralement grant_permission()/
revoke_permission() (TECH-023) — aucune nouvelle logique de permission,
uniquement une façade UI. Rendu HTTP réel, permissions réelles.
"""

import pytest
from django.test import Client, override_settings

from core.authz.models import Role, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from modules.documentation.models import DocumentPermission
from modules.documentation.services import create_folder, grant_permission, upload_document

READ = "read"
WRITE = "write"


def _document_permissions_url(document_id: int) -> str:
    return f"/documents/{document_id}/permissions/"


def _document_toggle_url(document_id: int) -> str:
    return f"/documents/{document_id}/permissions/basculer/"


def _document_remove_url(document_id: int) -> str:
    return f"/documents/{document_id}/permissions/retirer/"


def _document_add_url(document_id: int) -> str:
    return f"/documents/{document_id}/permissions/ajouter/"


def _folder_permissions_url(folder_id: int) -> str:
    return f"/documents/dossier/{folder_id}/permissions/"


def _folder_toggle_url(folder_id: int) -> str:
    return f"/documents/dossier/{folder_id}/permissions/basculer/"


@pytest.fixture
def storage_root(tmp_path):
    with override_settings(CORRUX_STORAGE_ROOT=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def documentation_activated(db):
    return Module.objects.create(
        id="documentation",
        name="Documentation / Archivage",
        version="1.0.0",
        state=Module.State.ACTIVATED,
        manifest_snapshot={},
    )


def _authenticated_client(user) -> Client:
    client = Client()
    session = client.session
    session[auth.SESSION_USER_ID_KEY] = user.id
    session.save()
    client.cookies["sessionid"] = session.session_key
    return client


@pytest.fixture
def owner(db):
    user = User.objects.create(username="proprietaire_perm304", full_name="Propriétaire")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


@pytest.fixture
def manager(db, owner):
    """Utilisateur avec write explicite sur le document/dossier —
    autorisé à gérer les permissions."""
    user = User.objects.create(username="gestionnaire", full_name="Gestionnaire")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


@pytest.fixture
def target_user(db):
    user = User.objects.create(username="cible_exceptionnelle", full_name="Awa Sow")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


@pytest.fixture
def document(documentation_activated, storage_root, owner, manager):
    doc = upload_document(content=b"x", filename="confidentiel.pdf", owner_user=owner)
    grant_permission(actor=owner, action=READ, document=doc, user=manager)
    grant_permission(actor=owner, action=WRITE, document=doc, user=manager)
    return doc


@pytest.fixture
def folder(owner, manager):
    f = create_folder(name="Dossier Confidentiel")
    grant_permission(actor=owner, action=READ, folder=f, user=manager)
    grant_permission(actor=owner, action=WRITE, folder=f, user=manager)
    return f


# --- A. Accès (document) --------------------------------------------------------


@pytest.mark.django_db
class TestDocumentAccess:
    def test_manager_can_view_the_modal(self, document, manager):
        client = _authenticated_client(manager)
        response = client.get(_document_permissions_url(document.id))
        assert response.status_code == 200

    def test_user_without_write_sees_permission_denied(self, document, target_user):
        client = _authenticated_client(target_user)
        response = client.get(_document_permissions_url(document.id))
        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()

    def test_anonymous_user_is_redirected_to_login(self, document):
        client = Client()
        response = client.get(_document_permissions_url(document.id))
        assert response.status_code == 302


# --- B. Contenu de la liste (document) ------------------------------------------


@pytest.mark.django_db
class TestDocumentListContent:
    def test_owner_always_shown_even_without_explicit_permission(
        self, document, manager, owner
    ):
        client = _authenticated_client(manager)
        content = client.get(_document_permissions_url(document.id)).content.decode()
        assert "Propriétaire" in content
        assert owner.full_name in content

    def test_explicit_grantee_shown_as_individual_user(self, document, manager):
        client = _authenticated_client(manager)
        content = client.get(_document_permissions_url(document.id)).content.decode()
        assert "Utilisateur individuel" in content
        assert manager.full_name in content

    def test_role_grantee_shown_with_role_label(self, document, manager, owner):
        role = Role.objects.create(name="role-perm304-test")
        grant_permission(actor=owner, action=READ, document=document, role=role)

        client = _authenticated_client(manager)
        content = client.get(_document_permissions_url(document.id)).content.decode()
        assert role.name in content
        assert "Rôle" in content

    def test_toggle_state_reflects_real_permissions(self, document, manager):
        client = _authenticated_client(manager)
        content = client.get(_document_permissions_url(document.id)).content.decode()
        # manager a read ET write -> deux cellules "on"
        assert content.count("corrux-permission-cell__toggle--on") >= 2


# --- C. Bascule (document) — critère d'acceptation explicite -------------------


@pytest.mark.django_db
class TestDocumentToggle:
    def test_toggle_grants_a_new_permission(self, document, manager, target_user):
        client = _authenticated_client(manager)
        client.post(
            _document_toggle_url(document.id),
            {"user_id": target_user.id, "action": "read"},
        )
        assert DocumentPermission.objects.filter(
            document=document, user=target_user, action="read"
        ).exists()

    def test_toggle_effect_is_immediate_on_has_document_permission(
        self, document, manager, target_user
    ):
        """Critère d'acceptation explicite : « modification reflétée
        immédiatement par TECH-023 »."""
        from modules.documentation.services import has_document_permission

        assert has_document_permission(target_user, document, "read") is False

        client = _authenticated_client(manager)
        client.post(
            _document_toggle_url(document.id),
            {"user_id": target_user.id, "action": "read"},
        )

        assert has_document_permission(target_user, document, "read") is True

    def test_toggle_off_revokes_the_permission(self, document, manager, target_user):
        client = _authenticated_client(manager)
        client.post(
            _document_toggle_url(document.id),
            {"user_id": target_user.id, "action": "read"},
        )
        assert DocumentPermission.objects.filter(
            document=document, user=target_user, action="read"
        ).exists()

        client.post(
            _document_toggle_url(document.id),
            {"user_id": target_user.id, "action": "read"},
        )
        assert not DocumentPermission.objects.filter(
            document=document, user=target_user, action="read"
        ).exists()

    def test_toggle_role_permission(self, document, manager, owner):
        role = Role.objects.create(name="role-perm304-toggle")
        target_role_user = User.objects.create(username="via_role_304", full_name="Via Rôle")
        UserRole.objects.create(user=target_role_user, role=role)

        client = _authenticated_client(manager)
        client.post(
            _document_toggle_url(document.id), {"role_id": role.id, "action": "read"}
        )

        from modules.documentation.services import has_document_permission

        assert has_document_permission(target_role_user, document, "read") is True

    def test_toggle_without_write_is_refused(self, document, target_user):
        client = _authenticated_client(target_user)
        response = client.post(
            _document_toggle_url(document.id),
            {"user_id": target_user.id, "action": "read"},
        )
        assert response.status_code == 403
        assert not DocumentPermission.objects.filter(
            document=document, user=target_user
        ).exists()


# --- D. Ajout — test explicitement requis par le contrat -------------------------


@pytest.mark.django_db
class TestDocumentAdd:
    def test_add_an_exceptional_individual_access(self, document, manager, target_user):
        """Test explicitement requis par le contrat du ticket : « ajout
        d'un accès individuel exceptionnel »."""
        client = _authenticated_client(manager)
        response = client.post(
            _document_add_url(document.id), {"grantee": f"user:{target_user.id}"}
        )

        assert response.status_code == 302
        assert DocumentPermission.objects.filter(
            document=document, user=target_user, action="read"
        ).exists()

    def test_added_access_appears_in_the_list(self, document, manager, target_user):
        client = _authenticated_client(manager)
        client.post(_document_add_url(document.id), {"grantee": f"user:{target_user.id}"})

        content = client.get(_document_permissions_url(document.id)).content.decode()
        assert target_user.full_name in content

    def test_add_a_role_access(self, document, manager, owner):
        role = Role.objects.create(name="role-perm304-add")
        client = _authenticated_client(manager)
        client.post(_document_add_url(document.id), {"grantee": f"role:{role.id}"})

        assert DocumentPermission.objects.filter(
            document=document, role=role, action="read"
        ).exists()

    def test_add_without_write_is_refused(self, document, target_user):
        client = _authenticated_client(target_user)
        response = client.post(
            _document_add_url(document.id), {"grantee": f"user:{target_user.id}"}
        )
        assert response.status_code == 403

    def test_add_with_invalid_grantee_has_no_effect(self, document, manager):
        client = _authenticated_client(manager)
        before = DocumentPermission.objects.count()
        client.post(_document_add_url(document.id), {"grantee": "user:999999"})
        assert DocumentPermission.objects.count() == before


# --- E. Retrait (document) --------------------------------------------------------


@pytest.mark.django_db
class TestDocumentRemove:
    def test_remove_revokes_both_read_and_write(self, document, manager, target_user, owner):
        grant_permission(actor=owner, action=READ, document=document, user=target_user)
        grant_permission(actor=owner, action=WRITE, document=document, user=target_user)

        client = _authenticated_client(manager)
        client.post(_document_remove_url(document.id), {"user_id": target_user.id})

        assert not DocumentPermission.objects.filter(
            document=document, user=target_user
        ).exists()

    def test_remove_role_access(self, document, manager, owner):
        role = Role.objects.create(name="role-perm304-remove")
        grant_permission(actor=owner, action=READ, document=document, role=role)

        client = _authenticated_client(manager)
        client.post(_document_remove_url(document.id), {"role_id": role.id})

        assert not DocumentPermission.objects.filter(document=document, role=role).exists()

    def test_remove_does_not_affect_other_grantees(
        self, document, manager, target_user, owner
    ):
        grant_permission(actor=owner, action=READ, document=document, user=target_user)

        client = _authenticated_client(manager)
        client.post(_document_remove_url(document.id), {"user_id": target_user.id})

        # manager (fixture document) garde ses propres accès.
        assert DocumentPermission.objects.filter(document=document, user=manager).exists()


# --- F. Dossier (mêmes garanties, composant unique) -------------------------------


@pytest.mark.django_db
class TestFolderPermissions:
    def test_manager_can_view_folder_modal(self, folder, manager):
        client = _authenticated_client(manager)
        response = client.get(_folder_permissions_url(folder.id))
        assert response.status_code == 200

    def test_no_owner_row_for_folders(self, folder, manager):
        """Folder n'a pas de propriétaire (TECH-023) — aucune ligne
        implicite, contrairement aux documents. Vérifié dans la table
        des accès existants uniquement — le menu déroulant "Ajouter un
        accès" liste légitimement tous les utilisateurs actifs
        (dont "Propriétaire", nom d'un utilisateur réel non lié à ce
        dossier), sans rapport avec cette assertion."""
        client = _authenticated_client(manager)
        content = client.get(_folder_permissions_url(folder.id)).content.decode()
        table_html = content.split("<table")[1].split("</table>")[0]
        assert "Propriétaire" not in table_html

    def test_folder_toggle_grants_permission_immediately(self, folder, manager, target_user):
        from modules.documentation.services import has_folder_permission

        assert has_folder_permission(target_user, folder, "read") is False

        client = _authenticated_client(manager)
        client.post(
            _folder_toggle_url(folder.id), {"user_id": target_user.id, "action": "read"}
        )

        assert has_folder_permission(target_user, folder, "read") is True

    def test_folder_permissions_do_not_leak_to_documents(
        self, documentation_activated, storage_root, folder, manager, target_user, owner
    ):
        """Vérifie que le composant "unique" ne mélange pas les deux
        cibles : une permission de dossier n'apparaît pas dans la liste
        d'un document, même contenu dans ce dossier (cohérent avec la
        décision déjà tranchée en TECH-023 : aucun héritage)."""
        document_in_folder = upload_document(
            content=b"x", filename="dans-dossier-304.pdf", owner_user=owner, folder=folder
        )
        grant_permission(actor=owner, action=READ, document=document_in_folder, user=manager)
        grant_permission(actor=owner, action=WRITE, document=document_in_folder, user=manager)

        client = _authenticated_client(manager)
        client.post(
            _folder_toggle_url(folder.id), {"user_id": target_user.id, "action": "read"}
        )

        content = client.get(
            _document_permissions_url(document_in_folder.id)
        ).content.decode()
        table_html = content.split("<table")[1].split("</table>")[0]
        assert target_user.full_name not in table_html


# --- G. Sécurité --------------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_toggle_get_is_rejected(self, document, manager):
        client = _authenticated_client(manager)
        response = client.get(_document_toggle_url(document.id))
        assert response.status_code == 405

    def test_grantee_full_name_is_html_escaped(
        self, documentation_activated, storage_root, owner, manager
    ):
        evil_user = User.objects.create(
            username="evil304", full_name="<script>alert(1)</script>"
        )
        document_with_evil = upload_document(content=b"x", filename="x.pdf", owner_user=owner)
        grant_permission(actor=owner, action=WRITE, document=document_with_evil, user=manager)
        grant_permission(actor=owner, action=READ, document=document_with_evil, user=manager)
        grant_permission(actor=owner, action=READ, document=document_with_evil, user=evil_user)

        client = _authenticated_client(manager)
        content = client.get(
            _document_permissions_url(document_with_evil.id)
        ).content.decode()
        assert "<script>alert(1)</script>" not in content

    def test_toggle_invalid_action_is_ignored(self, document, manager, target_user):
        client = _authenticated_client(manager)
        client.post(
            _document_toggle_url(document.id),
            {"user_id": target_user.id, "action": "delete"},
        )
        assert not DocumentPermission.objects.filter(
            document=document, user=target_user
        ).exists()
