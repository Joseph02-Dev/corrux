# shellcheck shell=sh
# CORRUX — fonctions de provisionnement partagées par les scripts de
# maintenance des paquets (postinst) et par corrux-setup.
#
# Installé dans /usr/lib/corrux/common.sh par corrux-core. POSIX sh
# (exécuté par /bin/sh = dash sur Debian/Ubuntu/Proxmox). Toutes les
# fonctions sont idempotentes : elles peuvent être rejouées à chaque
# configuration du paquet (installation, mise à jour, dpkg-reconfigure)
# sans jamais écraser un secret ou une donnée existante.

CORRUX_PREFIX=/opt/corrux
CORRUX_VENV="${CORRUX_PREFIX}/.venv"
CORRUX_WHEELHOUSE="${CORRUX_PREFIX}/wheels"
CORRUX_ETC=/etc/corrux
CORRUX_ENV_FILE="${CORRUX_ETC}/core.env"
CORRUX_VAR=/var/lib/corrux
CORRUX_USER=corrux
CORRUX_MODULES="documentation rh"

corrux_log() {
    echo "corrux: $*"
}

corrux_warn() {
    echo "corrux: AVERTISSEMENT : $*" >&2
}

# Chaîne aléatoire alphanumérique (aucun caractère à échapper dans un
# fichier EnvironmentFile systemd ni dans une chaîne SQL).
corrux_random_secret() {
    length="$1"
    # `|| true` : head ferme le tube dès qu'il a assez d'octets, tr reçoit
    # alors SIGPIPE — sans conséquence ici, mais fatal sous `set -e`.
    (LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$length") || true
}

corrux_ensure_user() {
    if ! getent passwd "$CORRUX_USER" >/dev/null; then
        adduser --system --group --home "$CORRUX_VAR" --no-create-home \
            --shell /usr/sbin/nologin --gecos "CORRUX" "$CORRUX_USER" >/dev/null
        corrux_log "compte système « ${CORRUX_USER} » créé."
    fi
}

corrux_ensure_dirs() {
    install -d -m 0755 -o root -g root "$CORRUX_ETC"
    install -d -m 0750 -o root -g "$CORRUX_USER" "${CORRUX_ETC}/tls"
    install -d -m 0750 -o "$CORRUX_USER" -g "$CORRUX_USER" "$CORRUX_VAR"
    install -d -m 0750 -o "$CORRUX_USER" -g "$CORRUX_USER" "${CORRUX_VAR}/storage"
    install -d -m 0755 -o root -g root "${CORRUX_VAR}/static"
}

# Environnement Python embarqué : construit à partir des wheels livrés
# dans le paquet (--no-index), jamais depuis Internet. Reconstruit à
# chaque configuration pour suivre les versions figées du paquet et la
# version de python3 du système (ex. après une montée de version de la
# distribution : `dpkg-reconfigure corrux-core`).
corrux_build_venv() {
    if [ ! -d "$CORRUX_WHEELHOUSE" ]; then
        corrux_warn "${CORRUX_WHEELHOUSE} absent : paquet construit sans wheels."
        return 1
    fi
    corrux_log "construction de l'environnement Python ($(python3 --version 2>&1))..."
    python3 -m venv --clear "$CORRUX_VENV"
    "${CORRUX_VENV}/bin/pip" install --quiet --no-index --disable-pip-version-check \
        --find-links "$CORRUX_WHEELHOUSE" -r "${CORRUX_PREFIX}/requirements.txt"
}

# Adresses sous lesquelles l'application sera jointe (DJANGO_ALLOWED_HOSTS).
corrux_default_allowed_hosts() {
    hosts="localhost,127.0.0.1"
    for h in $(hostname 2>/dev/null) $(hostname -f 2>/dev/null) $(hostname -I 2>/dev/null); do
        case ",${hosts}," in
            *",${h},"*) ;;
            *) hosts="${hosts},${h}" ;;
        esac
    done
    echo "$hosts"
}

