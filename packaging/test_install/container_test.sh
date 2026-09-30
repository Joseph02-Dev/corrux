#!/bin/sh
# Test d'installation de bout en bout, exécuté DANS un conteneur de la
# distribution cible (Debian 12/13, Ubuntu 22.04/24.04/26.04) — DEPLOY-001.
#
# Suit exactement le parcours client : `install.sh` (dépôt apt signé,
# vérification d'empreinte, `apt-get install corrux`), puis vérifie
# que l'application installée fonctionne : venv embarqué, base et
# migrations, gunicorn servant l'application et ses assets statiques,
# puis désinstallation propre.
#
# Un conteneur n'a pas systemd : PostgreSQL est démarré par
# pg_ctlcluster si les scripts du paquet postgresql ne l'ont pas fait,
# et corrux-core est lancé à la main avec la commande exacte de
# corrux-core.service.
#
# Usage : CORRUX_REPO_URL=<url du dépôt de test> sh container_test.sh <install.sh>
set -eu

INSTALL_SCRIPT="${1:?Usage: container_test.sh <install.sh>}"
: "${CORRUX_REPO_URL:?CORRUX_REPO_URL requis}"

step() { printf '\n=== %s ===\n' "$*"; }
fail() { echo "ÉCHEC : $*" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive

step "Prérequis du conteneur (curl pour lancer install.sh, comme un client)"
apt-get update -q >/dev/null
apt-get install -y -q curl ca-certificates >/dev/null

step "PostgreSQL démarré avant CORRUX (simule le service systemd)"
apt-get install -y -q postgresql >/dev/null
pg_ctlcluster "$(ls /etc/postgresql)" main start 2>/dev/null || true
runuser -u postgres -- psql -tAc "SELECT version()"

step "install.sh"
CORRUX_NONINTERACTIVE=1 sh "$INSTALL_SCRIPT"

step "Vérifications post-installation"
dpkg -s corrux corrux-core corrux-module-documentation corrux-module-rh >/dev/null
/opt/corrux/.venv/bin/python -c "import django, psycopg, argon2, yaml, gunicorn, whitenoise; print('venv OK, Django', django.__version__)"
test "$(stat -c '%a %U:%G' /etc/corrux/core.env)" = "640 root:corrux" || fail "droits de core.env"
getent passwd corrux >/dev/null || fail "compte corrux absent"
corrux-manage check
if corrux-manage showmigrations --plan | grep -q '^\[ \]'; then
    fail "migrations non appliquées"
fi
schemas="$(runuser -u postgres -- psql -d corrux -tAc "SELECT count(*) FROM information_schema.schemata WHERE schema_name IN ('core','documentation','rh')")"
test "$schemas" = "3" || fail "schémas core/documentation/rh absents (${schemas})"

step "Idempotence : reconfiguration du paquet (mise à jour simulée)"
secret_before="$(grep '^DJANGO_SECRET_KEY=' /etc/corrux/core.env)"
dpkg-reconfigure corrux-core
test "$(grep '^DJANGO_SECRET_KEY=' /etc/corrux/core.env)" = "$secret_before" || fail "secret régénéré"

step "Application servie (commande de corrux-core.service)"
(
    set -a; . /etc/corrux/core.env; set +a
    cd /opt/corrux
    runuser -u corrux -- /opt/corrux/.venv/bin/gunicorn --bind 127.0.0.1:8000 \
        --workers 2 --daemon --pid /tmp/gunicorn.pid corrux_core.wsgi:application
)
for _ in 1 2 3 4 5 6 7 8 9 10; do
    curl -fsS http://127.0.0.1:8000/healthz/ >/dev/null 2>&1 && break
    sleep 1
done
curl -fsS -H "X-Forwarded-Proto: https" http://127.0.0.1:8000/healthz/
echo
static_file="$(cd /var/lib/corrux/static && find . -name '*.css' | head -1 | sed 's|^\./||')"
test -n "$static_file" || fail "aucun asset statique collecté"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8000/static/${static_file}")"
test "$code" = "200" || fail "asset statique /static/${static_file} : HTTP ${code}"
echo "asset statique servi : /static/${static_file}"
kill "$(cat /tmp/gunicorn.pid)"

step "corrux-setup refuse une exécution sans root"
if runuser -u nobody -- corrux-setup >/dev/null 2>&1; then
    fail "corrux-setup accepté sans root"
fi

if [ "${CORRUX_TEST_SETUP:-0}" = "1" ]; then
    # Nécessite un conteneur --privileged (périphérique loop, mount).
    step "corrux-setup (scripté) sur un volume de sauvegarde loop"
    apt-get install -y -q gnupg >/dev/null
    GNUPGHOME="$(mktemp -d)"; export GNUPGHOME
    gpg --batch --quiet --passphrase '' --quick-generate-key "Backup Test <backup@corrux.invalid>" rsa3072 encr never
    gpg --batch --armor --export backup@corrux.invalid >/tmp/backup-public.asc
    install -m 0644 /tmp/backup-public.asc /etc/corrux/backup-gpg-public.key
    truncate -s 256M /tmp/backup.img
    device="$(losetup -f --show /tmp/backup.img)"
    corrux-setup --admin-username admin --admin-password 'Setup-Test-2026!x' \
        --admin-full-name "Admin Test" --certificate-common-name corrux.test \
        --backup-device-path "$device" --backup-mount-point /srv/corrux-backup \
        --backup-fstab-path /tmp/fstab \
        --format-backup-volume --skip-format-confirmation

    test -f /etc/corrux/tls/server.crt || fail "certificat absent"
    grep -q '^CORRUX_BACKUP_DESTINATION=/srv/corrux-backup$' /etc/corrux/backup.env || fail "backup.env"
    grep -q '^CORRUX_CERT_SERVER_CERT_PATH=' /etc/corrux/certs.env || fail "certs.env"
    grep -q 'ReadWritePaths=/srv/corrux-backup' \
        /etc/systemd/system/corrux-backup.service.d/corrux-setup.conf || fail "drop-in sauvegarde"
    test -L /etc/nginx/sites-enabled/corrux.conf || fail "site Nginx non activé"
    ls /srv/corrux-backup/*.tar.gpg >/dev/null || fail "aucune sauvegarde de vérification"
    if corrux-setup >/dev/null 2>&1; then fail "corrux-setup rejoué sur une instance configurée"; fi

    step "Connexion HTTPS de bout en bout (Nginx -> gunicorn, CSRF)"
    (
        set -a; . /etc/corrux/core.env; set +a
        cd /opt/corrux
        runuser -u corrux -- /opt/corrux/.venv/bin/gunicorn --bind 127.0.0.1:8000 \
            --workers 2 --daemon --pid /tmp/gunicorn.pid corrux_core.wsgi:application
    )
    nginx
    sleep 2
    base=https://corrux.test
    resolve="corrux.test:443:127.0.0.1"
    ca=/etc/corrux/tls/ca.crt
    jar=/tmp/cookies
    test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/login/)" = "301" \
        || fail "HTTP non redirigé vers HTTPS"
    page="$(curl -fsS --cacert "$ca" --resolve "$resolve" -c "$jar" "$base/login/")" \
        || fail "page de connexion HTTPS"
    token="$(echo "$page" | sed -n 's/.*name="csrfmiddlewaretoken" value="\([^"]*\)".*/\1/p' | head -1)"
    test -n "$token" || fail "jeton CSRF absent"
    code="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$ca" --resolve "$resolve" -b "$jar" -c "$jar" \
        -H "Referer: $base/login/" -H "Origin: $base" \
        --data-urlencode "csrfmiddlewaretoken=$token" --data-urlencode "username=admin" \
        --data-urlencode "password=Setup-Test-2026!x" "$base/login/")"
    test "$code" = "302" || fail "connexion administrateur : HTTP ${code} (302 attendu)"
    echo "connexion administrateur via HTTPS : OK"
    nginx -s stop
    kill "$(cat /tmp/gunicorn.pid)"
    umount /srv/corrux-backup
    losetup -d "$device"
fi

step "Désinstallation (données conservées)"
apt-get purge -y -q corrux corrux-core corrux-module-documentation corrux-module-rh >/dev/null
test ! -e /opt/corrux/.venv || fail "venv non supprimé"
test ! -e /etc/corrux || fail "/etc/corrux non supprimé à la purge"
test -d /var/lib/corrux/storage || fail "stockage documentaire supprimé"
test "$(runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='corrux'")" = "1" \
    || fail "base de données supprimée"

step "SUCCÈS"
