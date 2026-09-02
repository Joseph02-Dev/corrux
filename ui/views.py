"""Vues de référence de la couche présentation — UI-101, UI-102, UI-103,
UI-201.

Pages de documentation vivante (UI-101/102) et écrans réels (UI-103,
UI-201). Aucune logique métier propre : réutilise directement
core.identity/core.authz (TECH-002/003/008) comme unique autorité.
"""

import secrets
from pathlib import PurePosixPath

from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from core.audit.service import record_audit_event
from core.authz.decorators import require_permission
from core.authz.engine import has_permission
from core.authz.models import Role, UserRole
from core.backup.models import BackupRun
from core.identity import auth
from core.identity.models import User
from core.modules.manager import (
    ActiveDependentError,
    DependencyError,
    activate_module,
    deactivate_module,
)
from core.modules.models import Module
from ui.templatetags import ui_tags

# Provisoire : aucune page d'accueil/tableau de bord réelle n'existe
# encore (seuls UI-101/UI-102 fournissent des pages, toutes deux des
# démonstrations, pas des écrans produit). Signalé comme limite connue,
# pas inventé comme définitif — à remplacer dès qu'un vrai point
# d'entrée post-connexion existera.
DEFAULT_LOGIN_REDIRECT = "/shell-demo/"


def design_system_showcase(request):
    return render(request, "ui/showcase.html")


def shell_showcase(request):
    """Démonstration du shell (UI-102).

    « Login est hors shell » (ux-ui-design-v1.md §2) : un utilisateur
    anonyme ne voit jamais le shell — page simple sans Topbar/Sidebar.
    """
    if request.corrux_user is None:
        return render(request, "ui/shell_showcase_anonymous.html")
    return render(request, "ui/shell_showcase.html")


def _safe_redirect_target(request, candidate: str) -> str:
    """Ne redirige vers `candidate` que s'il s'agit d'une URL relative au
    même site (protection open redirect) — utilise le helper Django
    standard, pas une vérification maison."""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return DEFAULT_LOGIN_REDIRECT


def login_page(request):
    """Écran de connexion — UI-103.

    Réutilise directement core.identity.auth.authenticate()/login()
    (TECH-002) : même backend, même instrumentation d'audit (TECH-008,
    déjà intégrée à ces fonctions), aucune seconde logique
    d'authentification créée. Ne réutilise PAS core.identity.views.LoginView
    (API JSON, TECH-002) : cette vue rend/redirige nativement en HTML,
    ce que l'API JSON ne fait pas — TECH-002 n'est pas modifié pour autant.

    Message d'erreur strictement identique quelle que soit la cause
    (identifiant inconnu / mot de passe incorrect / compte verrouillé /
    compte inactif) — propriété de sécurité de TECH-002, préservée à
    l'identique ici, jamais affinée côté UI.
    """
    next_param = request.POST.get("next") or request.GET.get("next", "")

    if request.corrux_user is not None:
        return HttpResponseRedirect(_safe_redirect_target(request, next_param))

    error = ""
    submitted_username = ""

    if request.method == "POST":
        submitted_username = request.POST.get("username", "")
        password = request.POST.get("password", "")

        if not submitted_username or not password:
            error = auth.GENERIC_ERROR_MESSAGE
        else:
            try:
                user = auth.authenticate(username=submitted_username, raw_password=password)
            except auth.AuthenticationError:
                error = auth.GENERIC_ERROR_MESSAGE
            else:
                auth.login(request, user)
                return HttpResponseRedirect(_safe_redirect_target(request, next_param))

    return render(
        request,
        "ui/login.html",
        {"error": error, "username": submitted_username, "next": next_param},
    )


def profile_page(request):
    """Mon profil (UI-105) — infos du compte connecté, lecture seule.

    Première page de contenu réel utilisant le shell (shell/base.html) :
    anonyme -> redirection sûre vers /login/?next=/profil/ (réutilise le
    mécanisme construit et testé en UI-103, jamais exercé de bout en bout
    jusqu'ici). N'affiche que des champs déjà existants sur User
    (TECH-001) — aucun champ inventé.
    """
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        profile_url = reverse("ui-profile")
        return HttpResponseRedirect(f"{login_url}?next={profile_url}")

    user = request.corrux_user
    status_tone = "success" if user.status == User.Status.ACTIVE else "neutral"
    return render(request, "ui/profile.html", {"status_tone": status_tone})


