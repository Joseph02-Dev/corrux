"""Vues de référence de la couche présentation — UI-101, UI-102, UI-103,
UI-201.

Pages de documentation vivante (UI-101/102) et écrans réels (UI-103,
UI-201). Aucune logique métier propre : réutilise directement
core.identity/core.authz (TECH-002/003/008) comme unique autorité.
"""

import secrets
from datetime import date, datetime
from pathlib import PurePosixPath

from django.db import IntegrityError, transaction
from django.http import HttpResponseForbidden, HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from core.audit.models import AuditLog
from core.audit.service import record_audit_event
from core.authz.decorators import require_permission
from core.authz.engine import has_permission
from core.authz.models import Permission, Role, RolePermission, UserRole
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
from modules.documentation.documents_v1 import DocumentNotAccessibleError, attach
from modules.documentation.models import Document, DocumentPermission, Folder
from modules.documentation.services import (
    MIME_TYPE_BY_EXTENSION,
    DocumentUploadError,
    document_metadata_value,
    folder_breadcrumb,
    grant_permission,
    has_document_permission,
    has_folder_permission,
    list_permissions_for,
    list_visible_folder_contents,
    revoke_permission,
    search_documents,
    update_document,
    upload_document,
)
from modules.rh.models import Contract, Employee
from modules.rh.services import (
    attach_document_to_employee,
    create_contract,
    create_employee,
    deactivate_employee,
    employee_full_name,
    link_document_to_contract,
    list_employee_documents,
    update_contract,
    update_employee,
)
from ui.navigation import module_is_activated
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

    if request.method not in ("GET", "HEAD", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

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


@require_GET
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


@require_GET
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

    if request.method not in ("GET", "HEAD", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

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


@require_GET
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


def _format_datetime(value):
    """Même mécanisme que le filtre de template |date (déjà utilisé en
    UI-105, ui/templates/ui/profile.html) : conversion vers le fuseau
    local configuré (Europe/Paris, USE_TZ=True) puis format d/m/Y H:i.
    Générique — introduit en UI-205, réutilisé tel quel par UI-206
    (Journal d'audit) : aucun nouveau format global créé."""
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


def _format_file_size(size_bytes):
    """`size_bytes` absent (échec/refus de sauvegarde, cf. modèle
    BackupRun) -> valeur neutre. Aucune autre donnée que le champ réel,
    seule l'unité affichée varie. Générique — introduit en UI-205,
    réutilisé tel quel par UI-301 (Document.size_bytes, toujours
    renseigné) : aucun nouveau format créé."""
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
        _format_datetime(last_success.started_at)
        if last_success is not None
        else "Aucun succès enregistré"
    )
    status_value = _backup_status_badge_html(last_run) if last_run is not None else "—"
    destination_value = _backup_destination_dir(last_success)

    table_rows = [
        ui_tags.TableRow(
            cells=(
                _format_datetime(run.started_at),
                _format_backup_duration(run.started_at, run.finished_at),
                _backup_status_badge_html(run),
                _format_file_size(run.size_bytes),
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


# ============================================================================
# UI-206 — Journal d'audit
# ============================================================================
#
# Écran strictement en lecture : aucun POST, aucun record_audit_event()
# ici. AuditLog n'a pas de Meta.ordering (contrairement à BackupRun) :
# tri explicite -timestamp obligatoire. metadata["status"] n'est ni un
# champ structurel ni cohérent entre actions — le mapping ci-dessous est
# une normalisation de présentation uniquement, AuditLog et les
# producteurs d'événements ne sont pas modifiés.

_AUDIT_RESULT_MAP = {
    "success": ("Succès", "success"),
    "failure": ("Échec", "error"),
    "denied": ("Refusé", "warning"),
    "refused": ("Refusé", "warning"),
}
_AUDIT_DEFAULT_RESULT = ("Succès", "success")  # absence de status (ex. user.*)

_AUDIT_ACTOR_SYSTEM_VALUE = "system"


def _audit_result_badge_html(entry):
    status = (entry.metadata or {}).get("status")
    label, tone = _AUDIT_RESULT_MAP.get(status, _AUDIT_DEFAULT_RESULT)
    return _component_html(
        "ui/components/badge.html", ui_tags.corrux_badge, label=label, tone=tone
    )


def _audit_actor_label(entry):
    return "Système" if entry.actor_user is None else entry.actor_user.full_name


def _audit_action_filter_options():
    """Dérivées des actions réellement présentes en base — jamais une
    liste statique. Tri déterministe (alphabétique)."""
    actions = (
        AuditLog.objects.order_by("action").values_list("action", flat=True).distinct()
    )
    return [("", "Toutes")] + [(a, a) for a in actions]


def _audit_actor_filter_options():
    """« Système » (acteur nul, TECH-009) + les utilisateurs réels ayant
    au moins un événement, triés par nom."""
    options = [("", "Tous"), (_AUDIT_ACTOR_SYSTEM_VALUE, "Système")]
    actor_ids = (
        AuditLog.objects.exclude(actor_user__isnull=True)
        .values_list("actor_user_id", flat=True)
        .distinct()
    )
    for user in User.objects.filter(pk__in=actor_ids).order_by("full_name"):
        options.append((str(user.id), user.full_name))
    return options


def _parse_audit_filter_date(value):
    """Une date GET absente ou invalide est ignorée silencieusement
    (filtre non appliqué) — jamais une erreur 500 sur une entrée
    utilisateur, comportement le plus simple et sûr, sans nouveau
    composant de validation."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


@require_GET
def audit_list(request):
    """Journal d'audit — UI-206.

    Permission de lecture vérifiée manuellement (comme user_list/
    module_list/backup_list) pour afficher corrux_permission_denied
    plutôt qu'un 403 JSON sur ce point d'entrée principal.
    @require_GET : aucune mutation possible, une tentative POST doit
    être rejetée (405).
    """
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={reverse('ui-audit-list')}")

    if not has_permission(request.corrux_user, "core.audit.read"):
        return render(
            request, "ui/audit/list.html", {"permission_denied": True}, status=403
        )

    selected_action = request.GET.get("action", "")
    selected_actor = request.GET.get("actor", "")
    from_raw = request.GET.get("from", "")
    to_raw = request.GET.get("to", "")

    entries = AuditLog.objects.select_related("actor_user").order_by("-timestamp")

    if selected_action:
        entries = entries.filter(action=selected_action)

    if selected_actor == _AUDIT_ACTOR_SYSTEM_VALUE:
        entries = entries.filter(actor_user__isnull=True)
    elif selected_actor:
        # Jamais confiance en la valeur brute du client : validée comme
        # un identifiant réel avant d'être utilisée dans la requête ;
        # une valeur invalide est ignorée (filtre non appliqué), jamais
        # transmise telle quelle à l'ORM.
        try:
            actor_id = int(selected_actor)
        except ValueError:
            selected_actor = ""
        else:
            entries = entries.filter(actor_user_id=actor_id)

    from_date = _parse_audit_filter_date(from_raw)
    if from_date is not None:
        entries = entries.filter(timestamp__date__gte=from_date)

    to_date = _parse_audit_filter_date(to_raw)
    if to_date is not None:
        entries = entries.filter(timestamp__date__lte=to_date)

    entries = list(entries)

    table_rows = [
        ui_tags.TableRow(
            cells=(
                _format_datetime(entry.timestamp),
                _audit_actor_label(entry),
                entry.action,
                entry.target,
                _audit_result_badge_html(entry),
            ),
        )
        for entry in entries
    ]

    context = {
        "table_headers": ["Date/heure", "Utilisateur", "Action", "Ressource", "Résultat"],
        "table_rows": table_rows,
        "entries_empty": not entries,
        "action_options": _audit_action_filter_options(),
        "actor_options": _audit_actor_filter_options(),
        "selected_action": selected_action,
        "selected_actor": selected_actor,
        "from_value": from_raw if from_date is not None else "",
        "to_value": to_raw if to_date is not None else "",
    }
    return render(request, "ui/audit/list.html", context)


# ============================================================================
# UI-202 — Rôles : liste + Matrice de permissions
# ============================================================================
#
# « Onglet Rôles » du même écran que UI-201 (Utilisateurs & rôles) — même
# permission de lecture (core.user.read). La matrice avancée est réservée
# à l'Administrateur (core.role.write, décision produit #2 déjà validée) :
# aucun affichage, même en lecture seule, sans cette permission.
#
# Codes de permission canoniques : dérivés des codes réellement utilisés
# dans le code applicatif (ui/navigation.py, décorateurs de vues), jamais
# inventés. Groupés par module pour l'affichage (Administration /
# Documentation / Ressources humaines), conformément à la maquette.

_PREDEFINED_ROLE_NAMES = ("Administrateur", "Administrateur RH", "Valideur", "Employé")

_PERMISSION_MATRIX_GROUPS = (
    (
        "Administration",
        "core",
        (
            ("user", "read", "Utilisateurs — Lecture"),
            ("user", "write", "Utilisateurs — Écriture"),
            ("module", "read", "Modules — Lecture"),
            ("module", "write", "Modules — Écriture"),
            ("backup", "read", "Sauvegardes — Lecture"),
            ("audit", "read", "Journal d'audit — Lecture"),
        ),
    ),
    (
        "Documentation",
        "documentation",
        (("document", "read", "Documents — Lecture"),),
    ),
    (
        "Ressources humaines",
        "rh",
        (
            ("employe", "lire", "Employés — Lecture"),
            ("conge", "lire", "Congés — Lecture"),
        ),
    ),
)


def _predefined_roles_ordered():
    roles_by_name = {r.name: r for r in Role.objects.filter(name__in=_PREDEFINED_ROLE_NAMES)}
    return [roles_by_name[name] for name in _PREDEFINED_ROLE_NAMES if name in roles_by_name]


@require_GET
def role_list(request):
    """Liste des 4 rôles prédéfinis — même permission que UI-201
    (core.user.read), c'est le même écran, un autre onglet. Réutilise
    corrux_table (patron déjà établi par UI-203 pour les « cartes » de
    la maquette), aucun nouveau composant visuel de carte."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={reverse('ui-role-list')}")

    if not has_permission(request.corrux_user, "core.user.read"):
        return render(
            request, "ui/users/roles_list.html", {"permission_denied": True}, status=403
        )

    can_view_matrix = has_permission(request.corrux_user, "core.role.write")
    roles = _predefined_roles_ordered()
    role_table_rows = [
        ui_tags.TableRow(
            cells=(
                role.name,
                role.description or "—",
                str(role.user_roles.count()),
                (
                    _component_html(
                        "ui/components/button.html",
                        ui_tags.corrux_button,
                        label="Voir les permissions",
                        variant="tertiary",
                        href=reverse("ui-role-matrix"),
                    )
                    if can_view_matrix
                    else "—"
                ),
            ),
        )
        for role in roles
    ]
    context = {
        "role_table_headers": ["Nom", "Description", "Utilisateurs", "Actions"],
        "role_table_rows": role_table_rows,
    }
    return render(request, "ui/users/roles_list.html", context)


@require_GET
def role_matrix(request):
    """Matrice module × ressource × action — réservée à core.role.write.

    Aucun affichage, même en lecture seule, hors de cette permission
    (décision produit #2, déjà validée en ux-ui-design-v1.md)."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={reverse('ui-role-matrix')}")

    if not has_permission(request.corrux_user, "core.role.write"):
        return render(
            request, "ui/users/roles_matrix.html", {"permission_denied": True}, status=403
        )

    roles = _predefined_roles_ordered()
    granted = set(
        RolePermission.objects.filter(role__in=roles).values_list(
            "role_id", "permission__module_id", "permission__resource", "permission__action"
        )
    )

    toggle_url = reverse("ui-role-matrix-toggle")
    groups = []
    for group_label, module_id, codes in _PERMISSION_MATRIX_GROUPS:
        rows = []
        for resource, action, row_label in codes:
            cells = tuple(
                _component_html(
                    "ui/components/toggle_cell.html",
                    ui_tags.corrux_toggle_cell,
                    hidden_fields={
                        "role_id": role.id,
                        "module_id": module_id,
                        "resource": resource,
                        "action": action,
                    },
                    granted=(role.id, module_id, resource, action) in granted,
                    toggle_url=toggle_url,
                    label=f"{module_id}.{resource}.{action} — {role.name}",
                )
                for role in roles
            )
            rows.append(ui_tags.TableRow(cells=(row_label, *cells)))
        groups.append(
            {
                "label": group_label,
                "headers": ["Permission", *(role.name for role in roles)],
                "rows": rows,
            }
        )

    context = {"groups": groups}
    return render(request, "ui/users/roles_matrix.html", context)


@require_permission("core.role.write")
@require_POST
def role_matrix_toggle(request):
    """Bascule une cellule de la matrice — reflète immédiatement sur le
    moteur authz (has_permission), critère d'acceptation UI-202."""
    role = get_object_or_404(Role, pk=request.POST.get("role_id"))
    module_id = request.POST.get("module_id", "")
    resource = request.POST.get("resource", "")
    action = request.POST.get("action", "")
    code = f"{module_id}.{resource}.{action}"

    with transaction.atomic():
        permission, _ = Permission.objects.get_or_create(
            module_id=module_id, resource=resource, action=action
        )
        existing = RolePermission.objects.filter(role=role, permission=permission).first()
        if existing:
            existing.delete()
            audit_action = "role.permission_revoke"
        else:
            RolePermission.objects.create(role=role, permission=permission)
            audit_action = "role.permission_grant"

        record_audit_event(
            actor=request.corrux_user,
            action=audit_action,
            target=role.name,
            metadata={"permission": code},
        )

    return HttpResponseRedirect(reverse("ui-role-matrix"))


# ============================================================================
# UI-301 — Explorateur de documents
# ============================================================================
#
# Permission d'entrée sur l'écran : documentation.document.read (même
# code que la Sidebar, ui/navigation.py) — la maquette Lot 3 §1
# mentionne "documentation.dossier.lire"/"documentation.document.lire"
# (verbes français), une convention jamais utilisée nulle part ailleurs
# dans le code réel (ui/navigation.py, manifest.yaml Documentation,
# TECH-025) ; réutilisation explicite du code déjà en production plutôt
# qu'une invention parallèle — signalé, pas silencieux.
#
# Visibilité de chaque document/dossier LISTÉ : has_document_permission/
# has_folder_permission (TECH-023, via list_visible_folder_contents),
# jamais la seule permission d'entrée sur l'écran — « un document sans
# permission n'apparaît pas » (critère d'acceptation explicite).
#
# Le module Documentation doit être activé (comme le vérifie déjà la
# Sidebar, ui/navigation.py::module_is_activated) — revérifié ici côté
# serveur : la disparition du lien Sidebar seule n'est jamais un
# contrôle d'accès suffisant.
#
# "Confidentiel" (badge, critère d'acceptation) : interprétation
# documentée, pas une règle explicite des sources — un document est
# jugé "Confidentiel" s'il porte au moins une DocumentPermission (son
# accès a été spécifiquement configuré au-delà du seul propriétaire),
# seul signal disponible dans le modèle réel pour cette notion.


def _explorer_access(request, folder_id):
    """Vérifie l'activation du module + la permission d'entrée +, si un
    dossier précis est ciblé, la permission de lecture sur ce dossier.

    Retourne `(folder, error_response)` — `error_response` est `None`
    si tout est autorisé (et `folder` porte alors la cible réelle),
    sinon la réponse à retourner telle quelle. Partagé par
    `document_explorer` (UI-301) et `document_upload` (UI-302) : même
    écran, le second ajoute simplement un Drawer par-dessus."""
    if not module_is_activated("documentation") or not has_permission(
        request.corrux_user, "documentation.document.read"
    ):
        return None, render(
            request, "ui/documentation/explorer.html", {"permission_denied": True}, status=403
        )

    folder = None
    if folder_id is not None:
        folder = get_object_or_404(Folder, pk=folder_id)
        if not has_folder_permission(request.corrux_user, folder, "read"):
            return None, render(
                request,
                "ui/documentation/explorer.html",
                {"permission_denied": True},
                status=403,
            )
    return folder, None


def _explorer_context(folder, user):
    """Construit breadcrumb/table pour l'Explorateur — partagé par
    `document_explorer` et `document_upload` (même liste affichée
    derrière le Drawer de dépôt)."""
    documents, subfolders = list_visible_folder_contents(folder, user)

    breadcrumb_items = [("Documents", reverse("ui-document-explorer"))]
    for ancestor in folder_breadcrumb(folder):
        breadcrumb_items.append(
            (ancestor.name, reverse("ui-document-explorer-folder", args=[ancestor.id]))
        )

    table_rows = []
    for subfolder in subfolders:
        table_rows.append(
            ui_tags.TableRow(
                cells=(
                    _component_html(
                        "ui/components/button.html",
                        ui_tags.corrux_button,
                        label=subfolder.name,
                        variant="tertiary",
                        href=reverse("ui-document-explorer-folder", args=[subfolder.id]),
                    ),
                    "Dossier",
                    "—",
                    "—",
                ),
            )
        )
    for document in documents:
        name_cell = _component_html(
            "ui/components/button.html",
            ui_tags.corrux_button,
            label=document.filename,
            variant="tertiary",
            href=reverse("ui-document-detail", args=[document.id]),
        )
        if document.permissions.exists():
            name_cell += " " + _component_html(
                "ui/components/badge.html",
                ui_tags.corrux_badge,
                label="Confidentiel",
                tone="warning",
            )
        table_rows.append(
            ui_tags.TableRow(
                cells=(
                    name_cell,
                    "Document",
                    _format_file_size(document.size_bytes),
                    _format_datetime(document.created_at),
                ),
            )
        )

    upload_url = (
        reverse("ui-document-upload-folder", args=[folder.id])
        if folder is not None
        else reverse("ui-document-upload")
    )

    folder_permissions_url = ""
    if folder is not None and has_folder_permission(user, folder, "write"):
        folder_permissions_url = reverse("ui-folder-permissions", args=[folder.id])

    return {
        "breadcrumb_items": breadcrumb_items,
        "table_headers": ["Nom", "Type", "Taille", "Créé le"],
        "table_rows": table_rows,
        "is_empty": not documents and not subfolders,
        "upload_url": upload_url,
        "folder_permissions_url": folder_permissions_url,
    }


@require_GET
def document_explorer(request, folder_id=None):
    """Explorateur — navigation dossiers + liste + filtres : trois
    zones d'un seul écran (maquette Lot 3 §1), pas trois écrans
    distincts. Filtres (type/dossier/date/mot-clé, TECH-022) non câblés
    ici — recherche complète = UI-305, non anticipée.

    @require_GET : écran strictement en lecture, aucune mutation
    possible ici (le dépôt est une route dédiée, UI-302)."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={request.path}")

    folder, error_response = _explorer_access(request, folder_id)
    if error_response is not None:
        return error_response

    context = _explorer_context(folder, request.corrux_user)
    return render(request, "ui/documentation/explorer.html", context)


# ============================================================================
# UI-302 — Dépôt de document (Dropzone de référence)
# ============================================================================
#
# « Composant unique à 5 états » (maquette Lot 3 §2), réinterprétés pour
# un rendu strictement serveur, zéro JavaScript (cohérent avec tout le
# projet) :
#   1. Dropzone vide/survol -> rendu GET initial (formulaire statique).
#   2. Fichier sélectionné + métadonnées à compléter -> comportement
#      natif du navigateur sur l'input file/les champs texte, avant
#      toute requête ; aucun état serveur distinct n'existe pour cette
#      transition (documenté, pas ignoré).
#   3. Upload en cours (barre de progression + annulation) -> aucun
#      équivalent serveur sans JS/AJAX ; le navigateur affiche son
#      propre indicateur de chargement natif pendant le POST. Aucune
#      fausse barre de progression n'est simulée.
#   4. Upload réussi -> redirection vers l'Explorateur du dossier cible,
#      où le document déposé apparaît immédiatement (§8 vision-produit :
#      « un document déposé est retrouvable ») — réalise « confirmation
#      + lien vers le document » sans écran de confirmation dédié.
#   5. Upload échoué -> même écran (Explorateur + Drawer rouvert),
#      message d'erreur explicite (DocumentUploadError, TECH-021).
#
# Permission (décision documentée, cf. UI-301) : entrée sur l'écran =
# documentation.document.read (comme document_explorer). Dépôt DANS un
# dossier précis = has_folder_permission(..., "write") en plus — "write"
# est une action déjà réellement utilisée par TECH-023, contrairement à
# "documentation.document.creer" (maquette, jamais implémenté nulle
# part). Dépôt à la racine : aucune vérification supplémentaire (aucun
# objet Folder n'existe pour porter une permission à la racine).


def _upload_drawer_html(folder, upload_url, error_message=""):
    fields_html = render_to_string(
        "ui/documentation/upload_fields.html",
        {
            "folder_label": folder.name if folder else "Documents (racine)",
            "error_message": error_message,
        },
    )
    return _component_html(
        "ui/components/drawer.html",
        ui_tags.corrux_drawer,
        drawer_id="document-upload-drawer",
        title="Déposer un document",
        content=fields_html,
        action=upload_url,
        method="post",
        submit_label="Déposer",
        enctype="multipart/form-data",
        open=True,
    )


def document_upload(request, folder_id=None):
    """Dépôt d'un document — Drawer ouvert par-dessus l'Explorateur
    (maquette Lot 3 §2), même patron que UI-201 (Drawer réaffiché
    ouvert après une erreur de validation, sans JavaScript)."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={request.path}")

    folder, error_response = _explorer_access(request, folder_id)
    if error_response is not None:
        return error_response

    if folder is not None and not has_folder_permission(
        request.corrux_user, folder, "write"
    ):
        return render(
            request, "ui/documentation/explorer.html", {"permission_denied": True}, status=403
        )

    context = _explorer_context(folder, request.corrux_user)
    upload_url = context["upload_url"]

    error_message = ""
    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            error_message = "Veuillez sélectionner un fichier."
        else:
            try:
                upload_document(
                    content=uploaded_file.read(),
                    filename=uploaded_file.name,
                    owner_user=request.corrux_user,
                    folder=folder,
                    category=request.POST.get("category", "").strip(),
                    description=request.POST.get("description", "").strip(),
                )
            except DocumentUploadError as exc:
                error_message = str(exc)
            else:
                redirect_url = (
                    reverse("ui-document-explorer-folder", args=[folder.id])
                    if folder is not None
                    else reverse("ui-document-explorer")
                )
                return HttpResponseRedirect(redirect_url)
    elif request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "POST"])

    context["upload_drawer_html"] = _upload_drawer_html(folder, upload_url, error_message)
    return render(request, "ui/documentation/explorer.html", context)


