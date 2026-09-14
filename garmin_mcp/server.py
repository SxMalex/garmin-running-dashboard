"""Serveur MCP exposant Garmin Connect à un client IA (Claude Code, Claude Desktop…).

Architecture :
  - quelques *tools* curés pour les usages fréquents (pas, sommeil, FC, stress…),
  - un *tool* passe-plat `garmin_call(method, params)` qui donne accès aux ~130
    méthodes de la lib `garminconnect` sans toutes les redéclarer,
  - un *tool* `garmin_list_methods()` qui retourne le catalogue (nom + signature
    + docstring) pour que le modèle sache quoi appeler via le passe-plat.

Le client `Garmin` est connecté paresseusement à la première utilisation, en
réutilisant le même pattern de cache de session que `test_connection.py`
(tokens OAuth garth dans le tokenstore, refresh proactif, MFA si requis).

Transport : stdio (lancé en sous-processus par le client MCP).
"""

from __future__ import annotations

import inspect
import os
from typing import Any

from dotenv import load_dotenv
from garminconnect import Garmin
from mcp.server.fastmcp import FastMCP

DEFAULT_TOKENSTORE = "~/.garminconnect"

# Méthodes du cycle d'authentification : on ne les expose pas via le passe-plat,
# le serveur gère la session lui-même.
_AUTH_METHODS = {"login", "logout", "resume_login"}

# Serveur en lecture seule : on bloque toute méthode susceptible de modifier le
# compte Garmin. Filtrage par préfixe (1er token) du nom de méthode.
_WRITE_PREFIXES = frozenset(
    {"add", "set", "delete", "remove", "upload", "import", "create",
     "schedule", "unschedule"}
)
# Méthodes à effet de bord ne suivant pas la convention de préfixe ci-dessus.
_WRITE_METHODS = frozenset({"request_reload"})


def _is_destructive(method: str) -> bool:
    """Vrai si la méthode modifie le compte (écriture/suppression/upload…)."""
    return method.split("_")[0] in _WRITE_PREFIXES or method in _WRITE_METHODS

mcp = FastMCP("garmin")

_client: Garmin | None = None


def _get_client() -> Garmin:
    """Retourne un client Garmin connecté (connexion paresseuse + cache session)."""
    global _client
    if _client is not None:
        return _client

    # .env du projet, résolu par rapport à ce fichier (indépendant du cwd).
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    tokenstore = os.environ.get("GARMIN_TOKENSTORE", DEFAULT_TOKENSTORE)

    if not email or not password:
        raise RuntimeError(
            "GARMIN_EMAIL et GARMIN_PASSWORD doivent être définis (fichier .env)."
        )

    client = Garmin(
        email,
        password,
        # En contexte serveur (stdio), pas de saisie interactive possible :
        # le MFA doit avoir été fait une fois pour amorcer le tokenstore.
        prompt_mfa=lambda: (_ for _ in ()).throw(
            RuntimeError(
                "MFA requis mais session non amorcée. Lance d'abord "
                "`python test_connection.py` une fois pour créer le cache."
            )
        ),
    )
    client.login(tokenstore)
    _client = client
    return _client


# --------------------------------------------------------------------------- #
# Tools curés — usages fréquents, schémas explicites.
# --------------------------------------------------------------------------- #


@mcp.tool()
def garmin_profile() -> dict[str, Any]:
    """Profil de l'utilisateur connecté : nom complet, système d'unités."""
    c = _get_client()
    return {
        "full_name": c.get_full_name(),
        "unit_system": c.get_unit_system(),
    }


@mcp.tool()
def get_stats(cdate: str) -> dict[str, Any]:
    """Statistiques quotidiennes (pas, calories, distance) pour une date YYYY-MM-DD."""
    return _get_client().get_stats(cdate)


@mcp.tool()
def get_sleep_data(cdate: str) -> dict[str, Any]:
    """Données de sommeil (durée, phases, score) pour une date YYYY-MM-DD."""
    return _get_client().get_sleep_data(cdate)


@mcp.tool()
def get_heart_rates(cdate: str) -> dict[str, Any]:
    """Fréquence cardiaque (repos + courbe de la journée) pour une date YYYY-MM-DD."""
    return _get_client().get_heart_rates(cdate)


@mcp.tool()
def get_stress_data(cdate: str) -> dict[str, Any]:
    """Niveau de stress sur la journée pour une date YYYY-MM-DD."""
    return _get_client().get_stress_data(cdate)


@mcp.tool()
def get_body_battery(startdate: str, enddate: str | None = None) -> list[Any]:
    """Body Battery (énergie) entre deux dates YYYY-MM-DD (enddate optionnelle)."""
    return _get_client().get_body_battery(startdate, enddate)


