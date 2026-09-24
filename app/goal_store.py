"""
Persistance locale de l'objectif de course, du plan validé et du journal
des séances poussées dans Garmin. Un fichier JSON par athlète :
`DATA_DIR/{athlete_id}/goal.json`.

Séparé du cache (`CACHE_DIR`) : le cache est jetable (bouton Actualiser),
ces données ne le sont pas. Chaque écriture se fait sous verrou (sessions
Streamlit = threads d'un même process, `fcntl` pour un autre process),
relit le fichier puis le remplace atomiquement : deux onglets ne s'écrasent
pas un journal de push, et un lecteur (serveur MCP) ne voit jamais un
fichier à moitié écrit. `locked()` sert aussi à sérialiser toute une boucle
d'envoi vers Garmin.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path

try:  # verrou inter-process (absent sous Windows : le verrou de thread suffit alors)
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

VERSION = 1


def _default_data_dir() -> Path:
    """/app/.data dans Docker, ~/.local/share/garmin-dashboard sinon."""
    if os.getenv("DATA_DIR"):
        return Path(os.getenv("DATA_DIR"))
    if os.path.isdir("/app"):
        return Path("/app/.data")
    return Path.home() / ".local" / "share" / "garmin-dashboard"


def data_dir() -> Path:
    # Relu à chaque appel (et non figé à l'import comme CACHE_DIR) : les
    # tests et le serveur MCP peuvent fixer DATA_DIR après l'import.
    return _default_data_dir()


def _path(athlete_id: int) -> Path:
    return data_dir() / str(athlete_id) / "goal.json"


_THREAD_LOCK = threading.RLock()
_HELD = threading.local()


@contextlib.contextmanager
def locked(athlete_id: int):
    """
    Section critique sur le document de l'athlète (threads ET process).
    Réentrante : la boucle d'envoi tient le verrou et appelle `record_push`,
    qui le reprend — un second `flock` du même process sur un autre
    descripteur se bloquerait lui-même, d'où le compteur par thread.
    """
    with _THREAD_LOCK:
        depth = getattr(_HELD, "depth", 0)
        if depth:
            _HELD.depth = depth + 1
            try:
                yield
            finally:
                _HELD.depth = depth
            return
        path = _path(athlete_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_suffix(".lock"), "a") as lock_file:
            if fcntl:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
            _HELD.depth = 1
            try:
                yield
            finally:
                _HELD.depth = 0
                if fcntl:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)


def load(athlete_id: int, *, repair: bool = False) -> dict:
    """
    Document de l'athlète ({} si aucun objectif). Un contenu illisible (JSON
    ou encodage invalide) renvoie {"recovered_from_corrupt": True} ; il n'est
    mis de côté (`goal.json.bad-<horodatage>`) qu'avec `repair=True`, c'est-à-
    dire par une écriture, sous verrou — jamais par une simple lecture (le
    serveur MCP ne modifie rien). Les autres erreurs d'E/S (droits, fichiers
    ouverts…) remontent : les prendre pour une corruption effacerait le journal.
    """
    path = _path(athlete_id)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {"recovered_from_corrupt": True}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        if repair:
            path.rename(path.with_name(f"{path.name}.bad-{int(time.time())}"))
        return {"recovered_from_corrupt": True}


def _write(athlete_id: int, doc: dict) -> None:
    path = _path(athlete_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _update(athlete_id: int, mutate) -> dict:
    with locked(athlete_id):
        doc = load(athlete_id, repair=True)
        doc.pop("recovered_from_corrupt", None)
        doc.setdefault("version", VERSION)
        doc.setdefault("pushed", {})
        mutate(doc)
        doc["updated_at"] = time.time()
        _write(athlete_id, doc)
        return doc


def save_goal(athlete_id: int, goal: dict, prefs: dict) -> dict:
    """Enregistre l'objectif. Le plan validé est conservé : le journal des
    séances poussées doit survivre à un changement d'objectif."""
    def mutate(doc):
        doc["goal"] = goal
        doc["prefs"] = prefs
    return _update(athlete_id, mutate)


def validate_plan(athlete_id: int, plan_id: str, plan: dict) -> dict:
    """
    Fige le plan validé (semaines, séances, résumé). C'est CE plan qui est
    affiché et envoyé ensuite, pas un recalcul du jour : sinon les séances
    glissent d'une visite à l'autre et le calendrier reçoit des doublons.
    """
    def mutate(doc):
        doc["validated"] = {"plan_id": plan_id, "at": time.time(), "plan": plan}
    return _update(athlete_id, mutate)


def record_push(athlete_id: int, key: str, entry: dict) -> dict:
    """Journalise UNE séance poussée — appelé après chaque appel Garmin réussi."""
    def mutate(doc):
        doc["pushed"][key] = {**entry, "pushed_at": time.time()}
    return _update(athlete_id, mutate)


def forget_push(athlete_id: int, key: str) -> dict:
    def mutate(doc):
        doc["pushed"].pop(key, None)
    return _update(athlete_id, mutate)


def clear_goal(athlete_id: int) -> dict:
    """Supprime objectif et plan validé, garde le journal (séances encore dans Garmin)."""
    def mutate(doc):
        doc.pop("goal", None)
        doc.pop("prefs", None)
        doc.pop("validated", None)
    return _update(athlete_id, mutate)