@require_POST
def logout_action(request):
    """Déconnexion déclenchée depuis le menu utilisateur (UI-105).

    Réutilise directement core.identity.auth.logout() (TECH-002/TECH-008
    — même backend, même audit, aucune seconde logique) mais redirige
    vers /login/ plutôt que de renvoyer du JSON. core.identity.views.
    LogoutView (API JSON) n'est pas modifiée : elle reste le point
    d'entrée pour d'autres clients. Critère d'acceptation explicite
    UI-105 : « déconnexion effective, redirection vers Login ».
    """
    auth.logout(request)
    return HttpResponseRedirect(reverse("ui-login"))


# ============================================================================
# UI-201 — Utilisateurs & rôles
# ============================================================================
#
# Aucune seconde logique RBAC : has_permission()/require_permission()
# (TECH-003) sont l'unique autorité, jamais recalculée ni contournée.
# Le mot de passe temporaire n'est jamais journalisé, jamais stocké en
# clair, jamais renvoyé au-delà de la réponse immédiate qui le révèle.

# --- Helpers de rendu de composants (réutilise le contexte des inclusion
# tags existantes — pas de duplication de leurs valeurs par défaut) ------


def _component_html(template_name, tag_func, **kwargs):
    """Rend un composant existant hors du cycle {% load %}, en réutilisant
    exactement la même construction de contexte que l'inclusion tag
    (source unique) — pour l'assembler dans un fragment de confiance
    (ex. contenu d'un Drawer, cellule Actions d'une Table)."""
    return render_to_string(template_name, tag_func(**kwargs))


def _field_html(**kwargs):
    return _component_html("ui/components/field.html", ui_tags.corrux_field, **kwargs)


def _modal_trigger_html(**kwargs):
    return _component_html(
        "ui/components/modal_trigger.html", ui_tags.corrux_modal_trigger, **kwargs
    )


# --- Mot de passe temporaire -------------------------------------------


def _generate_temporary_password() -> str:
    """Mot de passe temporaire cryptographiquement sûr — `secrets`
    (jamais `random`), jamais une seconde primitive cryptographique.
    Aucune contrainte de complexité/longueur n'est codée ailleurs dans le
    projet (vérifié explicitement en Phase 2) : 12 octets, ~16
    caractères URL-safe, ~96 bits d'entropie."""
    return secrets.token_urlsafe(12)


# --- Rôles ---------------------------------------------------------------


def _role_select_options(include_empty=True):
    options = [(str(role.id), role.name) for role in Role.objects.order_by("name")]
    if include_empty:
        options = [("", "— Sélectionner —")] + options
    return options


def _user_primary_role(user):
    """Rôle unique géré par ce Drawer — le modèle autorise plusieurs
    rôles par utilisateur (UserRole), mais le champ Select de la
    maquette (Lot 2, §2-3) est singulier. Décision documentée en
    Phase 2 : ce Drawer gère l'assignation d'un seul rôle."""
    user_role = user.user_roles.select_related("role").first()
    return user_role.role if user_role else None


def _set_user_primary_role(user, role):
    UserRole.objects.filter(user=user).delete()
    if role is not None:
        UserRole.objects.create(user=user, role=role)


# --- Construction du contenu des Drawers (fragments de confiance) --------


def _create_drawer_content(errors=None, values=None):
    errors = errors or {}
    values = values or {}
    return "".join(
        [
            _field_html(
                label="Nom complet",
                name="full_name",
                field_id="id_create_full_name",
                value=values.get("full_name", ""),
                required=True,
                error=errors.get("full_name", ""),
            ),
            _field_html(
                label="Identifiant",
                name="username",
                field_id="id_create_username",
                value=values.get("username", ""),
                required=True,
                autocomplete="off",
                error=errors.get("username", ""),
            ),
            _field_html(
                label="Rôle",
                name="role",
                field_id="id_create_role",
                input_type="select",
                options=_role_select_options(),
                value=values.get("role_id", ""),
                required=True,
                error=errors.get("role", ""),
                help_text="Un mot de passe temporaire sera généré automatiquement."
                if not errors.get("role")
                else "",
            ),
        ]
    )


