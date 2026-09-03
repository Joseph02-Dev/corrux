"""Tests du dépôt de document (Dropzone) — UI-302.

Les « 5 états » de la maquette (Lot 3 §2) sont réinterprétés pour un
rendu strictement serveur, zéro JavaScript (cf. commentaire dans
ui/views.py) :
1. Vide/survol -> GET initial (testé).
2/3. Fichier sélectionné / upload en cours -> aucun équivalent serveur
     sans JS, non testables ici (documenté, pas ignoré).
4. Succès -> redirection + document réellement présent (testé).
5. Échec -> formulaire réaffiché avec message explicite (testé, y
   compris le cas taille max explicitement requis par le contrat).
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from core.authz.models import Permission, Role, RolePermission, UserRole
from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from modules.documentation.models import Document, DocumentMetadata
from modules.documentation.services import create_folder, grant_permission

ROOT_UPLOAD_URL = "/documents/deposer/"


def _folder_upload_url(folder_id: int) -> str:
    return f"/documents/dossier/{folder_id}/deposer/"


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
def uploader(db):
    user = User.objects.create(username="deposant", full_name="Déposant")
    auth.set_user_password(user, "Password123!")
    user.save()
    _grant_global(user, "documentation", "document", "read")
    return user


def _pdf_file(name="rapport.pdf", content=b"contenu pdf"):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


# --- 1. État vide (GET initial) --------------------------------------------------


@pytest.mark.django_db
class TestEmptyDropzoneState:
    def test_get_renders_the_upload_drawer_open(self, documentation_activated, uploader):
        client = _authenticated_client(uploader)
        content = client.get(ROOT_UPLOAD_URL).content.decode()
        assert "document-upload-drawer" in content
        assert "corrux-dropzone" in content

    def test_get_does_not_create_any_document(self, documentation_activated, uploader):
        client = _authenticated_client(uploader)
        client.get(ROOT_UPLOAD_URL)
        assert Document.objects.count() == 0

    def test_drawer_shows_the_target_folder(self, documentation_activated, storage_root, uploader):
        folder = create_folder(name="Contrats")
        grant_permission(actor=uploader, action="read", folder=folder, user=uploader)
        grant_permission(actor=uploader, action="write", folder=folder, user=uploader)

        client = _authenticated_client(uploader)
        content = client.get(_folder_upload_url(folder.id)).content.decode()

        assert "Contrats" in content

    def test_root_drawer_shows_root_label(self, documentation_activated, uploader):
        client = _authenticated_client(uploader)
        content = client.get(ROOT_UPLOAD_URL).content.decode()
        assert "Documents (racine)" in content


# --- 4. Upload réussi -------------------------------------------------------------


@pytest.mark.django_db
class TestSuccessfulUpload:
    def test_upload_at_root_creates_a_real_document(
        self, documentation_activated, storage_root, uploader
    ):
        client = _authenticated_client(uploader)
        response = client.post(ROOT_UPLOAD_URL, {"file": _pdf_file()})

        assert response.status_code == 302
        assert Document.objects.filter(filename="rapport.pdf", owner_user=uploader).exists()

    def test_upload_redirects_to_the_explorer(
        self, documentation_activated, storage_root, uploader
    ):
        client = _authenticated_client(uploader)
        response = client.post(ROOT_UPLOAD_URL, {"file": _pdf_file()})
        assert response.url == "/documents/"

    def test_uploaded_document_is_visible_in_the_explorer(
        self, documentation_activated, storage_root, uploader
    ):
        client = _authenticated_client(uploader)
        client.post(ROOT_UPLOAD_URL, {"file": _pdf_file(name="visible.pdf")})

        content = client.get("/documents/").content.decode()
        assert "visible.pdf" in content

    def test_upload_into_a_folder_with_write_permission_succeeds(
        self, documentation_activated, storage_root, uploader
    ):
        folder = create_folder(name="Cible")
        grant_permission(actor=uploader, action="read", folder=folder, user=uploader)
        grant_permission(actor=uploader, action="write", folder=folder, user=uploader)

        client = _authenticated_client(uploader)
        response = client.post(_folder_upload_url(folder.id), {"file": _pdf_file()})

        assert response.status_code == 302
        assert response.url == _folder_url(folder.id)
        document = Document.objects.get(filename="rapport.pdf")
        assert document.folder == folder

    def test_category_creates_a_document_metadata_entry(
        self, documentation_activated, storage_root, uploader
    ):
        client = _authenticated_client(uploader)
        client.post(ROOT_UPLOAD_URL, {"file": _pdf_file(), "category": "Finance"})

        document = Document.objects.get(filename="rapport.pdf")
        assert DocumentMetadata.objects.get(document=document, key="categorie").value == "Finance"

    def test_description_creates_a_document_metadata_entry(
        self, documentation_activated, storage_root, uploader
    ):
        client = _authenticated_client(uploader)
        client.post(
            ROOT_UPLOAD_URL, {"file": _pdf_file(), "description": "Un rapport important"}
        )

        document = Document.objects.get(filename="rapport.pdf")
        assert (
            DocumentMetadata.objects.get(document=document, key="description").value
            == "Un rapport important"
        )

    def test_uploaded_content_is_really_stored_and_readable(
        self, documentation_activated, storage_root, uploader
    ):
        from core.storage import files as storage

        client = _authenticated_client(uploader)
        client.post(ROOT_UPLOAD_URL, {"file": _pdf_file(content=b"contenu binaire reel")})

        document = Document.objects.get(filename="rapport.pdf")
        assert storage.read("documentation", document.storage_path, document.filename) == (
            b"contenu binaire reel"
        )


# --- 5. Upload échoué ---------------------------------------------------------------


@pytest.mark.django_db
class TestFailedUpload:
    def test_disallowed_extension_reshows_the_form_with_an_explicit_error(
        self, documentation_activated, storage_root, uploader
    ):
        client = _authenticated_client(uploader)
        response = client.post(
            ROOT_UPLOAD_URL,
            {"file": SimpleUploadedFile(
                "script.exe", b"x", content_type="application/x-msdownload"
            )},
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert "document-upload-drawer" in content
        assert "corrux-error-state" in content

    def test_disallowed_extension_creates_no_document(
        self, documentation_activated, storage_root, uploader
    ):
        client = _authenticated_client(uploader)
        client.post(
            ROOT_UPLOAD_URL,
            {"file": SimpleUploadedFile(
                "script.exe", b"x", content_type="application/x-msdownload"
            )},
        )
        assert Document.objects.count() == 0

    def test_file_over_max_size_is_rejected_with_explicit_error(
        self, documentation_activated, storage_root, uploader
    ):
        """Cas explicitement requis par le contrat du ticket."""
        oversized = SimpleUploadedFile(
            "gros.pdf", b"x" * (50 * 1024 * 1024 + 1), content_type="application/pdf"
        )
        client = _authenticated_client(uploader)
        response = client.post(ROOT_UPLOAD_URL, {"file": oversized})

        assert response.status_code == 200
        assert "corrux-error-state" in response.content.decode()
        assert Document.objects.count() == 0

    def test_no_file_selected_shows_an_explicit_error(
        self, documentation_activated, storage_root, uploader
    ):
        client = _authenticated_client(uploader)
        response = client.post(ROOT_UPLOAD_URL, {})

        assert response.status_code == 200
        assert "Veuillez sélectionner un fichier" in response.content.decode()
        assert Document.objects.count() == 0

    def test_error_screen_still_shows_the_underlying_explorer(
        self, documentation_activated, storage_root, uploader
    ):
        """Le Drawer est réaffiché PAR-DESSUS l'Explorateur (maquette),
        pas un écran d'erreur isolé — la liste reste visible derrière."""
        from modules.documentation.services import upload_document

        upload_document(content=b"x", filename="deja-la.pdf", owner_user=uploader)

        client = _authenticated_client(uploader)
        response = client.post(
            ROOT_UPLOAD_URL,
            {"file": SimpleUploadedFile("bad.exe", b"x", content_type="application/octet-stream")},
        )

        assert "deja-la.pdf" in response.content.decode()


