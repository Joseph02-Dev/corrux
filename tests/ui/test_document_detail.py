"""Tests du Détail document (Drawer consultation/édition) — UI-303.

Rendu HTTP réel, permissions réelles (TECH-023), stockage réel
(tmp_path).
"""

import pytest
from django.test import Client, override_settings

from core.identity import auth
from core.identity.models import User
from core.modules.models import Module
from modules.documentation.models import DocumentMetadata
from modules.documentation.services import (
    create_folder,
    document_metadata_value,
    grant_permission,
    upload_document,
)

READ = "read"
WRITE = "write"


def _detail_url(document_id: int) -> str:
    return f"/documents/{document_id}/"


def _edit_url(document_id: int) -> str:
    return f"/documents/{document_id}/modifier/"


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
    user = User.objects.create(username="proprietaire_detail", full_name="Propriétaire")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


@pytest.fixture
def other_user(db):
    user = User.objects.create(username="autre_detail", full_name="Autre Utilisateur")
    auth.set_user_password(user, "Password123!")
    user.save()
    return user


@pytest.fixture
def document(documentation_activated, storage_root, owner):
    return upload_document(
        content=b"contenu", filename="rapport.pdf", owner_user=owner, category="Finance"
    )


# --- Variante A — Consultation --------------------------------------------------


@pytest.mark.django_db
class TestConsultation:
    def test_owner_can_view_the_detail(self, document, owner):
        client = _authenticated_client(owner)
        response = client.get(_detail_url(document.id))
        assert response.status_code == 200

    def test_user_without_permission_sees_permission_denied(self, document, other_user):
        client = _authenticated_client(other_user)
        response = client.get(_detail_url(document.id))
        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()

    def test_anonymous_user_is_redirected_to_login(self, document):
        client = Client()
        response = client.get(_detail_url(document.id))
        assert response.status_code == 302

    def test_nonexistent_document_returns_404(self, documentation_activated, owner):
        client = _authenticated_client(owner)
        response = client.get(_detail_url(999999))
        assert response.status_code == 404

    def test_displays_real_metadata(self, document, owner):
        client = _authenticated_client(owner)
        content = client.get(_detail_url(document.id)).content.decode()

        assert "rapport.pdf" in content
        assert "application/pdf" in content
        assert "Propriétaire" in content  # full_name du owner
        assert "Finance" in content  # catégorie déjà fournie à l'upload

    def test_user_with_explicit_read_permission_sees_the_detail(
        self, document, owner, other_user
    ):
        grant_permission(actor=owner, action=READ, document=document, user=other_user)
        client = _authenticated_client(other_user)
        response = client.get(_detail_url(document.id))
        assert response.status_code == 200

    def test_modify_button_visible_only_with_write_permission(
        self, document, owner, other_user
    ):
        grant_permission(actor=owner, action=READ, document=document, user=other_user)

        owner_content = (
            _authenticated_client(owner).get(_detail_url(document.id)).content.decode()
        )
        reader_content = (
            _authenticated_client(other_user).get(_detail_url(document.id)).content.decode()
        )

        # Le propriétaire n'a que "read" implicite (décision TECH-023 :
        # aucun accès write implicite par simple propriété) -> pas de
        # bouton Modifier non plus pour lui, sauf permission explicite.
        assert "Modifier" not in owner_content
        assert "Modifier" not in reader_content

    def test_modify_button_visible_with_explicit_write_permission(
        self, document, owner, other_user
    ):
        grant_permission(actor=owner, action=READ, document=document, user=other_user)
        grant_permission(actor=owner, action=WRITE, document=document, user=other_user)
        client = _authenticated_client(other_user)
        content = client.get(_detail_url(document.id)).content.decode()
        assert "Modifier" in content


# --- Variante B — Édition, critère d'acceptation explicite ---------------------


@pytest.mark.django_db
class TestEditAccessControl:
    def test_edit_refused_without_write_permission(self, document, owner, other_user):
        """Critère d'acceptation explicite du ticket."""
        grant_permission(actor=owner, action=READ, document=document, user=other_user)
        client = _authenticated_client(other_user)
        response = client.get(_edit_url(document.id))
        assert response.status_code == 403
        assert "corrux-permission-denied" in response.content.decode()

    def test_edit_refused_for_owner_without_explicit_write(self, document, owner):
        """Le propriétaire n'a qu'un accès read implicite (TECH-023) —
        même lui doit se voir refuser l'édition sans permission write
        explicite."""
        client = _authenticated_client(owner)
        response = client.get(_edit_url(document.id))
        assert response.status_code == 403

    def test_edit_allowed_with_explicit_write_permission(self, document, owner, other_user):
        grant_permission(actor=owner, action=WRITE, document=document, user=other_user)
        client = _authenticated_client(other_user)
        response = client.get(_edit_url(document.id))
        assert response.status_code == 200

    def test_anonymous_user_is_redirected_to_login(self, document):
        client = Client()
        response = client.get(_edit_url(document.id))
        assert response.status_code == 302

    def test_post_without_write_permission_is_refused(self, document, owner, other_user):
        client = _authenticated_client(other_user)
        response = client.post(_edit_url(document.id), {"category": "Hack"})
        assert response.status_code == 403
        entry = DocumentMetadata.objects.filter(document=document, key="categorie").first()
        assert entry.value == "Finance"


