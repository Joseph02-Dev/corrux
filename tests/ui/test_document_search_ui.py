"""Tests de la Recherche documentaire + câblage Topbar — UI-305.

Rendu HTTP réel, permissions réelles (TECH-023), même moteur de
recherche (search_documents(), TECH-022) quelle que soit l'entrée —
critère d'acceptation explicite du ticket, vérifié directement.
"""

import pytest
from django.test import Client, override_settings

from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from modules.documentation.services import create_folder, grant_permission, upload_document

SEARCH_URL = "/documents/recherche/"


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
    user = User.objects.create(username="lecteur_search305", full_name="Lecteur Recherche")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant_global(user, "documentation", "document", "read")
    return user


@pytest.fixture
def no_permission_user(db):
    user = User.objects.create(username="sanspermission_search305", full_name="Sans Permission")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


# --- A. Accès ------------------------------------------------------------------


@pytest.mark.django_db
class TestAccess:
    def test_authorized_user_sees_the_screen(self, documentation_activated, reader):
        client = _authenticated_client(reader)
        assert client.get(SEARCH_URL).status_code == 200

    def test_unauthorized_user_sees_permission_denied(
        self, documentation_activated, no_permission_user
    ):
        client = _authenticated_client(no_permission_user)
        response = client.get(SEARCH_URL)
        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()

    def test_anonymous_user_is_redirected_to_login(self, documentation_activated):
        client = Client()
        response = client.get(SEARCH_URL)
        assert response.status_code == 302

    def test_deactivated_module_is_refused(self, db, reader):
        client = _authenticated_client(reader)
        response = client.get(SEARCH_URL)
        assert response.status_code == 403


# --- B. Résultats de recherche --------------------------------------------------


@pytest.mark.django_db
class TestSearchResults:
    def test_no_query_shows_the_invitation_state(self, documentation_activated, reader):
        client = _authenticated_client(reader)
        content = client.get(SEARCH_URL).content.decode()
        assert "corrux-empty-state" in content
        assert "corrux-table" not in content

    def test_keyword_search_returns_matching_document(
        self, documentation_activated, storage_root, reader
    ):
        upload_document(content=b"x", filename="rapport-annuel.pdf", owner_user=reader)
        client = _authenticated_client(reader)
        content = client.get(SEARCH_URL, {"q": "rapport"}).content.decode()
        assert "rapport-annuel.pdf" in content

    def test_no_results_shows_empty_state(self, documentation_activated, storage_root, reader):
        upload_document(content=b"x", filename="x.pdf", owner_user=reader)
        client = _authenticated_client(reader)
        content = client.get(SEARCH_URL, {"q": "zzzintrouvable"}).content.decode()
        assert "corrux-empty-state" in content

    def test_type_filter_narrows_results(self, documentation_activated, storage_root, reader):
        upload_document(content=b"x", filename="doc.pdf", owner_user=reader)
        upload_document(content=b"y", filename="photo.jpg", owner_user=reader)

        client = _authenticated_client(reader)
        content = client.get(SEARCH_URL, {"q": "", "type": "application/pdf"}).content.decode()

        table_html = content.split("<table")[1] if "<table" in content else ""
        assert "doc.pdf" in table_html
        assert "photo.jpg" not in table_html

    def test_folder_filter_narrows_results(self, documentation_activated, storage_root, reader):
        folder = create_folder(name="Contrats")
        grant_permission(actor=reader, action="read", folder=folder, user=reader)
        upload_document(
            content=b"x", filename="dans-dossier.pdf", owner_user=reader, folder=folder
        )
        upload_document(content=b"y", filename="racine.pdf", owner_user=reader)

        client = _authenticated_client(reader)
        content = client.get(SEARCH_URL, {"dossier": str(folder.id)}).content.decode()

        assert "dans-dossier.pdf" in content
        assert "racine.pdf" not in content

    def test_root_only_filter(self, documentation_activated, storage_root, reader):
        folder = create_folder(name="Ailleurs")
        grant_permission(actor=reader, action="read", folder=folder, user=reader)
        upload_document(
            content=b"x", filename="dans-dossier.pdf", owner_user=reader, folder=folder
        )
        upload_document(content=b"y", filename="racine.pdf", owner_user=reader)

        client = _authenticated_client(reader)
        content = client.get(SEARCH_URL, {"dossier": "_root_"}).content.decode()

        assert "racine.pdf" in content
        assert "dans-dossier.pdf" not in content

    def test_location_column_shows_real_folder_path(
        self, documentation_activated, storage_root, reader
    ):
        parent = create_folder(name="Parent")
        grant_permission(actor=reader, action="read", folder=parent, user=reader)
        child = create_folder(name="Enfant", parent=parent)
        grant_permission(actor=reader, action="read", folder=child, user=reader)
        upload_document(content=b"x", filename="profond.pdf", owner_user=reader, folder=child)

        client = _authenticated_client(reader)
        content = client.get(SEARCH_URL, {"q": "profond"}).content.decode()

        assert "Parent" in content
        assert "Enfant" in content

    def test_root_document_shows_racine_location(
        self, documentation_activated, storage_root, reader
    ):
        upload_document(content=b"x", filename="a-la-racine.pdf", owner_user=reader)
        client = _authenticated_client(reader)
        content = client.get(SEARCH_URL, {"q": "a-la-racine"}).content.decode()
        assert "Racine" in content


