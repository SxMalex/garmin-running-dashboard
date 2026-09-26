"""
Veille santé : « tu couves quelque chose ? ». Logique pure, testée.

Avant une infection, la physiologie de repos dévie souvent 24 à 48 h avant les
symptômes : FC de repos en hausse, HRV en baisse, respiration nocturne plus
rapide, SpO2 nocturne plus basse (Mishra 2020, Nature Biomedical Engineering ;
Natarajan 2020, npj Digital Medicine ; Miller 2020, PNAS). Aucun de ces signaux
n'est un diagnostic : une grosse séance, l'alcool, une mauvaise nuit ou
l'altitude font la même chose. C'est leur **concordance** qui compte.

Méthode : chaque signal de la dernière nuit est comparé à TA norme des jours
J−30 à J−3 (médiane et dispersion robuste, les 2 dernières nuits exclues pour ne
pas « normaliser » une déviation en cours). Un signal ne compte que s'il dévie
à la fois statistiquement (≥ 2 σ) ET d'une quantité physiologiquement notable.
Un signal absent ou trop jeune (< 10 nuits de référence) est dit, pas inventé.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BASELINE_DAYS = (30, 3)       # fenêtre de référence : J−30 … J−3
MIN_BASELINE_NIGHTS = 10
Z_THRESHOLD = 2.0
# Un signal ISOLÉ n'alerte que s'il persiste 2 nuits ou s'écarte très nettement :
# rejouée sur 60 nuits réelles, la règle « 1 signal ≥ 2 σ » allumait la carte une
# nuit sur sept (FC de repos après les grosses séances) — une alarme qu'on ne lit plus.
SINGLE_SIGNAL_Z = 3.0
STALE_AFTER_DAYS = 2          # au-delà, la « dernière nuit » est trop vieille


@dataclass(frozen=True)
class SignalDef:
    key: str
    label: str
    unit: str
    direction: int            # +1 : une hausse est suspecte ; −1 : une baisse
    min_delta: float          # écart absolu minimal pour compter
    sigma_floor: float        # dispersion minimale (capteurs quantifiés)
    relative: bool = False    # min_delta en fraction de la norme (HRV)


SIGNALS = (
    SignalDef("rhr", "FC de repos", "bpm", +1, 4.0, 1.5),
    SignalDef("hrv", "HRV nocturne", "ms", -1, 0.10, 3.0, relative=True),
    SignalDef("resp", "Respiration nocturne", "resp/min", +1, 1.0, 0.4),
    SignalDef("spo2", "SpO2 nocturne", "%", -1, 2.0, 1.0),
)


@dataclass
class SignalResult:
    key: str
    label: str
    unit: str
    value: float | None = None
    baseline: float | None = None
    delta: float | None = None
    z: float | None = None
    flagged: bool = False
    persistent: bool = False       # déjà en écart la nuit précédente
    status: str = "ok"             # ok / flagged / missing / learning
    note: str = ""


@dataclass
class HealthWatch:
    level: int                     # 0 rien, 1 à surveiller, 2 alerte
    status: str                    # good / warning / serious / info
    title: str
    message: str
    last_night: pd.Timestamp | None
    signals: list[SignalResult] = field(default_factory=list)

    @property
    def flagged(self) -> list[SignalResult]:
        return [s for s in self.signals if s.flagged]


def daily_health_frame(sleep_rows, hrv_rows, rhr_rows) -> pd.DataFrame:
    """
    Séries brutes Garmin (get_sleep_range / get_hrv_range /
    get_resting_hr_range) → une ligne par nuit : date, rhr, hrv, resp, spo2.
    """
    def _series(rows, *keys):
        out = {}
        for r in rows or []:
            if not isinstance(r, dict) or not r.get("calendarDate"):
                continue
            v = next((r.get(k) for k in keys if r.get(k) is not None), None)
            if isinstance(v, (int, float)) and np.isfinite(v) and v > 0:
                out[pd.Timestamp(r["calendarDate"])] = float(v)
        return pd.Series(out, dtype=float)

    frame = pd.DataFrame({
        "rhr": _series(rhr_rows, "restingHR", "restingHeartRate"),
        "hrv": _series(hrv_rows, "lastNightAvg"),
        "resp": _series(sleep_rows, "averageRespirationValue"),
        "spo2": _series(sleep_rows, "averageSpO2Value"),
    })
    frame.index.name = "date"
    return frame.sort_index()


def load_health_frame(gc, today, days: int = 34) -> pd.DataFrame:
    """
    Nuits des `days` derniers jours via un GarminClient (séries par plage déjà
    utilisées par le Comparatif). Chemin unique Accueil / serveur MCP.
    """
    start = (pd.Timestamp(today) - pd.Timedelta(days=days)).date().isoformat()
    end = pd.Timestamp(today).date().isoformat()
    return daily_health_frame(gc.get_sleep_range(start, end), gc.get_hrv_range(start, end),
                              gc.get_resting_hr_range(start, end))


def _deviation(sig: SignalDef, series: pd.Series, night: pd.Timestamp) -> SignalResult:
    res = SignalResult(sig.key, sig.label, sig.unit)
    s = series.dropna()
    if night not in s.index:
        res.status, res.note = "missing", "pas de mesure cette nuit"
        return res
    lo, hi = night - pd.Timedelta(days=BASELINE_DAYS[0]), night - pd.Timedelta(days=BASELINE_DAYS[1])
    ref = s[(s.index >= lo) & (s.index <= hi)]
    res.value = float(s[night])
    if len(ref) < MIN_BASELINE_NIGHTS:
        res.status = "learning"
        res.note = f"norme en construction ({len(ref)}/{MIN_BASELINE_NIGHTS} nuits)"
        return res
    med = float(ref.median())
    sigma = max(1.4826 * float((ref - med).abs().median()), sig.sigma_floor)
    res.baseline, res.delta = med, res.value - med
    res.z = res.delta / sigma * sig.direction          # > 0 = dans le sens suspect
    min_delta = sig.min_delta * med if sig.relative else sig.min_delta
    res.flagged = res.z >= Z_THRESHOLD and res.delta * sig.direction >= min_delta
    res.status = "flagged" if res.flagged else "ok"
    prev = night - pd.Timedelta(days=1)
    if res.flagged and prev in s.index:
        pz = (float(s[prev]) - med) / sigma * sig.direction
        res.persistent = pz >= Z_THRESHOLD
    return res


def health_watch(frame: pd.DataFrame, today: pd.Timestamp | None = None) -> HealthWatch | None:
    """Verdict de la dernière nuit, ou None si aucune donnée récente."""
    if frame is None or frame.empty:
        return None
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    night = frame.dropna(how="all").index.max()
    if night is None or pd.isna(night) or (today - night).days > STALE_AFTER_DAYS:
        return None
    signals = [_deviation(sig, frame[sig.key], night) for sig in SIGNALS]
    measured = [s for s in signals if s.status in ("ok", "flagged")]
    flagged = [s for s in signals if s.flagged]
    if len(flagged) == 1 and not (flagged[0].persistent or flagged[0].z >= SINGLE_SIGNAL_Z):
        flagged[0].note = "écart isolé d'une nuit : ignoré"
        flagged = []

    if len(flagged) >= 2:
        names = " et ".join(s.label for s in flagged)
        level, status, title = 2, "serious", "Ton corps lutte peut-être contre quelque chose"
        message = (f"{names} s'écartent ensemble de ta norme. Ce motif précède souvent "
                   "un rhume ou une infection de 1 à 2 jours : remplace l'intensité par du repos "
                   "ou un footing très facile, et surveille comment tu te sens.")
    elif len(flagged) == 1:
        s = flagged[0]
        level, status = 1, "warning"
        title = f"{s.label} inhabituelle" + (" (2e nuit)" if s.persistent else "")
        message = ("Un seul signal dévie : une grosse séance, l'alcool, la chaleur ou une nuit courte "
                   "suffisent à l'expliquer. Si ça persiste demain ou si un 2e signal s'y ajoute, "
                   "lève le pied.")
    elif measured:
        level, status, title = 0, "good", "Pas de signe de maladie"
        message = (f"{len(measured)} signal(aux) de récupération dans ta norme des 30 derniers jours "
                   f"({', '.join(s.label for s in measured)}).")
    else:
        level, status, title = 0, "info", "Veille santé en apprentissage"
        message = "Il faut une dizaine de nuits mesurées pour connaître ta norme."
    return HealthWatch(level, status, title, message, night, signals)
