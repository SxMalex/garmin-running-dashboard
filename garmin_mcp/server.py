"""Serveur MCP exposant Garmin Connect à un client IA (Claude Code, Claude Desktop…).

Architecture :
  - des *tools* « raisonnement » (`insights.py`) qui exposent les métriques et
    verdicts CALCULÉS par le dashboard — fraîcheur, séance du jour, dérive
    cardiaque, plan vers un objectif — en réutilisant la logique de `app/` ;
  - quelques *tools* curés pour les usages fréquents (pas, sommeil, FC, stress…),
  - un *tool* passe-plat `garmin_call(method, params)` qui donne accès aux ~100
    méthodes de lecture de la lib `garminconnect` sans toutes les redéclarer,
  - un *tool* `garmin_list_methods()` qui retourne le catalogue (nom + signature
    + docstring) pour que le modèle sache quoi appeler via le passe-plat.

Le client `Garmin` est connecté paresseusement à la première utilisation, par
les tokens du tokenstore (amorcé une fois par `test_connection.py`, MFA
compris). Tokenstore PROPRE au serveur (`~/.garminconnect-mcp`), distinct de
celui du dashboard : deux processus qui rafraîchissent le même jeton se
l'invalideraient mutuellement.

Serveur en lecture seule : liste blanche de méthodes (`get_`, `count_`,
`download_`), pas de liste noire — une nouvelle méthode d'écriture de la lib
ne passe pas par défaut.

Transport : stdio (lancé en sous-processus par le client MCP).
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402
from garminconnect import Garmin  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

import insights  # noqa: E402
import prompts  # noqa: E402
from garmin_client import GarminClient  # noqa: E402

DEFAULT_TOKENSTORE = "~/.garminconnect-mcp"  # ≠ dashboard hors Docker (~/.garminconnect)

# Méthodes du cycle d'authentification : on ne les expose pas via le passe-plat,
# le serveur gère la session lui-même.
_AUTH_METHODS = {"login", "logout", "resume_login"}

# Serveur en lecture seule : LISTE BLANCHE. `connectapi` / `connectwebproxy`
# sont codés en GET dans la lib ; `query_garmin_graphql` (POST, mutations
# possibles) et tout le reste sont refusés.
_READ_PREFIXES = ("get_", "count_", "download_")
_READ_METHODS = frozenset({"connectapi", "connectwebproxy"})

# `connectapi`/`connectwebproxy` acceptent **kwargs relayés tels quels jusqu'à
# `requests.Session.request` (cf. garminconnect.client._run_request) : sans
# liste blanche, `proxies`/`verify=False`/`cookies`/`auth`/`files`… partiraient
# avec le Bearer du tokenstore vers un tiers. Seul `path` (déjà positionnel) et
# `params` (query string, seul kwarg utilisé par app/garmin_client.py) sont
# nécessaires en lecture seule.
_CONNECTAPI_ALLOWED_KWARGS = frozenset({"path", "params"})


def _is_allowed(method: str) -> bool:
    """Vrai si la méthode ne fait que lire (liste blanche)."""
    if method.startswith("_") or method in _AUTH_METHODS:
        return False
    return method.startswith(_READ_PREFIXES) or method in _READ_METHODS


mcp = FastMCP("garmin")

_client: Garmin | None = None


def _get_client() -> Garmin:
    """
    Client Garmin connecté (paresseux). Les tokens du tokenstore suffisent ;
    email/mot de passe ne servent qu'à amorcer une session absente (le
    dashboard conseille de laisser GARMIN_PASSWORD vide : ce n'est pas bloquant).
    """
    global _client
    if _client is not None:
        return _client

    # .env du projet, résolu par rapport à ce fichier (indépendant du cwd).
    load_dotenv(ROOT / ".env")
    tokenstore = os.environ.get("GARMIN_TOKENSTORE_MCP") or DEFAULT_TOKENSTORE
    mfa_error = RuntimeError(
        "MFA requis mais session non amorcée. Lance d'abord "
        "`python test_connection.py` une fois pour créer le tokenstore."
    )

    try:
        client = Garmin()
        client.login(tokenstore)
    except Exception as token_error:
        email = os.environ.get("GARMIN_EMAIL")
        password = os.environ.get("GARMIN_PASSWORD")
        if not email or not password:
            raise RuntimeError(
                f"Pas de session dans {tokenstore} ({token_error}). Lance une fois "
                "`python test_connection.py` : il demande email, mot de passe (sauf "
                "s'ils sont dans .env) et code MFA, puis enregistre les tokens du "
                "serveur MCP."
            ) from token_error
        client = Garmin(email, password,
                        prompt_mfa=lambda: (_ for _ in ()).throw(mfa_error))
        client.login(tokenstore)
    _client = client
    return _client


_gc: GarminClient | None = None


def _get_gc() -> GarminClient:
    """GarminClient du dashboard (cache disque, streams, contrat DataFrame)."""
    global _gc
    if _gc is None:
        _use_dashboard_data_dir()
        _gc = GarminClient(_get_client())
    return _gc


def _use_dashboard_data_dir() -> None:
    """
    Objectif et plan validé : ceux du dashboard de dev (bind mount app/.data).
    Au premier usage et non à l'import : importer le module (tests) ne doit
    pas rediriger DATA_DIR de tout le process vers les vraies données.
    """
    if not os.environ.get("DATA_DIR") and (ROOT / "app" / ".data").is_dir():
        os.environ["DATA_DIR"] = str(ROOT / "app" / ".data")


# --------------------------------------------------------------------------- #
# Tools « raisonnement » — les calculs du dashboard, prêts à discuter.
# --------------------------------------------------------------------------- #


# Prompts « /bilan_semaine », « /prepa_course »… (prompts.py) : adaptés à la
# situation réelle (Run Coach, plan Objectif, veille santé) à chaque appel.
prompts.register(mcp, lambda: _get_gc())


@mcp.tool()
def daily_briefing() -> dict[str, Any]:
    """Verdict du jour : fraîcheur (CTL/ATL/TSB), récupération (HRV, sommeil),
    séance recommandée (plan Garmin Run Coach s'il est actif, sinon plan
    Objectif validé du dashboard, sinon logique interne modulée par la récupération) et indicateurs de risque (ACWR,
    monotonie). À appeler pour « dois-je m'entraîner dur aujourd'hui ? »."""
    return insights.daily_briefing(_get_gc())


@mcp.tool()
def training_load(days: int = 90) -> dict[str, Any]:
    """Charge d'entraînement hebdomadaire (TSS, CTL, ATL, TSB) sur `days` jours,
    sport croisé inclus, avec l'allure seuil et le facteur de calibration."""
    return insights.training_load(_get_gc(), days)


