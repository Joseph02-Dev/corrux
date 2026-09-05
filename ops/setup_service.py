"""Orchestration de corrux-setup — assistant d'installation initiale
(§4, §11.1) — TECH-012.

Fonctions pures d'orchestration (testables directement, sans prompt
interactif) — cf. ops/management/commands/corrux_setup.py pour le
wizard CLI qui les appelle après avoir recueilli les réponses du
technicien. Chaque étape valide avant de passer à la suivante (§4 UX) :
toute SetupError interrompt immédiatement le processus, jamais d'état
partiel silencieux.

Résolution d'un verrou de démarrage réel, découvert lors de l'audit
Phase 1 de ce ticket : le rôle « Administrateur » est seedé vide par
migration (core/migrations/0006_seed_predefined_roles.py, UI-201) —
aucune permission par défaut, celles-ci étant normalement attribuées
interactivement via la Matrice de permissions (UI-202). Mais cet écran
exige lui-même core.role.write, que ce rôle vide ne détient pas encore
— sans intervention, le tout premier compte administrateur d'une
installation fraîche ne pourrait rien faire du tout.
complete_administrator_bootstrap() résout ce verrou : une fois les
modules activés (donc leurs permissions enregistrées), elle attribue
au rôle Administrateur l'ensemble des permissions alors connues.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction

from core.audit.service import record_audit_event
from core.authz.models import Permission, Role, RolePermission, UserRole
from core.backup.database import DatabaseConnectionParams
from core.backup.models import BackupRun
from core.backup.service import BackupConfig, run_backup
from core.certs.service import CertificatePaths, initialize_ca_and_server_certificate
from core.identity import auth
from core.identity.models import User
from core.modules.manager import activate_module, install_module
from core.modules.manifest import parse_manifest_file
from core.modules.models import Module


class SetupError(Exception):
    """Une étape de corrux-setup a échoué — le processus s'arrête
    immédiatement, jamais d'état partiel."""


# --- 1. Compte administrateur initial --------------------------------------------


def create_initial_admin(*, username: str, password: str, full_name: str) -> User:
    """Crée le compte administrateur initial et lui assigne le rôle
    Administrateur (déjà seedé, vide, par la migration 0006)."""
    user = User.objects.create(username=username, full_name=full_name)
    auth.set_user_password(user, password)
    user.save()

    role = Role.objects.get(name="Administrateur")
    UserRole.objects.create(user=user, role=role)
    return user


def complete_administrator_bootstrap(*, actor: User) -> int:
    """Attribue au rôle Administrateur l'ensemble des permissions
    actuellement enregistrées — résout le verrou de démarrage (cf.
    docstring de module). Appelée après l'activation des modules, pas
    avant : leurs permissions doivent déjà être enregistrées.

    Retourne le nombre de permissions attribuées."""
    role = Role.objects.get(name="Administrateur")
    all_permissions = list(Permission.objects.all())
    for permission in all_permissions:
        RolePermission.objects.get_or_create(role=role, permission=permission)

    record_audit_event(
        actor=actor, action="setup.administrator_bootstrap_completed",
        target="Administrateur", metadata={"permission_count": len(all_permissions)},
    )
    return len(all_permissions)


# --- 2. Support de sauvegarde — détection/formatage/montage ---------------------


@dataclass(frozen=True)
class CandidateVolume:
    """Un volume candidat au support de sauvegarde — jamais le disque
    système (exclu par construction, cf. detect_candidate_volumes)."""

    device_path: str
    size: str
    fstype: str | None
    already_mounted_at: str | None


def detect_candidate_volumes(*, runner=subprocess.run) -> list[CandidateVolume]:
    """Liste les disques candidats — exclut explicitement celui portant
    la racine (§11.1 : « jamais un simple sous-dossier du disque
    système », a fortiori jamais le disque système lui-même)."""
    root_result = runner(
        ["findmnt", "-no", "SOURCE", "/"], capture_output=True, text=True, check=False
    )
    if root_result.returncode != 0:
        raise SetupError(
            f"Détection du disque système échouée : {root_result.stderr.strip()}"
        )
    root_device = root_result.stdout.strip()

    lsblk_result = runner(
        ["lsblk", "-J", "-o", "NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE"],
        capture_output=True, text=True, check=False,
    )
    if lsblk_result.returncode != 0:
        raise SetupError(f"Détection des volumes échouée : {lsblk_result.stderr.strip()}")

    data = json.loads(lsblk_result.stdout)
    candidates = []
    for device in data.get("blockdevices", []):
        # "disk" en production réelle ; "loop" inclus aussi — un
        # périphérique loopback se comporte identiquement pour le
        # formatage/montage, et c'est le seul moyen de tester cette
        # détection sans disque physique réel (audit Phase 1, TECH-012).
        if device.get("type") not in ("disk", "loop"):
            continue
        device_path = f"/dev/{device['name']}"
        if device_path == root_device:
            continue
        candidates.append(
            CandidateVolume(
                device_path=device_path,
                size=device.get("size") or "",
                fstype=device.get("fstype"),
                already_mounted_at=device.get("mountpoint"),
            )
        )
    return candidates