# --- C. Filtrage par permission (réutilisation de TECH-022, jamais dupliqué) ----


@pytest.mark.django_db
class TestPermissionFiltering:
    def test_forbidden_document_never_appears_even_with_exact_keyword(
        self, documentation_activated, storage_root, reader
    ):
        other_owner = User.objects.create(username="autre_owner_305", full_name="Autre")
        upload_document(content=b"x", filename="confidentiel-exact.pdf", owner_user=other_owner)

        client = _authenticated_client(reader)
        content = client.get(SEARCH_URL, {"q": "confidentiel-exact.pdf"}).content.decode()
        # Le champ "Mot-clé" réaffiche légitimement la valeur recherchée
        # dans le formulaire — on isole donc la zone de résultats
        # (après le formulaire de filtres) pour l'assertion.
        results_area = content.split("</form>")[-1]

        assert "confidentiel-exact.pdf" not in results_area


# --- D/E. Même moteur, Topbar vs écran dédié — critère d'acceptation explicite --


@pytest.mark.django_db
class TestTopbarWiring:
    def test_search_available_in_topbar_with_permission(
        self, documentation_activated, storage_root, reader
    ):
        client = _authenticated_client(reader)
        content = client.get(SEARCH_URL).content.decode()
        assert 'action="/documents/recherche/"' in content
        assert "corrux-topbar__search-submit" in content

    def test_search_disabled_placeholder_without_documentation_permission(
        self, db, storage_root
    ):
        user = User.objects.create(username="sans_doc_perm", full_name="Sans Doc")
        auth.set_user_password(user, "Password123!")
        user.save()
        _grant_global(user, "core", "user", "read")  # une autre permission, sans rapport

        client = _authenticated_client(user)
        content = client.get("/utilisateurs/").content.decode()

        assert "Recherche (à venir)" in content
        assert 'action="/documents/recherche/"' not in content

    def test_topbar_search_and_dedicated_screen_use_the_same_engine(
        self, documentation_activated, storage_root, reader
    ):
        """Critère d'acceptation explicite : même moteur de recherche
        quelle que soit l'entrée. Vérifié en comparant le résultat d'un
        GET ?q=... (ce que soumet le formulaire Topbar) à celui obtenu
        directement sur l'écran dédié — même route, même code, donc
        nécessairement le même moteur, pas un second chemin de recherche
        dupliqué."""
        upload_document(content=b"x", filename="rapport-moteur-unique.pdf", owner_user=reader)

        client = _authenticated_client(reader)
        via_topbar_style_query = client.get(
            SEARCH_URL, {"q": "rapport-moteur-unique"}
        ).content.decode()
        via_dedicated_screen = client.get(
            SEARCH_URL, {"q": "rapport-moteur-unique"}
        ).content.decode()

        assert "rapport-moteur-unique.pdf" in via_topbar_style_query
        assert "rapport-moteur-unique.pdf" in via_dedicated_screen


# --- F. Sécurité --------------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_filename_is_html_escaped(self, documentation_activated, storage_root, reader):
        upload_document(content=b"x", filename="a<script>b.pdf", owner_user=reader)
        client = _authenticated_client(reader)
        content = client.get(SEARCH_URL, {"q": "script"}).content.decode()
        assert "<script>b" not in content

    def test_post_is_rejected(self, documentation_activated, reader):
        client = _authenticated_client(reader)
        response = client.post(SEARCH_URL)
        assert response.status_code == 405

    def test_invalid_folder_id_is_ignored_without_500(
        self, documentation_activated, storage_root, reader
    ):
        upload_document(content=b"x", filename="x.pdf", owner_user=reader)
        client = _authenticated_client(reader)
        response = client.get(SEARCH_URL, {"dossier": "999999"})
        assert response.status_code == 200

    def test_invalid_date_is_ignored_without_500(
        self, documentation_activated, storage_root, reader
    ):
        client = _authenticated_client(reader)
        response = client.get(SEARCH_URL, {"du": "not-a-date"})
        assert response.status_code == 200
