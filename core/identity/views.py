"""Endpoints d'authentification — TECH-002.

Cf. architecture-technique-v1.md §9. Aucune vérification RBAC ici
(TECH-003).

Vues Django classiques (pas rest_framework.views.APIView) : APIView marque
ses vues `csrf_exempt` par défaut (DRF délègue la protection CSRF à
SessionAuthentication, qui ne s'active que si `request.user` est une
session Django standard authentifiée — ce qui n'est jamais le cas ici
puisque l'authentification CORRUX est gérée manuellement, cf.
core.identity.auth). Utiliser APIView aurait donc désactivé silencieusement
la protection CSRF sur le login. Une vue Django standard est protégée par
CsrfViewMiddleware nativement, sans contournement.
"""

import json

from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views import View

from core.identity import auth


def _parse_json_body(request) -> dict:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


class CsrfCookieView(View):
    """Amorce le cookie CSRF avant tout POST (login inclus).

    Django protège aussi les requêtes non authentifiées contre le
    « login CSRF » ; un client doit donc appeler cette route en GET avant
    de poster ses identifiants et renvoyer le token via l'en-tête
    X-CSRFToken.
    """

    def get(self, request):
        get_token(request)
        return JsonResponse({"detail": "ok"})


class LoginView(View):
    def post(self, request):
        data = _parse_json_body(request) or request.POST
        username = data.get("username", "")
        password = data.get("password", "")

        if not username or not password:
            return JsonResponse(
                {"detail": auth.GENERIC_ERROR_MESSAGE}, status=400
            )

        try:
            user = auth.authenticate(username=username, raw_password=password)
        except auth.AuthenticationError as exc:
            return JsonResponse({"detail": str(exc)}, status=401)

        auth.login(request, user)
        return JsonResponse({"username": user.username, "full_name": user.full_name})


class LogoutView(View):
    def post(self, request):
        auth.logout(request)
        return JsonResponse({"detail": "Déconnecté."})