# ============================================================================
# UI-303 — Détail document (Drawer consultation/édition)
# ============================================================================
#
# Un seul composant Drawer, deux variantes (maquette Lot 3 §3) — même
# patron que UI-201/UI-302 : le Drawer s'ouvre par-dessus l'Explorateur
# du dossier contenant le document, jamais un second écran.
#
# Permission (décision documentée, cohérente avec UI-301/302) :
# Variante A (consultation) = has_document_permission(..., "read") ;
# Variante B (édition) = has_document_permission(..., "write") — "write"
# déjà réellement utilisée par TECH-023, pas
# "documentation.document.modifier" (maquette, jamais implémenté nulle
# part, même écart déjà signalé en UI-301/302).
#
# Portée volontairement réduite par rapport à la maquette, écarts
# documentés explicitement (pas silencieux) :
# - "Nom" (filename) : jamais éditable — cf. update_document(),
#   services.py (le chemin de stockage physique dépend du filename
#   exact, aucune primitive de renommage n'existe).
# - "Dossier" (déplacement) : non exposé dans le formulaire d'édition —
#   aucun composant de sélection de dossier n'est établi nulle part
#   dans le projet ; update_document() supporte le paramètre côté
#   service pour un futur ticket, la vue UI-303 ne l'expose pas (le
#   document reste dans son dossier actuel lors d'une édition).
# - "Télécharger"/"Déplacer"/"Supprimer" : hors du texte du ticket lui-
#   même. "Supprimer" n'a d'ailleurs aucune primitive de stockage
#   correspondante (core.storage.files n'a pas de delete(), dette déjà
#   signalée depuis TECH-004/021). "Gérer les permissions" = UI-304,
#   non anticipé.


