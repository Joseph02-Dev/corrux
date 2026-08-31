"""Modèles roles / permissions — tables `core.roles`, `core.user_roles`,
`core.permissions`, `core.role_permissions`.

Cf. architecture-technique-v1.md §7. Le moteur RBAC (vérification d'accès
`module.resource.action`) est implémenté en TECH-003 : ce fichier ne porte
que la structure de données.
"""

from django.db import models

from core.identity.models import User


class Role(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        app_label = "core"
        db_table = '"core"."roles"'
        verbose_name = "Rôle"
        verbose_name_plural = "Rôles"

    def __str__(self):
        return self.name


class UserRole(models.Model):
    """Association utilisateur ↔ rôle (`core.user_roles`).

    Note : §7 liste les colonnes (user_id, role_id) sans mentionner de
    clé `id` explicite. Un id auto-incrémenté standard Django est conservé
    (pratique courante pour une table d'association), complété d'une
    contrainte d'unicité (user, role) qui reproduit exactement la
    sémantique décrite par l'architecture.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="user_roles")

    class Meta:
        app_label = "core"
        db_table = '"core"."user_roles"'
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="uniq_user_role"),
        ]
        verbose_name = "Association utilisateur ↔ rôle"
        verbose_name_plural = "Associations utilisateur ↔ rôle"

    def __str__(self):
        return f"{self.user.username} ↔ {self.role.name}"


class Permission(models.Model):
    """Permission granulaire `module.resource.action` (`core.permissions`).

    Note : `module_id` est stocké comme identifiant texte (ex.
    "documentation", "rh"), tel que déclaré dans le manifest.yaml du
    module (§13). Le §7 ne précise pas de clé étrangère vers une table
    `modules` (celle-ci — `core.modules` — n'existe pas encore : elle est
    ajoutée en TECH-005, hors périmètre de ce ticket). Aucune FK n'est donc
    ajoutée ici ; à réévaluer explicitement lors de TECH-005/TECH-006 si un
    lien relationnel est souhaité.
    """

    module_id = models.CharField(max_length=100)
    resource = models.CharField(max_length=100)
    action = models.CharField(max_length=50)

    class Meta:
        app_label = "core"
        db_table = '"core"."permissions"'
        constraints = [
            models.UniqueConstraint(
                fields=["module_id", "resource", "action"], name="uniq_permission"
            ),
        ]
        verbose_name = "Permission"
        verbose_name_plural = "Permissions"

    def __str__(self):
        return f"{self.module_id}.{self.resource}.{self.action}"


class RolePermission(models.Model):
    """Association rôle ↔ permission (`core.role_permissions`).

    Même remarque que `UserRole` concernant l'id auto-incrémenté.
    """

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="role_permissions"
    )

    class Meta:
        app_label = "core"
        db_table = '"core"."role_permissions"'
        constraints = [
            models.UniqueConstraint(fields=["role", "permission"], name="uniq_role_permission"),
        ]
        verbose_name = "Association rôle ↔ permission"
        verbose_name_plural = "Associations rôle ↔ permission"

    def __str__(self):
        return f"{self.role.name} ↔ {self.permission}"
