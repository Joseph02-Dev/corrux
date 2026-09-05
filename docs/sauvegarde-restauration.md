# Sauvegarde et restauration

La sauvegarde planifiée s'exécute automatiquement (`corrux-backup.timer`,
quotidiennement) une fois l'installation initiale terminée — aucune action
technicien n'est requise au jour le jour. Ce document couvre la
consultation de l'historique et la procédure de **restauration**, qui
reste manuelle (aucune restauration self-service outillée en V1).

## Consulter l'historique des sauvegardes

Depuis l'interface web CORRUX : menu **Administration › Sauvegardes**
(nécessite la permission `core.backup.read`). L'écran affiche la date du
dernier succès, le statut de chaque exécution, et la taille de chaque
archive.

En ligne de commande, depuis `/opt/corrux` (environnement virtuel activé,
variables `DB_*` définies) :

```bash
python manage.py shell -c "
from core.backup.models import BackupRun
for run in BackupRun.objects.order_by('-started_at')[:10]:
    print(run.started_at, run.status, run.size_bytes)
"
```

## Déclencher une sauvegarde manuellement

Utile pour vérifier la configuration avant de compter sur la
planification automatique, ou après une modification de la destination :

```bash
export CORRUX_BACKUP_DESTINATION=/mnt/corrux-backup
export CORRUX_BACKUP_GPG_RECIPIENT_KEY_PATH=/etc/corrux/backup-gpg-public.key
python manage.py run_backup
```

La commande refuse de s'exécuter (et journalise une alerte) si
`CORRUX_BACKUP_DESTINATION` ne pointe pas vers un volume physiquement
distinct du disque système — c'est le comportement attendu, pas une
erreur de configuration à contourner.

## Restaurer une sauvegarde

**Procédure manuelle, technicien uniquement — pas de fonctionnalité
applicative.** À utiliser en cas de perte du disque système, de
corruption de la base, ou de retour arrière après un incident.

### Étape 1 — Identifier l'archive à restaurer

Les archives se trouvent sur le support de sauvegarde
(`CORRUX_BACKUP_DESTINATION`), nommées
`corrux-backup-<horodatage>-<identifiant>.tar.gpg` :

```bash
ls -lt /mnt/corrux-backup/corrux-backup-*.tar.gpg | head -5
```

### Étape 2 — Déchiffrer l'archive

Nécessite la clé privée GPG correspondante (conservée séparément par le
technicien ou l'organisation — jamais sur la machine CORRUX elle-même) :

```bash
gpg --homedir /chemin/vers/trousseau-prive \
    --output /tmp/corrux-restore.tar \
    --decrypt /mnt/corrux-backup/corrux-backup-<horodatage>-<id>.tar.gpg
```

### Étape 3 — Extraire l'archive

```bash
mkdir -p /tmp/corrux-restore
tar -xf /tmp/corrux-restore.tar -C /tmp/corrux-restore
ls /tmp/corrux-restore
# database.dump   storage.tar.gz
```

### Étape 4 — Restaurer la base de données

**Arrêter l'application avant cette étape** (`systemctl stop corrux-core`
ou équivalent, pour éviter toute écriture concurrente) :

```bash
pg_restore --clean --if-exists \
  --dbname="postgresql://<utilisateur>:<mot_de_passe>@<hote>:<port>/<nom_base>" \
  /tmp/corrux-restore/database.dump
```

### Étape 5 — Restaurer le stockage documentaire

```bash
rm -rf /var/lib/corrux/storage
mkdir -p /var/lib/corrux/storage
tar -xzf /tmp/corrux-restore/storage.tar.gz -C /var/lib/corrux/storage
```

### Étape 6 — Redémarrer l'application et vérifier

```bash
systemctl start corrux-core
```

Se connecter à l'interface web et confirmer que les données restaurées
sont bien présentes (comptes, documents, fiches employé selon la date de
la sauvegarde restaurée).

### Étape 7 — Nettoyer les fichiers temporaires en clair

```bash
rm -rf /tmp/corrux-restore /tmp/corrux-restore.tar
```

Ne jamais laisser un dump de base ou une archive de stockage en clair sur
disque après la restauration.