def _document_detail_drawer_html(document, edit_url, can_edit):
    fields_html = render_to_string(
        "ui/documentation/detail_fields.html",
        {
            "filename": document.filename,
            "mime_type": document.mime_type,
            "owner_label": document.owner_user.full_name,
            "created_at_label": _format_datetime(document.created_at),
            "category": document_metadata_value(document, "categorie"),
            "description": document_metadata_value(document, "description"),
        },
    )
    if can_edit:
        fields_html += _component_html(
            "ui/components/button.html",
            ui_tags.corrux_button,
            label="Modifier",
            variant="primary",
            href=edit_url,
        )
        fields_html += _component_html(
            "ui/components/button.html",
            ui_tags.corrux_button,
            label="Gérer les permissions",
            variant="secondary",
            href=reverse("ui-document-permissions", args=[document.id]),
        )
    return _component_html(
        "ui/components/drawer.html",
        ui_tags.corrux_drawer,
        drawer_id="document-detail-drawer",
        title=document.filename,
        content=fields_html,
        action="",
        method="get",
        submit_label="Fermer",
        cancel_label="Fermer",
        open=True,
    )


@require_GET
def document_detail(request, document_id):
    """Variante A — consultation, lecture seule."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={request.path}")

    document = get_object_or_404(Document, pk=document_id)
    if not has_document_permission(request.corrux_user, document, "read"):
        return render(
            request, "ui/documentation/explorer.html", {"permission_denied": True}, status=403
        )

    can_edit = has_document_permission(request.corrux_user, document, "write")
    edit_url = reverse("ui-document-edit", args=[document.id])

    context = _explorer_context(document.folder, request.corrux_user)
    context["detail_drawer_html"] = _document_detail_drawer_html(document, edit_url, can_edit)
    return render(request, "ui/documentation/explorer.html", context)


def _document_edit_drawer_html(document, edit_url, category, description, error_message=""):
    fields_html = render_to_string(
        "ui/documentation/edit_fields.html",
        {
            "filename": document.filename,
            "category": category,
            "description": description,
            "error_message": error_message,
        },
    )
    return _component_html(
        "ui/components/drawer.html",
        ui_tags.corrux_drawer,
        drawer_id="document-edit-drawer",
        title=f"Modifier {document.filename}",
        content=fields_html,
        action=edit_url,
        method="post",
        submit_label="Enregistrer",
        cancel_label="Annuler",
        open=True,
    )


def document_edit(request, document_id):
    """Variante B — édition des métadonnées (catégorie/description).

    Critère d'acceptation explicite du ticket : inaccessible sans
    permission d'écriture — vérifié côté serveur, jamais seulement par
    l'absence du bouton "Modifier" côté Variante A."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={request.path}")

    document = get_object_or_404(Document, pk=document_id)
    if not has_document_permission(request.corrux_user, document, "write"):
        return render(
            request, "ui/documentation/explorer.html", {"permission_denied": True}, status=403
        )

    edit_url = reverse("ui-document-edit", args=[document.id])
    category = document_metadata_value(document, "categorie")
    description = document_metadata_value(document, "description")

    if request.method == "POST":
        category = request.POST.get("category", "").strip()
        description = request.POST.get("description", "").strip()
        update_document(
            document=document, folder=document.folder, category=category, description=description
        )
        return HttpResponseRedirect(reverse("ui-document-detail", args=[document.id]))
    elif request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "POST"])

    context = _explorer_context(document.folder, request.corrux_user)
    context["detail_drawer_html"] = _document_edit_drawer_html(
        document, edit_url, category, description
    )
    return render(request, "ui/documentation/explorer.html", context)


# ============================================================================
# UI-304 — Permissions document/dossier (composant générique)
# ============================================================================
#
# Un seul composant (Modal) pour document ET dossier (maquette Lot 3
# §4), réutilisant intégralement grant_permission()/revoke_permission()
# (TECH-023) — aucune nouvelle logique de permission, uniquement une
# façade UI. Bascule via corrux_toggle_cell (généralisé depuis UI-202
# pour ce ticket même, cf. ui_tags.py) ; retrait via
# corrux_inline_action_form (action à sens unique, pas une bascule).
#
# Permission de gestion (décision documentée, cohérente avec UI-301/
# 302/303) : has_document_permission/has_folder_permission(...,
# "write") — pas "documentation.document.gerer_permissions" (maquette,
# jamais implémenté nulle part).
#
# "Liste vide -> au moins un accès (le propriétaire)" (maquette) :
# uniquement pour un DOCUMENT (Folder n'a pas de propriétaire,
# TECH-023) — affiché en lecture seule, jamais une DocumentPermission
# réelle, jamais togglable/supprimable depuis cet écran (cohérent avec
# la règle déjà tranchée : l'accès du propriétaire reste toujours
# implicite, quoi qu'il arrive).


def _grantee_label(role, user):
    if user is not None:
        return f"{user.full_name} — Utilisateur individuel (accès exceptionnel)"
    return f"{role.name} — Rôle"


def _grantee_select_options():
    options = [("", "— Sélectionner —")]
    for role in Role.objects.filter(name__in=_PREDEFINED_ROLE_NAMES).order_by("name"):
        options.append((f"role:{role.id}", f"Rôle : {role.name}"))
    for grantee_user in User.objects.filter(status=User.Status.ACTIVE).order_by("full_name"):
        options.append((f"user:{grantee_user.id}", f"Utilisateur : {grantee_user.full_name}"))
    return options


