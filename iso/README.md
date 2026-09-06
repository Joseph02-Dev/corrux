# Build ISO CORRUX (BUILD-003 — prototype)

Construit une ISO serveur Debian 13 bootable (BIOS + UEFI) embarquant
les paquets CORRUX et un preseed d'installation automatisée. Ne
modifie jamais la chaîne de boot officielle Debian (isolinux/grub-efi
tels que fournis) — Secure Boot n'est pas cassé.

## Prérequis (machine de build)

- `xorriso`
- `dpkg-dev` (fournit `dpkg-scanpackages`)
- `isolinux` (fournit `isohdpfx.bin`, nécessaire à l'hybridation MBR)
- Accès réseau sortant vers `cdimage.debian.org` (téléchargement, mis
  en cache localement — un seul téléchargement par version Debian)
- Python 3 avec les dépendances du dépôt CORRUX installées

## Utilisation

```bash
export CORRUX_TECH_PASSWORD_HASH='$6$...'   # hash crypt SHA-512, jamais en clair
                                              # génération : openssl passwd -6 '<mot de passe>'

iso/build_iso.sh <version> <chemin_clé_publique_gpg_release> <répertoire_de_travail>
```

Exemple :

```bash
iso/build_iso.sh 1.0.0 /chemin/vers/corrux-release-public-key.asc /var/tmp/corrux-iso-build
```

Produit `<répertoire_de_travail>/corrux-server-<version>.iso` et son
`.sha256`.

## Ce que fait le script

1. Télécharge (et met en cache) l'image Debian 13 netinst officielle,
   vérifie son intégrité contre `SHA256SUMS` officiel.
2. Extrait l'image.
3. Reconstruit les 3 paquets `.deb` CORRUX à partir du code du dépôt
   (réutilise `packaging/build_packages.py`, TECH-043 — aucune
   réimplémentation) et constitue un mini-dépôt APT local
   (`iso/build_repo.sh`).
4. Embarque la clé publique de release CORRUX fournie en paramètre
   (jamais générée par ce script).
5. Injecte le preseed (`iso/preseed/corrux.preseed`), avec le hash du
   mot de passe technicien substitué depuis la variable
   d'environnement `CORRUX_TECH_PASSWORD_HASH` — jamais de valeur en
   clair, jamais de valeur par défaut.
6. Régénère `md5sum.txt` (contrôle d'intégrité debian-installer).
7. Réassemble une ISO hybride BIOS+UEFI (`xorriso`), calcule son
   checksum SHA-256.

## Ce que ce prototype NE couvre PAS (hors périmètre BUILD-003)

- **Test de boot réel** (VM/BIOS/UEFI/matériel) — nécessite un outil
  de virtualisation, ticket distinct **BUILD-004**.
- Signature GPG de l'ISO elle-même (la signature existante, TECH-013,
  couvre le bundle de mise à jour `.deb`, pas l'image ISO).
- Détermination du compte administrateur CORRUX applicatif : reste
  entièrement à la charge de `corrux-setup`, exécuté au premier
  démarrage — ce mécanisme ne configure que l'OS.

## Sécurité

- Aucun secret en dur dans le dépôt : mot de passe technicien fourni
  hashé via variable d'environnement au moment du build, clé publique
  de release fournie en paramètre (jamais générée à la volée par ce
  script pour un usage réel).
- Le compte technicien créé par le preseed n'est **pas** root (accès
  sudo uniquement) — cohérent avec le principe « pas d'exécution en
  root » (architecture-technique-v1.md §16).
