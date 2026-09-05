"""Génération de la configuration Nginx — reverse proxy TLS (§5, §10).

Fonction de rendu pure (aucune dépendance Django) — réutilisée à la
fois pour produire le fichier de configuration réel livré
(ops/nginx-corrux.conf, ports de production) et pour les tests
d'intégration de ce même ticket (ports non privilégiés), afin de ne
jamais dupliquer le contenu de configuration entre les deux.

Décisions reflétées ici, toutes déjà actées par l'architecture, aucune
invention :
- TLS 1.2+ uniquement (§10) — ssl_protocols TLSv1.2 TLSv1.3.
- HSTS activé (§10).
- Redirection HTTP -> HTTPS (§10) — un unique bloc server sur le port
  HTTP, qui ne fait jamais rien d'autre que rediriger.
- Routage vers corrux-core en localhost uniquement (§5 : « corrux-core
  n'écoute que sur localhost »).
- Aucun fichier servi directement par Nginx (§8, critère d'acceptation
  explicite de ce ticket) : un seul bloc `location /`, jamais de bloc
  dédié à l'arborescence de stockage documentaire — tout accès fichier
  transite par l'application.
"""

from __future__ import annotations

NGINX_CONFIG_TEMPLATE = """\
# CORRUX — reverse proxy Nginx (§5, §10, §8). Généré par
# ops/nginx_config.py — ne pas éditer les valeurs ci-dessous à la main,
# régénérer via corrux-setup (TECH-012) si les paramètres changent.

server {{
    listen {http_port};
    server_name {server_name};

    # §10 : redirection HTTP -> HTTPS — aucune autre réponse possible
    # sur ce port, jamais de contenu applicatif en clair.
    return 301 https://$host:{https_port}$request_uri;
}}

server {{
    listen {https_port} ssl http2;
    server_name {server_name};

    ssl_certificate {ssl_cert_path};
    ssl_certificate_key {ssl_key_path};

    # §10 : TLS 1.2+ uniquement.
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;

    # §10 : HSTS activé.
    add_header Strict-Transport-Security "max-age={hsts_max_age}; includeSubDomains" always;

    # §8 : aucun fichier servi directement par Nginx — un seul point
    # d'entrée applicatif, jamais un bloc dédié à l'arborescence de
    # stockage documentaire.
    # corrux-core n'écoute que sur localhost (§5).
    location / {{
        proxy_pass http://{upstream_address};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }}
}}
"""


def render_nginx_config(
    *,
    https_port: int = 443,
    http_port: int = 80,
    ssl_cert_path: str,
    ssl_key_path: str,
    upstream_address: str = "127.0.0.1:8000",
    server_name: str = "_",
    hsts_max_age: int = 31536000,
) -> str:
    """Rend la configuration Nginx complète — chaîne de texte, aucun
    effet de bord (n'écrit rien sur disque, n'appelle jamais nginx)."""
    return NGINX_CONFIG_TEMPLATE.format(
        https_port=https_port,
        http_port=http_port,
        ssl_cert_path=ssl_cert_path,
        ssl_key_path=ssl_key_path,
        upstream_address=upstream_address,
        server_name=server_name,
        hsts_max_age=hsts_max_age,
    )