def _build_permission_rows(permissions, toggle_url, remove_url, owner_row_label):
    grantees = {}
    order = []
    for perm in permissions:
        key = ("role", perm.role_id) if perm.role_id else ("user", perm.user_id)
        if key not in grantees:
            grantees[key] = {"role": perm.role, "user": perm.user, "actions": set()}
            order.append(key)
        grantees[key]["actions"].add(perm.action)

    rows = []
    if owner_row_label is not None:
        rows.append(
            ui_tags.TableRow(
                cells=(
                    owner_row_label,
                    _component_html(
                        "ui/components/badge.html",
                        ui_tags.corrux_badge,
                        label="Implicite",
                        tone="info",
                    ),
                    "—",
                    "—",
                ),
            )
        )

    for key in order:
        grantee = grantees[key]
        kind, _grantee_id = key
        role, grantee_user = grantee["role"], grantee["user"]
        label = _grantee_label(role, grantee_user)
        hidden_base = {"role_id": role.id} if kind == "role" else {"user_id": grantee_user.id}

        read_cell = _component_html(
            "ui/components/toggle_cell.html",
            ui_tags.corrux_toggle_cell,
            hidden_fields={**hidden_base, "action": "read"},
            granted="read" in grantee["actions"],
            toggle_url=toggle_url,
            label=f"{label} — lecture",
        )
        write_cell = _component_html(
            "ui/components/toggle_cell.html",
            ui_tags.corrux_toggle_cell,
            hidden_fields={**hidden_base, "action": "write"},
            granted="write" in grantee["actions"],
            toggle_url=toggle_url,
            label=f"{label} — écriture",
        )
        remove_cell = _component_html(
            "ui/components/inline_action_form.html",
            ui_tags.corrux_inline_action_form,
            hidden_fields=hidden_base,
            action_url=remove_url,
            label="Retirer",
            variant="danger",
        )
        rows.append(ui_tags.TableRow(cells=(label, read_cell, write_cell, remove_cell)))

    return rows


def _permissions_modal_html(*, document=None, folder=None, toggle_url, remove_url, add_url):
    permissions = list_permissions_for(document=document, folder=folder)
    owner_row_label = f"{document.owner_user.full_name} — Propriétaire" if document else None

    rows = _build_permission_rows(permissions, toggle_url, remove_url, owner_row_label)
    content = render_to_string(
        "ui/documentation/permissions_content.html",
        {
            "rows_empty": not rows,
            "table_headers": ["Bénéficiaire", "Lecture", "Écriture", ""],
            "table_rows": rows,
            "add_url": add_url,
            "grantee_options": _grantee_select_options(),
        },
    )
    return _component_html(
        "ui/components/content_modal.html",
        ui_tags.corrux_content_modal,
        modal_id="permissions-modal",
        title="Gérer les permissions",
        content=content,
        open=True,
    )


def _resolve_grantee(raw_value):
    """Analyse `"role:<id>"`/`"user:<id>"` -> `(role, user)`, exactement
    un des deux non-None, ou `(None, None)` si invalide/absent — jamais
    une confiance aveugle en l'identifiant fourni par le client."""
    kind, _, raw_id = (raw_value or "").partition(":")
    if kind == "role":
        role = Role.objects.filter(pk=raw_id).first()
        return (role, None) if role is not None else (None, None)
    if kind == "user":
        grantee_user = User.objects.filter(pk=raw_id).first()
        return (None, grantee_user) if grantee_user is not None else (None, None)
    return None, None


@require_GET
def document_permissions(request, document_id):
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={request.path}")

    document = get_object_or_404(Document, pk=document_id)
    if not has_document_permission(request.corrux_user, document, "write"):
        return render(
            request, "ui/documentation/explorer.html", {"permission_denied": True}, status=403
        )

    context = _explorer_context(document.folder, request.corrux_user)
    context["detail_drawer_html"] = _permissions_modal_html(
        document=document,
        toggle_url=reverse("ui-document-permissions-toggle", args=[document.id]),
        remove_url=reverse("ui-document-permissions-remove", args=[document.id]),
        add_url=reverse("ui-document-permissions-add", args=[document.id]),
    )
    return render(request, "ui/documentation/explorer.html", context)


def _toggle_permission_shared(request, *, document=None, folder=None):
    """Bascule read/write pour un bénéficiaire — logique partagée
    document/dossier (composant unique, maquette Lot 3 §4)."""
    role, grantee_user = _resolve_grantee(
        f"role:{request.POST['role_id']}"
        if request.POST.get("role_id")
        else (f"user:{request.POST['user_id']}" if request.POST.get("user_id") else "")
    )
    action = request.POST.get("action", "")
    if (role is not None or grantee_user is not None) and action in ("read", "write"):
        existing = DocumentPermission.objects.filter(
            document=document, folder=folder, role=role, user=grantee_user, action=action
        ).first()
        if existing:
            revoke_permission(actor=request.corrux_user, permission=existing)
        else:
            grant_permission(
                actor=request.corrux_user,
                action=action,
                document=document,
                folder=folder,
                role=role,
                user=grantee_user,
            )


def _remove_permission_shared(request, *, document=None, folder=None):
    """Retrait de tous les accès (read+write) d'un bénéficiaire —
    logique partagée document/dossier."""
    role_id = request.POST.get("role_id")
    user_id = request.POST.get("user_id")
    matching = DocumentPermission.objects.filter(document=document, folder=folder)
    matching = matching.filter(role_id=role_id) if role_id else matching.filter(user_id=user_id)
    for permission in matching:
        revoke_permission(actor=request.corrux_user, permission=permission)


def _add_permission_shared(request, *, document=None, folder=None):
    """Attribution d'un accès lecture initial à un nouveau bénéficiaire
    — logique partagée document/dossier."""
    role, grantee_user = _resolve_grantee(request.POST.get("grantee", ""))
    if role is not None or grantee_user is not None:
        grant_permission(
            actor=request.corrux_user,
            action="read",
            document=document,
            folder=folder,
            role=role,
            user=grantee_user,
        )


@require_POST
def document_permissions_toggle(request, document_id):
    if request.corrux_user is None:
        return HttpResponseForbidden()
    document = get_object_or_404(Document, pk=document_id)
    if not has_document_permission(request.corrux_user, document, "write"):
        return HttpResponseForbidden()

    _toggle_permission_shared(request, document=document)
    return HttpResponseRedirect(reverse("ui-document-permissions", args=[document.id]))


@require_POST
def document_permissions_remove(request, document_id):
    if request.corrux_user is None:
        return HttpResponseForbidden()
    document = get_object_or_404(Document, pk=document_id)
    if not has_document_permission(request.corrux_user, document, "write"):
        return HttpResponseForbidden()

    _remove_permission_shared(request, document=document)
    return HttpResponseRedirect(reverse("ui-document-permissions", args=[document.id]))


@require_POST
def document_permissions_add(request, document_id):
    if request.corrux_user is None:
        return HttpResponseForbidden()
    document = get_object_or_404(Document, pk=document_id)
    if not has_document_permission(request.corrux_user, document, "write"):
        return HttpResponseForbidden()

    _add_permission_shared(request, document=document)
    return HttpResponseRedirect(reverse("ui-document-permissions", args=[document.id]))


@require_GET
def folder_permissions(request, folder_id):
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={request.path}")

    folder = get_object_or_404(Folder, pk=folder_id)
    if not has_folder_permission(request.corrux_user, folder, "write"):
        return render(
            request, "ui/documentation/explorer.html", {"permission_denied": True}, status=403
        )

    context = _explorer_context(folder, request.corrux_user)
    context["detail_drawer_html"] = _permissions_modal_html(
        folder=folder,
        toggle_url=reverse("ui-folder-permissions-toggle", args=[folder.id]),
        remove_url=reverse("ui-folder-permissions-remove", args=[folder.id]),
        add_url=reverse("ui-folder-permissions-add", args=[folder.id]),
    )
    return render(request, "ui/documentation/explorer.html", context)


@require_POST
def folder_permissions_toggle(request, folder_id):
    if request.corrux_user is None:
        return HttpResponseForbidden()
    folder = get_object_or_404(Folder, pk=folder_id)
    if not has_folder_permission(request.corrux_user, folder, "write"):
        return HttpResponseForbidden()

    _toggle_permission_shared(request, folder=folder)
    return HttpResponseRedirect(reverse("ui-folder-permissions", args=[folder.id]))


@require_POST
def folder_permissions_remove(request, folder_id):
    if request.corrux_user is None:
        return HttpResponseForbidden()
    folder = get_object_or_404(Folder, pk=folder_id)
    if not has_folder_permission(request.corrux_user, folder, "write"):
        return HttpResponseForbidden()

    _remove_permission_shared(request, folder=folder)
    return HttpResponseRedirect(reverse("ui-folder-permissions", args=[folder.id]))


@require_POST
def folder_permissions_add(request, folder_id):
    if request.corrux_user is None:
        return HttpResponseForbidden()
    folder = get_object_or_404(Folder, pk=folder_id)
    if not has_folder_permission(request.corrux_user, folder, "write"):
        return HttpResponseForbidden()

    _add_permission_shared(request, folder=folder)
    return HttpResponseRedirect(reverse("ui-folder-permissions", args=[folder.id]))


# ============================================================================
# UI-305 — Recherche documentaire + câblage de la recherche globale
# ============================================================================
#
# Même moteur de recherche (search_documents(), TECH-022) quelle que
# soit l'entrée — Topbar (câblée dans ui_tags.py::corrux_topbar +
# ui/templates/ui/shell/topbar.html) ou cet écran dédié — critère
# d'acceptation explicite du ticket : un seul appel à search_documents()
# ici, la Topbar redirige simplement vers cette même route en GET.
#
# Permission d'entrée (décision documentée, cohérente avec UI-301+) :
# documentation.document.read (même code que l'Explorateur et la
# Topbar) — pas "documentation.document.lire" (maquette, jamais
# implémenté nulle part).
#
# Filtre "Dossier" : liste plate alphabétique de tous les dossiers —
# aucun composant d'arborescence n'est établi nulle part dans le projet
# (même limite déjà documentée en UI-303 pour l'édition). "Modifié"
# (maquette) correspond à Document.created_at — seul champ date du
# modèle (TECH-020), aucune notion de date de modification distincte
# n'existe.


def _search_type_options():
    options = [("", "Tous")]
    for extension, mime_type in MIME_TYPE_BY_EXTENSION.items():
        options.append((mime_type, extension.lstrip(".").upper()))
    return options


def _search_folder_options():
    options = [("", "Tous"), ("_root_", "Racine uniquement")]
    for target_folder in Folder.objects.order_by("name"):
        options.append((str(target_folder.id), target_folder.name))
    return options


