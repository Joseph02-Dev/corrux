# Renouvellement du certificat HTTPS

Le certificat serveur CORRUX (signé par la CA interne, générée lors de
l'installation initiale) a une durée de validité limitée. Un contrôle
automatique (`corrux-cert-check.timer`, quotidien) journalise une alerte
dans le journal d'audit lorsque l'expiration approche — le renouvellement
lui-même reste **manuel**, déclenché par le technicien (aucune
automatisation réseau).

## Vérifier l'état du certificat actuel

```bash
openssl x509 -in /etc/corrux/tls/server.crt -noout -enddate
```

Ou, pour déclencher manuellement le même contrôle que le timer planifié
(journalise une alerte dans `core.audit_log` si l'expiration est proche,
sans rien modifier) :

```bash
cd /opt/corrux && source .venv/bin/activate
export CORRUX_CERT_SERVER_CERT_PATH=/etc/corrux/tls/server.crt
python manage.py check_certificate_expiry
```

## Renouveler le certificat

Le renouvellement émet un **nouveau certificat serveur**, signé par la CA
interne **déjà existante** — la CA elle-même n'est jamais régénérée, les
postes clients ayant déjà installé le certificat racine n'ont donc rien à
refaire.

```bash
cd /opt/corrux && source .venv/bin/activate
export CORRUX_CERT_CA_KEY_PATH=/etc/corrux/tls/ca.key
export CORRUX_CERT_CA_CERT_PATH=/etc/corrux/tls/ca.crt
export CORRUX_CERT_SERVER_KEY_PATH=/etc/corrux/tls/server.key
export CORRUX_CERT_SERVER_CERT_PATH=/etc/corrux/tls/server.crt
export CORRUX_CERT_COMMON_NAME=<nom-ou-ip-de-la-machine>
python manage.py renew_certificate
```

En sortie : `Certificat serveur renouvelé (<nom>).`

## Recharger Nginx

Le nouveau certificat n'est pris en compte par Nginx qu'après un
rechargement de sa configuration :

```bash
nginx -t   # vérifie la configuration avant de recharger
systemctl reload nginx
```

## Vérifier

```bash
openssl x509 -in /etc/corrux/tls/server.crt -noout -enddate
```

La date affichée doit désormais être postérieure à la précédente. Se
reconnecter à l'interface web CORRUX depuis un poste client pour confirmer
qu'aucun nouvel avertissement de sécurité n'apparaît (le certificat racine
déjà installé sur les postes clients reste valide, seul le certificat
serveur a changé).