def _edit_drawer_content(user, errors=None, values=None):
    errors = errors or {}
    values = values or {}
    current_role = _user_primary_role(user)
    role_html = _field_html(
        label="Rôle",
        name="role",
        field_id=f"id_edit_{user.id}_role",
        input_type="select",
        options=_role_select_options(),
        value=values.get("role_id", str(current_role.id) if current_role else ""),
        error=errors.get("role", ""),
    )
    fields_html = "".join(
        [
            _field_html(
                label="Nom complet",
                name="full_name",
                field_id=f"id_edit_{user.id}_full_name",
                value=values.get("full_name", user.full_name),
                required=True,
                error=errors.get("full_name", ""),
            ),
            _field_html(
                label="Identifiant",
                name="username",
                field_id=f"id_edit_{user.id}_username",
                value=values.get("username", user.username),
                required=True,
                autocomplete="off",
                error=errors.get("username", ""),
            ),
            role_html,
        ]
    )

    reset_url = reverse("ui-user-reset-password", args=[user.id])
    reset_button = _component_html(
        "ui/components/button.html",
        ui_tags.corrux_button,
        label="Réinitialiser le mot de passe",
        variant="secondary",
        type="submit",
        formaction=reset_url,
    )

    danger_zone = ""
    if user.status == User.Status.ACTIVE:
        danger_zone = (
            '<div class="corrux-drawer__danger-zone">'
            + _modal_trigger_html(
                modal_id=f"deactivate-user-{user.id}",
                label="Désactiver ce compte",
                variant="danger",
            )
            + "</div>"
        )

    return fields_html + reset_button + danger_zone


# --- Assemblage de la page liste (réutilisé par GET normal et par les
# ré-affichages en erreur après une soumission invalide) -------------------


def _user_table_rows(users):
    rows = []
    for user in users:
        role = _user_primary_role(user)
        actions = _modal_trigger_html(
            modal_id=f"edit-user-{user.id}", label="Modifier", variant="secondary"
        )
        if user.status == User.Status.ACTIVE:
            actions += _modal_trigger_html(
                modal_id=f"deactivate-user-{user.id}", label="Désactiver", variant="danger"
            )
        rows.append(
            ui_tags.TableRow(
                cells=(
                    user.full_name,
                    user.username,
                    role.name if role else "—",
                    user.get_status_display(),
                ),
                actions_html=actions,
            )
        )
    return rows


def _render_user_list_page(
    request,
    *,
    open_drawer_id="",
    create_errors=None,
    create_values=None,
    edit_user_id=None,
    edit_errors=None,
    edit_values=None,
    http_status=200,
):
    search = request.GET.get("q", "").strip()
    users = User.objects.all().order_by("full_name")
    if search:
        users = users.filter(full_name__icontains=search) | users.filter(
            username__icontains=search
        )
    users = list(users)

    can_write = has_permission(request.corrux_user, "core.user.write")

    deactivate_modals_html = "".join(
        _component_html(
            "ui/components/modal.html",
            ui_tags.corrux_modal,
            modal_id=f"deactivate-user-{u.id}",
            title="Désactiver ce compte",
            message=(
                f"« {u.full_name} » ne pourra plus se connecter. Ses données et son "
                "historique sont conservés et la désactivation peut être annulée en "
                "réactivant le compte ultérieurement."
            ),
            confirm_label="Désactiver",
            action=reverse("ui-user-deactivate", args=[u.id]),
        )
        for u in users
        if u.status == User.Status.ACTIVE
    )

    edit_drawers_html = "".join(
        _component_html(
            "ui/components/drawer.html",
            ui_tags.corrux_drawer,
            drawer_id=f"edit-user-{u.id}",
            title=f"Modifier « {u.full_name} »",
            action=reverse("ui-user-edit", args=[u.id]),
            content=_edit_drawer_content(
                u,
                errors=(edit_errors if edit_user_id == u.id else None),
                values=(edit_values if edit_user_id == u.id else None),
            ),
            open=(open_drawer_id == f"edit-user-{u.id}"),
        )
        for u in users
    )

    context = {
        "can_write": can_write,
        "table_headers": ["Nom", "Identifiant", "Rôle", "Statut", "Actions"],
        "table_rows": _user_table_rows(users) if users else [],
        "users_empty": not users,
        "search": search,
        "user_count": len(users),
        "create_drawer_content": _create_drawer_content(create_errors, create_values),
        "create_url": reverse("ui-user-create"),
        "create_drawer_open": open_drawer_id == "create-user",
        "edit_drawers_html": edit_drawers_html,
        "deactivate_modals_html": deactivate_modals_html,
        "open_drawer_id": open_drawer_id,
    }
    return render(request, "ui/users/list.html", context, status=http_status)