def _parse_search_date(value):
    """Une date GET absente ou invalide est ignorée silencieusement
    (filtre non appliqué) — jamais une erreur 500 (même patron que
    UI-206, audit_list)."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _document_location_label(document):
    ancestors = folder_breadcrumb(document.folder)
    return " › ".join(f.name for f in ancestors) if ancestors else "Racine"


@require_GET
def document_search(request):
    """Écran de recherche complet — mêmes filtres que le moteur
    TECH-022 (mot-clé/type/dossier/date), résultats toujours filtrés
    par permission (search_documents() le garantit déjà, rien
    n'est dupliqué ici)."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={request.path}")

    if not module_is_activated("documentation") or not has_permission(
        request.corrux_user, "documentation.document.read"
    ):
        return render(
            request, "ui/documentation/search.html", {"permission_denied": True}, status=403
        )

    keyword = request.GET.get("q", "").strip()
    mime_type = request.GET.get("type", "").strip()
    folder_param = request.GET.get("dossier", "").strip()
    from_raw = request.GET.get("du", "").strip()
    to_raw = request.GET.get("au", "").strip()

    search_kwargs = {
        "user": request.corrux_user,
        "keyword": keyword,
        "mime_type": mime_type,
        "created_after": _parse_search_date(from_raw),
        "created_before": _parse_search_date(to_raw),
    }
    if folder_param == "_root_":
        search_kwargs["folder"] = None
    elif folder_param:
        target_folder = Folder.objects.filter(pk=folder_param).first()
        if target_folder is not None:
            search_kwargs["folder"] = target_folder
        else:
            folder_param = ""  # identifiant invalide, filtre ignoré

    results = search_documents(**search_kwargs)

    table_rows = [
        ui_tags.TableRow(
            cells=(
                _component_html(
                    "ui/components/button.html",
                    ui_tags.corrux_button,
                    label=document.filename,
                    variant="tertiary",
                    href=reverse("ui-document-detail", args=[document.id]),
                ),
                _document_location_label(document),
                _format_file_size(document.size_bytes),
                _format_datetime(document.created_at),
            ),
        )
        for document in results
    ]

    context = {
        "table_headers": ["Nom", "Emplacement", "Taille", "Créé le"],
        "table_rows": table_rows,
        "results_empty": not results,
        "searched": bool(request.GET),
        "keyword": keyword,
        "selected_type": mime_type,
        "selected_folder": folder_param,
        "from_value": from_raw if search_kwargs["created_after"] is not None else "",
        "to_value": to_raw if search_kwargs["created_before"] is not None else "",
        "type_options": _search_type_options(),
        "folder_options": _search_folder_options(),
    }
    return render(request, "ui/documentation/search.html", context)


# ============================================================================
# UI-401 — Employés : liste + Drawer création/édition
# ============================================================================
#
# Miroir structurel de UI-201 (Utilisateurs & rôles) — même patron
# exact (liste + Drawer création/édition en 2 variantes + Modal de
# désactivation), adapté aux champs Employee. Différences volontaires :
# - Aucune logique de mot de passe/rôle (Employee n'a ni identifiant de
#   connexion ni rôle propre).
# - Toute mutation délègue aux fonctions de service déjà construites et
#   testées par TECH-031 (create_employee/update_employee/
#   deactivate_employee) — jamais une réimplémentation de la logique
#   métier dans la vue (contrairement à user_edit, antérieur à
#   l'existence d'une couche service RH).
# - Permission : rh.employee.read/write (Option A, TECH-035) — pas
#   rh.employe.lire/creer (maquette, français, déjà résolu autrement).
# - Module RH doit être activé (comme les écrans Documentation
#   activables) — vérifié explicitement, contrairement à UI-201 qui
#   fait partie de Platform Core, jamais désactivable.
#
# Critère d'acceptation explicite : note affichée précisant qu'aucun
# compte utilisateur n'est créé automatiquement — texte dans le Drawer
# de création, cf. _employee_create_drawer_content().


def _employee_table_rows(employees):
    rows = []
    for employee in employees:
        actions = _modal_trigger_html(
            modal_id=f"edit-employee-{employee.id}", label="Modifier", variant="secondary"
        )
        if employee.status == Employee.Status.ACTIVE:
            actions += _modal_trigger_html(
                modal_id=f"deactivate-employee-{employee.id}",
                label="Marquer inactif",
                variant="danger",
            )
        rows.append(
            ui_tags.TableRow(
                cells=(
                    employee_full_name(employee),
                    employee.position,
                    employee.email or "—",
                    employee.hire_date.strftime("%d/%m/%Y"),
                    employee.get_status_display(),
                ),
                actions_html=actions,
            )
        )
    return rows


def _employee_create_drawer_content(errors=None, values=None):
    errors = errors or {}
    values = values or {}
    no_account_note = (
        '<p class="corrux-text-small">Créer une fiche employé ne crée jamais '
        "de compte utilisateur CORRUX — les deux restent des objets distincts. "
        "Un compte peut être créé séparément depuis Utilisateurs & rôles si "
        "nécessaire.</p>"
    )
    return no_account_note + "".join(
        [
            _field_html(
                label="Prénom", name="first_name", field_id="id_create_first_name",
                value=values.get("first_name", ""), required=True,
                error=errors.get("first_name", ""),
            ),
            _field_html(
                label="Nom", name="last_name", field_id="id_create_last_name",
                value=values.get("last_name", ""), required=True,
                error=errors.get("last_name", ""),
            ),
            _field_html(
                label="Email", name="email", field_id="id_create_email",
                input_type="email", value=values.get("email", ""),
                error=errors.get("email", ""),
            ),
            _field_html(
                label="Poste", name="position", field_id="id_create_position",
                value=values.get("position", ""), required=True,
                error=errors.get("position", ""),
            ),
            _field_html(
                label="Date d'entrée", name="hire_date", field_id="id_create_hire_date",
                input_type="date", value=values.get("hire_date", ""), required=True,
                error=errors.get("hire_date", ""),
            ),
        ]
    )


def _employee_edit_drawer_content(employee, errors=None, values=None):
    errors = errors or {}
    values = values or {}
    return "".join(
        [
            _field_html(
                label="Prénom", name="first_name", field_id=f"id_edit_{employee.id}_first_name",
                value=values.get("first_name", employee.first_name), required=True,
                error=errors.get("first_name", ""),
            ),
            _field_html(
                label="Nom", name="last_name", field_id=f"id_edit_{employee.id}_last_name",
                value=values.get("last_name", employee.last_name), required=True,
                error=errors.get("last_name", ""),
            ),
            _field_html(
                label="Email", name="email", field_id=f"id_edit_{employee.id}_email",
                input_type="email", value=values.get("email", employee.email),
                error=errors.get("email", ""),
            ),
            _field_html(
                label="Poste", name="position", field_id=f"id_edit_{employee.id}_position",
                value=values.get("position", employee.position), required=True,
                error=errors.get("position", ""),
            ),
            _field_html(
                label="Date d'entrée", name="hire_date",
                field_id=f"id_edit_{employee.id}_hire_date", input_type="date",
                value=values.get("hire_date", employee.hire_date.isoformat()), required=True,
                error=errors.get("hire_date", ""),
            ),
        ]
    )


def _render_employee_list_page(
    request,
    *,
    open_drawer_id="",
    create_errors=None,
    create_values=None,
    edit_employee_id=None,
    edit_errors=None,
    edit_values=None,
    http_status=200,
):
    search = request.GET.get("q", "").strip()
    employees = Employee.objects.all().order_by("last_name", "first_name")
    if search:
        employees = employees.filter(first_name__icontains=search) | employees.filter(
            last_name__icontains=search
        )
    employees = list(employees)

    can_write = has_permission(request.corrux_user, "rh.employee.write")

    deactivate_modals_html = "".join(
        _component_html(
            "ui/components/modal.html",
            ui_tags.corrux_modal,
            modal_id=f"deactivate-employee-{e.id}",
            title="Marquer cet employé comme inactif",
            message=(
                f"« {employee_full_name(e)} » sera marqué inactif. Son historique "
                "(contrats, congés, documents) est intégralement conservé et "
                "cette action peut être annulée ultérieurement."
            ),
            confirm_label="Marquer inactif",
            action=reverse("ui-employee-deactivate", args=[e.id]),
        )
        for e in employees
        if e.status == Employee.Status.ACTIVE
    )

    edit_drawers_html = "".join(
        _component_html(
            "ui/components/drawer.html",
            ui_tags.corrux_drawer,
            drawer_id=f"edit-employee-{e.id}",
            title=f"Modifier « {employee_full_name(e)} »",
            action=reverse("ui-employee-edit", args=[e.id]),
            content=_employee_edit_drawer_content(
                e,
                errors=(edit_errors if edit_employee_id == e.id else None),
                values=(edit_values if edit_employee_id == e.id else None),
            ),
            open=(open_drawer_id == f"edit-employee-{e.id}"),
        )
        for e in employees
    )

    context = {
        "can_write": can_write,
        "table_headers": ["Nom", "Poste", "Email", "Date d'entrée", "Statut", "Actions"],
        "table_rows": _employee_table_rows(employees) if employees else [],
        "employees_empty": not employees,
        "search": search,
        "employee_count": len(employees),
        "create_drawer_content": _employee_create_drawer_content(create_errors, create_values),
        "create_url": reverse("ui-employee-create"),
        "create_drawer_open": open_drawer_id == "create-employee",
        "edit_drawers_html": edit_drawers_html,
        "deactivate_modals_html": deactivate_modals_html,
        "open_drawer_id": open_drawer_id,
    }
    return render(request, "ui/employees/list.html", context, status=http_status)


@require_GET
def employee_list(request):
    """Liste des employés — UI-401.

    Permission de lecture vérifiée manuellement (même patron que
    user_list, UI-201) : point de navigation principal, doit afficher
    corrux_permission_denied plutôt qu'un 403 JSON brut."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={reverse('ui-employee-list')}")

    if not module_is_activated("rh") or not has_permission(
        request.corrux_user, "rh.employee.read"
    ):
        return render(
            request, "ui/employees/list.html", {"permission_denied": True}, status=403
        )

    return _render_employee_list_page(request)


def _parse_hire_date(raw_value):
    try:
        return date.fromisoformat(raw_value)
    except (TypeError, ValueError):
        return None


@require_permission("rh.employee.write")
@require_POST
def employee_create(request):
    first_name = request.POST.get("first_name", "").strip()
    last_name = request.POST.get("last_name", "").strip()
    email = request.POST.get("email", "").strip()
    position = request.POST.get("position", "").strip()
    hire_date_raw = request.POST.get("hire_date", "").strip()

    errors = {}
    if not first_name:
        errors["first_name"] = "Ce champ est requis."
    if not last_name:
        errors["last_name"] = "Ce champ est requis."
    if not position:
        errors["position"] = "Ce champ est requis."
    hire_date_value = _parse_hire_date(hire_date_raw)
    if not hire_date_raw:
        errors["hire_date"] = "Ce champ est requis."
    elif hire_date_value is None:
        errors["hire_date"] = "Date invalide."

    values = {
        "first_name": first_name, "last_name": last_name, "email": email,
        "position": position, "hire_date": hire_date_raw,
    }
    if errors:
        return _render_employee_list_page(
            request, open_drawer_id="create-employee",
            create_errors=errors, create_values=values, http_status=400,
        )

    employee = create_employee(
        first_name=first_name, last_name=last_name, email=email,
        position=position, hire_date=hire_date_value,
    )

    record_audit_event(
        actor=request.corrux_user, action="rh.employee_create",
        target=employee_full_name(employee), metadata={},
    )

    return HttpResponseRedirect(reverse("ui-employee-list"))


@require_permission("rh.employee.write")
def employee_edit(request, employee_id):
    employee = get_object_or_404(Employee, pk=employee_id)

    if request.method not in ("GET", "HEAD", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    if request.method != "POST":
        return _render_employee_list_page(
            request, open_drawer_id=f"edit-employee-{employee.id}"
        )

    first_name = request.POST.get("first_name", "").strip()
    last_name = request.POST.get("last_name", "").strip()
    email = request.POST.get("email", "").strip()
    position = request.POST.get("position", "").strip()
    hire_date_raw = request.POST.get("hire_date", "").strip()

    errors = {}
    if not first_name:
        errors["first_name"] = "Ce champ est requis."
    if not last_name:
        errors["last_name"] = "Ce champ est requis."
    if not position:
        errors["position"] = "Ce champ est requis."
    hire_date_value = _parse_hire_date(hire_date_raw)
    if not hire_date_raw:
        errors["hire_date"] = "Ce champ est requis."
    elif hire_date_value is None:
        errors["hire_date"] = "Date invalide."

    values = {
        "first_name": first_name, "last_name": last_name, "email": email,
        "position": position, "hire_date": hire_date_raw,
    }
    if errors:
        return _render_employee_list_page(
            request, open_drawer_id=f"edit-employee-{employee.id}",
            edit_employee_id=employee.id, edit_errors=errors, edit_values=values,
            http_status=400,
        )

    # Le statut n'est jamais modifié par ce formulaire — action séparée
    # dédiée (« Marquer inactif », employee_deactivate), cohérent avec
    # la maquette (Drawer Édition sans champ Statut).
    update_employee(
        employee=employee, first_name=first_name, last_name=last_name, email=email,
        position=position, hire_date=hire_date_value, status=employee.status,
    )

    record_audit_event(
        actor=request.corrux_user, action="rh.employee_update",
        target=employee_full_name(employee), metadata={},
    )

    return HttpResponseRedirect(reverse("ui-employee-list"))


@require_permission("rh.employee.write")
@require_POST
def employee_deactivate(request, employee_id):
    """Marque un employé inactif — critère d'acceptation explicite :
    conserve l'historique (délègue à deactivate_employee(), TECH-031,
    qui ne modifie jamais que le champ status)."""
    employee = get_object_or_404(Employee, pk=employee_id)
    deactivate_employee(employee=employee)

    record_audit_event(
        actor=request.corrux_user, action="rh.employee_deactivate",
        target=employee_full_name(employee), metadata={},
    )
    return HttpResponseRedirect(reverse("ui-employee-list"))


# ============================================================================
# UI-402 — Fiche employé : en-tête + onglet Informations
# ============================================================================
#
# En-tête et barre d'onglets construits comme des fonctions partagées
# (_employee_record_header/_employee_record_tabs) — critère
# d'acceptation explicite du ticket : réutilisables sans duplication
# par UI-403/404/405, mêmes composants génériques
# (corrux_record_header/corrux_record_tabs) qu'eux.
#
# Permission (décision documentée) : rh.employee.read uniquement — pas
# de mécanisme d'auto-accès à sa propre fiche. La maquette mentionne
# « Employé (accès à sa propre fiche, en lecture, selon permission) »,
# mais aucune permission d'objet n'existe nulle part pour RH
# (contrairement à Documentation, TECH-023) ; interprété comme : le
# rôle Employé se voit accorder rh.employee.read via la matrice de
# permissions (UI-202), pas un nouveau mécanisme de bypass à inventer
# ici sans mandat clair. Signalé, pas résolu silencieusement.
#
# Onglets Documents/Contrats/Congés : non cliquables (aucune url) tant
# que UI-403/404/405 ne sont pas construits — jamais un lien mort.


def _employee_record_tabs(employee, active_tab):
    """Barre d'onglets partagée — réutilisée telle quelle par
    UI-403/404/405 (critère d'acceptation explicite)."""
    tabs = [
        ("Informations", reverse("ui-employee-detail", args=[employee.id]), "informations"),
        ("Documents", reverse("ui-employee-documents", args=[employee.id]), "documents"),
        ("Contrats", reverse("ui-employee-contracts", args=[employee.id]), "contrats"),
        ("Congés", "", "conges"),
    ]
    return _component_html(
        "ui/components/record_tabs.html", ui_tags.corrux_record_tabs,
        tabs=tabs, active_tab=active_tab,
    )


def _employee_record_header(employee, can_edit):
    """En-tête partagé — réutilisé tel quel par UI-403/404/405 (critère
    d'acceptation explicite).

    « bouton Modifier → UI-401 Variante B » (comportement attendu
    explicite du ticket) : réutilise directement la route d'édition
    déjà construite par UI-401 (elle rend la liste employés avec le
    Drawer d'édition de cet employé déjà ouvert) — jamais une seconde
    route "standalone" inventée pour ce ticket."""
    status_tone = "success" if employee.status == Employee.Status.ACTIVE else "neutral"
    edit_url = reverse("ui-employee-edit", args=[employee.id]) if can_edit else ""
    return _component_html(
        "ui/components/record_header.html", ui_tags.corrux_record_header,
        name=employee_full_name(employee), subtitle=employee.position,
        status_label=employee.get_status_display(), status_tone=status_tone,
        edit_url=edit_url,
    )


@require_GET
def employee_detail(request, employee_id):
    """Fiche employé — onglet Informations, lecture seule — UI-402."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(
            f"{login_url}?next={reverse('ui-employee-detail', args=[employee_id])}"
        )

    employee = get_object_or_404(Employee, pk=employee_id)

    if not module_is_activated("rh") or not has_permission(
        request.corrux_user, "rh.employee.read"
    ):
        return render(
            request, "ui/employees/detail.html", {"permission_denied": True}, status=403
        )

    can_edit = has_permission(request.corrux_user, "rh.employee.write")
    context = {
        "header_html": _employee_record_header(employee, can_edit),
        "tabs_html": _employee_record_tabs(employee, "informations"),
        "employee": employee,
    }
    return render(request, "ui/employees/detail.html", context)


# ============================================================================
# UI-403 — Fiche employé : onglet Documents (intégration stricte Documentation)
# ============================================================================
#
# Décision produit confirmée (Option B, audit Phase 1) : aucun dossier
# Documentation dédié par employé (TECH-030/031 ne prévoit rien de tel
# — un dossier par employé serait un changement d'architecture réel,
# pas du câblage). L'onglet réutilise directement
# list_employee_documents()/attach_document_to_employee() (TECH-034,
# déjà construits et testés) : une liste filtrée par la table de
# liaison RH (employee_documents), pas une navigation par dossier
# Documentation. Mêmes composants (Table, Dropzone) que l'Explorateur/
# le Dropzone Documentation (UI-301/302) — critère d'acceptation
# explicite : « aucun composant documentaire nouveau créé ».
#
# Permission : « les permissions appliquées sont celles du module
# Documentation » (maquette) — le contenu de CET onglet précis exige
# documentation.document.read, une vérification SÉPARÉE et
# ADDITIONNELLE à rh.employee.read (qui gate la fiche employé dans son
# ensemble, UI-402). L'état "Permission denied" reste local à l'onglet
# (en-tête + barre d'onglets restent visibles) — maquette : « États :
# par onglet... Permission denied si l'utilisateur n'a pas accès à
# l'onglet », pas la page entière masquée.


def _employee_documents_access(request, employee_id):
    """Vérifie rh.employee.read (accès à la fiche) PUIS
    documentation.document.read (accès au contenu de cet onglet
    précis, séparément) — retourne (employee, error_response)."""
    employee = get_object_or_404(Employee, pk=employee_id)

    if not module_is_activated("rh") or not has_permission(
        request.corrux_user, "rh.employee.read"
    ):
        return None, render(
            request, "ui/employees/detail.html", {"permission_denied": True}, status=403
        )

    can_edit = has_permission(request.corrux_user, "rh.employee.write")
    header_html = _employee_record_header(employee, can_edit)
    tabs_html = _employee_record_tabs(employee, "documents")

    if not module_is_activated("documentation") or not has_permission(
        request.corrux_user, "documentation.document.read"
    ):
        context = {
            "header_html": header_html, "tabs_html": tabs_html, "tab_permission_denied": True,
        }
        return None, render(
            request, "ui/employees/documents_tab.html", context, status=403
        )

    return employee, None


def _employee_documents_context(employee, request):
    documents = list_employee_documents(employee=employee, requesting_user=request.corrux_user)
    table_rows = [
        ui_tags.TableRow(
            cells=(
                document.filename,
                document.mime_type,
                _format_file_size(document.size_bytes),
                _format_datetime(document.created_at),
            ),
        )
        for document in documents
    ]
    can_edit = has_permission(request.corrux_user, "rh.employee.write")
    return {
        "header_html": _employee_record_header(employee, can_edit),
        "tabs_html": _employee_record_tabs(employee, "documents"),
        "table_headers": ["Nom", "Type", "Taille", "Créé le"],
        "table_rows": table_rows,
        "documents_empty": not documents,
        "upload_url": reverse("ui-employee-document-upload", args=[employee.id]),
    }


@require_GET
def employee_documents_tab(request, employee_id):
    """Onglet Documents — UI-403."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(
            f"{login_url}?next={reverse('ui-employee-documents', args=[employee_id])}"
        )

    employee, error_response = _employee_documents_access(request, employee_id)
    if error_response is not None:
        return error_response

    context = _employee_documents_context(employee, request)
    return render(request, "ui/employees/documents_tab.html", context)