@mcp.tool()
def activity_analysis(activity_id: int) -> dict[str, Any]:
    """Analyse d'une sortie : FC optique calée sur la cadence (faux relevés) et
    dérive cardiaque (Pa:HR), avec la raison si elle n'est pas mesurable."""
    return insights.activity_analysis(_get_gc(), activity_id)


@mcp.tool()
def aerobic_trend() -> dict[str, Any]:
    """Progression de l'endurance : efficacité aérobie (vitesse ÷ FC) et dérive
    cardiaque des sorties longues récentes."""
    return insights.aerobic_trend(_get_gc())


@mcp.tool()
def race_plan_preview(
    distance: str, race_date: str, runs_per_week: int = 4, long_run_weekday: int = 6,
    target_time: str | None = None, include_strength: bool = True,
) -> dict[str, Any]:
    """Plan course + renforcement vers une course (distance : « 5 km », « 10 km »,
    « Semi-marathon », « Marathon » ; race_date YYYY-MM-DD ; long_run_weekday
    0=lundi…6=dimanche ; target_time « 1:55:00 »). Aperçu en lecture seule :
    rien n'est enregistré ni envoyé à Garmin. Chaque séance porte son « why »."""
    return insights.race_plan_preview(_get_gc(), distance, race_date, runs_per_week,
                                      long_run_weekday, target_time, include_strength)


@mcp.tool()
def health_watch() -> dict[str, Any]:
    """Veille santé « est-ce que je couve quelque chose ? » : FC de repos, HRV,
    respiration et SpO2 de la dernière nuit comparées à la norme personnelle des
    30 derniers jours. Niveau 0 rien, 1 à surveiller, 2 plusieurs signaux
    concordants (souvent 1 à 2 jours avant un rhume). Pas un diagnostic."""
    return insights.health_watch(_get_gc())


@mcp.tool()
def running_form() -> dict[str, Any]:
    """Forme de foulée à allure égale (contact au sol, ratio vertical, longueur de
    foulée, puissance : dérive des 6 dernières semaines) et pic de sortie unique
    (dernière sortie et prochaine sortie longue prévue vs la plus longue du mois)."""
    return insights.running_form(_get_gc())


@mcp.tool()
def current_goal() -> dict[str, Any]:
    """Objectif de course enregistré dans le dashboard et son plan actuel. Pour
    modifier l'objectif ou envoyer des séances à la montre : page Objectif du
    dashboard (le serveur MCP ne modifie rien)."""
    return insights.current_goal(_get_gc())


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
        if not _is_allowed(name):
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
    if not _is_allowed(method):
        raise ValueError(
            f"Refusé : « {method} » n'est pas une méthode de lecture (serveur en "
            "lecture seule : get_*, count_*, download_*, connectapi en GET)."
        )
    if method in _READ_METHODS:
        extra = set(params or {}) - _CONNECTAPI_ALLOWED_KWARGS
        if extra:
            raise ValueError(
                f"Refusé : arguments non autorisés pour « {method} » : {sorted(extra)} "
                "(liste blanche : path, params — serveur en lecture seule)."
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