@mcp.tool()
def get_activities(
    start: int = 0, limit: int = 20, activitytype: str | None = None
) -> Any:
    """Liste des activités sportives, paginée (start/limit), filtrable par type."""
    return _get_client().get_activities(start, limit, activitytype)


@mcp.tool()
def get_activities_by_date(
    startdate: str, enddate: str | None = None, activitytype: str | None = None
) -> list[Any]:
    """Activités entre deux dates YYYY-MM-DD, filtrables par type d'activité."""
    return _get_client().get_activities_by_date(startdate, enddate, activitytype)


@mcp.tool()
def get_training_readiness(cdate: str) -> dict[str, Any]:
    """Score de « training readiness » pour une date YYYY-MM-DD.

    Toutes les montres ne calculent pas cette métrique (Forerunner 255 p. ex.).
    Garmin répond alors par une liste vide : le tool retourne
    `{"supported": false, …}` avec les substituts à utiliser — ce n'est pas une
    erreur d'appel, inutile de le relancer.
    """
    data = _get_client().get_training_readiness(cdate)
    if data is None or (isinstance(data, (list, dict)) and not data):
        return {
            "supported": False,
            "cdate": cdate,
            "readiness": None,
            "note": (
                f"Aucune donnée de training readiness pour {cdate} : métrique "
                "non supportée par la montre du compte (Forerunner 255 p. ex.) "
                "ou pas encore calculée. Substituts : get_hrv_data, "
                "get_sleep_data, get_body_battery, get_training_status."
            ),
        }
    # L'API renvoie une liste (un relevé par device) là où la lib annonce un
    # dict : on l'enveloppe plutôt que de laisser FastMCP échouer à la
    # validation du schéma de sortie.
    return {"supported": True, "cdate": cdate, "readiness": data}


# --------------------------------------------------------------------------- #
# Catalogue + passe-plat générique — couvre TOUTES les méthodes de la lib.
# --------------------------------------------------------------------------- #


@mcp.tool()
def garmin_list_methods(filter: str | None = None) -> list[dict[str, str]]:
    """Catalogue des méthodes Garmin (lecture seule) appelables via `garmin_call`.

    Retourne nom, signature et 1re ligne de docstring de chaque méthode publique.
    Les méthodes qui modifient le compte sont exclues (serveur en lecture seule).
    `filter` : sous-chaîne pour ne garder que les méthodes correspondantes
    (ex. "sleep", "get_", "weigh").
    """
    out: list[dict[str, str]] = []
    for name, fn in inspect.getmembers(Garmin, predicate=inspect.isfunction):
        if name.startswith("_") or name in _AUTH_METHODS or _is_destructive(name):
            continue
        if filter and filter.lower() not in name.lower():
            continue
        doc = (inspect.getdoc(fn) or "").splitlines()
        sig = str(inspect.signature(fn)).replace("(self, ", "(").replace("(self)", "()")
        out.append(
            {
                "method": name,
                "signature": sig,
                "doc": doc[0] if doc else "",
            }
        )
    return out


@mcp.tool()
def garmin_call(method: str, params: dict[str, Any] | None = None) -> Any:
    """Appelle une méthode de lecture de la lib garminconnect par son nom.

    Utilise `garmin_list_methods` pour découvrir les méthodes et leurs paramètres.
    `params` : dict des arguments nommés (ex. {"cdate": "2026-06-15"}).
    Serveur en lecture seule : les méthodes qui modifient le compte sont refusées.
    Une réponse vide de Garmin est retournée comme `{"empty": true, …}` explicite
    (métrique non supportée par la montre, ou pas encore calculée).
    """
    if method.startswith("_") or method in _AUTH_METHODS:
        raise ValueError(f"Méthode non autorisée : {method}")
    if _is_destructive(method):
        raise ValueError(
            f"Refusé : « {method} » modifie le compte (serveur en lecture seule)."
        )
    c = _get_client()
    fn = getattr(c, method, None)
    if fn is None or not callable(fn):
        raise ValueError(f"Méthode Garmin inconnue : {method}")
    result = fn(**(params or {}))
    if result is None or (isinstance(result, (list, dict, str)) and not result):
        return {
            "empty": True,
            "method": method,
            "params": params or {},
            "note": (
                f"« {method} » a répondu sans données. Cause habituelle : "
                "métrique non supportée par la montre du compte, ou pas encore "
                "calculée pour la date demandée. L'appel a abouti — inutile de "
                "le relancer."
            ),
        }
    return result


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
