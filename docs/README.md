# CORRUX — Documentation technicien

Procédures opérationnelles pas-à-pas pour l'installation et l'exploitation
courante d'une instance CORRUX. Chaque procédure est autonome : un
technicien peut la suivre sans support supplémentaire.

- [Installation initiale](installation.md) — première mise en route sur un
  PC/serveur neuf (`corrux-setup`).
- [Sauvegarde et restauration](sauvegarde-restauration.md) — consultation
  des sauvegardes planifiées, procédure de restauration manuelle.
- [Renouvellement du certificat HTTPS](certificat.md) — `corrux-cert
  renew`, vérification d'expiration.
- [Mise à jour](mise-a-jour.md) — mode hors ligne (support amovible) et
  mode en ligne (dépôt apt distant).

## Convention commune à toutes les procédures

Toutes les commandes ci-après s'exécutent depuis la racine de
l'installation CORRUX (`/opt/corrux/`, cf. [installation.md](installation.md)),
avec l'environnement virtuel Python de l'application activé :

```bash
cd /opt/corrux
source .venv/bin/activate
```

Les variables d'environnement `DB_NAME`, `DB_USER`, `DB_PASSWORD`,
`DB_HOST`, `DB_PORT` et `DJANGO_SETTINGS_MODULE=corrux_core.settings`
doivent être définies dans l'environnement du technicien ou dans un
fichier `.env` chargé au démarrage du shell — la procédure
d'installation ([installation.md](installation.md)) les met en place lors
de la première mise en route.
