"""Tests du composant Modal de confirmation destructive — UI-204.

Rendu réel (patron établi UI-101 à UI-106). Couvre précisément les
sections A à H du mandat UI-204 : API/rendu, formulaire, accessibilité,
sécurité/échappement, trigger, JavaScript, icône, non-régression.
"""

from pathlib import Path

import pytest
from django.template import engines
from django.template.loader import render_to_string

JS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "ui" / "static" / "ui" / "js" / "modal.js"
)


def _render(snippet: str, context: dict | None = None) -> str:
    template = engines["django"].from_string("{% load ui_tags %}" + snippet)
    return template.render(context or {})


MODAL_A = (
    '{% corrux_modal modal_id="deactivate-module-documentation" '
    'title="Désactiver le module Documentation" '
    'message="Les données seront conservées et pourront être restaurées." '
    'confirm_label="Désactiver" '
    'action="/modules/documentation/deactivate/" %}'
)

MODAL_B = (
    '{% corrux_modal modal_id="deactivate-user-42" '
    'title="Désactiver ce compte" '
    'message="L\'utilisateur ne pourra plus se connecter." '
    'confirm_label="Désactiver le compte" '
    'action="/users/42/deactivate/" '
    'cancel_label="Ne pas désactiver" %}'
)


# --- A. API / rendu ---------------------------------------------------------


class TestModalRendering:
    def test_modal_a_renders_title_and_message(self):
        html = _render(MODAL_A)
        assert "Désactiver le module Documentation" in html
        assert "Les données seront conservées" in html

    def test_modal_b_renders_title_and_message(self):
        html = _render(MODAL_B)
        assert "Désactiver ce compte" in html
        assert "ne pourra plus se connecter" in html

    def test_two_prop_sets_produce_distinct_rendering(self):
        html_a = _render(MODAL_A)
        html_b = _render(MODAL_B)
        assert html_a != html_b
        assert "deactivate-module-documentation" in html_a
        assert "deactivate-user-42" in html_b
        assert "deactivate-user-42" not in html_a
        assert "deactivate-module-documentation" not in html_b

    def test_both_instances_use_the_same_template(self):
        """Aucune duplication : même fichier modal.html pour les deux
        usages (vérifié par la présence de la classe .corrux-modal
        identique dans les deux rendus)."""
        assert "corrux-modal" in _render(MODAL_A)
        assert "corrux-modal" in _render(MODAL_B)

    def test_cancel_button_present_with_default_label(self):
        html = _render(MODAL_A)
        assert "Annuler" in html

    def test_cancel_button_label_can_be_customized(self):
        html = _render(MODAL_B)
        assert "Ne pas désactiver" in html

    def test_confirm_button_present_with_correct_label(self):
        html = _render(MODAL_A)
        assert "Désactiver" in html

    def test_no_django_comment_leaks_into_output(self):
        html = _render(MODAL_A)
        assert "{#" not in html
        assert "{% comment %}" not in html


# --- B. Formulaire -----------------------------------------------------------


class TestModalForm:
    def test_confirm_form_uses_post_method(self):
        html = _render(MODAL_A)
        assert 'method="post"' in html

    def test_confirm_form_action_matches_provided_url(self):
        html = _render(MODAL_A)
        assert 'action="/modules/documentation/deactivate/"' in html

    def test_csrf_token_is_present_in_confirm_form(self, rf):
        """{% csrf_token %} ne produit un jeton réel que dans un contexte
        de requête (comme en HTTP réel) — un rendu isolé sans requête
        associée le laisse vide par conception Django, pas un défaut du
        composant. Un vrai objet request est donc fourni ici."""
        request = rf.get("/")
        html = render_to_string(
            "ui/components/modal.html",
            {
                "modal_id": "deactivate-module-documentation",
                "title": "t", "message": "m", "confirm_label": "c",
                "action": "/modules/documentation/deactivate/",
                "cancel_label": "Annuler",
            },
            request=request,
        )
        assert "csrfmiddlewaretoken" in html

    def test_cancel_form_uses_native_dialog_method_not_a_network_request(self):
        html = _render(MODAL_A)
        assert 'method="dialog"' in html

    def test_no_get_method_is_ever_used_for_the_destructive_action(self):
        html = _render(MODAL_A)
        assert 'method="get"' not in html.lower()


# --- C. Accessibilité ---------------------------------------------------------


class TestModalAccessibility:
    def test_dialog_element_is_used(self):
        html = _render(MODAL_A)
        assert "<dialog" in html

    def test_role_dialog_is_present(self):
        html = _render(MODAL_A)
        assert 'role="dialog"' in html

    def test_aria_labelledby_points_to_real_title_id(self):
        html = _render(MODAL_A)
        assert 'aria-labelledby="deactivate-module-documentation-title"' in html
        assert 'id="deactivate-module-documentation-title"' in html

    def test_aria_describedby_points_to_real_message_id(self):
        html = _render(MODAL_A)
        assert 'aria-describedby="deactivate-module-documentation-message"' in html
        assert 'id="deactivate-module-documentation-message"' in html

    def test_no_manual_aria_modal_attribute_added(self):
        """Le contrat interdit explicitement de réimplémenter aria-modal
        manuellement — natif à <dialog> via showModal()."""
        html = _render(MODAL_A)
        assert "aria-modal" not in html

    def test_trash_icon_is_decorative_not_labelled(self):
        """Icône accompagnant un titre déjà explicite : décorative."""
        html = _render(MODAL_A)
        assert 'aria-hidden="true"' in html


