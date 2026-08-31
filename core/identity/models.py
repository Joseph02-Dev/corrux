"""Modèle utilisateur — table `core.users`.

Cf. architecture-technique-v1.md §7 (schéma `core`) et §9 (identité 100%
locale, mots de passe hashés en argon2id). L'authentification (login,
sessions, vérification de mot de passe) est implémentée en TECH-002 : ce
fichier ne porte que la structure de données.
"""

from django.db import models


class User(models.Model):
    class Status(models.TextChoices):
        """Actif/Inactif — cf. ux-ui-design-v1.md §4 (StatusBadge)."""

        ACTIVE = "active", "Actif"
        INACTIVE = "inactive", "Inactif"

    username = models.CharField(max_length=150, unique=True)
    password_hash = models.CharField(
        max_length=255,
        help_text="Hash Argon2id (django.contrib.auth.hashers.make_password).",
    )
    full_name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core"
        db_table = '"core"."users"'
        verbose_name = "Utilisateur"
        verbose_name_plural = "Utilisateurs"

    def __str__(self):
        return self.username