# --- Vues ------------------------------------------------------------------


def user_list(request):
    """Liste des utilisateurs — UI-201.

    Permission de lecture vérifiée manuellement (pas via le décorateur
    require_permission, qui renvoie du JSON) : cette vue est le point de
    navigation principal de l'écran, elle doit afficher
    corrux_permission_denied (UI-104) plutôt qu'un 403 JSON brut — même
    autorité (has_permission, TECH-003), traitement de réponse différent.
    """
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={reverse('ui-user-list')}")

    if not has_permission(request.corrux_user, "core.user.read"):
        return render(request, "ui/users/list.html", {"permission_denied": True}, status=403)

    return _render_user_list_page(request)


@require_permission("core.user.write")
@require_POST
def user_create(request):
    full_name = request.POST.get("full_name", "").strip()
    username = request.POST.get("username", "").strip()
    role_id = request.POST.get("role", "").strip()

    errors = {}
    if not full_name:
        errors["full_name"] = "Ce champ est requis."
    if not username:
        errors["username"] = "Ce champ est requis."

    role = None
    if not role_id:
        errors["role"] = "Ce champ est requis."
    else:
        role = Role.objects.filter(pk=role_id).first()
        if role is None:
            errors["role"] = "Rôle invalide."

    if not errors and User.objects.filter(username=username).exists():
        errors["username"] = "Cet identifiant est déjà utilisé."

    values = {"full_name": full_name, "username": username, "role_id": role_id}
    if errors:
        return _render_user_list_page(
            request,
            open_drawer_id="create-user",
            create_errors=errors,
            create_values=values,
            http_status=400,
        )

    temporary_password = _generate_temporary_password()
    user = User(username=username, full_name=full_name)
    auth.set_user_password(user, temporary_password)

    try:
        with transaction.atomic():
            user.save()
            UserRole.objects.create(user=user, role=role)
    except IntegrityError:
        return _render_user_list_page(
            request,
            open_drawer_id="create-user",
            create_errors={"username": "Cet identifiant est déjà utilisé."},
            create_values=values,
            http_status=400,
        )

    # Le secret n'apparaît jamais dans les métadonnées d'audit.
    record_audit_event(
        actor=request.corrux_user,
        action="user.create",
        target=username,
        metadata={"role": role.name},
    )

    return render(
        request,
        "ui/users/secret_reveal.html",
        {
            "heading": "Compte créé",
            "intro": f"Le compte « {username} » a été créé.",
            "username": username,
            "temporary_password": temporary_password,
        },
    )


