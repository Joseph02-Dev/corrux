"""Tests de l'Explorateur de documents — UI-301.

Rendu HTTP réel, permissions réelles (TECH-023), stockage réel
(tmp_path). Le module Documentation doit être ACTIVATED pour que
l'écran soit accessible (comme la Sidebar) — vérifié explicitement.
"""

import pytest
from django.test import Client, override_settings

from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from modules.documentation.services import create_folder, grant_permission, upload_document

ROOT_URL = "/documents/"


def _folder_url(folder_id: int) -> str:
    return f"/documents/dossier/{folder_id}/"


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


def _grant_global(user, module_id, resource, action):
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
    user = User.objects.create(username="lecteur_explorer", full_name="Lecteur Explorateur")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant_global(user, "documentation", "document", "read")
    return user


@pytest.fixture
def no_permission_user(db):
    user = User.objects.create(username="sanspermission_explorer", full_name="Sans Permission")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


# --- A. Permission d'entrée sur l'écran -----------------------------------------


@pytest.mark.django_db
class TestScreenPermission:
    def test_authorized_user_sees_the_screen(self, documentation_activated, reader):
        client = _authenticated_client(reader)
        assert client.get(ROOT_URL).status_code == 200

    def test_unauthorized_user_sees_permission_denied(
        self, documentation_activated, no_permission_user
    ):
        client = _authenticated_client(no_permission_user)
        response = client.get(ROOT_URL)
        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()

    def test_anonymous_user_is_redirected_to_login(self, documentation_activated):
        client = Client()
        response = client.get(ROOT_URL)
        assert response.status_code == 302
        assert response.url == f"/login/?next={ROOT_URL}"

    def test_deactivated_module_is_refused_even_with_permission(self, db, reader):
        """Aucun Module 'documentation' ACTIVATED créé ici — le lien
        Sidebar disparaîtrait, mais l'écran doit refuser aussi côté
        serveur, pas seulement masquer le lien."""
        client = _authenticated_client(reader)
        response = client.get(ROOT_URL)
        assert response.status_code == 403


# --- B. Navigation dossier/sous-dossier -----------------------------------------


@pytest.mark.django_db
class TestFolderNavigation:
    def test_root_shows_root_level_folders_and_documents(
        self, documentation_activated, storage_root, reader
    ):
        folder = create_folder(name="Contrats")
        grant_permission(actor=reader, action="read", folder=folder, user=reader)
        upload_document(content=b"x", filename="racine.pdf", owner_user=reader)

        client = _authenticated_client(reader)
        content = client.get(ROOT_URL).content.decode()

        assert "Contrats" in content
        assert "racine.pdf" in content

    def test_navigating_into_a_subfolder_shows_its_contents_only(
        self, documentation_activated, storage_root, reader
    ):
        folder = create_folder(name="Contrats")
        grant_permission(actor=reader, action="read", folder=folder, user=reader)
        other_folder = create_folder(name="Autre dossier")
        upload_document(
            content=b"x", filename="dans-contrats.pdf", owner_user=reader, folder=folder
        )
        upload_document(
            content=b"y", filename="dans-autre.pdf", owner_user=reader, folder=other_folder
        )

        client = _authenticated_client(reader)
        content = client.get(_folder_url(folder.id)).content.decode()

        assert "dans-contrats.pdf" in content
        assert "dans-autre.pdf" not in content

    def test_breadcrumb_reflects_the_real_folder_path(
        self, documentation_activated, storage_root, reader
    ):
        parent = create_folder(name="Parent")
        grant_permission(actor=reader, action="read", folder=parent, user=reader)
        child = create_folder(name="Enfant", parent=parent)
        grant_permission(actor=reader, action="read", folder=child, user=reader)

        client = _authenticated_client(reader)
        content = client.get(_folder_url(child.id)).content.decode()

        assert "Documents" in content
        assert "Parent" in content
        assert "Enfant" in content

    def test_nonexistent_folder_returns_404(self, documentation_activated, reader):
        client = _authenticated_client(reader)
        response = client.get(_folder_url(999999))
        assert response.status_code == 404

    def test_restricted_folder_shows_permission_denied(
        self, documentation_activated, storage_root, reader, no_permission_user
    ):
        """Dossier restreint — l'utilisateur a la permission d'entrée
        sur l'écran (documentation.document.read) mais aucune
        permission sur CE dossier précis (aucun droit ni propriétaire,
        Folder n'a pas de champ owner)."""
        _grant_global(no_permission_user, "documentation", "document", "read")
        folder = create_folder(name="Confidentiel")

        client = _authenticated_client(no_permission_user)
        response = client.get(_folder_url(folder.id))

        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()


