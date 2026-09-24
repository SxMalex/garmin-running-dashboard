"""
Page Forme & Récupération — le croisement charge d'entraînement (CTL/ATL/TSB)
× récupération (HRV, sommeil, Body Battery, FC repos), avec verdict du jour.
Remplace l'ancienne page Santé et l'onglet ⚡ Charge de la page Statistiques.
"""

from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from forme_logic import compute_forme_verdict, parse_recovery
from next_session_logic import compute_pmc_series, compute_tsb, load_risk, reference_threshold_sec
from ui_mode import explain, help_text, is_pro
from stats_tabs import tab_charge
from ui_helpers import (
    cached_load_activities,
    get_athlete_id,
    get_garmin_client,
    render_garmin_attribution,
    render_refresh_button,
    require_login,
)

st.set_page_config(
    page_title="Forme & Récup — Running Dashboard",
    page_icon="⚡",
    layout="wide",
)

require_login()

_athlete_id = get_athlete_id()


# ---------------------------------------------------------------------------
# Chargement (caché par date)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_hrv(athlete_id: int, cdate: str):
    return get_garmin_client().get_hrv(cdate)


@st.cache_data(ttl=3600, show_spinner=False)
def load_sleep(athlete_id: int, cdate: str):
    return get_garmin_client().get_sleep(cdate)


@st.cache_data(ttl=3600, show_spinner=False)
def load_daily_stats(athlete_id: int, cdate: str):
    return get_garmin_client().get_daily_stats(cdate)


@st.cache_data(ttl=3600, show_spinner="Chargement Body Battery…")
def load_body_battery(athlete_id: int, start: str, end: str):
    return get_garmin_client().get_body_battery(start, end)


# ---------------------------------------------------------------------------
# Parsing défensif des réponses Garmin
# ---------------------------------------------------------------------------
def _parse_bb_points(day: dict) -> list[tuple]:
    """
    Extrait les points (timestamp, niveau) d'une journée Body Battery.
    Le format de bodyBatteryValuesArray varie selon les versions de l'API :
    [[ts, level], …] ou [[ts, statut, level, version], …].
    """
    points = []
    for row in day.get("bodyBatteryValuesArray") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        ts = row[0]
        level = next(
            (v for v in row[1:] if isinstance(v, (int, float)) and 0 <= v <= 100),
            None,
        )
        if ts and level is not None:
            points.append((pd.to_datetime(ts, unit="ms"), level))
    return points


def _fmt_hm(seconds) -> str:
    if not seconds:
        return "—"
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h{m:02d}"


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## ⚙️ Paramètres")
    selected_date = st.date_input(
        "Jour analysé", value=date.today(), max_value=date.today()
    )
    bb_days = st.slider("Période Body Battery (jours)", 3, 30, 7)
    st.divider()
    charge_period = st.selectbox(
        "Période charge d'entraînement",
        options=["3 derniers mois", "6 derniers mois", "12 derniers mois"],
        index=0,
    )
    render_refresh_button("🔄 Actualiser")

cdate = selected_date.isoformat()

st.title("⚡ Forme & Récupération")
st.caption(
    "Charge d'entraînement (CTL/ATL/TSB) croisée avec ta récupération "
    "(HRV, sommeil, Body Battery, FC repos)."
)

# ---------------------------------------------------------------------------
# Données du jour + charge
# ---------------------------------------------------------------------------
df, error = cached_load_activities(_athlete_id)
if error:
    st.error(f"Erreur Garmin : {error}")
    st.stop()
# Historique complet : la charge des autres sports compte dans le TSB.
ctl, atl, tsb = compute_tsb(df) if not df.empty else (0.0, 0.0, None)

recovery = parse_recovery(load_hrv(_athlete_id, cdate), load_sleep(_athlete_id, cdate),
                          load_daily_stats(_athlete_id, cdate))
hrv_summary = recovery["hrv_summary"]
hrv_status = recovery["hrv_status"]
hrv_last = recovery["hrv_last"]
hrv_baseline = hrv_summary.get("baseline") or {}
sleep_dto = recovery["sleep_dto"]
sleep_sec = recovery["sleep_sec"]
sleep_score = recovery["sleep_score"]
daily = recovery["daily"]

# ---------------------------------------------------------------------------
# Verdict du jour
# ---------------------------------------------------------------------------
verdict = compute_forme_verdict(tsb, hrv_status, sleep_score)
_verdict_render = {2: st.success, 1: st.info, 0: st.warning}[verdict["level"]]
_verdict_render(
    f"**{verdict['label']}** — {verdict['headline']}",
    icon=verdict["icon"],
)
if verdict["reasons"]:
    st.caption(" · ".join(verdict["reasons"]))

