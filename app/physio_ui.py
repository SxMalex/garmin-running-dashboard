"""
Rendus Streamlit des analyses physiologiques (`physio_logic`) : qualité du
signal cardio et dérive cardiaque d'une sortie.
"""

import logging

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from garminconnect import GarminConnectTooManyRequestsError

import chart_theme as ct
from physio_logic import (
    DECOUPLING_LEVELS,
    DECOUPLING_TREND_MAX_RUNS,
    DECOUPLING_TREND_WEEKS,
    aerobic_decoupling,
    decoupling_candidates,
    decoupling_history,
    decoupling_level,
    efficiency_change,
    efficiency_trend,
    hr_cadence_lock,
)
from ui_helpers import get_garmin_client
from ui_mode import decoupling_params, explain, help_text, is_pro, lock_params, render_pro_settings

PHYSIO_SETTINGS = ["lock_tol_bpm", "lock_min_duration_s", "lock_min_jump_bpm",
                   "decoupling_warmup_min", "decoupling_min_moving_min",
                   "decoupling_max_speed_cv"]


def render_physio_settings() -> None:
    """Curseurs Pro des seuils (FC calée, dérive) — mêmes valeurs sur toutes les pages."""
    render_pro_settings(PHYSIO_SETTINGS)

logger = logging.getLogger(__name__)

LEVEL_COLORS = {"solide": ct.GOOD, "a_consolider": ct.WARNING, "marquee": ct.SERIOUS}


def _km_at(streams: dict, t_s: float) -> float | None:
    times = np.asarray([x if x is not None else np.nan for x in streams.get("time") or []], float)
    dists = np.asarray([x if x is not None else np.nan for x in streams.get("distance") or []], float)
    ok = np.isfinite(times) & np.isfinite(dists)
    if ok.sum() < 2:
        return None
    return float(np.interp(t_s, times[ok], dists[ok])) / 1000


def _fmt_clock(t_s: float) -> str:
    t = int(round(t_s))
    return f"{t // 3600}h{(t % 3600) // 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def render_signal_quality(streams: dict) -> dict:
    """
    Bloc « Qualité du signal & endurance » d'une course. Retourne le résultat
    de dérive (pour réutilisation éventuelle par la page).
    """
    lock = hr_cadence_lock(streams, **lock_params())
    res = aerobic_decoupling(streams, exclude_mask=lock["mask"], **decoupling_params())

    st.subheader("🫀 Qualité du signal & endurance")
    col_sig, col_dec = st.columns(2)

    with col_sig:
        if lock["detected"]:
            spans = []
            for start, end in lock["segments"]:
                k0, k1 = _km_at(streams, start), _km_at(streams, end)
                where = f"km {k0:.1f} → {k1:.1f}" if k0 is not None else ""
                spans.append(f"{_fmt_clock(start)} → {_fmt_clock(end)} {where}".strip())
            st.warning(
                f"**FC probablement fausse** sur {lock['locked_s'] / 60:.0f} min "
                f"({lock['share']:.0%} de la sortie) : le capteur au poignet a "
                "recopié ta cadence.\n\n" + "\n".join(f"- {s}" for s in spans)
            )
            st.caption("Ces passages sont exclus du calcul de dérive. Serrer le "
                       "bracelet ou porter une ceinture cardio évite le phénomène.")
        else:
            st.success("**Signal cardio cohérent** — aucun décrochage du capteur optique détecté.")

    with col_dec:
        pct = res["decoupling_pct"]
        if res["valid"]:
            level = decoupling_level(pct)
            color = LEVEL_COLORS[level]
            st.markdown(
                f"<div style='border-left:4px solid {color};padding:4px 12px'>"
                f"<div style='color:{ct.INK_MUTED};font-size:.85rem'>Dérive cardiaque</div>"
                f"<div style='font-size:1.8rem;font-weight:700'>{pct:+.1f} %</div>"
                f"<div style='color:{color};font-weight:600'>"
                f"{DECOUPLING_LEVELS[level]['label']}</div></div>",
                unsafe_allow_html=True,
            )
            st.caption(
                f"Efficacité {res['ef_first']:.2f} → {res['ef_second']:.2f} m/min par battement "
                f"entre les deux moitiés ({res['moving_min']:.0f} min d'effort, échauffement exclu)."
                + (" Parcours vallonné : chiffre à prendre avec prudence." if res["hilly"] else "")
            )
        else:
            st.info(f"**Dérive cardiaque non mesurable** — {res['reason']}")
        if is_pro():
            raw = {"Dérive (%)": pct, "EF 1re / 2de moitié": (res["ef_first"], res["ef_second"]),
                   "Écart de vitesse entre moitiés": res["half_speed_diff"],
                   "CV vitesse": res["speed_cv"], "D+ lissé (m/km)": res["climb_m_per_km"],
                   "Effort (min)": res["moving_min"], "FC calée (s)": round(lock["locked_s"])}
            st.caption(" · ".join(f"{k} : {v}" for k, v in raw.items() if v is not None))

    e1, e2 = st.columns(2)
    with e1:
        explain("cadence_lock")
    with e2:
        explain("decoupling")
    return res


