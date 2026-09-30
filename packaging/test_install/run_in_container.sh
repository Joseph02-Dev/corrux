#!/usr/bin/env bash
# Sert un site de publication (packaging/build_apt_site.sh) en local et
# exécute container_test.sh dans un conteneur de la distribution cible.
#
# Usage : run_in_container.sh <répertoire_du_site> <image> [--setup]
#   --setup : teste aussi corrux-setup (conteneur --privileged : loop, mount).
#
# Le conteneur partage le réseau de l'hôte : le dépôt de test est joint
# sur 127.0.0.1. Aucun PostgreSQL ne doit écouter sur le port 5432 de
# l'hôte (celui du conteneur ne pourrait pas démarrer).
set -euo pipefail

SITE_DIR="$(cd "${1:?Usage: run_in_container.sh <site> <image> [--setup]}" && pwd)"
IMAGE="${2:?Usage: run_in_container.sh <site> <image> [--setup]}"
SETUP="${3:-}"
PORT="${CORRUX_TEST_REPO_PORT:-8765}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$SITE_DIR" >/dev/null 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
for _ in $(seq 1 20); do
    curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/InRelease" && break
    sleep 0.5
done

docker_args=(--rm --network host -e "CORRUX_REPO_URL=http://127.0.0.1:${PORT}"
    -v "${SITE_DIR}/install.sh:/corrux-install.sh:ro"
    -v "${HERE}/container_test.sh:/container_test.sh:ro")
if [[ "$SETUP" == "--setup" ]]; then
    docker_args+=(--privileged -e CORRUX_TEST_SETUP=1)
fi
# Réglages apt propres à l'environnement d'exécution (ex. proxy), optionnels.
if [[ -n "${CORRUX_TEST_APT_CONF:-}" ]]; then
    docker_args+=(-v "${CORRUX_TEST_APT_CONF}:/etc/apt/apt.conf.d/99local:ro")
fi
if [[ -n "${CORRUX_TEST_PREP:-}" ]]; then
    docker_args+=(-v "${CORRUX_TEST_PREP}:/prep.sh:ro")
fi
for mount in ${CORRUX_TEST_EXTRA_MOUNTS:-}; do
    docker_args+=(-v "$mount")
done

docker run "${docker_args[@]}" "$IMAGE" \
    sh -c '[ -f /prep.sh ] && . /prep.sh; sh /container_test.sh /corrux-install.sh'