# --- C. Filtrage par permission (critère d'acceptation explicite) ---------------


@pytest.mark.django_db
class TestPermissionFiltering:
    def test_document_without_permission_does_not_appear(
        self, documentation_activated, storage_root, reader
    ):
        owner = User.objects.create(username="autre_proprietaire", full_name="Autre")
        upload_document(content=b"x", filename="prive-autrui.pdf", owner_user=owner)

        client = _authenticated_client(reader)
        content = client.get(ROOT_URL).content.decode()

        assert "prive-autrui.pdf" not in content

    def test_owned_document_appears(self, documentation_activated, storage_root, reader):
        upload_document(content=b"x", filename="mon-document.pdf", owner_user=reader)

        client = _authenticated_client(reader)
        content = client.get(ROOT_URL).content.decode()

        assert "mon-document.pdf" in content

    def test_document_with_explicit_permission_appears(
        self, documentation_activated, storage_root, reader
    ):
        owner = User.objects.create(username="proprietaire_partage", full_name="Propriétaire")
        document = upload_document(content=b"x", filename="partage.pdf", owner_user=owner)
        grant_permission(actor=owner, action="read", document=document, user=reader)

        client = _authenticated_client(reader)
        content = client.get(ROOT_URL).content.decode()

        assert "partage.pdf" in content

    def test_subfolder_without_permission_does_not_appear(
        self, documentation_activated, storage_root, reader
    ):
        """Un dossier auquel l'utilisateur n'a pas accès ne doit pas
        apparaître dans la liste de la racine — même règle que pour les
        documents."""
        create_folder(name="Dossier restreint")

        client = _authenticated_client(reader)
        content = client.get(ROOT_URL).content.decode()

        assert "Dossier restreint" not in content


# --- D. Badge Confidentiel (critère d'acceptation explicite) --------------------


@pytest.mark.django_db
class TestConfidentialBadge:
    def test_document_with_a_permission_shows_confidential_badge(
        self, documentation_activated, storage_root, reader
    ):
        document = upload_document(
            content=b"x", filename="avec-permission.pdf", owner_user=reader
        )
        grant_permission(actor=reader, action="read", document=document, user=reader)

        client = _authenticated_client(reader)
        content = client.get(ROOT_URL).content.decode()

        assert "Confidentiel" in content

    def test_document_without_any_permission_shows_no_confidential_badge(
        self, documentation_activated, storage_root, reader
    ):
        upload_document(content=b"x", filename="sans-permission.pdf", owner_user=reader)

        client = _authenticated_client(reader)
        content = client.get(ROOT_URL).content.decode()

        assert "Confidentiel" not in content


# --- E. État vide -------------------------------------------------------------------


@pytest.mark.django_db
class TestEmptyState:
    def test_empty_root_shows_empty_state(self, documentation_activated, reader):
        client = _authenticated_client(reader)
        content = client.get(ROOT_URL).content.decode()
        assert "corrux-empty-state" in content

    def test_empty_folder_shows_empty_state(self, documentation_activated, reader):
        folder = create_folder(name="Vide")
        client = _authenticated_client(reader)
        content = client.get(_folder_url(folder.id)).content.decode()
        assert "corrux-empty-state" in content


# --- F. Sécurité --------------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_filename_is_html_escaped(self, documentation_activated, storage_root, reader):
        upload_document(
            content=b"x", filename="a<script>alert(1);b.pdf", owner_user=reader
        )
        client = _authenticated_client(reader)
        content = client.get(ROOT_URL).content.decode()
        assert "<script>alert(1);b" not in content
        assert "&lt;script&gt;" in content

    def test_no_write_route_exists_for_explorer(self, documentation_activated, reader):
        client = _authenticated_client(reader)
        response = client.post(ROOT_URL)
        assert response.status_code == 405