# ---------------------------------------------------------------------------
# Tendance multi-sorties (page Progression)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _load_streams_strict(athlete_id: int, activity_id: int) -> dict:
    # Une exception n'est pas mise en cache par st.cache_data : une sortie
    # refusée par Garmin (429) sera réessayée au prochain chargement.
    return get_garmin_client().get_streams(activity_id, strict=True)


def _load_candidate_streams(athlete_id: int, activity_ids: tuple) -> tuple[dict, str | None]:
    """
    Streams des sorties candidates. S'arrête au premier refus de Garmin (429,
    panne, session expirée) au lieu d'enchaîner les échecs ; les streams
    obtenus restent en cache disque 30 jours. Retourne (streams, cause).
    """
    streams_by_id = {}
    with st.spinner("Analyse de la dérive des sorties longues…"):
        for activity_id in activity_ids:
            try:
                streams_by_id[activity_id] = _load_streams_strict(athlete_id, activity_id)
            except GarminConnectTooManyRequestsError:
                logger.warning("Garmin 429 sur les streams de %s", activity_id)
                return streams_by_id, "Garmin limite le nombre d'appels"
            except Exception:
                logger.warning("Streams de %s indisponibles", activity_id, exc_info=True)
                return streams_by_id, "Garmin n'a pas répondu"
    return streams_by_id, None


def render_aerobic_progress(activities_df, athlete_id: int) -> None:
    """Section « Efficacité aérobie » : tendance EF + dérive des sorties longues."""
    st.subheader("🫀 Efficacité aérobie")
    trend = efficiency_trend(activities_df)
    col_kpi, col_chart = st.columns([1, 4])
    if trend.empty:
        st.info("Pas assez de courses (≥ 20 min, hors compétition) pour suivre l'efficacité.")
    else:
        change = efficiency_change(trend, days=90)
        col_kpi.metric(
            "Efficacité actuelle",
            f"{trend['ef_smooth'].iloc[-1]:.2f}",
            delta=f"{change:+.1f} % sur 90 j" if change is not None else None,
            help=help_text("ef") + " Médiane glissante sur 6 semaines.",
        )
        with col_chart:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=trend["startTimeLocal"], y=trend["ef"], mode="markers",
                marker=dict(size=8, color=ct.rgba(ct.GREEN, 0.35)),
                hovertemplate="%{x|%d/%m/%Y} · %{y:.2f} m/min/bpm<extra>sortie</extra>",
            ))
            fig.add_trace(go.Scatter(
                x=trend["startTimeLocal"], y=trend["ef_smooth"], mode="lines",
                line=dict(color=ct.GREEN, width=2),
                hovertemplate="%{x|%d/%m/%Y} · tendance %{y:.2f}<extra></extra>",
            ))
            fig.update_layout(height=260, showlegend=False,
                              yaxis=dict(title="m/min par battement"),
                              margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig)

    params = decoupling_params()
    candidates = decoupling_candidates(activities_df, min_duration_min=params["min_moving_s"] / 60 + 5)
    if not candidates:
        st.caption(f"Aucune sortie de plus de {params['min_moving_s'] / 60 + 5:.0f} min sur les "
                   f"{DECOUPLING_TREND_WEEKS} dernières semaines pour mesurer la dérive cardiaque.")
        return
    ids = tuple(int(c["activityId"]) for c in candidates)
    streams_by_id, failed = _load_candidate_streams(athlete_id, ids)
    items = [(c, streams_by_id.get(int(c["activityId"]))) for c in candidates]
    hist = decoupling_history(items, lock_params=lock_params(), **params)

    st.markdown("**Dérive cardiaque des sorties longues**")
    if hist.empty:
        st.info("Aucune sortie longue régulière récente : la dérive ne se mesure que sur "
                "≥ 40 min d'effort à allure constante (ni fractionné, ni progressif).")
    else:
        fig = go.Figure()
        for level, meta in DECOUPLING_LEVELS.items():
            part = hist[hist["level"] == level]
            if part.empty:
                continue
            fig.add_trace(go.Scatter(
                x=part["startTimeLocal"], y=part["decoupling_pct"], mode="markers",
                name=meta["label"],
                marker=dict(size=11, color=LEVEL_COLORS[level],
                            line=dict(color=ct.SURFACE, width=2)),
                customdata=part[["activityName", "moving_min"]].to_numpy(),
                hovertemplate="%{x|%d/%m/%Y} · %{customdata[0]}<br>"
                              "%{y:+.1f} % sur %{customdata[1]:.0f} min"
                              f"<extra>{meta['label']}</extra>",
            ))
        for y in (5, 10):
            fig.add_hline(y=y, line=dict(color=ct.BASELINE, width=1, dash="dot"))
        fig.update_layout(height=260, yaxis=dict(title="Dérive (%)", ticksuffix=" %"),
                          legend=dict(orientation="h", y=1.12),
                          margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig)
    measured = len(streams_by_id)
    note = (f"{len(hist)} sortie(s) mesurable(s) sur {len(candidates)} sorties longues "
            f"récentes (max {DECOUPLING_TREND_MAX_RUNS}, {DECOUPLING_TREND_WEEKS} semaines).")
    if failed:
        note += (f" {failed} : {measured}/{len(candidates)} analysées, la suite sera "
                 "tentée au prochain chargement.")
    st.caption(note)
    explain("ef")
