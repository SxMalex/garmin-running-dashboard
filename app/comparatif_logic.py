"""
Logique pure du comparatif annuel — aligne plusieurs années sur un axe « jour
de l'année » pour les superposer, lisse les séries quotidiennes bruitées et
extrait les instantanés « où j'en étais à la même date ». Testable sans Streamlit.

Convention d'alignement : le jour de l'année est corrigé des années bissextiles
pour que le 1er mars vaille toujours 60, quelle que soit l'année comparée.
"""

from datetime import date

import numpy as np
import pandas as pd

# Jour de l'année du 1er mars hors année bissextile — pivot de la correction.
_MARCH_1_DOY = 60

# Mappings {colonne: clé JSON Garmin} des séries quotidiennes par plage.
SLEEP_FIELDS = {
    "sleep_sec": "sleepTimeSeconds",
    "sleep_score": "sleepScoreValue",
    "deep_sec": "deepSleepSeconds",
    "light_sec": "lightSleepSeconds",
    "rem_sec": "remSleepSeconds",
    "sleep_hr": "avgHeartRate",
    "sleep_stress": "avgSleepStress",
}
HRV_FIELDS = {
    "hrv": "lastNightAvg",
    "hrv_weekly": "weeklyAvg",
}
VO2MAX_FIELDS = {
    "vo2max": "vo2MaxPreciseValue",
}
RESTING_HR_FIELDS = {
    "resting_hr": "restingHR",
}


def aligned_doy(dates: pd.Series) -> pd.Series:
    """
    Jour de l'année comparable d'une année sur l'autre : en année bissextile,
    tout ce qui suit février est décalé de −1 pour que le 1er mars vaille 60
    partout. Le 29 février retombe donc sur 59, comme le 28.
    """
    dt = pd.to_datetime(dates)
    doy = dt.dt.dayofyear
    leap_shift = dt.dt.is_leap_year & (doy >= _MARCH_1_DOY)
    return (doy - leap_shift.astype(int)).astype(int)


def add_year_doy(df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    """Ajoute les colonnes `year` et `doy` (aligné) à partir d'une colonne date."""
    if df.empty:
        out = df.copy()
        out["year"] = pd.Series(dtype=int)
        out["doy"] = pd.Series(dtype=int)
        return out
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col])
    out["year"] = out[date_col].dt.year.astype(int)
    out["doy"] = aligned_doy(out[date_col])
    return out


def records_to_df(
    records: list[dict] | None,
    fields: dict[str, str],
    date_key: str = "calendarDate",
) -> pd.DataFrame:
    """
    Convertit une liste de dicts Garmin en DataFrame `date, year, doy, <fields>`.

    Les valeurs sont forcées en numérique (NaN si absentes ou non convertibles),
    les doublons de date sont dédupliqués sur la dernière occurrence.
    Retourne un DataFrame vide avec les bonnes colonnes s'il n'y a rien.
    """
    rows = []
    for rec in records or []:
        if not isinstance(rec, dict) or not rec.get(date_key):
            continue
        row = {"date": rec[date_key]}
        for col, key in fields.items():
            row[col] = rec.get(key)
        rows.append(row)

    if not rows:
        empty = pd.DataFrame(columns=["date", *fields, "year", "doy"])
        return empty

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    for col in fields:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    return add_year_doy(df).reset_index(drop=True)


def smooth_by_year(
    df: pd.DataFrame,
    value_col: str,
    window: int = 7,
    out_col: str | None = None,
) -> pd.DataFrame:
    """
    Moyenne glissante de `value_col`, calculée indépendamment dans chaque année
    (pas de contamination de janvier par le décembre précédent). `min_periods=1`
    pour que les premiers jours de l'année restent tracés.
    """
    target = out_col or f"{value_col}_smooth"
    out = df.copy()
    if out.empty:
        out[target] = pd.Series(dtype=float)
        return out
    out = out.sort_values(["year", "doy"])
    out[target] = (
        out.groupby("year")[value_col]
        .transform(lambda s: s.rolling(window, min_periods=1).mean())
    )
    return out