@require_permission("core.user.write")
def user_edit(request, user_id):
    user = get_object_or_404(User, pk=user_id)

    if request.method != "POST":
        return _render_user_list_page(request, open_drawer_id=f"edit-user-{user.id}")

    full_name = request.POST.get("full_name", "").strip()
    username = request.POST.get("username", "").strip()
    role_id = request.POST.get("role", "").strip()

    errors = {}
    if not full_name:
        errors["full_name"] = "Ce champ est requis."
    if not username:
        errors["username"] = "Ce champ est requis."

    role = None
    if role_id:
        role = Role.objects.filter(pk=role_id).first()
        if role is None:
            errors["role"] = "Rôle invalide."

    if not errors and User.objects.filter(username=username).exclude(pk=user.id).exists():
        errors["username"] = "Cet identifiant est déjà utilisé."

    values = {"full_name": full_name, "username": username, "role_id": role_id}
    if errors:
        return _render_user_list_page(
            request,
            open_drawer_id=f"edit-user-{user.id}",
            edit_user_id=user.id,
            edit_errors=errors,
            edit_values=values,
            http_status=400,
        )

    try:
        with transaction.atomic():
            user.full_name = full_name
            user.username = username
            user.save(update_fields=["full_name", "username"])
            _set_user_primary_role(user, role)
    except IntegrityError:
        return _render_user_list_page(
            request,
            open_drawer_id=f"edit-user-{user.id}",
            edit_user_id=user.id,
            edit_errors={"username": "Cet identifiant est déjà utilisé."},
            edit_values=values,
            http_status=400,
        )

    record_audit_event(
        actor=request.corrux_user,
        action="user.update",
        target=username,
        metadata={"role": role.name if role else None},
    )
    return HttpResponseRedirect(reverse("ui-user-list"))


@require_permission("core.user.write")
@require_POST
def user_reset_password(request, user_id):
    user = get_object_or_404(User, pk=user_id)

    temporary_password = _generate_temporary_password()
    auth.set_user_password(user, temporary_password)
    # Une réinitialisation lève aussi un éventuel verrouillage anti-
    # bruteforce hérité (même logique qu'un login réussi, TECH-002) :
    # un nouveau mot de passe ne doit pas hériter d'un ancien verrouillage.
    user.failed_login_attempts = 0
    user.locked_until = None
    user.save(update_fields=["password_hash", "failed_login_attempts", "locked_until"])

    record_audit_event(
        actor=request.corrux_user,
        action="user.password_reset",
        target=user.username,
        metadata={},
    )

    return render(
        request,
        "ui/users/secret_reveal.html",
        {
            "heading": "Mot de passe réinitialisé",
            "intro": f"Un nouveau mot de passe a été généré pour « {user.username} ».",
            "username": user.username,
            "temporary_password": temporary_password,
        },
    )


@require_permission("core.user.write")
@require_POST
def user_deactivate(request, user_id):
    """Désactivation — jamais de suppression (User, UserRole et données
    associées restent intacts, seul `status` change)."""
    user = get_object_or_404(User, pk=user_id)
    user.status = User.Status.INACTIVE
    user.save(update_fields=["status"])

    record_audit_event(
        actor=request.corrux_user,
        action="user.deactivate",
        target=user.username,
        metadata={},
    )
    return HttpResponseRedirect(reverse("ui-user-list"))


# ============================================================================
# UI-203 — Modules
# ============================================================================
#
# activate_module()/deactivate_module() (TECH-006/007) restent les
# seules autorités métier : cette vue ne recalcule ni le graphe de
# dépendances, ni les conditions de transition d'état — elle appelle ces
# fonctions telles quelles et se contente de mettre en forme leurs
# résultats/exceptions. Les deux fonctions journalisent déjà elles-mêmes
# (succès ET refus, TECH-008) : aucun second record_audit_event ici.

_MODULE_STATE_TONE = {
    Module.State.ACTIVATED: "success",
    Module.State.DEACTIVATED: "neutral",
    Module.State.INSTALLED: "info",
}


def _module_table_rows(modules, can_write):
    rows = []
    for module in modules:
        status_html = _component_html(
            "ui/components/badge.html",
            ui_tags.corrux_badge,
            label=module.get_state_display(),
            tone=_MODULE_STATE_TONE.get(module.state, "neutral"),
        )
        actions_html = ""
        if can_write:
            if module.state == Module.State.ACTIVATED:
                actions_html = _modal_trigger_html(
                    modal_id=f"deactivate-module-{module.id}",
                    label="Désactiver",
                    variant="danger",
                )
            else:
                actions_html = _component_html(
                    "ui/components/button.html",
                    ui_tags.corrux_button,
                    label="Activer",
                    variant="primary",
                    type="submit",
                    formaction=reverse("ui-module-activate", args=[module.id]),
                )
        rows.append(
            ui_tags.TableRow(
                cells=(module.name, module.version, status_html),
                actions_html=actions_html,
            )
        )
    return rows