# --- D. Sécurité / échappement ------------------------------------------------


class TestModalEscaping:
    XSS_PAYLOADS = ["<script>alert(1)</script>", '<img src=x onerror=alert(1)>']

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_title_is_escaped(self, payload):
        html = render_to_string(
            "ui/components/modal.html",
            {
                "modal_id": "x", "title": payload, "message": "m",
                "confirm_label": "c", "action": "/x/", "cancel_label": "Annuler",
            },
        )
        assert payload not in html

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_message_is_escaped(self, payload):
        html = render_to_string(
            "ui/components/modal.html",
            {
                "modal_id": "x", "title": "t", "message": payload,
                "confirm_label": "c", "action": "/x/", "cancel_label": "Annuler",
            },
        )
        assert payload not in html

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_confirm_label_is_escaped(self, payload):
        html = render_to_string(
            "ui/components/modal.html",
            {
                "modal_id": "x", "title": "t", "message": "m",
                "confirm_label": payload, "action": "/x/", "cancel_label": "Annuler",
            },
        )
        assert payload not in html

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_cancel_label_is_escaped(self, payload):
        html = render_to_string(
            "ui/components/modal.html",
            {
                "modal_id": "x", "title": "t", "message": "m",
                "confirm_label": "c", "action": "/x/", "cancel_label": payload,
            },
        )
        assert payload not in html

    def test_no_safe_filter_used_in_modal_template(self):
        source = Path(
            "ui/templates/ui/components/modal.html"
        ).read_text(encoding="utf-8")
        assert "|safe" not in source


# --- E. Trigger ----------------------------------------------------------------


class TestModalTrigger:
    def test_trigger_renders_label(self):
        html = _render('{% corrux_modal_trigger modal_id="m1" label="Désactiver" %}')
        assert "Désactiver" in html

    def test_trigger_is_correctly_linked_to_its_modal_via_data_attribute(self):
        html = _render('{% corrux_modal_trigger modal_id="deactivate-module-rh" label="X" %}')
        assert 'data-modal-target="deactivate-module-rh"' in html

    def test_trigger_default_variant_is_secondary(self):
        html = _render('{% corrux_modal_trigger modal_id="m1" label="X" %}')
        assert "corrux-button--secondary" in html

    def test_trigger_explicit_variant_is_respected(self):
        html = _render(
            '{% corrux_modal_trigger modal_id="m1" label="X" variant="danger" %}'
        )
        assert "corrux-button--danger" in html

    def test_trigger_invalid_variant_raises_explicitly(self):
        with pytest.raises(ValueError):
            _render('{% corrux_modal_trigger modal_id="m1" label="X" variant="bogus" %}')

    def test_trigger_is_a_plain_button_not_a_form_submission(self):
        html = _render('{% corrux_modal_trigger modal_id="m1" label="X" %}')
        assert 'type="button"' in html


# --- F. JavaScript ---------------------------------------------------------------


class TestModalJavaScript:
    def test_modal_js_file_exists(self):
        assert JS_PATH.is_file()

    def test_modal_js_calls_native_show_modal(self):
        content = JS_PATH.read_text(encoding="utf-8")
        assert "showModal" in content

    def test_modal_js_does_not_reimplement_close_manually_via_click_outside(self):
        """Le contrat interdit de réimplémenter manuellement le piège de
        focus / la fermeture Échap : le script ne doit contenir aucune
        gestion de touche clavier ni de calcul de focus."""
        content = JS_PATH.read_text(encoding="utf-8")
        assert "keydown" not in content
        assert "Escape" not in content
        assert "focus(" not in content

    def test_modal_js_contains_no_business_logic(self):
        """Aucune référence à un concept métier (module, utilisateur,
        permission...) dans le script générique."""
        content = JS_PATH.read_text(encoding="utf-8").lower()
        for forbidden in ("permission", "role", "employe", "documentation", "rh_"):
            assert forbidden not in content

    def test_no_inline_script_introduced_in_modal_or_trigger_templates(self):
        for path in (
            "ui/templates/ui/components/modal.html",
            "ui/templates/ui/components/modal_trigger.html",
        ):
            source = Path(path).read_text(encoding="utf-8")
            assert "<script" not in source

    def test_shell_base_loads_the_script_exactly_once_as_external_file(self):
        source = Path("ui/templates/ui/shell/base.html").read_text(encoding="utf-8")
        assert source.count("modal.js") == 1
        assert "<script>" not in source  # jamais de script inline


# --- G. Icône ----------------------------------------------------------------


class TestTrashIcon:
    def test_trash_icon_is_available(self):
        html = _render('{% corrux_icon name="trash" %}')
        assert "<svg" in html

    def test_existing_icons_are_unaffected(self):
        for name in (
            "check", "close", "chevron-down", "search", "user",
            "warning", "folder", "settings", "logout", "bell",
        ):
            html = _render(f'{{% corrux_icon name="{name}" %}}')
            assert "<svg" in html
