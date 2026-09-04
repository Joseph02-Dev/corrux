"""Modèles du schéma `rh` — table PostgreSQL `rh.*`.

Cf. architecture-technique-v1.md §7. Structure de données uniquement,
même patron que TECH-020 (Documentation) : aucune logique métier
(CRUD, validation, workflow d'approbation), aucune vue HTTP.

--- Contrainte architecturale impérative ---
`document_ref` (Contract.document_ref, EmployeeDocuments.document_ref)
est un entier OPAQUE (l'identifiant retourné par
`modules.documentation.documents_v1.attach()`, TECH-024) — **jamais**
une ForeignKey Django vers `modules.documentation.models.Document`.
RH ne doit jamais importer les modèles internes de Documentation
(architecture §15 : « aucun accès direct aux modèles internes depuis
un autre module » ; confirmé par TECH-024 : la relation
employé <-> document est portée uniquement par `employee_documents`,
une « table de liaison pure » côté RH — Documentation ne la connaît
jamais). Toute résolution de métadonnées à partir d'un `document_ref`
passera exclusivement par `documents_v1.get()`/`list_for_owner()`
(TECH-034, futur), jamais par un accès direct au modèle.

--- Résolutions documentées (§7 ne les précise pas explicitement) ---

on_delete : §7 ne spécifie aucune stratégie de suppression, même
constat que pour TECH-020. Résolu par cohérence avec les précédents
déjà établis dans le projet :
- Employee.user -> User = SET_NULL (pas PROTECT, volontairement
  différent du patron TECH-020) : la relation est explicitement
  optionnelle et représente un simple lien vers un compte, pas une
  propriété — « compte utilisateur et fiche employé restent deux
  objets distincts » (critère d'acceptation explicite de ce ticket).
  Si le compte lié venait à disparaître, la fiche employé doit
  survivre intacte, seulement délliée — c'est le comportement voulu
  par la séparation déjà actée, pas une perte d'information à éviter.
- Contract.employee / LeaveRequest.employee / EmployeeDocuments.employee
  -> Employee = PROTECT (même raisonnement que TECH-020 : empêche une
  suppression en cascade accidentelle de l'historique contrats/congés/
  documents d'un employé via une seule suppression de fiche ; aucune
  fonctionnalité de suppression d'employé n'existe d'ailleurs nulle
  part dans le produit — seul "marquer comme inactif" est prévu,
  maquettes-ui-v1-lot4.md).
- LeaveRequest.approver_user -> User = PROTECT (même raisonnement que
  core.audit_log.actor_user, TECH-008 : une trace de qui a approuvé/
  refusé une demande ne doit jamais disparaître silencieusement).

Écart signalé, pas résolu silencieusement : maquettes-ui-v1-lot4.md
(Drawer Contrat) liste un champ "Statut" pour les contrats, absent du
schéma `contracts` de §7 (id, employee_id, type, start_date, end_date,
document_ref — aucun statut). Ce ticket pose le schéma exactement tel
que défini par §7, source normative explicite de TECH-030 ("poser le
schéma rh (§7)") ; l'écart avec la maquette reste à trancher par le
ticket qui implémentera réellement la gestion des contrats (TECH-032),
pas ici.

Contract.end_date et Contract.document_ref : nullables — un contrat
peut être à durée indéterminée (maquette : "Date de fin (si
applicable)") et un contrat peut être créé avant qu'un document ne lui
soit lié (maquette : action « Lier un document » séparée de la
création).
"""

from __future__ import annotations

from django.db import models

from core.identity.models import User


class Employee(models.Model):
    class Status(models.TextChoices):
        """Actif/Inactif — même convention que User.Status (TECH-001),
        cohérent avec ux-ui-design-v1.md §4 (StatusBadge)."""

        ACTIVE = "active", "Actif"
        INACTIVE = "inactive", "Inactif"

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profile",
    )
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    position = models.CharField(max_length=255)
    hire_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        app_label = "rh"
        db_table = '"rh"."employees"'
        verbose_name = "Employé"
        verbose_name_plural = "Employés"

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Contract(models.Model):
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="contracts"
    )
    type = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    # Référence opaque documents.v1 (Document.id) — jamais une
    # ForeignKey vers modules.documentation.models.Document, cf.
    # contrainte architecturale en tête de fichier.
    document_ref = models.IntegerField(null=True, blank=True)

    class Meta:
        app_label = "rh"
        db_table = '"rh"."contracts"'
        verbose_name = "Contrat"
        verbose_name_plural = "Contrats"

    def __str__(self):
        return f"{self.type} — {self.employee}"


class LeaveRequest(models.Model):
    class Status(models.TextChoices):
        """Valeurs imposées explicitement par §7 :
        status[pending|approved|rejected]."""

        PENDING = "pending", "En attente"
        APPROVED = "approved", "Approuvé"
        REJECTED = "rejected", "Refusé"

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="leave_requests"
    )
    type = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    approver_user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_leave_requests",
    )

    class Meta:
        app_label = "rh"
        db_table = '"rh"."leave_requests"'
        verbose_name = "Demande de congé"
        verbose_name_plural = "Demandes de congé"

    def __str__(self):
        return f"{self.employee} — {self.type} ({self.get_status_display()})"


class EmployeeDocument(models.Model):
    """Table de liaison pure (architecture §7) : la seule association
    employé <-> document connue du système. Documentation ne la
    connaît jamais — cf. contrainte architecturale en tête de fichier."""

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="employee_documents"
    )
    # Référence opaque documents.v1 (Document.id) — jamais une
    # ForeignKey vers modules.documentation.models.Document.
    document_ref = models.IntegerField()

    class Meta:
        app_label = "rh"
        db_table = '"rh"."employee_documents"'
        verbose_name = "Document employé"
        verbose_name_plural = "Documents employé"
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "document_ref"],
                name="uniq_employee_document",
            ),
        ]

    def __str__(self):
        return f"{self.employee} — document {self.document_ref}"