def _render_module_list_page(
    request,
    *,
    activation_error="",
    blocked_module_id=None,
    blocked_dependents=None,
    http_status=200,
):
    modules = list(Module.objects.order_by("name"))
    can_write = has_permission(request.corrux_user, "core.module.write")

    table_headers = ["Nom", "Version", "Statut"]
    if can_write:
        table_headers.append("Actions")

    deactivate_confirm_modals_html = "".join(
        _component_html(
            "ui/components/modal.html",
            ui_tags.corrux_modal,
            modal_id=f"deactivate-module-{m.id}",
            title=f"Désactiver « {m.name} »",
            message=(
                f"Le module « {m.name} » sera désactivé. Ses données et permissions "
                "restent intactes et il pourra être réactivé ultérieurement."
            ),
            confirm_label="Désactiver",
            action=reverse("ui-module-deactivate", args=[m.id]),
        )
        for m in modules
        if can_write and m.state == Module.State.ACTIVATED
    )

    info_modal_html = ""
    if blocked_module_id:
        blocked_module = next((m for m in modules if m.id == blocked_module_id), None)
        if blocked_module is not None:
            info_modal_html = _component_html(
                "ui/components/info_modal.html",
                ui_tags.corrux_info_modal,
                modal_id=f"blocked-deactivate-{blocked_module.id}",
                title=f"Désactivation de « {blocked_module.name} » impossible",
                message=(
                    "Les modules suivants en dépendent et sont actuellement actifs :"
                ),
                items=blocked_dependents or [],
                open=True,
            )

    context = {
        "can_write": can_write,
        "table_headers": table_headers,
        "table_rows": _module_table_rows(modules, can_write) if modules else [],
        "modules_empty": not modules,
        "activation_error": activation_error,
        "deactivate_confirm_modals_html": deactivate_confirm_modals_html,
        "info_modal_html": info_modal_html,
    }
    return render(request, "ui/modules/list.html", context, status=http_status)


def module_list(request):
    """Catalogue des modules — UI-203.

    Permission de lecture vérifiée manuellement (comme user_list,
    UI-201) : point de navigation principal, doit afficher
    corrux_permission_denied (UI-104) plutôt qu'un 403 JSON brut.
    """
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={reverse('ui-module-list')}")

    if not has_permission(request.corrux_user, "core.module.read"):
        return render(
            request, "ui/modules/list.html", {"permission_denied": True}, status=403
        )

    return _render_module_list_page(request)


@require_permission("core.module.write")
@require_POST
def module_activate(request, module_id):
    """Active un module — délègue entièrement à activate_module()
    (TECH-006), seule autorité. Aucune vérification de dépendance
    recalculée ici : le message affiché en cas de refus provient
    exclusivement de DependencyError.unsatisfied."""
    get_object_or_404(Module, pk=module_id)

    try:
        activate_module(module_id, actor=request.corrux_user)
    except DependencyError as exc:
        details = "; ".join(f"{d.module} ({d.reason})" for d in exc.unsatisfied)
        return _render_module_list_page(
            request,
            activation_error=(
                f"Activation de « {module_id} » refusée : "
                f"dépendance(s) non satisfaite(s) — {details}."
            ),
            http_status=400,
        )

    return HttpResponseRedirect(reverse("ui-module-list"))


@require_permission("core.module.write")
@require_POST
def module_deactivate(request, module_id):
    """Désactive un module — délègue entièrement à deactivate_module()
    (TECH-006/007), seule autorité. Si refusée (dépendant actif), affiche
    le Modal d'information (UI-203) listant exactement
    ActiveDependentError.active_dependents — aucun recalcul du graphe de
    dépendances côté UI."""
    get_object_or_404(Module, pk=module_id)

    try:
        deactivate_module(module_id, actor=request.corrux_user)
    except ActiveDependentError as exc:
        return _render_module_list_page(
            request,
            blocked_module_id=module_id,
            blocked_dependents=exc.active_dependents,
            http_status=400,
        )

    return HttpResponseRedirect(reverse("ui-module-list"))