def _employee_upload_drawer_html(employee, upload_url, error_message=""):
    fields_html = render_to_string(
        "ui/employees/upload_fields.html", {"error_message": error_message}
    )
    return _component_html(
        "ui/components/drawer.html", ui_tags.corrux_drawer,
        drawer_id="employee-document-upload-drawer", title="Déposer un document",
        content=fields_html, action=upload_url, method="post", submit_label="Déposer",
        enctype="multipart/form-data", open=True,
    )


def employee_document_upload(request, employee_id):
    """Dépôt de document depuis la fiche employé — UI-403.

    Même composant Dropzone que UI-302 (sans variante RH, maquette),
    mais rattache via attach_document_to_employee() (TECH-034) — jamais
    upload_document() seul, pour créer le lien EmployeeDocument en plus
    du dépôt Documentation."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={request.path}")

    employee, error_response = _employee_documents_access(request, employee_id)
    if error_response is not None:
        return error_response

    upload_url = reverse("ui-employee-document-upload", args=[employee.id])
    error_message = ""

    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            error_message = "Veuillez sélectionner un fichier."
        else:
            try:
                attach_document_to_employee(
                    employee=employee,
                    content=uploaded_file.read(),
                    filename=uploaded_file.name,
                    owner_user=request.corrux_user,
                    category=request.POST.get("category", "").strip(),
                )
            except DocumentUploadError as exc:
                error_message = str(exc)
            else:
                return HttpResponseRedirect(reverse("ui-employee-documents", args=[employee.id]))
    elif request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "POST"])

    context = _employee_documents_context(employee, request)
    context["upload_drawer_html"] = _employee_upload_drawer_html(
        employee, upload_url, error_message
    )
    return render(request, "ui/employees/documents_tab.html", context)


# ============================================================================
# UI-404 — Fiche employé : onglet Contrats + Drawer contrat
# ============================================================================
#
# Décision produit confirmée (Option A, audit Phase 1) : « Lier un
# document » ouvre réellement l'Explorateur Documentation en mode
# sélection — extension proportionnée, pas une réécriture. Ne touche
# PAS document_explorer/_explorer_context (UI-301, déjà committés et
# testés) : un sélecteur dédié réutilise directement les fonctions de
# DONNÉES déjà partagées (list_visible_folder_contents/
# folder_breadcrumb, modules.documentation.services) avec sa PROPRE
# présentation (bouton « Choisir » au lieu d'un lien de consultation) —
# zéro risque de régression sur les écrans déjà livrés.
#
# Correction de sécurité appliquée avant ce ticket (voir
# modules/rh/services.py) : link_document_to_contract() revérifie
# désormais l'accès via documents_v1.get() avant de lier — écart réel
# de TECH-032, surfacé par l'usage réel de ce sélecteur.
#
# « Document lié » n'est proposé qu'en édition (le contrat doit déjà
# exister pour que le sélecteur ait un contract_id à cibler) — jamais
# à la création, cohérent avec le patron déjà établi pour les
# permissions document/dossier (UI-304, accessible seulement depuis le
# détail, jamais depuis un formulaire de création).


def _document_picker_access(request, contract_id, folder_id=None):
    """Correction (Option A, audit Phase 1 UI-405) : rh.contract.write —
    pas rh.employee.write. Le sélecteur ne sert qu'à modifier le
    document lié d'UN contrat précis ; la maquette et le manifeste
    RH (TECH-035) déclarent une ressource "contract" distincte de
    "employee" précisément pour cette granularité. PUIS
    documentation.document.read pour parcourir réellement."""
    contract = get_object_or_404(Contract, pk=contract_id)

    if not module_is_activated("rh") or not has_permission(
        request.corrux_user, "rh.contract.write"
    ):
        return None, None, render(
            request, "ui/employees/detail.html", {"permission_denied": True}, status=403
        )

    if not module_is_activated("documentation") or not has_permission(
        request.corrux_user, "documentation.document.read"
    ):
        return None, None, render(
            request, "ui/rh/document_picker.html", {"permission_denied": True}, status=403
        )

    folder = None
    if folder_id is not None:
        folder = get_object_or_404(Folder, pk=folder_id)
        if not has_folder_permission(request.corrux_user, folder, "read"):
            return None, None, render(
                request, "ui/rh/document_picker.html", {"permission_denied": True}, status=403
            )

    return contract, folder, None


def _document_picker_context(contract, folder, user):
    documents, subfolders = list_visible_folder_contents(folder, user)

    breadcrumb_items = [("Documents", reverse("ui-document-picker", args=[contract.id]))]
    for ancestor in folder_breadcrumb(folder):
        breadcrumb_items.append(
            (ancestor.name, reverse("ui-document-picker-folder", args=[contract.id, ancestor.id]))
        )

    table_rows = []
    for subfolder in subfolders:
        table_rows.append(
            ui_tags.TableRow(
                cells=(
                    _component_html(
                        "ui/components/button.html", ui_tags.corrux_button,
                        label=subfolder.name, variant="tertiary",
                        href=reverse(
                            "ui-document-picker-folder", args=[contract.id, subfolder.id]
                        ),
                    ),
                    "Dossier", "—", "",
                ),
            )
        )
    for document in documents:
        choose_button = _component_html(
            "ui/components/inline_action_form.html", ui_tags.corrux_inline_action_form,
            hidden_fields={},
            action_url=reverse("ui-document-picker-select", args=[contract.id, document.id]),
            label="Choisir", variant="primary",
        )
        table_rows.append(
            ui_tags.TableRow(
                cells=(
                    document.filename, _format_file_size(document.size_bytes),
                    _format_datetime(document.created_at), choose_button,
                ),
            )
        )

    folder_upload_url = (
        reverse("ui-document-picker-upload-folder", args=[contract.id, folder.id])
        if folder is not None
        else reverse("ui-document-picker-upload", args=[contract.id])
    )

    return {
        "breadcrumb_items": breadcrumb_items,
        "table_headers": ["Nom", "Taille", "Créé le", ""],
        "table_rows": table_rows,
        "is_empty": not documents and not subfolders,
        "upload_url": folder_upload_url,
        "contract": contract,
    }


@require_GET
def document_picker(request, contract_id, folder_id=None):
    """Explorateur en mode sélection — UI-404 (Option A)."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={request.path}")

    contract, folder, error_response = _document_picker_access(request, contract_id, folder_id)
    if error_response is not None:
        return error_response

    context = _document_picker_context(contract, folder, request.corrux_user)
    return render(request, "ui/rh/document_picker.html", context)


