"""
Client Garmin Connect — récupération et mise en cache des données d'entraînement.

Mono-utilisateur : l'authentification passe par la lib non officielle
`garminconnect` (garth). Les tokens OAuth sont persistés dans le tokenstore
(valides ~1 an) ; le mot de passe n'est jamais stocké par l'application.

Le cache disque est cloisonné par athlete_id (profileId Garmin) — hérité du
dashboard Strava, et toujours utile si tu changes un jour de compte Garmin.
"""

import os
import json
import time
import shutil
import hashlib
import logging
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from formatting import (
    normalize_activity_type,
    seconds_to_pace_str,
    speed_to_pace,
    speed_to_pace_seconds,
)

logger = logging.getLogger(__name__)

def _default_cache_dir() -> Path:
    """Cache disque : /app/.cache dans Docker, ~/.cache/garmin-dashboard sinon."""
    if os.getenv("CACHE_DIR"):
        return Path(os.getenv("CACHE_DIR"))
    if os.path.isdir("/app"):
        return Path("/app/.cache")
    return Path.home() / ".cache" / "garmin-dashboard"


CACHE_DIR = _default_cache_dir()
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))

# Délai appliqué après chaque appel API réel (cache miss) : Garmin n'a pas de
# rate limit documenté mais bannit temporairement les clients trop agressifs.
API_COOLDOWN_S = 0.4

# Fenêtre maximale acceptée par chaque endpoint « par plage de dates » — au-delà
# Garmin répond 400 (« date range is too big » / « cannot be more than 28 days »).
# Valeurs vérifiées contre l'API réelle (août 2026).
SLEEP_WINDOW_DAYS = 28
RESTING_HR_WINDOW_DAYS = 28
HRV_WINDOW_DAYS = 365
VO2MAX_WINDOW_DAYS = 365


def default_tokenstore() -> str:
    """Tokenstore garth : /app/.garmin dans Docker, ~/.garminconnect sinon."""
    if os.getenv("GARMIN_TOKENSTORE"):
        return os.getenv("GARMIN_TOKENSTORE")
    if os.path.isdir("/app"):
        return "/app/.garmin"
    return "~/.garminconnect"


# ---------------------------------------------------------------------------
# Authentification (pattern officiel python-garminconnect, compatible MFA web)
# ---------------------------------------------------------------------------

def resume_session() -> Optional[Garmin]:
    """
    Reprend une session depuis le tokenstore, sans identifiants.
    Retourne None si aucun token valide n'est disponible.
    """
    try:
        api = Garmin()
        api.login(default_tokenstore())
        return api
    except Exception as e:
        logger.info("Pas de session Garmin à reprendre : %s", e)
        return None


def _dump_tokens(api: Garmin) -> None:
    """Persiste les tokens garth dans le tokenstore (le mode return_on_mfa
    de garminconnect ne le fait pas lui-même)."""
    path = Path(default_tokenstore()).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    api.client.dump(str(path))


def _finalize_login(api: Garmin) -> Garmin:
    """
    Recharge une session propre depuis le tokenstore : en mode return_on_mfa,
    `login()` retourne avant de charger le profil (display_name, unités…).
    """
    fresh = resume_session()
    return fresh if fresh is not None else api


def login_with_credentials(email: str, password: str):
    """
    Connexion par identifiants, sans prompt bloquant (compatible Streamlit).

    Retourne ("ok", api) si connecté, ou ("needs_mfa", pending) si un code MFA
    est requis — passer `pending` et le code à `complete_mfa()`.
    """
    api = Garmin(email=email, password=password, return_on_mfa=True)
    status, client_state = api.login()
    if status == "needs_mfa":
        return "needs_mfa", (api, client_state)
    _dump_tokens(api)
    return "ok", _finalize_login(api)


def complete_mfa(pending, mfa_code: str) -> Garmin:
    """Termine un login MFA entamé par `login_with_credentials`."""
    api, client_state = pending
    api.resume_login(client_state, mfa_code)
    _dump_tokens(api)
    return _finalize_login(api)


def clear_tokens() -> None:
    """Déconnexion : supprime le tokenstore garth."""
    path = Path(default_tokenstore()).expanduser()
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def athlete_id_of(api: Garmin) -> int:
    """profileId Garmin de la session — sert à cloisonner le cache disque."""
    try:
        profile = api.client.connectapi("/userprofile-service/socialProfile") or {}
        for key in ("profileId", "id", "userProfileId"):
            if profile.get(key):
                return int(profile[key])
    except Exception:
        pass
    # Repli stable : hash du display_name (UUID) de la session
    display_name = getattr(api, "display_name", "") or ""
    if display_name:
        return int(hashlib.md5(display_name.encode()).hexdigest()[:8], 16)
    return 0


# ---------------------------------------------------------------------------
# Utilitaires de cache disque (cloisonné par athlete_id)
# ---------------------------------------------------------------------------

# Les streams d'une activité passée ne changent (quasiment) plus : ils ont leur
# propre dossier, un TTL long, et survivent au bouton « Actualiser » — sinon la
# tendance de dérive re-téléchargerait N activités à chaque rafraîchissement.
STREAMS_BUCKET = "streams"
STREAMS_TTL = int(os.getenv("STREAMS_CACHE_TTL", str(30 * 86400)))


