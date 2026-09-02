"""Modèles du schéma `documentation` — table PostgreSQL `documentation.*`.

Cf. architecture-technique-v1.md §7. Structure de données uniquement :
aucune logique de stockage (core.storage.files, TECH-004, jamais appelé
ici), aucune vérification de permission (TECH-023, futur), aucune vue
HTTP. Isolation par schéma PostgreSQL via `db_table` qualifié, même
convention que `core` (TECH-001) — le rôle PostgreSQL dédié (défense en
profondeur, §16) reste différé aux tickets packaging/ops, précédent déjà
établi pour `core` (cf. corrux_core/settings.py).

--- Résolutions documentées (§7 ne les précise pas explicitement) ---

on_delete : §7 ne spécifie aucune stratégie de suppression. Résolu par
cohérence avec le précédent déjà établi dans le projet
(core.audit_log.actor_user = PROTECT, "un enregistrement d'audit ne
doit jamais disparaître silencieusement" ; core.module_dependencies =
CASCADE, une ligne de dépendance est sans signification sans son
module) :
- Document.owner_user, Document.folder, Folder.parent_folder = PROTECT
  (données "précieuses" — empêche une suppression en cascade
  accidentelle d'un grand nombre de documents via une seule suppression
  de dossier/utilisateur ; aucune fonctionnalité de suppression
  n'existe d'ailleurs encore nulle part dans le produit).
- DocumentPermission/DocumentMetadata -> leur cible = CASCADE (une ligne
  de permission ou de métadonnée est sans signification une fois sa
  cible supprimée, même logique que module_dependencies).

document_permissions : §7 décrit littéralement une alternative
("document_id ou folder_id", "role_id/user_id"), pas une liste de
colonnes directement transposable. Modélisé avec 4 FK nullables
(document, folder, role, user) + CheckConstraint garantissant
exactement une cible (document/folder) et exactement un bénéficiaire
(role/user) — mécanisme natif Django, aucune infrastructure inventée.

document_metadata : §7 ne liste littéralement que "document_id" (pas
d'alternative folder_id, à la différence de document_permissions) : FK
document unique, requise.

Document.folder : §7 ne précise pas si un document peut exister sans
dossier. Résolu en faveur de la nullabilité (document à la racine),
l'absence de contrainte contraire dans les sources ne justifie pas
d'imposer un dossier obligatoire.
"""

from django.db import models
from django.db.models import Q

from core.authz.models import Role
from core.identity.models import User


class Folder(models.Model):
    parent_folder = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="subfolders",
    )
    name = models.CharField(max_length=255)

    class Meta:
        app_label = "documentation"
        db_table = '"documentation"."folders"'
        verbose_name = "Dossier"
        verbose_name_plural = "Dossiers"

    def __str__(self):
        return self.name


class Document(models.Model):
    owner_user = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="owned_documents"
    )
    folder = models.ForeignKey(
        Folder,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="documents",
    )
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField()
    # Référence de stockage (résolue exclusivement via core.storage.files,
    # TECH-004) — jamais un chemin physique manipulé directement ici,
    # aucun accès disque dans ce module.
    storage_path = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "documentation"
        db_table = '"documentation"."documents"'
        verbose_name = "Document"
        verbose_name_plural = "Documents"

    def __str__(self):
        return self.filename


class DocumentPermission(models.Model):
    """Structure de données uniquement — la vérification d'accès réelle
    (has_permission-like) est TECH-023, pas ce ticket."""

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="permissions",
    )
    folder = models.ForeignKey(
        Folder,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="permissions",
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="document_permissions",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="document_permissions",
    )
    action = models.CharField(max_length=50)

    class Meta:
        app_label = "documentation"
        db_table = '"documentation"."document_permissions"'
        verbose_name = "Permission de document"
        verbose_name_plural = "Permissions de document"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(document__isnull=False, folder__isnull=True)
                    | Q(document__isnull=True, folder__isnull=False)
                ),
                name="document_permission_exactly_one_target",
            ),
            models.CheckConstraint(
                condition=(
                    Q(role__isnull=False, user__isnull=True)
                    | Q(role__isnull=True, user__isnull=False)
                ),
                name="document_permission_exactly_one_grantee",
            ),
        ]

    def __str__(self):
        target = f"document={self.document_id}" if self.document_id else f"folder={self.folder_id}"
        grantee = f"role={self.role_id}" if self.role_id else f"user={self.user_id}"
        return f"{target} · {grantee} · {self.action}"


class DocumentMetadata(models.Model):
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="metadata_entries"
    )
    key = models.CharField(max_length=100)
    value = models.TextField()

    class Meta:
        app_label = "documentation"
        db_table = '"documentation"."document_metadata"'
        verbose_name = "Métadonnée de document"
        verbose_name_plural = "Métadonnées de document"

    def __str__(self):
        return f"{self.key}={self.value}"