# --- Variante B — Édition réussie -----------------------------------------------


@pytest.mark.django_db
class TestEditSuccess:
    def test_edit_updates_category(self, document, owner, other_user):
        grant_permission(actor=owner, action=WRITE, document=document, user=other_user)
        client = _authenticated_client(other_user)

        response = client.post(
            _edit_url(document.id), {"category": "Juridique", "description": ""}
        )

        assert response.status_code == 302
        assert document_metadata_value(document, "categorie") == "Juridique"

    def test_edit_updates_description(self, document, owner, other_user):
        grant_permission(actor=owner, action=WRITE, document=document, user=other_user)
        client = _authenticated_client(other_user)

        client.post(
            _edit_url(document.id), {"category": "Finance", "description": "Notes internes"}
        )

        assert document_metadata_value(document, "description") == "Notes internes"

    def test_edit_redirects_to_detail(self, document, owner, other_user):
        grant_permission(actor=owner, action=WRITE, document=document, user=other_user)
        client = _authenticated_client(other_user)

        response = client.post(_edit_url(document.id), {"category": "X", "description": "Y"})

        assert response.url == _detail_url(document.id)

    def test_clearing_category_removes_the_metadata_entry(
        self, document, owner, other_user
    ):
        grant_permission(actor=owner, action=WRITE, document=document, user=other_user)
        client = _authenticated_client(other_user)

        client.post(_edit_url(document.id), {"category": "", "description": ""})

        assert not DocumentMetadata.objects.filter(document=document, key="categorie").exists()

    def test_updated_metadata_is_visible_in_the_detail_view(
        self, document, owner, other_user
    ):
        grant_permission(actor=owner, action=READ, document=document, user=other_user)
        grant_permission(actor=owner, action=WRITE, document=document, user=other_user)
        client = _authenticated_client(other_user)

        client.post(
            _edit_url(document.id), {"category": "Nouvelle Categorie", "description": ""}
        )
        content = client.get(_detail_url(document.id)).content.decode()

        assert "Nouvelle Categorie" in content

    def test_filename_is_never_modifiable_via_this_form(
        self, document, owner, other_user, storage_root
    ):
        """Aucun champ 'filename' n'est lu par la vue — une tentative
        de le transmettre n'a strictement aucun effet, le fichier réel
        reste retrouvable sous son nom d'origine."""
        from core.storage import files as storage

        grant_permission(actor=owner, action=WRITE, document=document, user=other_user)
        client = _authenticated_client(other_user)

        client.post(
            _edit_url(document.id),
            {"filename": "renomme.pdf", "category": "X", "description": ""},
        )

        document.refresh_from_db()
        assert document.filename == "rapport.pdf"
        content = storage.read("documentation", document.storage_path, document.filename)
        assert content == b"contenu"

    def test_edit_does_not_move_the_document(self, storage_root, owner, other_user):
        """Le dossier n'est pas exposé dans le formulaire d'édition
        (décision documentée) : le document reste dans son dossier
        actuel après une édition."""
        folder = create_folder(name="Contrats")
        grant_permission(actor=owner, action=READ, folder=folder, user=owner)
        grant_permission(actor=owner, action=WRITE, folder=folder, user=owner)
        document_in_folder = upload_document(
            content=b"x", filename="dans-dossier.pdf", owner_user=owner, folder=folder
        )
        grant_permission(
            actor=owner, action=WRITE, document=document_in_folder, user=other_user
        )

        client = _authenticated_client(other_user)
        client.post(_edit_url(document_in_folder.id), {"category": "X", "description": ""})

        document_in_folder.refresh_from_db()
        assert document_in_folder.folder == folder


# --- Sécurité ---------------------------------------------------------------------


@pytest.mark.django_db
class TestSecurity:
    def test_category_is_html_escaped_in_detail_view(
        self, documentation_activated, storage_root, owner
    ):
        document_with_html = upload_document(
            content=b"x",
            filename="x.pdf",
            owner_user=owner,
            category="<script>alert(1)</script>",
        )
        client = _authenticated_client(owner)
        content = client.get(_detail_url(document_with_html.id)).content.decode()
        assert "<script>alert(1)</script>" not in content

    def test_put_method_is_rejected_on_edit(self, document, owner, other_user):
        grant_permission(actor=owner, action=WRITE, document=document, user=other_user)
        client = _authenticated_client(other_user)
        response = client.put(_edit_url(document.id))
        assert response.status_code == 405
