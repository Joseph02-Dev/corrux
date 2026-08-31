"""Middleware attachant l'utilisateur CORRUX authentifié à la requête.

Résout uniquement `request.corrux_user` (User CORRUX ou None) depuis la
session. Ne fait aucune vérification de permission — TECH-003 s'appuiera
sur cet attribut pour son moteur RBAC.
"""

from core.identity import auth


class CorruxAuthenticationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.corrux_user = auth.get_authenticated_user(request)
        return self.get_response(request)