# ---------------------------------------------------------------------------
# Métriques du jour
# ---------------------------------------------------------------------------
c1, c2, c3, c4, c5, c6 = st.columns(6)

if tsb is not None and not df.empty:
    if tsb > 10:
        tsb_delta, tsb_dc = "Bien reposé", "normal"
    elif tsb > -20:
        tsb_delta, tsb_dc = "Charge normale", "off"
    else:
        tsb_delta, tsb_dc = "Récupération nécessaire", "inverse"
    c1.metric("⚡ TSB — Fraîcheur", f"{tsb:+.1f}", delta=tsb_delta, delta_color=tsb_dc,
              help=f"CTL (forme 42 j) : {ctl:.1f} · ATL (fatigue 7 j) : {atl:.1f}")
else:
    c1.metric("⚡ TSB — Fraîcheur", "—")

_baseline_str = (
    f"baseline {hrv_baseline.get('balancedLow')}–{hrv_baseline.get('balancedUpper')}"
    if hrv_baseline.get("balancedLow") else None
)
c2.metric(
    "💓 HRV nuit",
    f"{int(hrv_last)} ms" if hrv_last else "—",
    delta=(hrv_status or "").capitalize() or None,
    delta_color="off", delta_arrow="off",
    help=_baseline_str or "Variabilité de la fréquence cardiaque pendant le sommeil",
)

c3.metric(
    "😴 Sommeil",
    _fmt_hm(sleep_sec),
    delta=f"Score {sleep_score}" if sleep_score is not None else None,
    delta_color="off", delta_arrow="off",
)

bb_high = daily.get("bodyBatteryHighestValue")
bb_low = daily.get("bodyBatteryLowestValue")
c4.metric(
    "🔋 Body Battery",
    f"{int(bb_high)}" if bb_high is not None else "—",
    delta=f"min {int(bb_low)}" if bb_low is not None else None,
    delta_color="off", delta_arrow="off",
)

rhr = daily.get("restingHeartRate")
c5.metric("❤️ FC repos", f"{int(rhr)} bpm" if rhr else "—")

stress = daily.get("averageStressLevel")
c6.metric("🧠 Stress moyen", f"{int(stress)}" if stress and stress >= 0 else "—")

e1, e2 = st.columns(2)
with e1:
    explain("tsb")
with e2:
    explain("hrv")

if is_pro() and not df.empty:
    risk = load_risk(compute_pmc_series(df, reference_threshold_sec(df)))
    if risk:
        _zone_label = {"sous_charge": "Sous-charge", "optimal": "Zone optimale",
                       "vigilance": "Vigilance", "risque": "Hausse brutale"}
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("ACWR 7/28 j", f"{risk['acwr']:.2f}" if risk["acwr"] is not None else "—",
                  delta=_zone_label.get(risk["acwr_zone"]), delta_color="off", delta_arrow="off",
                  help=help_text("acwr") + " Zones Gabbett 2016 ; indicateur discuté (Impellizzeri 2020).")
        r2.metric("Monotonie 7 j", f"{risk['monotony']:.2f}" if risk["monotony"] is not None else "—",
                  delta="élevée" if risk["monotony_high"] else None, delta_color="inverse",
                  help=help_text("monotony"))
        r3.metric("Strain 7 j", f"{risk['strain']:.0f}" if risk["strain"] is not None else "—",
                  help="Charge de la semaine × monotonie (Foster).")
        r4.metric("TSS aigu / chronique", f"{risk['acute']:.0f} / {risk['chronic']:.0f}",
                  help="Moyennes quotidiennes sur 7 et 28 jours, jours de repos inclus.")

st.divider()

# ---------------------------------------------------------------------------
# Charge d'entraînement (CTL / ATL / TSB) — ex-onglet ⚡ de Statistiques
# ---------------------------------------------------------------------------
st.subheader("⚡ Charge d'entraînement")
if df.empty:
    st.info("Pas d'activité chargée.")
else:
    _period_days = {"3 derniers mois": 90, "6 derniers mois": 180, "12 derniers mois": 365}
    cutoff = datetime.now() - timedelta(days=_period_days[charge_period])
    tab_charge.render(df, cutoff)

st.divider()

# ---------------------------------------------------------------------------
# Récupération : sommeil du jour + Body Battery
# ---------------------------------------------------------------------------
col_sleep, col_bb = st.columns([2, 3])