def format_volume(device_path: str, *, actor: User | None, runner=subprocess.run) -> None:
    """Formate un volume en ext4 — opération destructive. N'est JAMAIS
    appelée sans confirmation explicite préalable (couche appelante,
    wizard interactif) : cette fonction elle-même ne demande jamais de
    confirmation, elle exécute — la sécurité vient de qui l'appelle et
    quand, pas d'ici."""
    result = runner(
        ["mkfs.ext4", "-F", device_path], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SetupError(f"Formatage de {device_path} échoué : {result.stderr.strip()}")

    record_audit_event(
        actor=actor, action="setup.volume_formatted", target=device_path, metadata={},
    )


def mount_volume_persistently(
    device_path: str,
    mount_point: Path,
    *,
    fstab_path: Path,
    actor: User | None,
    runner=subprocess.run,
) -> None:
    """Monte le volume et l'ajoute au fichier fstab fourni (chemin
    TOUJOURS injectable, jamais `/etc/fstab` codé en dur — testable
    sans jamais toucher au système réel qui exécute les tests)."""
    mount_point.mkdir(parents=True, exist_ok=True)
    result = runner(
        ["mount", device_path, str(mount_point)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SetupError(f"Montage de {device_path} échoué : {result.stderr.strip()}")

    fstab_line = f"{device_path}\t{mount_point}\text4\tdefaults\t0\t2\n"
    with open(fstab_path, "a") as fstab_file:
        fstab_file.write(fstab_line)

    record_audit_event(
        actor=actor,
        action="setup.volume_mounted",
        target=device_path,
        metadata={"mount_point": str(mount_point)},
    )


# --- 3. Orchestration complète ----------------------------------------------------


@dataclass(frozen=True)
class CorruxSetupConfig:
    """Paramètres complets d'une exécution de corrux-setup — jamais de
    valeur codée en dur dans run_corrux_setup() elle-même."""

    admin_username: str
    admin_password: str
    admin_full_name: str
    certificate_common_name: str
    certificate_paths: CertificatePaths
    backup_device_path: str
    backup_mount_point: Path
    backup_fstab_path: Path
    format_backup_volume: bool
    documentation_manifest_path: Path
    rh_manifest_path: Path
    backup_storage_root: Path
    backup_gpg_recipient_key_path: Path
    backup_db_params: DatabaseConnectionParams
    backup_system_root: Path = Path("/")


@dataclass(frozen=True)
class CorruxSetupResult:
    """Résultat d'une exécution réussie — permet de vérifier les
    critères d'acceptation explicites du ticket (HTTPS fonctionnel,
    sauvegarde active, modules activés) sans avoir à tout requêter à
    nouveau."""

    admin: User
    backup_run: BackupRun
    documentation_module: Module
    rh_module: Module
    permissions_granted_to_administrator: int


def run_corrux_setup(config: CorruxSetupConfig) -> CorruxSetupResult:
    """Orchestration complète — §4 : identité -> compte admin ->
    certificat -> support de sauvegarde -> activation des modules.

    Chaque étape valide avant de passer à la suivante (§4 UX) ; toute
    SetupError interrompt immédiatement l'exécution — enveloppée dans
    une transaction atomique : si une étape échoue, AUCUNE ligne créée
    par les étapes précédentes (compte admin y compris) ne subsiste en
    base — jamais d'état partiel, y compris pour la toute première
    étape. Bug réel trouvé par test avant correction : sans cette
    enveloppe, un échec de montage du support de sauvegarde (étape
    tardive) laissait le compte administrateur déjà créé — contraire
    au critère d'acceptation explicite du ticket (« ne peut pas se
    terminer sans support désigné »).

    Note : la transaction couvre l'état BASE DE DONNÉES uniquement —
    les fichiers déjà écrits sur disque (certificat, formatage) avant
    un échec ultérieur ne sont pas automatiquement annulés (limite
    inhérente à toute opération fichier/disque, hors périmètre d'une
    transaction SQL).
    """
    with transaction.atomic():
        admin = create_initial_admin(
            username=config.admin_username, password=config.admin_password,
            full_name=config.admin_full_name,
        )

        initialize_ca_and_server_certificate(
            config.certificate_paths, common_name=config.certificate_common_name, actor=admin,
        )

        if config.format_backup_volume:
            format_volume(config.backup_device_path, actor=admin)
        mount_volume_persistently(
            config.backup_device_path, config.backup_mount_point,
            fstab_path=config.backup_fstab_path, actor=admin,
        )

        documentation_manifest = parse_manifest_file(config.documentation_manifest_path)
        install_module(documentation_manifest, actor=admin)
        documentation_module = activate_module("documentation", actor=admin)

        rh_manifest = parse_manifest_file(config.rh_manifest_path)
        install_module(rh_manifest, actor=admin)
        rh_module = activate_module("rh", actor=admin)

        permissions_granted = complete_administrator_bootstrap(actor=admin)

        backup_config = BackupConfig(
            destination_dir=config.backup_mount_point,
            db_params=config.backup_db_params,
            storage_root=config.backup_storage_root,
            gpg_recipient_key_path=config.backup_gpg_recipient_key_path,
            system_root=config.backup_system_root,
        )
        backup_run = run_backup(backup_config, actor=admin)
        if backup_run.status != BackupRun.Status.SUCCESS:
            raise SetupError(
                f"La sauvegarde de vérification finale a échoué (statut : "
                f"{backup_run.status}) — l'installation n'est pas considérée "
                f"comme terminée avec succès."
            )

        return CorruxSetupResult(
            admin=admin, backup_run=backup_run,
            documentation_module=documentation_module, rh_module=rh_module,
            permissions_granted_to_administrator=permissions_granted,
        )
