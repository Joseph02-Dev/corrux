"""Décorateur d'autorisation pour vues Django classiques — TECH-003.

S'appuie sur `request.corrux_user`, résolu par
core.identity.middleware.CorruxAuthenticationMiddleware (TECH-002) : ne
réimplémente aucune authentification, ne fait confiance à aucune donnée
fournie par le client (rôle, permission, en-tête) — seule la résolution
serveur via core.authz.engine fait autorité.
"""

from functools import wraps

from django.http import JsonResponse

from core.authz.engine import has_permission


def require_permission(permission_code: str):
    """Protège une vue : 401 si non authentifié, 403 si permission absente.

    Usage :
        @require_permission("documentation.document.read")
        def ma_vue(request):
            ...
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            user = getattr(request, "corrux_user", None)
            if user is None:
                return JsonResponse(
                    {"detail": "Authentification requise."}, status=401
                )
            if not has_permission(user, permission_code):
                return JsonResponse({"detail": "Permission refusée."}, status=403)
            return view_func(request, *args, **kwargs)

        return wrapped_view

    return decorator