# ============================================================================
# UI-205 — Sauvegardes
# ============================================================================
#
# Écran strictement en lecture : aucune mutation, aucun POST, aucun
# record_audit_event() ici. run_backup() (TECH-009) reste l'unique
# primitive créant un BackupRun — déclenchée exclusivement par
# corrux-backup.service (systemd), jamais depuis cette vue. Cette vue ne
# fait que lire core.backup_runs et mettre en forme les champs réels.

_BACKUP_STATUS_TONE = {
    BackupRun.Status.SUCCESS: "success",
    BackupRun.Status.FAILURE: "error",
    BackupRun.Status.REFUSED: "warning",
}


def _format_backup_datetime(value):
    """Même mécanisme que le filtre de template |date (déjà utilisé en
    UI-105, ui/templates/ui/profile.html) : conversion vers le fuseau
    local configuré (Europe/Paris, USE_TZ=True) puis format d/m/Y H:i."""
    return date_format(timezone.localtime(value), "d/m/Y H:i")


def _format_backup_duration(started_at, finished_at):
    total_seconds = int((finished_at - started_at).total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {seconds:02d} s"
    return f"{seconds} s"


def _format_backup_size(size_bytes):
    """`size_bytes` absent (échec/refus, cf. modèle) -> valeur neutre.
    Aucune autre donnée que le champ réel, seule l'unité affichée varie."""
    if size_bytes is None:
        return "—"
    value = float(size_bytes)
    units = ("o", "Ko", "Mo", "Go", "To")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{int(value)} {unit}" if unit == "o" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {units[-1]}"  # pragma: no cover


def _backup_destination_dir(run):
    """Répertoire parent de `location` — affichage uniquement, jamais
    utilisé pour un accès filesystem (cf. contrat UI-205 §11)."""
    if not run or not run.location:
        return "—"
    return str(PurePosixPath(run.location).parent)


def _backup_status_badge_html(run):
    return _component_html(
        "ui/components/badge.html",
        ui_tags.corrux_badge,
        label=run.get_status_display(),
        tone=_BACKUP_STATUS_TONE.get(run.status, "neutral"),
    )


@require_GET
def backup_list(request):
    """Historique des sauvegardes — UI-205.

    @require_GET : écran strictement en lecture, aucune mutation
    possible — une tentative POST doit être rejetée (405), conformément
    au contrat. Permission de lecture vérifiée manuellement (comme
    user_list/module_list) pour afficher corrux_permission_denied
    (UI-104) plutôt qu'un 403 JSON brut sur ce point d'entrée principal.
    """
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={reverse('ui-backup-list')}")

    if not has_permission(request.corrux_user, "core.backup.read"):
        return render(
            request, "ui/backups/list.html", {"permission_denied": True}, status=403
        )

    # Meta.ordering = ["-started_at"] (core/backup/models.py) : déjà
    # trié du plus récent au plus ancien, aucun tri parallèle nécessaire.
    runs = list(BackupRun.objects.all())
    last_success = next((r for r in runs if r.status == BackupRun.Status.SUCCESS), None)
    last_run = runs[0] if runs else None

    last_success_value = (
        _format_backup_datetime(last_success.started_at)
        if last_success is not None
        else "Aucun succès enregistré"
    )
    status_value = _backup_status_badge_html(last_run) if last_run is not None else "—"
    destination_value = _backup_destination_dir(last_success)

    table_rows = [
        ui_tags.TableRow(
            cells=(
                _format_backup_datetime(run.started_at),
                _format_backup_duration(run.started_at, run.finished_at),
                _backup_status_badge_html(run),
                _format_backup_size(run.size_bytes),
                _backup_destination_dir(run),
            ),
        )
        for run in runs
    ]

    context = {
        "runs_empty": not runs,
        "last_success_value": last_success_value,
        "status_value": status_value,
        "destination_value": destination_value,
        "table_headers": ["Date/heure", "Durée", "Statut", "Taille", "Destination"],
        "table_rows": table_rows,
    }
    return render(request, "ui/backups/list.html", context)