# --- Permissions ---------------------------------------------------------------------


@pytest.mark.django_db
class TestPermissions:
    def test_unauthorized_user_sees_permission_denied(self, documentation_activated, db):
        user = User.objects.create(username="sans_droit_upload", full_name="Sans Droit")
        auth.set_user_password(user, "Password123!")
        user.save()

        client = _authenticated_client(user)
        response = client.get(ROOT_UPLOAD_URL)
        assert response.status_code == 403

    def test_anonymous_user_is_redirected_to_login(self, documentation_activated):
        client = Client()
        response = client.get(ROOT_UPLOAD_URL)
        assert response.status_code == 302

    def test_upload_into_folder_without_write_permission_is_refused(
        self, documentation_activated, storage_root, uploader
    ):
        folder = create_folder(name="Restreint")
        # uploader a la permission d'entrée sur l'écran, mais AUCUNE
        # permission sur ce dossier précis.
        client = _authenticated_client(uploader)
        response = client.get(_folder_upload_url(folder.id))
        assert response.status_code == 403

    def test_read_only_folder_permission_is_not_sufficient_to_upload(
        self, documentation_activated, storage_root, uploader
    ):
        """"write" est explicitement requis pour déposer DANS un dossier
        — "read" seul (qui suffit pour naviguer/voir) ne doit pas
        suffire à y déposer."""
        folder = create_folder(name="Lecture seule")
        grant_permission(actor=uploader, action="read", folder=folder, user=uploader)

        client = _authenticated_client(uploader)
        response = client.get(_folder_upload_url(folder.id))
        assert response.status_code == 403

    def test_deactivated_module_is_refused(self, db, uploader):
        client = _authenticated_client(uploader)
        response = client.get(ROOT_UPLOAD_URL)
        assert response.status_code == 403


# --- Sécurité ------------------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_error_message_is_html_escaped_if_it_contained_special_characters(
        self, documentation_activated, storage_root, uploader
    ):
        """Le message d'erreur de DocumentUploadError inclut le nom de
        fichier fourni par le client — doit être échappé."""
        client = _authenticated_client(uploader)
        response = client.post(
            ROOT_UPLOAD_URL,
            {"file": SimpleUploadedFile("<script>x</script>.exe", b"x")},
        )
        content = response.content.decode()
        assert "<script>x</script>" not in content

    def test_put_method_is_rejected(self, documentation_activated, uploader):
        client = _authenticated_client(uploader)
        response = client.put(ROOT_UPLOAD_URL)
        assert response.status_code == 405