with col_sleep:
    st.subheader("😴 Structure du sommeil")
    phases = [
        ("Profond", sleep_dto.get("deepSleepSeconds"), "#3987e5"),
        ("Léger", sleep_dto.get("lightSleepSeconds"), "#6da7ec"),
        ("Paradoxal (REM)", sleep_dto.get("remSleepSeconds"), "#9085e9"),
        ("Éveillé", sleep_dto.get("awakeSleepSeconds"), "#e66767"),
    ]
    phases = [(label, sec, color) for label, sec, color in phases if sec]
    if phases:
        fig_sleep = go.Figure(go.Pie(
            labels=[p[0] for p in phases],
            values=[p[1] for p in phases],
            hole=0.45,
            marker=dict(colors=[p[2] for p in phases]),
            textinfo="label+percent",
            textfont=dict(size=11),
            customdata=[_fmt_hm(p[1]) for p in phases],
            hovertemplate="<b>%{label}</b><br>%{customdata}<extra></extra>",
        ))
        fig_sleep.update_layout(
            height=320,
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig_sleep)
    else:
        st.info("Pas de données de sommeil pour cette nuit.")

with col_bb:
    st.subheader("🔋 Body Battery")
    bb_start = (selected_date - timedelta(days=bb_days - 1)).isoformat()
    bb_data = load_body_battery(_athlete_id, bb_start, cdate) or []

    all_points = []
    for day in bb_data:
        if isinstance(day, dict):
            all_points.extend(_parse_bb_points(day))

    if all_points:
        bb_df = pd.DataFrame(all_points, columns=["ts", "level"]).sort_values("ts")
        fig_bb = go.Figure()
        fig_bb.add_trace(go.Scatter(
            x=bb_df["ts"], y=bb_df["level"],
            mode="lines",
            line=dict(color="#0ca30c", width=1.5),
            fill="tozeroy",
            fillcolor="rgba(12,163,12,0.10)",
            hovertemplate="%{x|%d/%m %H:%M} · %{y:.0f}<extra></extra>",
        ))
        fig_bb.update_layout(
            height=320,
            yaxis=dict(range=[0, 105], title="Niveau"),
            margin=dict(l=0, r=0, t=10, b=0), showlegend=False,
        )
        st.plotly_chart(fig_bb)
        charged = sum(d.get("charged") or 0 for d in bb_data if isinstance(d, dict))
        drained = sum(d.get("drained") or 0 for d in bb_data if isinstance(d, dict))
        st.caption(
            f"Sur {bb_days} jours : **+{charged}** rechargé · **−{drained}** dépensé."
        )
    else:
        st.info("Pas de données Body Battery sur la période.")

st.divider()

# ---------------------------------------------------------------------------
# Sommeil sur 7 jours
# ---------------------------------------------------------------------------
st.subheader("📅 Sommeil sur 7 jours")

sleep_rows = []
with st.spinner("Chargement de l'historique de sommeil…"):
    for i in range(6, -1, -1):
        d = selected_date - timedelta(days=i)
        raw = load_sleep(_athlete_id, d.isoformat()) or {}
        dto = (raw.get("dailySleepDTO") or {}) if isinstance(raw, dict) else {}
        sleep_rows.append({
            "date": d,
            "total_h": (dto.get("sleepTimeSeconds") or 0) / 3600,
            "deep_h": (dto.get("deepSleepSeconds") or 0) / 3600,
            "light_h": (dto.get("lightSleepSeconds") or 0) / 3600,
            "rem_h": (dto.get("remSleepSeconds") or 0) / 3600,
            "score": ((dto.get("sleepScores") or {}).get("overall") or {}).get("value"),
        })

sleep_week = pd.DataFrame(sleep_rows)
if sleep_week["total_h"].sum() > 0:
    fig_week = go.Figure()
    for key, label, color in [
        ("deep_h", "Profond", "#3987e5"),
        ("light_h", "Léger", "#6da7ec"),
        ("rem_h", "REM", "#9085e9"),
    ]:
        fig_week.add_trace(go.Bar(
            x=sleep_week["date"], y=sleep_week[key],
            name=label, marker_color=color,
            hovertemplate="%{x|%a %d/%m} · %{y:.1f} h<extra>" + label + "</extra>",
        ))
    fig_week.add_hline(y=8, line_dash="dot", line_color="#3a3f4a")
    fig_week.update_layout(
        barmode="stack",
        height=300,
        yaxis=dict(title="Heures"),
        margin=dict(l=0, r=0, t=30, b=0),
    )
    st.plotly_chart(fig_week)
    avg_sleep = sleep_week.loc[sleep_week["total_h"] > 0, "total_h"].mean()
    st.caption(f"Durée moyenne sur la semaine : **{_fmt_hm(avg_sleep * 3600)}** (repère : 8 h).")
else:
    st.info("Pas d'historique de sommeil disponible.")

render_garmin_attribution()