@require_POST
def document_picker_select(request, contract_id, document_id):
    """Choisit un document existant pour ce contrat — UI-404.

    Correction (Option A, audit Phase 1 UI-405) : rh.contract.write."""
    contract = get_object_or_404(Contract, pk=contract_id)
    if not has_permission(request.corrux_user, "rh.contract.write"):
        return HttpResponseForbidden()

    try:
        link_document_to_contract(
            contract=contract, document_ref=document_id, requesting_user=request.corrux_user
        )
    except DocumentNotAccessibleError:
        return HttpResponseForbidden()

    return HttpResponseRedirect(reverse("ui-employee-contracts", args=[contract.employee_id]))


def _document_picker_upload_drawer_html(contract, upload_url, error_message=""):
    fields_html = render_to_string(
        "ui/employees/upload_fields.html", {"error_message": error_message}
    )
    return _component_html(
        "ui/components/drawer.html", ui_tags.corrux_drawer,
        drawer_id="document-picker-upload-drawer", title="Déposer un document",
        content=fields_html, action=upload_url, method="post", submit_label="Déposer",
        enctype="multipart/form-data", open=True,
    )


def document_picker_upload(request, contract_id, folder_id=None):
    """Dépose un nouveau document et le lie immédiatement au contrat —
    « sélectionner OU déposer » (maquette). Même Dropzone que UI-302,
    dépôt via documents_v1.attach() (jamais un accès direct au
    stockage), liaison via link_document_to_contract() (revérifie
    l'accès)."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(f"{login_url}?next={request.path}")

    contract, folder, error_response = _document_picker_access(request, contract_id, folder_id)
    if error_response is not None:
        return error_response

    if folder is not None and not has_folder_permission(
        request.corrux_user, folder, "write"
    ):
        return render(
            request, "ui/rh/document_picker.html", {"permission_denied": True}, status=403
        )

    upload_url = (
        reverse("ui-document-picker-upload-folder", args=[contract.id, folder.id])
        if folder is not None
        else reverse("ui-document-picker-upload", args=[contract.id])
    )
    error_message = ""

    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            error_message = "Veuillez sélectionner un fichier."
        else:
            try:
                document_ref = attach(
                    content=uploaded_file.read(), filename=uploaded_file.name,
                    owner_user=request.corrux_user, folder=folder,
                    category=request.POST.get("category", "").strip(),
                )
            except DocumentUploadError as exc:
                error_message = str(exc)
            else:
                link_document_to_contract(
                    contract=contract, document_ref=document_ref,
                    requesting_user=request.corrux_user,
                )
                return HttpResponseRedirect(
                    reverse("ui-employee-contracts", args=[contract.employee_id])
                )
    elif request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "POST"])

    context = _document_picker_context(contract, folder, request.corrux_user)
    context["upload_drawer_html"] = _document_picker_upload_drawer_html(
        contract, upload_url, error_message
    )
    return render(request, "ui/rh/document_picker.html", context)


# --- Onglet Contrats ---------------------------------------------------------------


def _contract_table_rows(contracts, can_edit):
    rows = []
    for contract in contracts:
        document_label = f"Document #{contract.document_ref}" if contract.document_ref else "—"
        actions = (
            _modal_trigger_html(
                modal_id=f"edit-contract-{contract.id}", label="Modifier", variant="secondary"
            )
            if can_edit
            else ""
        )
        rows.append(
            ui_tags.TableRow(
                cells=(
                    contract.type,
                    contract.start_date.strftime("%d/%m/%Y"),
                    contract.end_date.strftime("%d/%m/%Y") if contract.end_date else "—",
                    contract.get_status_display(),
                    document_label,
                ),
                actions_html=actions,
            )
        )
    return rows


def _contract_status_options():
    return [(value, label) for value, label in Contract.Status.choices]


def _contract_create_drawer_content(errors=None, values=None):
    errors = errors or {}
    values = values or {}
    return "".join(
        [
            _field_html(
                label="Type de contrat", name="type", field_id="id_create_contract_type",
                value=values.get("type", ""), required=True, error=errors.get("type", ""),
            ),
            _field_html(
                label="Date de début", name="start_date", field_id="id_create_contract_start",
                input_type="date", value=values.get("start_date", ""), required=True,
                error=errors.get("start_date", ""),
            ),
            _field_html(
                label="Date de fin (si applicable)", name="end_date",
                field_id="id_create_contract_end", input_type="date",
                value=values.get("end_date", ""), error=errors.get("end_date", ""),
            ),
            _field_html(
                label="Statut", name="status", field_id="id_create_contract_status",
                input_type="select", options=_contract_status_options(),
                value=values.get("status", Contract.Status.ACTIVE),
            ),
            '<p class="corrux-text-small">Le document contractuel peut être lié une fois '
            "le contrat créé, depuis la fiche employé.</p>",
        ]
    )


def _contract_edit_drawer_content(contract, errors=None, values=None):
    errors = errors or {}
    values = values or {}
    document_section = (
        f'<p class="corrux-text-small">Document lié : Document #{contract.document_ref}</p>'
        if contract.document_ref
        else '<p class="corrux-text-small">Aucun document lié.</p>'
    )
    link_button = _component_html(
        "ui/components/button.html", ui_tags.corrux_button,
        label="Lier un document", variant="secondary",
        href=reverse("ui-document-picker", args=[contract.id]),
    )
    return "".join(
        [
            _field_html(
                label="Type de contrat", name="type",
                field_id=f"id_edit_contract_{contract.id}_type",
                value=values.get("type", contract.type), required=True,
                error=errors.get("type", ""),
            ),
            _field_html(
                label="Date de début", name="start_date",
                field_id=f"id_edit_contract_{contract.id}_start", input_type="date",
                value=values.get("start_date", contract.start_date.isoformat()), required=True,
                error=errors.get("start_date", ""),
            ),
            _field_html(
                label="Date de fin (si applicable)", name="end_date",
                field_id=f"id_edit_contract_{contract.id}_end", input_type="date",
                value=values.get(
                    "end_date", contract.end_date.isoformat() if contract.end_date else ""
                ),
                error=errors.get("end_date", ""),
            ),
            _field_html(
                label="Statut", name="status",
                field_id=f"id_edit_contract_{contract.id}_status", input_type="select",
                options=_contract_status_options(),
                value=values.get("status", contract.status),
            ),
            document_section,
            link_button,
        ]
    )


def _render_employee_contracts_page(
    request, employee, *, open_drawer_id="",
    create_errors=None, create_values=None,
    edit_contract_id=None, edit_errors=None, edit_values=None,
    http_status=200,
):
    """Correction (Option A, audit Phase 1 UI-405) : can_edit_contracts
    (rh.contract.write) est distinct de can_edit_employee
    (rh.employee.write, en-tête partagé) — deux ressources déclarées
    séparément par le manifeste RH (TECH-035), la maquette exige la
    même granularité (rh.contrat.lire/creer distinct de
    rh.employe.lire/creer)."""
    contracts = list(Contract.objects.filter(employee=employee).order_by("-start_date"))
    can_edit_employee = has_permission(request.corrux_user, "rh.employee.write")
    can_edit_contracts = has_permission(request.corrux_user, "rh.contract.write")

    edit_drawers_html = "".join(
        _component_html(
            "ui/components/drawer.html", ui_tags.corrux_drawer,
            drawer_id=f"edit-contract-{c.id}", title=f"Modifier le contrat « {c.type} »",
            action=reverse("ui-employee-contract-edit", args=[employee.id, c.id]),
            content=_contract_edit_drawer_content(
                c,
                errors=(edit_errors if edit_contract_id == c.id else None),
                values=(edit_values if edit_contract_id == c.id else None),
            ),
            open=(open_drawer_id == f"edit-contract-{c.id}"),
        )
        for c in contracts
    )

    context = {
        "header_html": _employee_record_header(employee, can_edit_employee),
        "tabs_html": _employee_record_tabs(employee, "contrats"),
        "table_headers": ["Type", "Début", "Fin", "Statut", "Document lié", "Actions"],
        "table_rows": _contract_table_rows(contracts, can_edit_contracts) if contracts else [],
        "contracts_empty": not contracts,
        "can_edit": can_edit_contracts,
        "create_drawer_content": _contract_create_drawer_content(create_errors, create_values),
        "create_url": reverse("ui-employee-contract-create", args=[employee.id]),
        "create_drawer_open": open_drawer_id == "create-contract",
        "edit_drawers_html": edit_drawers_html,
    }
    return render(request, "ui/employees/contracts_tab.html", context, status=http_status)


@require_GET
def employee_contracts_tab(request, employee_id):
    """Onglet Contrats — UI-404.

    Correction (Option A, audit Phase 1 UI-405) : rh.employee.read
    (accès à la fiche) PUIS rh.contract.read séparément pour le
    contenu de cet onglet précis — même patron à deux niveaux que
    UI-403 (documentation.document.read)."""
    if request.corrux_user is None:
        login_url = reverse("ui-login")
        return HttpResponseRedirect(
            f"{login_url}?next={reverse('ui-employee-contracts', args=[employee_id])}"
        )

    employee = get_object_or_404(Employee, pk=employee_id)
    if not module_is_activated("rh") or not has_permission(
        request.corrux_user, "rh.employee.read"
    ):
        return render(
            request, "ui/employees/detail.html", {"permission_denied": True}, status=403
        )

    can_edit_employee = has_permission(request.corrux_user, "rh.employee.write")
    if not has_permission(request.corrux_user, "rh.contract.read"):
        context = {
            "header_html": _employee_record_header(employee, can_edit_employee),
            "tabs_html": _employee_record_tabs(employee, "contrats"),
            "tab_permission_denied": True,
        }
        return render(request, "ui/employees/contracts_tab.html", context, status=403)

    return _render_employee_contracts_page(request, employee)


def _parse_contract_date(raw_value):
    try:
        return date.fromisoformat(raw_value)
    except (TypeError, ValueError):
        return None


@require_permission("rh.contract.write")
@require_POST
def employee_contract_create(request, employee_id):
    employee = get_object_or_404(Employee, pk=employee_id)

    contract_type = request.POST.get("type", "").strip()
    start_date_raw = request.POST.get("start_date", "").strip()
    end_date_raw = request.POST.get("end_date", "").strip()
    status = request.POST.get("status", Contract.Status.ACTIVE).strip()

    errors = {}
    if not contract_type:
        errors["type"] = "Ce champ est requis."
    start_date_value = _parse_contract_date(start_date_raw)
    if not start_date_raw:
        errors["start_date"] = "Ce champ est requis."
    elif start_date_value is None:
        errors["start_date"] = "Date invalide."
    end_date_value = _parse_contract_date(end_date_raw) if end_date_raw else None
    if end_date_raw and end_date_value is None:
        errors["end_date"] = "Date invalide."

    values = {
        "type": contract_type, "start_date": start_date_raw, "end_date": end_date_raw,
        "status": status,
    }
    if errors:
        return _render_employee_contracts_page(
            request, employee, open_drawer_id="create-contract",
            create_errors=errors, create_values=values, http_status=400,
        )

    create_contract(
        employee=employee, type=contract_type, start_date=start_date_value,
        end_date=end_date_value, status=status,
    )

    record_audit_event(
        actor=request.corrux_user, action="rh.contract_create",
        target=employee_full_name(employee), metadata={"type": contract_type},
    )
    return HttpResponseRedirect(reverse("ui-employee-contracts", args=[employee.id]))


@require_permission("rh.contract.write")
def employee_contract_edit(request, employee_id, contract_id):
    employee = get_object_or_404(Employee, pk=employee_id)
    contract = get_object_or_404(Contract, pk=contract_id, employee=employee)

    if request.method not in ("GET", "HEAD", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    if request.method != "POST":
        return _render_employee_contracts_page(
            request, employee, open_drawer_id=f"edit-contract-{contract.id}"
        )

    contract_type = request.POST.get("type", "").strip()
    start_date_raw = request.POST.get("start_date", "").strip()
    end_date_raw = request.POST.get("end_date", "").strip()
    status = request.POST.get("status", "").strip()

    errors = {}
    if not contract_type:
        errors["type"] = "Ce champ est requis."
    start_date_value = _parse_contract_date(start_date_raw)
    if not start_date_raw:
        errors["start_date"] = "Ce champ est requis."
    elif start_date_value is None:
        errors["start_date"] = "Date invalide."
    end_date_value = _parse_contract_date(end_date_raw) if end_date_raw else None
    if end_date_raw and end_date_value is None:
        errors["end_date"] = "Date invalide."

    values = {
        "type": contract_type, "start_date": start_date_raw, "end_date": end_date_raw,
        "status": status,
    }
    if errors:
        return _render_employee_contracts_page(
            request, employee, open_drawer_id=f"edit-contract-{contract.id}",
            edit_contract_id=contract.id, edit_errors=errors, edit_values=values,
            http_status=400,
        )

    update_contract(
        contract=contract, type=contract_type, start_date=start_date_value,
        end_date=end_date_value, status=status, document_ref=contract.document_ref,
    )

    record_audit_event(
        actor=request.corrux_user, action="rh.contract_update",
        target=employee_full_name(employee), metadata={"type": contract_type},
    )
    return HttpResponseRedirect(reverse("ui-employee-contracts", args=[employee.id]))
