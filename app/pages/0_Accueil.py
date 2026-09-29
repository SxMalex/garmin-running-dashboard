"""
Accueil — cockpit du jour : la séance, la fraîcheur, la récupération de la
nuit, la semaine et les signaux tirés des données. Le détail complet des
activités vit sur la page Activités ; la forme sur Forme & récup.
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import goal_store
from coach_logic import target_label
from formatting import md_escape, seconds_to_pace_str, weekday_fr
from forme_logic import compute_forme_verdict, hrv_label, parse_recovery
from home_logic import (
    HOME_HEADLINES,
    SHOE_RETIRE_KM,
    SHOE_WARN_KM,
    home_signals,
    planned_from_coach,
    planned_from_goal,
    run_totals,
    week_days,
)
from next_session_logic import (
    MIN_RUNS_FOR_SESSION,
    SESSION_TYPES,
    compute_pmc_series,
    compute_tsb,
    load_risk,
    reference_threshold_sec,
    todays_session,
)
from physio_logic import efficiency_change, efficiency_trend
from ui_helpers import (
    cache_nonce,
    cached_coach_context,
    cached_load_activities,
    drop_session,
    get_athlete_id,
    get_garmin_client,
    render_activity_map,
    render_garmin_attribution,
    require_login,
    validated_plan_sessions,
)
from ui_mode import explain, is_pro
from ui_theme import (
    chip,
    esc,
    freshness_bar,
    html_block,
    session_card,
    signal_card,
    tsb_status,
    verdict_chip,
    week_strip,
)

require_login()
_athlete_id = get_athlete_id()


# `nonce` : clé de cache per-session, incrémentée par Actualiser (en-tête).
@st.cache_data(ttl=86400, show_spinner=False)
def load_shoes(athlete_id: int, nonce: int) -> list:
    return get_garmin_client().get_shoes()


@st.cache_data(ttl=3600, show_spinner=False)
def load_today(athlete_id: int, cdate: str, nonce: int) -> dict:
    """Sommeil, HRV et stats quotidiennes du jour."""
    client = get_garmin_client()
    return parse_recovery(client.get_hrv(cdate), client.get_sleep(cdate),
                          client.get_daily_stats(cdate))


@st.cache_data(ttl=3600, show_spinner=False)
def load_streams(athlete_id: int, activity_id: int) -> dict:
    return get_garmin_client().get_streams(activity_id)


# Tout l'historique : le TSB et la séance suggérée en dépendent.
df, error = cached_load_activities(_athlete_id)

if error:
    st.error(f"**Erreur de connexion Garmin**\n\n{error}")
    if st.button("Reconnecter à Garmin", icon=":material/sync:"):
        drop_session()
        st.rerun()
    st.stop()

if df.empty:
    st.warning("Aucune activité trouvée. Vérifie ton compte Garmin Connect.")
    st.stop()

running_df = df[df["activityType"] == "running"]
TODAY = date.today()

today = load_today(_athlete_id, TODAY.isoformat(), cache_nonce())
daily = today["daily"]
sleep_sec, sleep_score = today["sleep_sec"], today["sleep_score"]
hrv_status, hrv_last = today["hrv_status"], today["hrv_last"]

# Toutes les activités, pas seulement la course : le wing, le vélo ou la
# muscu fatiguent aussi (cf. compute_pmc_series).
ctl, atl, tsb = compute_tsb(df)
verdict = compute_forme_verdict(tsb, hrv_status, sleep_score)

# « Pourquoi » par défaut : les facteurs du verdict, pas sa phrase (déjà en titre).
_why = " · ".join(verdict["reasons"]) or verdict["headline"]

goal_sessions = validated_plan_sessions()
# Même chaîne que Prochaine sortie et le MCP : Run Coach > plan Objectif
# validé > logique interne, modulée par la récupération du jour (rec = None
# sous MIN_RUNS_FOR_SESSION courses).
_coach = cached_coach_context(_athlete_id)
_today_session = todays_session(df, hrv_status, sleep_score, _coach, goal_sessions)
rec = _today_session["rec"]
# Garmin muet sur Run Coach : le plan Objectif n'est annoncé nulle part (ni
# séance, ni pastille, ni semaine) — un Run Coach peut être actif.
_goal_plan = None if _today_session["coach_unknown"] else goal_sessions

# ---------------------------------------------------------------------------
# En-tête : la date, le verdict en une phrase, les pastilles
# ---------------------------------------------------------------------------
_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"]
_date_txt = f"{weekday_fr(TODAY).capitalize()} {TODAY.day} {_MONTHS[TODAY.month - 1]}"
_chips = [verdict_chip(verdict)]
# Même priorité que la séance : Run Coach actif d'abord, sinon plan Objectif.
_goal = goal_store.load(_athlete_id).get("goal") if _goal_plan and not _coach else None
if _coach:
    if _coach.get("days_to_event") is not None:
        _chips.append(chip(f"{_coach['plan']['name']} · J−{_coach['days_to_event']}"))
elif _goal:
    _days_left = (date.fromisoformat(_goal["race_date"]) - TODAY).days
    _chips.append(chip(f"{_goal['distance']} · J−{_days_left}"))
html_block(
    f'<div class="gd-kicker">{esc(_date_txt)}</div>'
    f'<h1 class="gd-headline">{esc(HOME_HEADLINES[verdict["level"]])}</h1>'
    f'<div>{"".join(_chips)}</div>'
)

# ---------------------------------------------------------------------------
# Séance du jour + fraîcheur
# ---------------------------------------------------------------------------
col_session, col_fresh = st.columns([2, 1], gap="medium")
with col_session, st.container(key="card-session"):
    if rec is None:
        html_block(session_card(
            kicker="Séance du jour", number="—", unit="", title="Pas encore de séance suggérée",
            why=f"Il faut au moins {MIN_RUNS_FOR_SESSION} courses dans l'historique pour "
                "proposer une séance."))
    else:
        s = SESSION_TYPES[rec["session_key"]]
        _task = rec.get("coach_task")
        if _task:
            _when = ("Aujourd'hui" if _task["date"] == TODAY
                     else weekday_fr(_task["date"]).capitalize() + _task["date"].strftime(" %d/%m"))
            _bits = [_coach["plan"]["name"]]
            if _coach["phase"]:
                _bits.append(f"phase {_coach['phase']['label']}")
            if _coach["days_to_event"] is not None:
                _bits.append(f"J−{_coach['days_to_event']}")
            # duration_min vaut 0 quand Garmin ne fournit pas la durée : la reco
            # fusionnée porte la valeur de repli.
            _dur = _task.get("duration_min") or rec.get("duration_min")
            card = session_card(
                kicker="Séance du jour · Garmin Run Coach", number=str(_dur) if _dur else "—",
                unit="min" if _dur else "", title=_task["name"], target=target_label(_task),
                when=f"{_when} — {', '.join(_bits)}", why=_why)
        elif rec.get("goal_session"):
            # Plan Objectif validé (celui que la page Objectif envoie au calendrier).
            _g = rec["goal_session"]
            card = session_card(
                kicker="Séance du jour · plan Objectif", number=f"{_g['distance_km']:g}", unit="km",
                title=_g["title"], target=_g.get("target", ""), when=rec["suggested_date_str"],
                why=_g.get("why") or _why)
        else:
            card = session_card(
                kicker="Séance suggérée", number=f"{rec['target_dist_km']:g}", unit="km",
                title=s["label"], target=f"à {rec['target_pace_str']}",
                when=rec["suggested_date_str"], why=_why)
        html_block(card)
        if _today_session["coach_unknown"]:
            st.info("Garmin n'a pas répondu sur ton plan Run Coach : séance calculée par le "
                    "dashboard. Si un plan Run Coach est en cours, c'est ta montre qui fait foi.",
                    icon=":material/cloud_off:")
        if _today_session["alert"]:
            st.warning(_today_session["alert"], icon=":material/health_and_safety:")
        with st.container(horizontal=True):
            st.page_link("pages/5_Next_Session.py", label="Parcours et export GPX",
                         icon=":material/route:")
            if goal_sessions:
                st.page_link("pages/9_Objectif.py", label="Mon plan", icon=":material/event:")

with col_fresh, st.container(key="card-fresh"):
    _label, _status = tsb_status(tsb)
    html_block(
        '<div class="gd-kicker">Fraîcheur (TSB)</div>'
        f'<div><span class="gd-big">{"—" if tsb is None else f"{tsb:+.0f}"}</span> '
        f'{chip(_label, _status)}</div>'
        + freshness_bar(tsb)
    )
    f1, f2 = st.columns(2)
    f1.metric("Forme (CTL)", f"{ctl:.0f}")
    f2.metric("Fatigue (ATL)", f"{atl:.0f}")
    st.page_link("pages/3_Forme.py", label="Détail de la forme",
                 icon=":material/battery_charging_full:")
explain("tsb")

# ---------------------------------------------------------------------------
# Récupération de la nuit + semaine
# ---------------------------------------------------------------------------
col_rec, col_week = st.columns([1, 2], gap="medium")
with col_rec, st.container(key="card-recovery"):
    html_block('<div class="gd-kicker">Récupération cette nuit</div>')
    r1, r2 = st.columns(2)
    r3, r4 = st.columns(2)
    if sleep_sec:
        h, m = divmod(int(sleep_sec) // 60, 60)
        r1.metric("Sommeil", f"{h}h{m:02d}",
                  delta=f"score {sleep_score}" if sleep_score is not None else None,
                  delta_color="off", delta_arrow="off")
    else:
        r1.metric("Sommeil", "—")
    r2.metric("HRV", f"{int(hrv_last)} ms" if hrv_last else "—",
              delta=(hrv_label(hrv_status) or "").capitalize() or None,
              delta_color="off", delta_arrow="off")
    bb_high = daily.get("bodyBatteryHighestValue")
    r3.metric("Body Battery", f"{int(bb_high)}" if bb_high is not None else "—")
    rhr = daily.get("restingHeartRate")
    r4.metric("FC repos", f"{int(rhr)} bpm" if rhr else "—")

_week = run_totals(df, TODAY - timedelta(days=TODAY.weekday()))
_month = run_totals(df, TODAY.replace(day=1))
with col_week, st.container(key="card-week"):
    _planned = planned_from_coach(_coach) if _coach else planned_from_goal(_goal_plan)
    html_block(
        '<div class="gd-session-top"><div class="gd-kicker">Cette semaine</div>'
        f'<div><strong class="gd-big" style="font-size:1.6rem">{_week["km"]:g}</strong> km · '
        f'{_week["runs"]} sortie(s)</div></div>'
        + week_strip(week_days(df, TODAY, _planned))
    )
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Ce mois", f"{_month['km']:g} km",
              delta=f"{_month['runs']} sortie(s)", delta_color="off", delta_arrow="off")
    p2.metric("Allure du mois", seconds_to_pace_str(_month["pace_sec"]),
              help="Temps total ÷ distance totale des courses du mois.")
    p3.metric("D+ du mois", f"{_month['elevation']:.0f} m")
    if is_pro():
        p4.metric("FC moy. du mois",
                  f"{_month['hr']:.0f} bpm" if _month["hr"] is not None else "—",
                  help="Moyenne des FC moyennes des courses du mois, pondérée par leur durée.")

# ---------------------------------------------------------------------------
# Ce que tes données disent
# ---------------------------------------------------------------------------
_shoes = load_shoes(_athlete_id, cache_nonce())
_pmc = compute_pmc_series(df, reference_threshold_sec(df))
_signals = home_signals(load_risk(_pmc), efficiency_change(efficiency_trend(df), days=90),
                        _shoes)
if _signals:
    st.subheader("Ce que tes données disent")
    st.caption("Des signaux qu'on ne voit pas à l'œil nu, recalculés à chaque visite.")
    cols = st.columns(len(_signals), gap="small")
    for i, (col, sig) in enumerate(zip(cols, _signals)):
        with col, st.container(key=f"card-signal-{i}"):
            html_block(signal_card(**sig))
if _shoes:
    with st.expander(f"Toutes tes chaussures ({len(_shoes)})", icon=":material/steps:"):
        for shoe in sorted(_shoes, key=lambda s: (s["retired"], -(s["distance_km"] or 0))):
            km = shoe["distance_km"] or 0
            if shoe["retired"]:
                st.caption(f"~~{md_escape(shoe['name'])}~~ — {km:.0f} km *(retirée)*")
            else:
                st.progress(min(km / SHOE_RETIRE_KM, 1.0), text=f"**{md_escape(shoe['name'])}** — {km:.0f} km")
        st.caption(f"Repères : usure à partir de {SHOE_WARN_KM} km, remplacement vers {SHOE_RETIRE_KM} km.")

# ---------------------------------------------------------------------------
# Dernière sortie (résumé — le détail complet est sur la page Activités)
# ---------------------------------------------------------------------------
if not running_df.empty:
    last = running_df.sort_values("startTimeLocal", ascending=False).iloc[0]
    with st.container(key="card-last"):
        _when = pd.to_datetime(last["startTimeLocal"])
        html_block(f'<div class="gd-kicker">Dernière sortie · '
                   f'{esc(weekday_fr(_when.date()).capitalize())} {_when.strftime("%d/%m à %H:%M")}</div>'
                   f'<div class="gd-session-title">{esc(last["activityName"])}</div>')
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Distance", f"{last['distance_km']:.2f} km")
        m2.metric("Durée", f"{int(last['duration_min'])} min")
        m3.metric("Allure", last["avgPace"])
        m4.metric("FC moy", f"{int(last['avgHR'])} bpm" if pd.notna(last.get("avgHR")) else "—")
        if is_pro():
            m5, m6, m7, m8 = st.columns(4)
            m5.metric("FC max", f"{int(last['maxHR'])} bpm" if pd.notna(last.get("maxHR")) else "—")
            m6.metric("Cadence", f"{int(last['avgCadence'])} spm" if pd.notna(last.get("avgCadence")) else "—")
            m7.metric("Calories", f"{int(last['calories'])} kcal" if pd.notna(last.get("calories")) else "—")
            m8.metric("D+", f"{int(last['elevationGain'])} m" if pd.notna(last.get("elevationGain")) else "—")
        render_activity_map(load_streams(_athlete_id, int(last["activityId"])), height=300)
        st.page_link("pages/1_Activities.py", label="Splits, streams et détail complet",
                     icon=":material/list:")

    total_km = running_df["distance_km"].sum()
    _since = running_df["startTimeLocal"].min().strftime("%m/%Y")
    st.caption(f"Historique complet : {total_km:.0f} km sur {len(running_df)} sorties depuis {_since}")
render_garmin_attribution()