def daily_sum_by_year(
    df: pd.DataFrame,
    value_col: str,
    date_col: str = "startTimeLocal",
) -> pd.DataFrame:
    """
    Somme `value_col` par jour calendaire, avec `year` et `doy` alignés.
    Sert de base aux cumuls annuels (km, D+, temps).
    """
    if df.empty or value_col not in df.columns:
        return pd.DataFrame(columns=["year", "doy", value_col])
    tmp = add_year_doy(df[[date_col, value_col]].copy(), date_col)
    tmp[value_col] = pd.to_numeric(tmp[value_col], errors="coerce").fillna(0.0)
    return (
        tmp.groupby(["year", "doy"], as_index=False)[value_col]
        .sum()
        .sort_values(["year", "doy"])
        .reset_index(drop=True)
    )


def cumulative_by_year(
    daily: pd.DataFrame,
    value_col: str,
    last_doy: dict[int, int] | None = None,
) -> pd.DataFrame:
    """
    Cumul depuis le 1er janvier, jour par jour, pour chaque année.

    Réindexe sur tous les jours (1 → dernier jour connu de l'année) afin que la
    courbe soit continue même sans activité. `last_doy` permet de fixer le
    dernier jour tracé par année (typiquement aujourd'hui pour l'année en
    cours) ; par défaut on s'arrête au dernier jour actif.
    """
    if daily.empty:
        return pd.DataFrame(columns=["year", "doy", value_col, "cumul"])

    frames = []
    for year, grp in daily.groupby("year"):
        end = (last_doy or {}).get(int(year), int(grp["doy"].max()))
        end = max(end, int(grp["doy"].max()))
        index = pd.RangeIndex(1, end + 1, name="doy")
        series = (
            grp.set_index("doy")[value_col]
            .reindex(index, fill_value=0.0)
        )
        frames.append(pd.DataFrame({
            "year": int(year),
            "doy": index,
            value_col: series.to_numpy(),
            "cumul": series.cumsum().to_numpy(),
        }))
    return pd.concat(frames, ignore_index=True)


def snapshot_at_doy(
    df: pd.DataFrame,
    value_col: str,
    doy: int,
    tolerance: int = 10,
) -> dict[int, float]:
    """
    Valeur de chaque année « à la même date » : dernière mesure non nulle dans
    la fenêtre [doy − tolerance, doy]. Les années sans mesure dans la fenêtre
    sont absentes du résultat. `tolerance` absorbe les trous (VO2max non mesuré
    les jours sans course, nuit non enregistrée…).
    """
    if df.empty or value_col not in df.columns:
        return {}
    window = df[
        (df["doy"] <= doy)
        & (df["doy"] >= doy - max(0, tolerance))
        & df[value_col].notna()
    ]
    if window.empty:
        return {}
    last = window.sort_values("doy").groupby("year").tail(1)
    return {int(r.year): float(getattr(r, value_col)) for r in last.itertuples()}


def year_summary(running_df: pd.DataFrame, doy_limit: int) -> pd.DataFrame:
    """
    Tableau récapitulatif par année, tronqué au même jour de l'année pour que
    la comparaison reste honnête : sorties, km, D+, temps, allure moyenne
    (pondérée par la distance) et FC moyenne.
    """
    columns = ["year", "sorties", "km", "denivele", "heures", "pace_sec", "fc_moy"]
    if running_df.empty:
        return pd.DataFrame(columns=columns)

    df = add_year_doy(running_df.copy(), "startTimeLocal")
    df = df[df["doy"] <= doy_limit]
    if df.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for year, grp in df.groupby("year"):
        km = float(grp["distance_km"].sum())
        minutes = float(grp["duration_min"].sum())
        hr = pd.to_numeric(grp.get("avgHR"), errors="coerce")
        rows.append({
            "year": int(year),
            "sorties": int(len(grp)),
            "km": round(km, 1),
            "denivele": float(pd.to_numeric(grp.get("elevationGain"), errors="coerce").fillna(0).sum()),
            "heures": round(minutes / 60, 1),
            "pace_sec": round(minutes * 60 / km, 1) if km > 0 else np.nan,
            "fc_moy": round(float(hr.mean()), 0) if hr is not None and hr.notna().any() else np.nan,
        })
    return pd.DataFrame(rows).sort_values("year", ascending=False).reset_index(drop=True)


def _weighted_mean(values, weights) -> float:
    """Moyenne pondérée en ignorant les lignes sans valeur ou sans poids."""
    vals = pd.to_numeric(values, errors="coerce")
    wts = pd.to_numeric(weights, errors="coerce")
    usable = vals.notna() & wts.notna() & (wts > 0)
    if not usable.any():
        return np.nan
    return float((vals[usable] * wts[usable]).sum() / wts[usable].sum())


