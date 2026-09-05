# Mise à jour

CORRUX propose deux canaux de mise à jour, tous deux vérifiés par
signature GPG et checksums SHA-256 contre la clé publique de release déjà
installée sur le système — **aucune mise à jour automatique en tâche de
fond** : le déclenchement reste toujours un acte volontaire du technicien.

- **Mode hors ligne** (support amovible) : mode de référence, garanti sur
  toute installation, sans accès Internet requis.
- **Mode en ligne** (dépôt apt distant) : canal complémentaire, utilisable
  quand la machine dispose ponctuellement d'un accès Internet — mêmes
  garanties de sécurité, apt vérifiant nativement la signature du dépôt.

Dans les deux cas, un échec de vérification (signature invalide,
checksum invalide, dépôt non authentifié) **refuse totalement et
atomiquement** la mise à jour : aucun paquet n'est appliqué, un message
explicite est affiché, et l'événement est journalisé dans le journal
d'audit.

## Mode hors ligne (support amovible)

### Étape 1 — Préparer le support

Le support (clé USB ou équivalent) doit contenir, à sa racine :
- les fichiers `.deb` de la mise à jour,
- `update-manifest.yaml` (version, liste des paquets et leurs checksums
  SHA-256, modules impactés),
- `update-manifest.yaml.sig` (signature GPG détachée du manifeste,
  produite avec la clé privée de release).

Ce bundle est fourni tel quel par l'éditeur CORRUX — le technicien n'a
qu'à le copier sur le support, jamais à le reconstruire.

### Étape 2 — Brancher le support et identifier le périphérique

```bash
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE
```

Noter le chemin du périphérique correspondant au support (ex. `/dev/sdc1`).

### Étape 3 — Appliquer la mise à jour

```bash
cd /opt/corrux && source .venv/bin/activate
export CORRUX_UPDATE_TRUSTED_KEY_PATH=/etc/corrux/release-public-key.asc
python manage.py apply_offline_update --device /dev/sdc1
```

En sortie, en cas de succès :

```
Mise à jour appliquée avec succès (<version>).
Paquets appliqués : <liste des .deb installés>
Modules migrés : <liste des modules>
```

En cas d'échec (signature invalide, checksum invalide, paquet absent du
support), la commande affiche le message d'erreur explicite et se termine
sans avoir rien modifié sur le système — recommencer avec un support de
mise à jour authentique.

## Mode en ligne (dépôt apt distant)

Utilisable quand la machine dispose ponctuellement d'un accès Internet.
Le dépôt distant est signé avec la **même clé** que le mode hors ligne ;
la vérification est assurée nativement par apt lui-même (mécanisme
standard de dépôt signé), pas par un mécanisme propre à CORRUX.

```bash
cd /opt/corrux && source .venv/bin/activate
export CORRUX_UPDATE_REPOSITORY_URL=https://updates.corrux.exemple/
export CORRUX_UPDATE_TRUSTED_KEYRING_PATH=/etc/corrux/release-keyring.gpg
export CORRUX_UPDATE_CA_CERT_PATH=/etc/ssl/certs/ca-certificates.crt
python manage.py apply_online_update \
  --packages corrux-core,corrux-module-documentation,corrux-module-rh \
  --modules core,documentation,rh \
  --target-version <version>
```

`--packages` : les paquets `corrux-*` à mettre à jour, séparés par des
virgules (jamais un motif générique — toujours la liste exacte attendue).
`--modules` : les modules Django dont les migrations en attente doivent
être exécutées après l'installation des paquets (mêmes noms que les
schémas de base de données : `core`, `documentation`, `rh`).

La sortie et le comportement en cas d'échec sont identiques au mode hors
ligne — l'état final du système est le même quel que soit le canal
utilisé.

## Après une mise à jour, dans tous les cas

Consulter le journal d'audit (**Administration › Journal d'audit**,
action `update.applied` ou `update.refused`) pour confirmer la version
appliquée et les paquets concernés. Si des unités systemd ont été
modifiées par la mise à jour, recharger la configuration système :

```bash
systemctl daemon-reload
```
