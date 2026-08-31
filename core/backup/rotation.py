"""Politique de rétention des sauvegardes — §11.

§11 ne fixe pas de politique définitive (« ex. 7 quotidiennes + 4
hebdomadaires — valeurs par défaut à confirmer », marqué explicitement
non tranché dans le document). Implémentation volontairement générique :
conserve les N sauvegardes les plus récentes, supprime les fichiers des
plus anciennes au-delà de ce nombre. La politique quotidienne/hebdomadaire
précise reste une décision produit non tranchée par ce ticket.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_RETENTION_COUNT = 7  # valeur par défaut provisoire, configurable


def apply_retention(backup_files: list[Path], *, keep: int = DEFAULT_RETENTION_COUNT) -> list[Path]:
    """Supprime les fichiers de sauvegarde excédentaires, en conservant au
    plus `keep` fichiers. `backup_files` doit être trié du plus récent au
    plus ancien par l'appelant (par date d'exécution en base — source de
    vérité, pas par mtime fichier). Retourne les fichiers supprimés.

    N'est jamais appelée sur un échec/refus (cf. core/backup/service.py) :
    les anciennes sauvegardes ne sont donc jamais menacées par une
    nouvelle sauvegarde qui n'a pas encore été validée.
    """
    to_delete = backup_files[keep:]
    deleted = []
    for path in to_delete:
        path.unlink(missing_ok=True)
        deleted.append(path)
    return deleted