# /etc/corrux/core.env : généré une seule fois, jamais écrasé (il porte
# la clé secrète Django et le mot de passe de la base).
corrux_ensure_env() {
    if [ -f "$CORRUX_ENV_FILE" ]; then
        return 0
    fi
    umask 027
    cat >"$CORRUX_ENV_FILE" <<EOF
# CORRUX — configuration de l'application (généré à l'installation).
# Lu par corrux-core.service et par corrux-manage. Contient des secrets :
# ne jamais le copier hors de cette machine.
DJANGO_SETTINGS_MODULE=corrux_core.settings
DJANGO_SECRET_KEY=$(corrux_random_secret 64)
DJANGO_ALLOWED_HOSTS=$(corrux_default_allowed_hosts)
DB_NAME=corrux
DB_USER=corrux
DB_PASSWORD=$(corrux_random_secret 40)
DB_HOST=127.0.0.1
DB_PORT=5432
CORRUX_STORAGE_ROOT=${CORRUX_VAR}/storage
CORRUX_STATIC_ROOT=${CORRUX_VAR}/static
EOF
    chown root:"$CORRUX_USER" "$CORRUX_ENV_FILE"
    chmod 0640 "$CORRUX_ENV_FILE"
    umask 022
    corrux_log "configuration générée : ${CORRUX_ENV_FILE}"
}

corrux_load_env() {
    set -a
    # shellcheck disable=SC1090
    . "$CORRUX_ENV_FILE"
    set +a
}

corrux_database_is_local() {
    case "$DB_HOST" in
        127.0.0.1|localhost|::1) return 0 ;;
    esac
    return 1
}

corrux_postgres_ready() {
    command -v psql >/dev/null 2>&1 || return 1
    runuser -u postgres -- psql -q -tAc "SELECT 1" >/dev/null 2>&1
}

# Rôle et base PostgreSQL locaux. Uniquement si la base est locale
# (DB_HOST 127.0.0.1/localhost) : une base distante est la
# responsabilité de son administrateur. Retourne 1 si PostgreSQL n'est
# pas joignable (ex. conteneur sans systemd) — corrux-setup rejoue
# alors cette étape.
corrux_ensure_database() {
    corrux_load_env
    if ! corrux_database_is_local; then
        corrux_log "base distante (${DB_HOST}) : création ignorée."
        return 0
    fi
    if ! corrux_postgres_ready; then
        corrux_warn "PostgreSQL local non joignable — base non initialisée (relancer : corrux-setup)."
        return 1
    fi
    if [ "$(runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'")" != "1" ]; then
        runuser -u postgres -- psql -q -c "CREATE ROLE \"${DB_USER}\" LOGIN PASSWORD '${DB_PASSWORD}'"
        corrux_log "rôle PostgreSQL « ${DB_USER} » créé."
    fi
    if [ "$(runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'")" != "1" ]; then
        runuser -u postgres -- createdb -O "$DB_USER" -E UTF8 -T template0 "$DB_NAME"
        corrux_log "base PostgreSQL « ${DB_NAME} » créée."
    fi
}

# Les modules métier sont déclarés dans INSTALLED_APPS : Django ne peut
# démarrer que lorsque tous les paquets CORRUX sont dépaquetés.
corrux_all_modules_present() {
    for module in $CORRUX_MODULES; do
        [ -d "${CORRUX_PREFIX}/modules/${module}" ] || return 1
    done
    [ -x "${CORRUX_VENV}/bin/python" ]
}

corrux_manage() {
    (cd "$CORRUX_PREFIX" && corrux_load_env && "${CORRUX_VENV}/bin/python" manage.py "$@")
}

# Schémas, migrations et assets statiques. Appelé par le postinst de
# chaque paquet CORRUX : le dernier configuré trouve l'ensemble complet.
corrux_apply_migrations_if_ready() {
    if ! corrux_all_modules_present; then
        return 0
    fi
    corrux_load_env
    if corrux_database_is_local && ! corrux_postgres_ready; then
        corrux_warn "PostgreSQL local non joignable — migrations reportées (relancer : corrux-setup)."
        return 0
    fi
    corrux_log "application des migrations..."
    corrux_manage create_schemas >/dev/null
    corrux_manage migrate --noinput --verbosity 0
    corrux_manage collectstatic --noinput --clear --verbosity 0
}

# Mise à jour d'une instance déjà en service : redémarrage pour charger
# le nouveau code. Une instance jamais configurée (corrux-setup non
# exécuté) n'est jamais démarrée ici.
corrux_restart_if_running() {
    if [ -d /run/systemd/system ] && systemctl is-active --quiet corrux-core.service; then
        systemctl restart corrux-core.service || corrux_warn "redémarrage de corrux-core échoué."
    fi
}