def _column(df: pd.DataFrame, name: str) -> pd.Series:
    """Colonne du DataFrame, ou une colonne de NaN si elle est absente."""
    if name in df.columns:
        return df[name]
    return pd.Series(np.nan, index=df.index)


def day_comparison(running_df: pd.DataFrame, day: date) -> pd.DataFrame:
    """
    Ce qui a été couru le même jour calendaire, année par année.

    Une ligne par année ayant au moins une sortie ce jour-là ; les années sans
    sortie sont absentes du résultat (à l'appelant d'afficher « repos »). Une
    journée à plusieurs sorties est agrégée : distances, durées, D+, charge et
    calories sommés, allure recalculée sur les totaux, FC et cadence moyennées au
    prorata de la durée, FC max prise au maximum.

    Le filtre se fait sur le (mois, jour) RÉEL de `day`, pas sur un doy aligné :
    celui-ci fait exprès retomber le 29 février sur la même valeur que le 28
    (cf. `aligned_doy`) pour superposer les courbes, si bien qu'aucun doy ne
    désigne le 29 février — le 29/02/2028, on comparerait le 28. Le 29 février
    ne trouve donc que les autres 29 février (années bissextiles), et le 28
    n'agrège jamais le 29.
    """
    columns = [
        "year", "date", "start_time", "sorties", "km", "minutes", "pace_sec",
        "avgHR", "maxHR", "elevation", "cadence", "trainingLoad", "calories", "names",
    ]
    if running_df.empty or "startTimeLocal" not in running_df.columns:
        return pd.DataFrame(columns=columns)

    runs = running_df.copy()
    runs["startTimeLocal"] = pd.to_datetime(runs["startTimeLocal"])
    runs = runs[(runs["startTimeLocal"].dt.month == day.month)
                & (runs["startTimeLocal"].dt.day == day.day)]
    if runs.empty:
        return pd.DataFrame(columns=columns)
    runs["year"] = runs["startTimeLocal"].dt.year.astype(int)

    rows = []
    for year, grp in runs.groupby("year"):
        grp = grp.sort_values("startTimeLocal")
        km = float(pd.to_numeric(_column(grp, "distance_km"), errors="coerce").fillna(0).sum())
        minutes = float(pd.to_numeric(_column(grp, "duration_min"), errors="coerce").fillna(0).sum())
        max_hr = pd.to_numeric(_column(grp, "maxHR"), errors="coerce")
        rows.append({
            "year": int(year),
            "date": grp["startTimeLocal"].iloc[0].normalize(),
            "start_time": grp["startTimeLocal"].iloc[0].strftime("%H:%M"),
            "sorties": int(len(grp)),
            "km": round(km, 2),
            "minutes": round(minutes, 1),
            "pace_sec": round(minutes * 60 / km, 1) if km > 0 else np.nan,
            "avgHR": _weighted_mean(_column(grp, "avgHR"), _column(grp, "duration_min")),
            "maxHR": float(max_hr.max()) if max_hr.notna().any() else np.nan,
            "elevation": float(pd.to_numeric(_column(grp, "elevationGain"), errors="coerce").fillna(0).sum()),
            "cadence": _weighted_mean(_column(grp, "avgCadence"), _column(grp, "duration_min")),
            "trainingLoad": float(pd.to_numeric(_column(grp, "trainingLoad"), errors="coerce").fillna(0).sum()),
            "calories": float(pd.to_numeric(_column(grp, "calories"), errors="coerce").fillna(0).sum()),
            "names": [
                str(name) for name in _column(grp, "activityName").tolist()
                if isinstance(name, str) and name
            ],
        })
    return pd.DataFrame(rows).sort_values("year", ascending=False).reset_index(drop=True)


def split_at_doy(
    df: pd.DataFrame,
    doy: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Coupe une série annuelle en deux à `doy` : la partie « déjà vécue cette
    année » et la partie postérieure des années passées (tracée en pointillé,
    pour ne pas laisser croire que l'année en cours est en retard).
    Le point de coupe est présent dans les deux morceaux — la courbe reste jointive.
    """
    if df.empty:
        return df, df
    return df[df["doy"] <= doy].copy(), df[df["doy"] >= doy].copy()
