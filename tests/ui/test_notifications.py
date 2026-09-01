"""Tests de l'icône Notifications — UI-106 (V1 statique).

Rendu réel (patron établi UI-101 à UI-105). Vérifie explicitement
l'absence de toute donnée dynamique inventée (compteur, pastille), de
JavaScript, et l'accessibilité réelle (pas de simple présence d'ARIA).
"""

import pytest
from django.template import engines
from django.test import Client

from core.identity import auth
from core.identity.models import User

SHELL_DEMO_URL = "/shell-demo/"


def _render(snippet: str, context: dict | None = None) -> str:
    template = engines["django"].from_string("{% load ui_tags %}" + snippet)
    return template.render(context or {})


def _authenticated_client(user) -> Client:
    client = Client()
    session = client.session
    session[auth.SESSION_USER_ID_KEY] = user.id
    session.save()
    client.cookies["sessionid"] = session.session_key
    return client


@pytest.fixture
def active_user(db):
    user = User.objects.create(username="jdupont", full_name="Jean Dupont")
    auth.set_user_password(user, "MotDePasseReel123!")
    user.save()
    return user


class TestBellIcon:
    def test_bell_icon_renders_via_existing_icon_system(self):
        html = _render('{% corrux_icon name="bell" %}')
        assert "<svg" in html
        assert 'viewBox="0 0 24 24"' in html

    def test_bell_icon_is_decorative_by_default(self):
        html = _render('{% corrux_icon name="bell" %}')
        assert 'aria-hidden="true"' in html

    def test_bell_icon_with_label_gets_accessible_name(self):
        html = _render('{% corrux_icon name="bell" label="Notifications" %}')
        assert 'role="img"' in html
        assert 'aria-label="Notifications"' in html
        assert "<title>Notifications</title>" in html

    def test_existing_icons_are_unaffected_by_the_new_entry(self):
        """Non-régression explicite du jeu d'icônes existant (UI-101)."""
        for name in (
            "check", "close", "chevron-down", "search", "user",
            "warning", "folder", "settings", "logout",
        ):
            html = _render(f'{{% corrux_icon name="{name}" %}}')
            assert "<svg" in html

    def test_unknown_icon_still_raises_explicitly(self):
        with pytest.raises(ValueError):
            _render('{% corrux_icon name="does-not-exist" %}')


@pytest.mark.django_db
class TestNotificationsInTopbar:
    def test_bell_icon_is_always_present_even_with_zero_notifications(
        self, active_user
    ):
        """Critère d'acceptation explicite UI-106 : icône toujours visible,
        aucune erreur en l'absence de notification."""
        client = _authenticated_client(active_user)
        response = client.get(SHELL_DEMO_URL)

        assert response.status_code == 200
        content = response.content.decode()
        assert "corrux-notifications" in content
        assert "corrux-notifications__trigger" in content

    def test_panel_shows_a_real_empty_state_not_a_fake_count(self, active_user):
        client = _authenticated_client(active_user)
        content = client.get(SHELL_DEMO_URL).content.decode()

        assert "Aucune notification" in content
        assert "corrux-empty-state" in content  # composant UI-104 réutilisé

    def test_no_notification_count_or_badge_number_is_ever_rendered(self, active_user):
        """Aucun compteur inventé, aucune pastille numérique — V1
        strictement statique, aucune donnée simulée présentée comme
        réelle."""
        client = _authenticated_client(active_user)
        content = client.get(SHELL_DEMO_URL).content.decode()

        notifications_block = content.split('<details class="corrux-notifications">')[
            1
        ].split("</details>")[0]
        assert "corrux-badge" not in notifications_block

    def test_disclosure_is_native_details_summary_no_javascript(self, active_user):
        """Le mécanisme Notifications lui-même n'utilise aucun JavaScript
        (toujours vrai). La page peut désormais contenir le script
        générique modal.js (UI-204, décision explicite Option 2) — cette
        vérification porte donc sur le bloc Notifications précisément,
        pas sur la page entière."""
        client = _authenticated_client(active_user)
        content = client.get(SHELL_DEMO_URL).content.decode()

        notifications_block = content.split('<details class="corrux-notifications">')[
            1
        ].split("</details>")[0]
        assert "<summary" in notifications_block
        assert "<script" not in notifications_block

    def test_accessible_name_comes_from_the_icon_label_not_a_redundant_aria(
        self, active_user
    ):
        """Pas d'aria-label dupliqué/contradictoire sur le déclencheur : le
        nom accessible provient du label porté par l'icône elle-même."""
        client = _authenticated_client(active_user)
        content = client.get(SHELL_DEMO_URL).content.decode()

        trigger_block = content.split('class="corrux-notifications__trigger"')[1].split(
            "</summary>"
        )[0]
        assert 'aria-label="Notifications"' in trigger_block
        # Un seul aria-label dans le déclencheur (porté par l'icône), pas
        # un second directement sur <summary>.
        assert trigger_block.count("aria-label=") == 1

    def test_notifications_trigger_is_keyboard_focusable(self, active_user):
        """<summary> est nativement focusable/opérable au clavier (pas de
        tabindex ni de rôle personnalisé nécessaire — sémantique native)."""
        client = _authenticated_client(active_user)
        content = client.get(SHELL_DEMO_URL).content.decode()
        assert "<summary" in content
        assert 'tabindex="-1"' not in content

    def test_no_django_comment_leaks_into_topbar_output(self, active_user):
        client = _authenticated_client(active_user)
        content = client.get(SHELL_DEMO_URL).content.decode()
        assert "{#" not in content
        assert "{% comment %}" not in content

    def test_anonymous_visitor_sees_no_notifications_chrome(self):
        """Cohérent avec le reste du shell : rien n'est affiché hors
        session authentifiée."""
        client = Client()
        content = client.get(SHELL_DEMO_URL).content.decode()
        assert "corrux-notifications" not in content


class TestNoBackendNotificationInfrastructure:
    """Vérifie explicitement l'absence de toute infrastructure backend de
    notifications — contrainte centrale de ce ticket."""

    def test_no_notifications_app_or_module_exists(self):
        import importlib.util

        assert importlib.util.find_spec("core.notifications") is None

    @pytest.mark.django_db
    def test_no_notification_model_is_registered(self):
        from django.apps import apps

        model_names = {m.__name__.lower() for m in apps.get_models()}
        assert "notification" not in model_names
