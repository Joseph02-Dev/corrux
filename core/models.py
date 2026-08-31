"""Point d'entrée des modèles de l'app Django unique `core` (Platform Core).

Les modèles sont physiquement répartis par sous-domaine (identity/, authz/,
...) pour rester lisibles, mais partagent un seul app_label ("core") et un
seul dossier de migrations (core/migrations/) — cf. TECH-001
(core/identity/models.py, core/authz/models.py). Ce fichier réexporte les
modèles pour que Django les découvre au chargement de l'app.
"""

from core.authz.models import Permission, Role, RolePermission, UserRole  # noqa: F401
from core.identity.models import User  # noqa: F401

__all__ = ["User", "Role", "UserRole", "Permission", "RolePermission"]
