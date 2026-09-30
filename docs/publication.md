# Publication d'une version (éditeur)

Chaque version publiée produit, sur GitHub Pages :

- `https://joseph02-dev.github.io/corrux/install.sh` — script d'installation
  (`curl … | sudo sh`) ;
- `https://joseph02-dev.github.io/corrux/` — dépôt apt signé (paquets
  `corrux`, `corrux-core`, `corrux-module-documentation`, `corrux-module-rh`) ;
- une GitHub Release avec les `.deb`, `SHA256SUMS` et la clé publique.

Tout est fait par `.github/workflows/release.yml` ; seuls les réglages
ci-dessous sont manuels, **une seule fois**.

## 1. Clé de signature du dépôt (une fois)

Sur un poste de confiance (jamais sur un serveur client) :

```bash
gpg --full-generate-key
#   type : RSA and RSA (défaut) — taille : 4096 — expiration : 2y (au choix)
#   nom : CORRUX Archive Signing Key — e-mail : adresse de l'éditeur
#   phrase de passe : longue et aléatoire (conservée dans un coffre-fort)

gpg --list-secret-keys --keyid-format long     # relever l'empreinte (40 caractères)
gpg --armor --export-secret-keys <EMPREINTE> > corrux-archive-signing.asc
```

Conserver une sauvegarde hors ligne de cette clé et de son certificat de
révocation (`~/.gnupg/openpgp-revocs.d/`). **Perdre cette clé impose de
redistribuer une nouvelle clé à tous les clients.**

## 2. Secrets GitHub (une fois)

Dépôt GitHub > *Settings* > *Secrets and variables* > *Actions* >
*New repository secret* :

| Nom | Valeur |
|---|---|
| `CORRUX_APT_SIGNING_KEY` | contenu complet de `corrux-archive-signing.asc` |
| `CORRUX_APT_SIGNING_PASSPHRASE` | la phrase de passe (vide si aucune) |

Supprimer ensuite `corrux-archive-signing.asc` du poste (`shred -u`).

## 3. GitHub Pages (une fois)

Dépôt GitHub > *Settings* > *Pages* > *Build and deployment* >
*Source* : **GitHub Actions**.

## 4. Publier une version

```bash
git checkout main && git pull
git tag v1.0.0
git push origin v1.0.0
```

Le workflow *Release* :

1. télécharge les composants Python (wheels) pour Python 3.10 à 3.13 ;
2. construit les 4 paquets et le dépôt apt, le signe ;
3. **installe réellement** la version signée dans des conteneurs
   Debian 12, Debian 13, Ubuntu 22.04 et Ubuntu 24.04 (install.sh, apt,
   corrux-setup, connexion HTTPS, désinstallation) — la publication est
   annulée au moindre échec ;
4. publie le site sur GitHub Pages et crée la GitHub Release.

Tags de pré-version : `v1.1.0-rc1` devient la version Debian `1.1.0~rc1`
(classée avant `1.1.0`).

## Remarques

- Le dépôt ne contient que la **dernière** version publiée ; les
  précédentes restent téléchargeables depuis les GitHub Releases.
- `install.sh` publié contient l'empreinte de la clé de signature :
  une clé substituée sur le serveur est refusée par le script.
- Les pull requests exécutent le même test d'installation avec une clé
  jetable (`.github/workflows/ci.yml`, job `install-test`).
- Construction locale (sans publication) :

  ```bash
  CORRUX_SIGNING_KEY_ID=<empreinte> CORRUX_REPO_URL=http://127.0.0.1:8765 \
    packaging/build_apt_site.sh 1.0.0 /tmp/corrux-site
  packaging/test_install/run_in_container.sh /tmp/corrux-site ubuntu:24.04 --setup
  ```
