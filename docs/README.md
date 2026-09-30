# CORRUX — Documentation technicien

Procédures opérationnelles pas-à-pas pour l'installation et l'exploitation
courante d'une instance CORRUX. Chaque procédure est autonome : un
technicien peut la suivre sans support supplémentaire.

- [Installation initiale](installation.md) — installation par `curl` ou
  `apt` sur Debian, Ubuntu ou Proxmox VE, puis mise en service
  (`corrux-setup`).
- [Publication d'une version](publication.md) — pour l'éditeur : dépôt
  apt signé et script d'installation.
- [Sauvegarde et restauration](sauvegarde-restauration.md) — consultation
  des sauvegardes planifiées, procédure de restauration manuelle.
- [Renouvellement du certificat HTTPS](certificat.md) — `corrux-cert
  renew`, vérification d'expiration.
- [Mise à jour](mise-a-jour.md) — mode hors ligne (support amovible) et
  mode en ligne (dépôt apt distant).

## Convention commune à toutes les procédures

Toutes les commandes d'administration s'exécutent en root, via
`corrux-manage`, qui charge automatiquement la configuration de
l'instance (`/etc/corrux/core.env` : base de données, clé secrète,
chemins) et l'environnement Python de CORRUX :

```bash
sudo -i
corrux-manage check
```

Les variables propres à une procédure (ex. `CORRUX_BACKUP_DESTINATION`)
sont exportées dans cette même session root avant la commande.