def _cache_path(athlete_id: int, key: str, bucket: Optional[str] = None) -> Path:
    """Cache file path under a per-athlete subdirectory."""
    safe_key = hashlib.md5(key.encode()).hexdigest()
    base = CACHE_DIR / str(athlete_id)
    if bucket:
        base = base / bucket
    return base / f"{safe_key}.json"


def _cache_get(
    athlete_id: int, key: str, ttl: Optional[int] = None, bucket: Optional[str] = None
) -> Optional[object]:
    path = _cache_path(athlete_id, key, bucket)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)
        if time.time() - entry["timestamp"] < (CACHE_TTL if ttl is None else ttl):
            return entry["data"]
        path.unlink(missing_ok=True)
    except (json.JSONDecodeError, KeyError, OSError) as e:
        logger.warning("Erreur lecture cache : %s", e)
    return None


def _cache_set(
    athlete_id: int, key: str, data: object, bucket: Optional[str] = None
) -> None:
    path = _cache_path(athlete_id, key, bucket)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Écriture atomique : un lecteur concurrent (autre session, serveur
        # MCP) ne doit jamais lire un fichier à moitié écrit. Les sessions
        # Streamlit sont des threads d'un même process : pid ET thread.
        tmp = path.with_suffix(f".{os.getpid()}-{threading.get_ident()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"timestamp": time.time(), "data": data}, f, default=str)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    except (OSError, TypeError, ValueError) as e:
        logger.warning("Impossible d'écrire le cache : %s", e)


# ---------------------------------------------------------------------------
# Fonctions pures — transformation des réponses Garmin (testables sans API)
# ---------------------------------------------------------------------------

def build_streams(raw_details: dict) -> dict[str, list]:
    """
    Convertit la réponse `get_activity_details` Garmin (metricDescriptors +
    activityDetailMetrics) en streams façon Strava :
    time, distance, latlng, heartrate, altitude, velocity_smooth, cadence,
    grade_smooth. Les clés sans données sont absentes du dict retourné.
    """
    descriptors = {
        d.get("key"): d.get("metricsIndex")
        for d in raw_details.get("metricDescriptors") or []
    }
    rows = raw_details.get("activityDetailMetrics") or []
    if not rows or not descriptors:
        return {}

    def col(key: str) -> list:
        idx = descriptors.get(key)
        if idx is None:
            return []
        out = []
        for r in rows:
            metrics = r.get("metrics") or []
            out.append(metrics[idx] if idx < len(metrics) else None)
        return out

    streams: dict[str, list] = {}
    mapping = {
        "time": "sumDuration",
        "distance": "sumDistance",
        "heartrate": "directHeartRate",
        "altitude": "directElevation",
        "velocity_smooth": "directSpeed",
        "cadence": "directDoubleCadence",  # spm réels (les deux pieds)
    }
    for stream_key, garmin_key in mapping.items():
        values = col(garmin_key)
        if values and any(v is not None for v in values):
            streams[stream_key] = values

    lats, lons = col("directLatitude"), col("directLongitude")
    if lats and lons and any(v is not None for v in lats):
        streams["latlng"] = [
            [la, lo] if la is not None and lo is not None else None
            for la, lo in zip(lats, lons)
        ]

    grade = compute_grade_stream(
        streams.get("distance", []), streams.get("altitude", [])
    )
    if grade:
        streams["grade_smooth"] = grade
    return streams


def compute_grade_stream(
    distances_m: list, altitudes_m: list, window: int = 15
) -> list:
    """
    Pente (%) lissée à partir des séries distance/altitude.
    Équivalent du stream `grade_smooth` de Strava, absent chez Garmin.
    """
    if not distances_m or not altitudes_m or len(distances_m) != len(altitudes_m):
        return []
    d = pd.to_numeric(pd.Series(distances_m), errors="coerce")
    a = pd.to_numeric(pd.Series(altitudes_m), errors="coerce").interpolate(
        limit_direction="both"
    )
    dd = d.diff()
    da = a.diff()
    grade = (da / dd.replace(0, np.nan) * 100).fillna(0.0)
    grade = grade.clip(-40, 40)
    smoothed = grade.rolling(window, center=True, min_periods=1).mean()
    return smoothed.fillna(0.0).round(2).tolist()


def compute_km_splits(streams: dict) -> list[dict]:
    """
    Splits par kilomètre calculés depuis les streams (Garmin ne fournit pas
    de découpage fixe au km — les laps dépendent du réglage autolap).
    Retourne le même format que les splits_metric Strava.
    """
    dists = streams.get("distance") or []
    times = streams.get("time") or []
    if len(dists) < 2 or len(times) != len(dists):
        return []

    hrs = streams.get("heartrate") or []
    alts = streams.get("altitude") or []

    d = pd.to_numeric(pd.Series(dists), errors="coerce")
    t = pd.to_numeric(pd.Series(times), errors="coerce")
    valid = d.notna() & t.notna()
    if valid.sum() < 2:
        return []

    splits = []
    total_m = float(d[valid].max())
    n_splits = int(total_m // 1000) + (1 if total_m % 1000 >= 50 else 0)

    prev_d, prev_t = 0.0, 0.0
    for i in range(1, n_splits + 1):
        target = min(i * 1000.0, total_m)
        # Premier index où la distance cumulée atteint la borne du split
        mask = valid & (d >= target)
        if not mask.any():
            break
        idx = int(mask.idxmax())
        cur_d = float(d.iloc[idx])
        cur_t = float(t.iloc[idx])
        seg_d = cur_d - prev_d
        seg_t = cur_t - prev_t
        if seg_d <= 0 or seg_t <= 0:
            prev_d, prev_t = cur_d, cur_t
            continue

        start_mask = valid & (d >= prev_d)
        start_idx = int(start_mask.idxmax()) if start_mask.any() else 0
        seg_hr = [
            h for h in hrs[start_idx:idx + 1] if h is not None
        ] if len(hrs) == len(dists) else []
        elev_diff = 0.0
        if len(alts) == len(dists):
            seg_alt = [a for a in alts[start_idx:idx + 1] if a is not None]
            if len(seg_alt) >= 2:
                elev_diff = float(seg_alt[-1]) - float(seg_alt[0])

        speed = seg_d / seg_t
        splits.append({
            "split": i,
            "distance_m": round(seg_d, 1),
            "elapsed_s": round(seg_t, 1),
            "moving_s": round(seg_t, 1),
            "pace_sec": speed_to_pace_seconds(speed),
            "pace": speed_to_pace(speed),
            "avg_hr": round(sum(seg_hr) / len(seg_hr), 1) if seg_hr else None,
            "elev_diff": round(elev_diff, 1),
            "pace_zone": None,
        })
        prev_d, prev_t = cur_d, cur_t
    return splits


def laps_to_splits(lap_dtos: list[dict]) -> list[dict]:
    """Convertit les lapDTOs Garmin au format splits du dashboard."""
    splits = []
    for i, lap in enumerate(lap_dtos or [], 1):
        dist = lap.get("distance", 0) or 0
        dur = lap.get("duration", 0) or 0
        speed = lap.get("averageSpeed", 0) or 0
        splits.append({
            "lap": lap.get("lapIndex", i),
            "distance_km": round(dist / 1000, 2),
            "duration_min": round(dur / 60, 2),
            "pace": speed_to_pace(speed),
            "pace_sec": speed_to_pace_seconds(speed),
            "avgHR": lap.get("averageHR"),
            "avgCadence": lap.get("averageRunCadence"),  # déjà en spm
            "elevationGain": lap.get("elevationGain"),
        })
    return splits


def summarize_activity(summary_dto: dict) -> dict:
    """Extrait un résumé du summaryDTO d'une activité Garmin."""
    distance_m = summary_dto.get("distance", 0) or 0
    duration_s = (
        summary_dto.get("movingDuration") or summary_dto.get("duration") or 0
    )
    avg_speed = summary_dto.get("averageSpeed", 0) or 0
    return {
        "distance_km": round(distance_m / 1000, 2),
        "duration_min": round(duration_s / 60, 1),
        "avgPace": speed_to_pace(avg_speed),
        "avgHR": summary_dto.get("averageHR"),
        "maxHR": summary_dto.get("maxHR"),
        "avgCadence": summary_dto.get("averageRunCadence"),
        "calories": summary_dto.get("calories"),
        "elevationGain": summary_dto.get("elevationGain"),
        "avgPower": summary_dto.get("averagePower"),
    }


def activity_row(act: dict) -> dict:
    """Convertit une activité Garmin (liste) en ligne du DataFrame commun."""
    distance_m = act.get("distance", 0) or 0
    duration_s = act.get("movingDuration") or act.get("duration") or 0
    avg_speed_ms = act.get("averageSpeed", 0) or 0
    type_key = (act.get("activityType") or {}).get("typeKey", "unknown")
    event_key = (act.get("eventType") or {}).get("typeKey")

    cadence = act.get("averageRunningCadenceInStepsPerMinute")  # déjà en spm
    if cadence is None:
        cadence = act.get("averageBikingCadenceInRevPerMinute")

    return {
        "activityId": act.get("activityId"),
        "startTimeLocal": act.get("startTimeLocal"),
        "activityName": act.get("activityName", ""),
        "activityType": normalize_activity_type(type_key),
        "distance_km": round(distance_m / 1000, 2),
        "duration_min": round(duration_s / 60, 1),
        "avgPace": speed_to_pace(avg_speed_ms),
        "avgPace_sec": speed_to_pace_seconds(avg_speed_ms),
        "avgHR": act.get("averageHR"),
        "maxHR": act.get("maxHR"),
        "avgCadence": cadence,
        "calories": int(act["calories"]) if act.get("calories") else None,
        "elevationGain": act.get("elevationGain"),
        "avgSpeed_ms": avg_speed_ms,
        "startLat": act.get("startLatitude"),
        "startLon": act.get("startLongitude"),
        "workoutType": event_key or "uncategorized",
        "trainingLoad": act.get("activityTrainingLoad"),
        "vo2max": act.get("vO2MaxValue"),
    }


def _activities_to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if not df.empty:
        # startTimeLocal Garmin est déjà en heure locale naïve ("YYYY-MM-DD HH:MM:SS")
        df["startTimeLocal"] = pd.to_datetime(df["startTimeLocal"])
    return df


# ---------------------------------------------------------------------------
# Séries quotidiennes « par plage de dates » (page Comparatif annuel)
# ---------------------------------------------------------------------------

def date_windows(start: str, end: str, max_days: int) -> list[tuple[str, str]]:
    """
    Découpe l'intervalle de dates ISO [start, end] (bornes incluses) en fenêtres
    de `max_days` jours au plus — les endpoints Garmin par plage refusent
    au-delà. Retourne [] si `end` précède `start`.
    """
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first or max_days < 1:
        return []
    windows, cursor = [], first
    while cursor <= last:
        stop = min(cursor + timedelta(days=max_days - 1), last)
        windows.append((cursor.isoformat(), stop.isoformat()))
        cursor = stop + timedelta(days=1)
    return windows


def _rows_with_date(data) -> list[dict]:
    """Ne garde que les dicts portant une `calendarDate` (réponses hétérogènes)."""
    items = data if isinstance(data, list) else [data]
    return [d for d in items if isinstance(d, dict) and d.get("calendarDate")]


def extract_sleep_days(data) -> list[dict]:
    """
    `dailySleepsByDate` → liste de résumés de nuits, avec le score global
    remonté au premier niveau sous `sleepScoreValue` (il vit dans
    `sleepScores.overall.value`, hors de portée d'un mapping plat).
    """
    rows = []
    for day in _rows_with_date(data):
        scores = day.get("sleepScores") if isinstance(day.get("sleepScores"), dict) else {}
        overall = scores.get("overall") if isinstance(scores.get("overall"), dict) else {}
        rows.append({**day, "sleepScoreValue": overall.get("value")})
    return rows


def extract_hrv_days(data) -> list[dict]:
    """`hrv/daily` → contenu de `hrvSummaries`."""
    if isinstance(data, dict):
        return _rows_with_date(data.get("hrvSummaries") or [])
    return _rows_with_date(data)


def extract_vo2max_days(data) -> list[dict]:
    """`metrics/maxmet/daily` → sous-objet `generic` de chaque jour mesuré."""
    items = data if isinstance(data, list) else [data]
    return _rows_with_date([
        d.get("generic") for d in items
        if isinstance(d, dict) and isinstance(d.get("generic"), dict)
    ])


def extract_resting_hr_days(data) -> list[dict]:
    """`stats/heartRate/daily` → `values` remontées au premier niveau."""
    rows = []
    for day in (data if isinstance(data, list) else [data]):
        if not isinstance(day, dict) or not day.get("calendarDate"):
            continue
        values = day.get("values") if isinstance(day.get("values"), dict) else {}
        rows.append({"calendarDate": day["calendarDate"], **values})
    return rows


# ---------------------------------------------------------------------------
# Classe principale
# ---------------------------------------------------------------------------

class GarminClient:
    """
    Encapsule la session Garmin Connect et la récupération des données.
    Expose le même contrat de DataFrames que l'ancien StravaClient, pour que
    les pages et la logique métier restent inchangées.
    """

    def __init__(self, api: Garmin, athlete_id: Optional[int] = None):
        self.api = api
        self.athlete_id = athlete_id if athlete_id is not None else athlete_id_of(api)

    # ------------------------------------------------------------------
    # Activités
    # ------------------------------------------------------------------

    def get_activities(self, limit: int = 50) -> pd.DataFrame:
        """
        Récupère les N dernières activités et retourne un DataFrame.

        Colonnes : activityId, startTimeLocal, activityName, activityType,
        distance_km, duration_min, avgPace, avgPace_sec, avgHR, maxHR,
        avgCadence, calories, elevationGain, avgSpeed_ms, startLat, startLon,
        workoutType (eventType Garmin), trainingLoad, vo2max
        """
        cache_key = f"activities_{limit}"
        cached = _cache_get(self.athlete_id, cache_key)
        if cached is not None:
            logger.info("Activités chargées depuis le cache.")
            return _activities_to_df(cached)

        logger.info("Récupération de %d activités depuis Garmin...", limit)

        activities: list[dict] = []
        start = 0
        batch_size = 100
        while len(activities) < limit:
            want = min(batch_size, limit - len(activities))
            batch = self.api.get_activities(start=start, limit=want)
            if not batch:
                break
            activities.extend(batch)
            if len(batch) < want:
                break
            start += len(batch)
        time.sleep(API_COOLDOWN_S)

        rows = [activity_row(act) for act in activities[:limit]]
        _cache_set(self.athlete_id, cache_key, rows)
        return _activities_to_df(rows)

    def get_activity_details(self, activity_id: int) -> dict:
        """
        Détails complets d'une activité : dict avec les clés
        "details" (summaryDTO brut), "splits" (laps Garmin),
        "splits_metric" (par km, calculés depuis les streams), "summary".
        Retourne {} en cas d'erreur API (l'erreur est loggée).
        """
        cache_key = f"activity_detail_{activity_id}"
        cached = _cache_get(self.athlete_id, cache_key)
        if cached is not None:
            return cached

        logger.info("Récupération des détails de l'activité %s...", activity_id)

        try:
            raw = self.api.get_activity(str(activity_id))
        except Exception as e:
            logger.error("Erreur récupération activité %s : %s", activity_id, e)
            return {}

        summary_dto = raw.get("summaryDTO") or {}

        try:
            laps_raw = self.api.get_activity_splits(str(activity_id))
            splits = laps_to_splits(laps_raw.get("lapDTOs") or [])
        except Exception as e:
            logger.warning("Impossible de récupérer les laps : %s", e)
            splits = []

        streams = self.get_streams(activity_id)

        result = {
            "details": summary_dto,
            "splits": splits,
            "splits_metric": compute_km_splits(streams),
            "summary": summarize_activity(summary_dto),
        }
        _cache_set(self.athlete_id, cache_key, result)
        time.sleep(API_COOLDOWN_S)
        return result

    def get_streams(self, activity_id: int, strict: bool = False) -> dict[str, list]:
        """
        Streams haute résolution d'une activité, format Strava :
        time, distance, latlng, heartrate, altitude, velocity_smooth,
        cadence, grade_smooth.

        `strict=True` relève l'erreur API au lieu de renvoyer `{}` : une boucle
        sur plusieurs activités doit pouvoir s'arrêter au premier refus (429)
        plutôt que de marteler Garmin.
        """
        cache_key = f"streams_v2_{activity_id}"
        cached = _cache_get(self.athlete_id, cache_key, ttl=STREAMS_TTL, bucket=STREAMS_BUCKET)
        if cached is not None:
            return cached

        try:
            raw = self.api.get_activity_details(
                str(activity_id), maxchart=2000, maxpoly=4000
            )
        except Exception as e:
            if strict:
                raise
            logger.warning("Streams indisponibles pour l'activité %s : %s", activity_id, e)
            time.sleep(1.0)
            return {}
        try:
            result = build_streams(raw)
        except Exception as e:
            # Réponse inattendue : ce n'est pas un refus de Garmin, pas la
            # peine d'arrêter une boucle ni de planter la page.
            logger.warning("Streams illisibles pour l'activité %s : %s", activity_id, e)
            return {}
        # Un stream vide peut être transitoire (activité en cours de synchro) :
        # on ne le fige pas pour 30 jours.
        if result:
            _cache_set(self.athlete_id, cache_key, result, bucket=STREAMS_BUCKET)
        time.sleep(API_COOLDOWN_S)
        return result

    def get_splits_aggregate(self, activity_ids: list[int]) -> pd.DataFrame:
        """
        Charge les splits_metric pour une liste d'activités et retourne un
        DataFrame long : activityId, split, pace_sec, pace_min, avg_hr, elev_diff.
        """
        rows = []
        for aid in activity_ids:
            detail_data = self.get_activity_details(aid)
            if not detail_data:
                continue
            for s in detail_data.get("splits_metric", []):
                if s["pace_sec"] <= 0:
                    continue
                rows.append({
                    "activityId": aid,
                    "split": s["split"],
                    "pace_sec": s["pace_sec"],
                    "pace_min": s["pace_sec"] / 60,
                    "avg_hr": s.get("avg_hr"),
                    "elev_diff": s.get("elev_diff", 0) or 0,
                })
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    # ------------------------------------------------------------------
    # Statistiques agrégées (pur DataFrame — identique au dashboard Strava)
    # ------------------------------------------------------------------

    def get_weekly_stats(self, df: pd.DataFrame) -> pd.DataFrame:
        """Agrège les activités par semaine."""
        running_df = _filter_running(df)
        if running_df is None:
            return pd.DataFrame()
        running_df["week"] = running_df["startTimeLocal"].dt.to_period("W").apply(
            lambda r: r.start_time
        )
        weekly = (
            running_df.groupby("week")
            .agg(
                km_total=("distance_km", "sum"),
                nb_sorties=("activityId", "count"),
                pace_moyen_sec=("avgPace_sec", _agg_mean_pace),
                hr_moyen=("avgHR", "mean"),
                denivele_total=("elevationGain", "sum"),
            )
            .reset_index()
        )
        return _finalize_stats(weekly).sort_values("week")

    def get_monthly_stats(self, df: pd.DataFrame) -> pd.DataFrame:
        """Agrège les activités par mois."""
        running_df = _filter_running(df)
        if running_df is None:
            return pd.DataFrame()
        running_df["month"] = running_df["startTimeLocal"].dt.to_period("M").apply(
            lambda r: r.start_time
        )
        monthly = (
            running_df.groupby("month")
            .agg(
                km_total=("distance_km", "sum"),
                nb_sorties=("activityId", "count"),
                pace_moyen_sec=("avgPace_sec", _agg_mean_pace),
                hr_moyen=("avgHR", "mean"),
            )
            .reset_index()
        )
        monthly = _finalize_stats(monthly)
        monthly["month_label"] = monthly["month"].dt.strftime("%b %Y")
        return monthly.sort_values("month")

    def get_hr_zones(self, df: pd.DataFrame, hr_zones: list) -> pd.DataFrame:
        """Distribue les activités dans les zones FC (bornes bpm réelles)."""
        if df.empty or not hr_zones:
            return pd.DataFrame()

        running_df = df[df["activityType"] == "running"].dropna(subset=["avgHR"]).copy()
        if running_df.empty:
            return pd.DataFrame()

        rows = []
        for i, zone in enumerate(hr_zones, 1):
            low = zone.get("min", 0) or 0
            high = zone.get("max", -1)
            unlimited = not high or high < 0
            label = f"Z{i} (≥{low} bpm)" if unlimited else f"Z{i} ({low}–{high} bpm)"
            mask = running_df["avgHR"] >= low if unlimited else (
                (running_df["avgHR"] >= low) & (running_df["avgHR"] < high)
            )
            rows.append({"zone": label, "nb_activites": int(mask.sum())})

        return pd.DataFrame(rows)

    def get_summary_metrics(self, df: pd.DataFrame) -> dict:
        """Retourne les métriques résumées pour la semaine et le mois courants."""
        if df.empty:
            return {
                "km_semaine": 0, "km_mois": 0,
                "pace_moyen": "—", "hr_moyen": "—",
                "nb_sorties_semaine": 0, "nb_sorties_mois": 0,
            }

        running = df[df["activityType"] == "running"].copy()
        from datetime import datetime, timedelta
        now = datetime.now()
        start_of_week = now - timedelta(days=now.weekday())
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        week_mask = running["startTimeLocal"] >= start_of_week
        month_mask = running["startTimeLocal"] >= start_of_month

        km_semaine = running.loc[week_mask, "distance_km"].sum()
        km_mois = running.loc[month_mask, "distance_km"].sum()
        nb_semaine = week_mask.sum()
        nb_mois = month_mask.sum()

        pace_vals = running.loc[running["avgPace_sec"] > 0, "avgPace_sec"]
        pace_moyen = seconds_to_pace_str(pace_vals.mean()) if not pace_vals.empty else "—"

        hr_vals = running["avgHR"].dropna()
        hr_moyen = f"{int(hr_vals.mean())} bpm" if not hr_vals.empty else "—"

        return {
            "km_semaine": round(km_semaine, 1),
            "km_mois": round(km_mois, 1),
            "pace_moyen": pace_moyen,
            "hr_moyen": hr_moyen,
            "nb_sorties_semaine": int(nb_semaine),
            "nb_sorties_mois": int(nb_mois),
        }

    # ------------------------------------------------------------------
    # Profil, zones FC, matériel, prédictions
    # ------------------------------------------------------------------

    def get_full_name(self) -> str:
        try:
            return self.api.get_full_name() or ""
        except Exception:
            return ""

    def get_hr_zones_definition(self) -> list[dict]:
        """
        Zones FC configurées dans Garmin Connect (profil course à pied).
        Retourne [{"min": bpm, "max": bpm}, …] (max=-1 pour la dernière zone),
        ou [] si l'endpoint n'est pas disponible.
        """
        cache_key = "hr_zones"
        cached = _cache_get(self.athlete_id, cache_key)
        if cached is not None:
            return cached
        try:
            data = self.api.connectapi("/biometric-service/heartRateZones")
            configs = data if isinstance(data, list) else []
            conf = next(
                (z for z in configs if (z.get("sport") or "").upper() == "RUNNING"),
                configs[0] if configs else None,
            )
            if not conf:
                return []
            floors = [conf.get(f"zone{i}Floor") for i in range(1, 6)]
            if any(f is None for f in floors):
                return []
            zones = [
                {"min": int(floors[i]), "max": int(floors[i + 1])}
                for i in range(4)
            ] + [{"min": int(floors[4]), "max": -1}]
            _cache_set(self.athlete_id, cache_key, zones)
            time.sleep(API_COOLDOWN_S)
            return zones
        except Exception as e:
            logger.warning("Zones FC indisponibles : %s", e)
            return []

    def get_shoes(self) -> list[dict]:
        """
        Chaussures du profil Garmin (gear) : [{"name", "distance_km", "retired"}].
        Retourne [] si le gear n'est pas accessible.
        """
        cache_key = "gear_shoes"
        cached = _cache_get(self.athlete_id, cache_key)
        if cached is not None:
            return cached
        try:
            device = self.api.get_device_last_used() or {}
            upn = device.get("userProfileNumber")
            if not upn:
                return []
            gear = self.api.get_gear(upn) or []
            shoes = []
            for g in gear:
                if (g.get("gearTypeName") or "").lower() != "shoes":
                    continue
                distance_m = 0.0
                try:
                    stats = self.api.get_gear_stats(g.get("uuid")) or {}
                    distance_m = stats.get("totalDistance", 0) or 0
                except Exception:
                    pass
                name = (
                    g.get("customMakeModel")
                    or g.get("displayName")
                    or f"{g.get('gearMakeName', '')} {g.get('gearModelName', '')}".strip()
                    or "Chaussure"
                )
                shoes.append({
                    "name": name,
                    "distance_km": round(distance_m / 1000),
                    "retired": bool(g.get("dateEnd")),
                })
            _cache_set(self.athlete_id, cache_key, shoes)
            time.sleep(API_COOLDOWN_S)
            return shoes
        except Exception as e:
            logger.warning("Gear indisponible : %s", e)
            return []

    def get_race_predictions(self) -> dict:
        """
        Prédictions de course natives Garmin, en secondes :
        {"time5K", "time10K", "timeHalfMarathon", "timeMarathon"}.
        """
        cache_key = "race_predictions"
        cached = _cache_get(self.athlete_id, cache_key)
        if cached is not None:
            return cached
        try:
            data = self.api.get_race_predictions()
            if isinstance(data, list):
                data = data[-1] if data else {}
            data = data or {}
            _cache_set(self.athlete_id, cache_key, data)
            time.sleep(API_COOLDOWN_S)
            return data
        except Exception as e:
            logger.warning("Prédictions de course indisponibles : %s", e)
            return {}

    def get_training_plans(self) -> dict:
        """
        Plans d'entraînement Garmin du compte (Garmin Run Coach compris), bruts.
        Retourne {} si l'endpoint n'est pas disponible.
        """
        cache_key = "training_plans"
        cached = _cache_get(self.athlete_id, cache_key)
        if cached is not None:
            return cached
        try:
            data = self.api.get_training_plans() or {}
            _cache_set(self.athlete_id, cache_key, data)
            time.sleep(API_COOLDOWN_S)
            return data
        except Exception as e:
            logger.warning("Plans d'entraînement indisponibles : %s", e)
            return {}

    def get_adaptive_plan(self, plan_id: int) -> dict:
        """
        Détail d'un plan adaptatif : séances programmées (`taskList`) et phases.

        Le plan se réajuste côté Garmin après chaque séance — le TTL de cache
        habituel suffit, le bouton d'actualisation force la relecture.
        """
        cache_key = f"adaptive_plan_{plan_id}"
        cached = _cache_get(self.athlete_id, cache_key)
        if cached is not None:
            return cached
        try:
            data = self.api.get_adaptive_training_plan_by_id(plan_id) or {}
            _cache_set(self.athlete_id, cache_key, data)
            time.sleep(API_COOLDOWN_S)
            return data
        except Exception as e:
            logger.warning("Plan adaptatif %s indisponible : %s", plan_id, e)
            return {}

    def get_personal_records(self) -> list:
        """Records personnels Garmin (liste brute, parsée par progression_logic)."""
        cache_key = "personal_records"
        cached = _cache_get(self.athlete_id, cache_key)
        if cached is not None:
            return cached
        try:
            data = self.api.get_personal_record() or []
            _cache_set(self.athlete_id, cache_key, data)
            time.sleep(API_COOLDOWN_S)
            return data
        except Exception as e:
            logger.warning("Records personnels indisponibles : %s", e)
            return []

    def get_race_predictions_range(self, start: str, end: str) -> list:
        """Historique journalier des prédictions de course entre deux dates ISO."""
        cache_key = f"race_predictions_{start}_{end}"
        cached = _cache_get(self.athlete_id, cache_key)
        if cached is not None:
            return cached
        try:
            data = self.api.get_race_predictions(
                startdate=start, enddate=end, _type="daily"
            ) or []
            if isinstance(data, dict):
                data = [data]
            _cache_set(self.athlete_id, cache_key, data)
            time.sleep(API_COOLDOWN_S)
            return data
        except Exception as e:
            logger.warning("Historique des prédictions indisponible : %s", e)
            return []

    # ------------------------------------------------------------------
    # Santé / bien-être (page Forme & Récupération)
    # ------------------------------------------------------------------

    def _daily(self, method_name: str, cdate: str, cache_prefix: str):
        """Wrapper générique : appel journalier avec cache disque."""
        cache_key = f"{cache_prefix}_{cdate}"
        cached = _cache_get(self.athlete_id, cache_key)
        if cached is not None:
            return cached
        try:
            data = getattr(self.api, method_name)(cdate)
            _cache_set(self.athlete_id, cache_key, data)
            time.sleep(API_COOLDOWN_S)
            return data
        except Exception as e:
            logger.warning("%s(%s) indisponible : %s", method_name, cdate, e)
            return None

    def get_sleep(self, cdate: str):
        return self._daily("get_sleep_data", cdate, "sleep")

    def get_training_readiness(self, cdate: str):
        return self._daily("get_training_readiness", cdate, "readiness")

    def get_training_status(self, cdate: str):
        return self._daily("get_training_status", cdate, "training_status")

    def get_daily_stats(self, cdate: str):
        return self._daily("get_stats", cdate, "daily_stats")

    def get_hrv(self, cdate: str):
        """HRV de la nuit : hrvSummary (lastNightAvg, baseline, status) + readings."""
        return self._daily("get_hrv_data", cdate, "hrv")

    def get_body_battery(self, start: str, end: str):
        """Body Battery entre deux dates ISO (liste de jours)."""
        cache_key = f"body_battery_{start}_{end}"
        cached = _cache_get(self.athlete_id, cache_key)
        if cached is not None:
            return cached
        try:
            data = self.api.get_body_battery(start, end)
            _cache_set(self.athlete_id, cache_key, data)
            time.sleep(API_COOLDOWN_S)
            return data
        except Exception as e:
            logger.warning("Body Battery indisponible : %s", e)
            return []

    # ------------------------------------------------------------------
    # Séries quotidiennes par plage (page Comparatif annuel)
    # ------------------------------------------------------------------

    def _connect_range(
        self,
        cache_prefix: str,
        start: str,
        end: str,
        window_days: int,
        request: Callable[[str, str], tuple[str, Optional[dict]]],
        extract: Callable[[object], list[dict]],
    ) -> list[dict]:
        """
        Concatène un endpoint `connectapi` journalier sur [start, end], fenêtre
        par fenêtre (Garmin plafonne chaque appel à `window_days` jours).

        Le cache est posé par fenêtre : les fenêtres des années révolues ont une
        clé stable, seule la dernière (celle qui contient aujourd'hui) bouge.
        Une fenêtre en échec est loggée et ignorée — le comparatif s'affiche
        avec les années disponibles plutôt que de tomber en erreur.
        """
        rows: list[dict] = []
        for win_start, win_end in date_windows(start, end, window_days):
            cache_key = f"{cache_prefix}_{win_start}_{win_end}"
            cached = _cache_get(self.athlete_id, cache_key)
            if cached is not None:
                rows.extend(cached)
                continue
            try:
                path, params = request(win_start, win_end)
                raw = self.api.connectapi(path, params=params) if params \
                    else self.api.connectapi(path)
                window_rows = extract(raw or [])
                _cache_set(self.athlete_id, cache_key, window_rows)
                time.sleep(API_COOLDOWN_S)
                rows.extend(window_rows)
            except Exception as e:
                logger.warning(
                    "%s indisponible sur %s → %s : %s",
                    cache_prefix, win_start, win_end, e,
                )
        return rows

    def get_sleep_range(self, start: str, end: str) -> list[dict]:
        """Résumés de nuits entre deux dates ISO (durée, phases, score, stress)."""
        return self._connect_range(
            "sleep_range", start, end, SLEEP_WINDOW_DAYS,
            lambda s, e: (
                "/wellness-service/wellness/dailySleepsByDate",
                {"startDate": s, "endDate": e, "nonSleepBufferMinutes": 60},
            ),
            extract_sleep_days,
        )

    def get_hrv_range(self, start: str, end: str) -> list[dict]:
        """HRV nuit par nuit entre deux dates ISO (lastNightAvg, baseline, status)."""
        return self._connect_range(
            "hrv_range", start, end, HRV_WINDOW_DAYS,
            lambda s, e: (f"/hrv-service/hrv/daily/{s}/{e}", None),
            extract_hrv_days,
        )

    def get_vo2max_range(self, start: str, end: str) -> list[dict]:
        """VO2max quotidien entre deux dates ISO (jours sans mesure absents)."""
        return self._connect_range(
            "vo2max_range", start, end, VO2MAX_WINDOW_DAYS,
            lambda s, e: (f"/metrics-service/metrics/maxmet/daily/{s}/{e}", None),
            extract_vo2max_days,
        )

    def get_resting_hr_range(self, start: str, end: str) -> list[dict]:
        """FC de repos quotidienne entre deux dates ISO."""
        return self._connect_range(
            "resting_hr_range", start, end, RESTING_HR_WINDOW_DAYS,
            lambda s, e: (f"/usersummary-service/stats/heartRate/daily/{s}/{e}", None),
            extract_resting_hr_days,
        )

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def invalidate_cache(self, include_streams: bool = False) -> None:
        """
        Supprime le cache disque de cet athlète uniquement. Les streams
        (immuables, coûteux à re-télécharger) sont conservés sauf demande
        explicite.
        """
        athlete_dir = CACHE_DIR / str(self.athlete_id)
        if not athlete_dir.exists():
            return
        for child in athlete_dir.iterdir():
            if child.name == STREAMS_BUCKET and not include_streams:
                _sweep_streams(child)
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        logger.info("Cache invalidé pour l'athlète %s.", self.athlete_id)


def _sweep_streams(folder: Path) -> None:
    """Purge des streams expirés et des fichiers temporaires orphelins."""
    now = time.time()
    for f in folder.iterdir():
        try:
            if f.suffix == ".tmp" or now - f.stat().st_mtime > STREAMS_TTL:
                f.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Wrapper d'erreur partagé pour les pages Streamlit
# ---------------------------------------------------------------------------

def safe_load_activities(
    client: GarminClient, limit: int
) -> tuple[pd.DataFrame, str | None]:
    """
    Encapsule `client.get_activities(limit)` avec des messages d'erreur lisibles.
    Retourne (DataFrame, message). `message` est None en cas de succès.
    """
    try:
        df = client.get_activities(limit=limit)
        return df, None
    except GarminConnectAuthenticationError:
        return pd.DataFrame(), (
            "Session Garmin expirée ou révoquée. Reconnecte-toi depuis la page d'accueil."
        )
    except GarminConnectTooManyRequestsError:
        return pd.DataFrame(), (
            "Garmin rate-limite les requêtes (429). Réessaie dans quelques minutes."
        )
    except GarminConnectConnectionError as e:
        return pd.DataFrame(), f"Erreur réseau Garmin : {e}"
    except Exception as e:
        logger.exception("Erreur inattendue dans safe_load_activities")
        return pd.DataFrame(), f"Erreur inattendue : {e}"


# ---------------------------------------------------------------------------
# Helpers internes (spécifiques aux stats DataFrame de GarminClient)
# ---------------------------------------------------------------------------

def _agg_mean_pace(x):
    """Moyenne de pace en ignorant les valeurs nulles (utilisée dans groupby.agg)."""
    return x[x > 0].mean() if (x > 0).any() else 0


def _filter_running(df: pd.DataFrame) -> pd.DataFrame | None:
    """Filtre aux activités de course ; retourne None si vide."""
    if df.empty:
        return None
    runs = df[df["activityType"] == "running"].copy()
    return runs if not runs.empty else None


def _finalize_stats(agg: pd.DataFrame) -> pd.DataFrame:
    """Applique les transformations communes aux DataFrames d'agrégation."""
    agg["pace_moyen"] = agg["pace_moyen_sec"].apply(seconds_to_pace_str)
    agg["km_total"] = agg["km_total"].round(1)
    agg["hr_moyen"] = agg["hr_moyen"].round(0)
    return agg
