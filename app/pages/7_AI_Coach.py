"""
Page IA Coach — Génère des prompts prêts à copier dans n'importe quel LLM.
Aucune dépendance Ollama — compatible Streamlit Community Cloud.

Deux réglages de date : « situation au » rejoue le contexte d'un jour passé
(sorties jusqu'à ce jour, charge, HRV, sommeil) ; « prochaine séance » donne à
l'IA le créneau (jour + heure) de la sortie à préparer, repas compris.
"""

import html
from datetime import date, datetime, time, timedelta

import streamlit as st
import pandas as pd

import goal_store
from coach_logic import nutrition_focus, target_label
from forme_logic import hrv_label, parse_recovery
from formatting import seconds_to_pace_str, weekday_fr
from next_session_logic import compute_pmc_series, compute_tsb, reference_threshold_sec
from progression_logic import RACE_TARGETS, fmt_race_time, parse_personal_records
from ui_helpers import (
    cached_coach_context,
    cached_load_activities,
    get_garmin_client,
    render_garmin_attribution,
    get_athlete_id,
    require_login,
)

st.set_page_config(
    page_title="IA Coach — Running Dashboard",
    page_icon="🤖",
    layout="wide",
)

require_login()

_athlete_id = get_athlete_id()

st.markdown("""
<style>
    .summary-block {
        background: #F5F4EF;
        border: 1px solid #E4E1D8;
        border-radius: 12px;
        padding: 12px 16px;
        font-family: monospace;
        font-size: 0.85rem;
        line-height: 1.6;
        color: #3D4048;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Prompt système partagé
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
Tu es un coach running expert et bienveillant avec plus de 20 ans d'expérience.
Tu analyses les données d'entraînement d'un coureur et fournis des conseils personnalisés,
précis et motivants.

Tes analyses couvrent :
- L'évaluation de la charge d'entraînement (volume, intensité, récupération)
- La progression du pace et de la fréquence cardiaque
- La prévention des blessures (sur-entraînement, sous-récupération)
- Des recommandations concrètes pour la prochaine semaine
- Des encouragements adaptés au niveau du coureur

Tu réponds toujours en français, avec un ton professionnel mais chaleureux.
Tu bases tes analyses uniquement sur les données fournies.
Quand tu n'as pas assez de données pour conclure, tu le précises honnêtement.
Tes réponses sont structurées avec des titres clairs et des listes à puces quand c'est pertinent.\
"""

_ANALYSIS_REQUEST = """\
Analyse ces données et donne-moi :
1. Une évaluation de ma charge d'entraînement actuelle
2. Les points forts observés
3. Les points d'amélioration
4. Des recommandations concrètes pour la prochaine semaine
5. Une note de motivation personnalisée\
"""

# Prompt nutrition — volontairement orienté « idées de plats », pas « plan
# nutritionnel » : sans poids ni journal alimentaire, aucune quantité ne serait
# sérieusement calculable, et ce n'est pas le rôle de l'application.
_NUTRITION_SYSTEM_PROMPT = """\
Tu es un coach running expérimenté qui sait aussi cuisiner simplement.
Tu proposes des idées de repas faciles à un coureur amateur, en tenant compte de
son entraînement des jours à venir.

Tes principes :
- des plats simples, du quotidien, avec des ingrédients courants en France ;
- tu expliques en une phrase pourquoi le plat tombe bien par rapport à la séance ;
- tu restes sur des repères d'alimentation générale : pas de calcul de calories
  ni de macros précises, pas de complément alimentaire, pas de régime restrictif ;
- si une question relève de la diététique individuelle ou médicale, tu invites à
  consulter un professionnel plutôt que d'improviser.

Tu réponds toujours en français, de façon concrète et brève.\
"""

_NUTRITION_REQUEST = """\
Propose-moi 3 idées de plats faciles pour les prochains jours, en tenant compte
des séances prévues ci-dessus. Pour chacun :
1. Le nom du plat et les ingrédients principaux
2. Le temps de préparation approximatif
3. Quand le manger (veille de séance, après la séance, jour de repos) et pourquoi
4. Une variante encore plus rapide si je suis pressé\
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _until(df: pd.DataFrame, asof: date) -> pd.DataFrame:
    """Activités jusqu'au jour `asof` inclus (situation rejouée à une date passée)."""
    if df is None or df.empty:
        return df
    return df[pd.to_datetime(df["startTimeLocal"]).dt.date <= asof]


def _format_activities_summary(df: pd.DataFrame, n: int = 10, asof: date | None = None) -> str:
    if df is None or df.empty:
        return "Aucune activité disponible."
    running = df[df["activityType"] == "running"].copy()
    if asof is not None:
        running = _until(running, asof)
    if running.empty:
        return "Aucune activité de course disponible."

    running = running.sort_values("startTimeLocal", ascending=False).head(n)
    lines = [f"=== {len(running)} dernières sorties course ===\n"]

    for _, row in running.iterrows():
        date_str = pd.to_datetime(row["startTimeLocal"]).strftime("%d/%m/%Y")
        name = row.get("activityName") or "Course"
        dist = row.get("distance_km", 0)
        dur = row.get("duration_min", 0)
        pace = row.get("avgPace", "—")
        hr = row.get("avgHR")
        cadence = row.get("avgCadence")
        elev = row.get("elevationGain")
        calories = row.get("calories")

        hr_str  = f"{int(hr)} bpm"      if hr       and not pd.isna(hr)       else "N/A"
        cad_str = f"{int(cadence)} spm" if cadence  and not pd.isna(cadence)  else "N/A"
        elev_str = f"{int(elev)} m D+"  if elev     and not pd.isna(elev)     else "N/A"
        cal_str  = f"{int(calories)} kcal" if calories and not pd.isna(calories) else "N/A"

        lines.append(
            f"- {date_str} | {name}\n"
            f"  Distance : {dist:.1f} km | Durée : {dur:.0f} min | Allure : {pace}\n"
            f"  FC moy : {hr_str} | Cadence : {cad_str} | D+ : {elev_str} | Calories : {cal_str}"
        )

    total_km = running["distance_km"].sum()
    avg_pace_sec = running.loc[running["avgPace_sec"] > 0, "avgPace_sec"].mean()
    avg_hr = running["avgHR"].dropna().mean()

    lines.append(
        f"\n=== Statistiques sur ces {len(running)} sorties ===\n"
        f"- Volume total : {total_km:.1f} km\n"
        f"- Allure moyenne : {seconds_to_pace_str(avg_pace_sec)}\n"
        f"- FC moyenne : {int(avg_hr) if avg_hr and not pd.isna(avg_hr) else 'N/A'} bpm"
    )
    return "\n".join(lines)


def _load_at(activities_df: pd.DataFrame, asof: date) -> tuple[float, float, float] | None:
    """
    (CTL, ATL, TSB) en fin de journée `asof`. Aujourd'hui : `compute_tsb`, le
    chiffre de tout le dashboard. Date passée : la même série PMC (même seuil
    unique, historique complet), relue au jour demandé.
    """
    if activities_df.empty:
        return None
    if asof >= date.today():
        return compute_tsb(activities_df)
    pmc = compute_pmc_series(activities_df, reference_threshold_sec(activities_df))
    row = pmc[pd.to_datetime(pmc["date"]).dt.date == asof] if not pmc.empty else pmc
    if row.empty:
        return None
    ctl, atl = round(float(row.iloc[0]["ctl"]), 1), round(float(row.iloc[0]["atl"]), 1)
    return ctl, atl, round(ctl - atl, 1)


def _format_forme_summary(client, activities_df: pd.DataFrame, asof: date | None = None) -> str:
    """
    Contexte forme/récupération pour le prompt : charge (CTL/ATL/TSB), HRV,
    sommeil sur 7 jours, FC repos, records et prédictions. Chaque appel client
    passe par le cache disque — coût quasi nul après le premier chargement.

    `asof` (défaut aujourd'hui) rejoue la situation d'un jour passé. Records et
    prédictions n'existent qu'au présent : omis pour une date passée, plutôt
    que de prêter au coureur d'alors des chronos qu'il n'avait pas encore.
    """
    today = asof or date.today()
    is_today = today >= date.today()
    lines = ["=== Forme & récupération ===" if is_today else
             f"=== Forme & récupération au {today.strftime('%d/%m/%Y')} ==="]

    load = _load_at(activities_df, today)
    if load:
        ctl, atl, tsb = load
        lines.append(
            f"- Charge d'entraînement : CTL {ctl} (forme 42 j), "
            f"ATL {atl} (fatigue 7 j), TSB {tsb:+} (fraîcheur)"
        )

    # Même lecture que les pages (parse_recovery) : « NONE » = pas de statut.
    recovery = parse_recovery(client.get_hrv(today.isoformat()), None)
    hrv_summary = recovery["hrv_summary"]
    if recovery["hrv_last"]:
        baseline = hrv_summary.get("baseline") or {}
        status = hrv_label(recovery["hrv_status"]) or "pas encore de référence"
        lines.append(
            f"- HRV cette nuit : {recovery['hrv_last']} ms "
            f"(baseline {baseline.get('balancedLow', '?')}–{baseline.get('balancedUpper', '?')} ms, "
            f"statut {status})"
        )

    sleep_hours, sleep_scores = [], []
    for i in range(7):
        raw = client.get_sleep((today - timedelta(days=i)).isoformat()) or {}
        dto = (raw.get("dailySleepDTO") or {}) if isinstance(raw, dict) else {}
        if dto.get("sleepTimeSeconds"):
            sleep_hours.append(dto["sleepTimeSeconds"] / 3600)
            score = ((dto.get("sleepScores") or {}).get("overall") or {}).get("value")
            if score is not None:
                sleep_scores.append(score)
    if sleep_hours:
        score_txt = (
            f", score moyen {sum(sleep_scores) / len(sleep_scores):.0f}"
            if sleep_scores else ""
        )
        lines.append(
            f"- Sommeil (7 derniers jours) : "
            f"{sum(sleep_hours) / len(sleep_hours):.1f} h/nuit en moyenne{score_txt}"
        )

    daily_raw = client.get_daily_stats(today.isoformat())
    daily = daily_raw[0] if isinstance(daily_raw, list) and daily_raw else (daily_raw or {})
    if isinstance(daily, dict) and daily.get("restingHeartRate"):
        lines.append(f"- FC de repos : {daily['restingHeartRate']} bpm")

    if not is_today:
        return "\n".join(lines)

    records = [
        r for r in parse_personal_records(client.get_personal_records())
        if r["group"] == "course"
    ]
    if records:
        lines.append(
            "- Records personnels course : "
            + " · ".join(f"{r['label']} {r['value_str']}" for r in records)
        )

    preds = client.get_race_predictions()
    pred_parts = [
        f"{label} {fmt_race_time(preds[key])}"
        for label, _km, key in RACE_TARGETS if preds.get(key)
    ]
    if pred_parts:
        lines.append("- Prédictions Garmin actuelles : " + " · ".join(pred_parts))

    return "\n".join(lines)


# Séance du plan Objectif → axe nutritionnel (clés de coach_logic.NUTRITION_FOCUS).
_GOAL_NUTRITION_KEY = {"tempo": "tempo", "interval": "tempo", "race_pace": "tempo",
                       "race": "tempo", "long": "sortie_longue"}


def _format_nutrition_context(client, coach, goal_sessions=None, days: int = 4) -> str:
    """
    Contexte alimentaire : séances des prochains jours et dépense énergétique
    récente. Même priorité que la séance du jour (todays_session) : plan Garmin
    Run Coach, sinon plan Objectif validé du dashboard, sinon la seule dépense.
    """
    today = date.today()
    lines = ["=== Séances des prochains jours ==="]

    if coach and coach.get("week"):
        upcoming = [t for t in coach["week"] if t["date"] < today + timedelta(days=days)]
        for task in upcoming:
            when = "aujourd'hui" if task["date"] == today else weekday_fr(task["date"])
            if task["rest_day"]:
                lines.append(f"- {when} {task['date'].strftime('%d/%m')} : repos")
                continue
            sport = "course" if task["sport"] == "running" else "renforcement"
            target = f" ({target_label(task)})" if task["sport"] == "running" else ""
            lines.append(
                f"- {when} {task['date'].strftime('%d/%m')} : {task['name']} — "
                f"{sport}, {task['duration_min']} min{target}"
            )
        next_run = coach.get("next_run")
        lines.append(f"\nÀ retenir pour la prochaine course : {nutrition_focus(next_run)}.")
        if coach.get("phase") and coach.get("days_to_event") is not None:
            lines.append(
                f"Contexte : plan « {coach['plan']['name']} », phase "
                f"{coach['phase']['label']}, objectif dans {coach['days_to_event']} jours."
            )
    elif goal_sessions:
        horizon = (today + timedelta(days=days)).isoformat()
        upcoming = [s for s in goal_sessions if today.isoformat() <= s["date"] < horizon]
        for s in upcoming:
            day = date.fromisoformat(s["date"])
            when = "aujourd'hui" if day == today else weekday_fr(day)
            if s["kind"] == "strength":
                what = f"{s['title']} — renforcement, {s.get('duration_min', 0):.0f} min"
            else:
                target = f" ({s['target']})" if s.get("target") else ""
                what = f"{s['title']} — course, {s.get('distance_km', 0):g} km{target}"
            lines.append(f"- {when} {day.strftime('%d/%m')} : {what}")
        if not upcoming:
            lines.append("- Repos : aucune séance du plan ces prochains jours.")
        next_run = next((s for s in goal_sessions if s["date"] >= today.isoformat()
                         and s["kind"] != "strength"), None)
        focus_task = ({"session_key": _GOAL_NUTRITION_KEY.get(next_run["kind"], "endurance")}
                      if next_run else None)
        lines.append(f"\nÀ retenir pour la prochaine course : {nutrition_focus(focus_task)}.")
        lines.append("Contexte : plan Objectif validé dans le dashboard (course + renforcement).")
    else:
        lines.append("- Aucun plan d'entraînement actif.")

    # Dépense énergétique : moyenne sur 7 jours, cache disque partagé avec les
    # autres pages (aucun appel réseau supplémentaire en pratique).
    totals, actives = [], []
    for i in range(7):
        raw = client.get_daily_stats((today - timedelta(days=i)).isoformat())
        daily = raw[0] if isinstance(raw, list) and raw else (raw or {})
        if isinstance(daily, dict) and daily.get("totalKilocalories"):
            totals.append(daily["totalKilocalories"])
            actives.append(daily.get("activeKilocalories") or 0)
    if totals:
        lines.append(
            f"\n=== Dépense énergétique ===\n"
            f"- Moyenne sur 7 jours : {sum(totals) / len(totals):.0f} kcal/jour au "
            f"total, dont {sum(actives) / len(actives):.0f} kcal d'activité"
        )

    return "\n".join(lines)


def _format_session_slot(slot: datetime, now: datetime) -> str:
    """Créneau de la prochaine séance, en clair (jour, heure, délai)."""
    delta_h = (slot - now).total_seconds() / 3600
    when = ("aujourd'hui" if slot.date() == now.date() else
            "demain" if slot.date() == now.date() + timedelta(days=1) else weekday_fr(slot.date()))
    delay = (f"dans {delta_h:.0f} h" if 0 <= delta_h < 48 else
             f"dans {(slot.date() - now.date()).days} jours" if delta_h >= 48 else "créneau déjà passé")
    return ("=== Prochaine séance prévue ===\n"
            f"- {when} {slot.strftime('%d/%m/%Y')} à {slot.strftime('%H:%M').replace(':', ' h ')} ({delay})")


def _planned_on(day: date, coach, goal_sessions) -> tuple[str, str | None]:
    """
    (bloc de contexte, "session" / "rest" / None) : ce que le plan prévoit ce jour-là —
    même priorité que la séance du jour : Run Coach, sinon plan Objectif validé.
    Sans ce bloc, l'IA choisissait une séance différente de celle de la montre ;
    le booléen évite de lui dire « ne la remplace pas » un jour où rien n'est prévu.
    """
    lines = ["=== Séance prévue ce jour-là par le plan ==="]
    if coach and coach.get("tasks") is not None:
        tasks = [t for t in coach["tasks"] if t["date"] == day]
        known = max((t["date"] for t in coach["tasks"]), default=None)
        for t in tasks:
            if t["rest_day"]:
                lines.append("- Repos (plan Garmin Run Coach)")
            else:
                target = f" ({target_label(t)})" if t["sport"] == "running" else ""
                lines.append(f"- {t['name']} — {t['duration_min']} min{target} (plan Garmin Run Coach)")
        if not tasks:
            lines.append("- Au-delà de l'horizon connu du plan Garmin Run Coach (≈ 1 semaine) : "
                         "la séance n'est pas encore fixée." if known is not None and day > known else
                         "- Rien de prévu ce jour-là par le plan Garmin Run Coach.")
        kind = ("session" if any(not t["rest_day"] for t in tasks) else "rest") if tasks else None
        return "\n".join(lines), kind
    if goal_sessions:
        day_s = [x for x in goal_sessions if x["date"] == day.isoformat()]
        for x in day_s:
            what = (f"renforcement, {x.get('duration_min', 0):.0f} min" if x["kind"] == "strength" else
                    f"course, {x.get('distance_km', 0):g} km" + (f" ({x['target']})" if x.get("target") else ""))
            lines.append(f"- {x['title']} — {what} (plan Objectif validé)")
        if not day_s:
            lines.append("- Rien de prévu ce jour-là par le plan Objectif.")
        return "\n".join(lines), "session" if day_s else None
    return "\n".join(lines + ["- Aucun plan actif : séance à choisir librement."]), None


def _slot_request(slot: datetime, nutrition: bool, planned: str | None = None) -> str:
    """Question ajoutée quand le coureur précise son créneau."""
    at = f"{weekday_fr(slot.date())} {slot.strftime('%d/%m')} à {slot.strftime('%H:%M').replace(':', ' h ')}"
    if nutrition:
        return (f"\n\nMa prochaine séance a lieu {at} : dis-moi aussi quoi manger et à quelle "
                "heure avant (repas, en-cas), puis quoi manger après pour récupérer.")
    if planned == "rest":
        return (f"\n6. Je voudrais courir {at}, mais mon plan prévoit du repos ce jour-là (voir "
                "ci-dessus) : est-ce raisonnable compte tenu de ma fraîcheur, et si oui, quoi faire "
                "de très léger sans compromettre les séances suivantes ?")
    if planned == "session":
        return (f"\n6. Ma prochaine séance a lieu {at} et mon plan la prévoit (voir ci-dessus) : ne la "
                "remplace pas, dis-moi comment l'aborder compte tenu de ma fraîcheur à ce moment-là, "
                "et comment m'y préparer (sommeil, échauffement, repas selon l'heure).")
    return (f"\n6. Ma prochaine séance a lieu {at} : que me conseilles-tu d'y faire, compte tenu "
            "de ma fraîcheur à ce moment-là, et comment m'y préparer (sommeil, échauffement, "
            "repas selon l'heure) ?")


def _build_prompt(context: str, question: str, system_prompt: str = _SYSTEM_PROMPT) -> str:
    return (
        f"{system_prompt}\n\n"
        f"---\n\n"
        f"{context}\n\n"
        f"---\n\n"
        f"{question}"
    )




# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("Coach IA")
st.caption(
    "Copiez le prompt généré et collez-le dans **Claude**, **ChatGPT**, **Gemini** "
    "ou n'importe quel autre LLM. Le contexte inclut tes sorties, ta charge "
    "d'entraînement, ton HRV, ton sommeil, tes records et tes prédictions Garmin."
)

df, error = cached_load_activities(_athlete_id)

# Sidebar
with st.sidebar:
    st.markdown("## ⚙️ Paramètres")
    nb_activites = st.slider(
        "Activités à inclure",
        min_value=5, max_value=50, value=10,
        help="Nombre de sorties récentes incluses dans le prompt",
    )
    _diet_notes = st.text_area(
        "Contraintes alimentaires",
        placeholder="Ex. : pas de porc, peu de temps le soir, je n'aime pas le poisson",
        help="Ajouté au prompt « Idées de repas » — goûts, allergies, temps "
             "disponible, ce qu'il reste dans le frigo…",
        height=90,
    )

if error:
    st.error(f"**Erreur Garmin :** {error}")
    st.stop()
if df.empty:
    st.warning("Aucune activité disponible.")
    st.stop()

# ---------------------------------------------------------------------------
# Dates : situation rejouée (analyse) et créneau de la prochaine séance
# ---------------------------------------------------------------------------
_today = date.today()
_first_day = pd.to_datetime(df["startTimeLocal"]).min().date()
for _key, _default in (("ai_asof", _today), ("ai_slot_on", False),
                       ("ai_slot_date", _today + timedelta(days=1)), ("ai_slot_time", time(18, 0))):
    if _key not in st.session_state:
        st.session_state[_key] = _default
# Une date gardée en session peut sortir des bornes (lendemain de minuit) :
# st.date_input refuserait alors la valeur.
st.session_state["ai_asof"] = min(max(st.session_state["ai_asof"], _first_day), _today)
st.session_state["ai_slot_date"] = max(st.session_state["ai_slot_date"], _today)

prompt_kind = st.radio(
    "Type de prompt",
    options=["🏃 Analyse d'entraînement", "🍽️ Idées de repas"],
    horizontal=True,
    key="ai_coach_prompt_kind",
    label_visibility="collapsed",
)
_is_nutrition = prompt_kind.endswith("Idées de repas")

with st.container(key="card-ai-dates"):
    d1, d2, d3, d4 = st.columns([1.2, 1.1, 1, 0.8], gap="medium", vertical_alignment="bottom")
    with d1:
        asof = st.date_input("Situation au", key="ai_asof", min_value=_first_day, max_value=_today,
                             format="DD/MM/YYYY", disabled=_is_nutrition,
                             help="Rejoue ton contexte à une date passée : sorties jusqu'à ce jour, "
                                  "charge, HRV et sommeil d'alors. Les idées de repas partent toujours "
                                  "d'aujourd'hui.")
    with d2:
        slot_on = st.toggle("Prochaine séance", key="ai_slot_on",
                            help="Donne à l'IA le jour et l'heure de ta prochaine sortie : elle adapte "
                                 "la séance à ta fraîcheur d'alors, et les repas à l'horaire.")
    with d3:
        slot_day = st.date_input("Le", key="ai_slot_date", min_value=_today, format="DD/MM/YYYY",
                                 disabled=not slot_on)
    with d4:
        slot_time = st.time_input("À", key="ai_slot_time", step=timedelta(minutes=15),
                                  disabled=not slot_on)
if _is_nutrition:
    asof = _today
_slot = datetime.combine(slot_day, slot_time) if slot_on else None

# Métriques rapides
running_only = _until(df[df["activityType"] == "running"], asof)
if not running_only.empty:
    recent = running_only.sort_values("startTimeLocal", ascending=False).head(10)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sorties analysées", min(nb_activites, len(running_only)))
    c2.metric("Volume (10 dernières)", f"{recent['distance_km'].sum():.1f} km")
    pace_vals = recent[recent["avgPace_sec"] > 0]["avgPace_sec"]
    c3.metric("Allure moyenne", seconds_to_pace_str(pace_vals.mean()) if not pace_vals.empty else "—")
    hr_vals = recent["avgHR"].dropna()
    c4.metric("FC moyenne", f"{hr_vals.mean():.0f} bpm" if not hr_vals.empty else "—")

st.divider()

if _is_nutrition:
    with st.spinner("Préparation du contexte alimentaire…"):
        _coach = cached_coach_context(_athlete_id)
        context = _format_nutrition_context(get_garmin_client(), _coach,
                                        goal_store.validated_sessions(_athlete_id))
    if _diet_notes.strip():
        context += f"\n\n=== Mes contraintes ===\n{_diet_notes.strip()}"
    system_prompt, request = _NUTRITION_SYSTEM_PROMPT, _NUTRITION_REQUEST
else:
    with st.spinner("Préparation du contexte forme & récupération…"):
        # Historique complet : le TSB du prompt doit être celui du dashboard.
        _forme_context = _format_forme_summary(get_garmin_client(), df, asof)
    context = _format_activities_summary(df, n=nb_activites, asof=asof) + "\n\n" + _forme_context
    system_prompt, request = _SYSTEM_PROMPT, _ANALYSIS_REQUEST
    if asof < _today:
        request = request.replace("Analyse ces données",
                                  f"Analyse ma situation au {asof.strftime('%d/%m/%Y')} (je te la "
                                  "rejoue a posteriori)", 1)
if _slot:
    context += "\n\n" + _format_session_slot(_slot, datetime.now())
    _has_plan = False
    if not _is_nutrition:           # (le prompt repas liste déjà les séances du plan)
        _coach_ctx = cached_coach_context(_athlete_id)
        _goal = goal_store.validated_sessions(_athlete_id)
        _block, _has_plan = _planned_on(_slot.date(), _coach_ctx, _goal)
        context += "\n\n" + _block
    request += _slot_request(_slot, _is_nutrition, planned=_has_plan)

with st.expander("📋 Données incluses dans le prompt", expanded=False):
    # Les noms d'activités Garmin sont saisis par l'utilisateur : échappés ET
    # rendus ligne par ligne avec <br>. Une ligne vide dans le bloc HTML le
    # refermerait et la suite serait lue comme du Markdown (image distante…).
    _lines = "<br>".join(html.escape(line) or "&nbsp;" for line in context.splitlines())
    st.markdown(f'<div class="summary-block">{_lines}</div>', unsafe_allow_html=True)

st.divider()

# ---------------------------------------------------------------------------
# Prompt d'analyse complet — copier-coller dans n'importe quel LLM
# ---------------------------------------------------------------------------
st.subheader("Prompt prêt à copier")
if _is_nutrition:
    st.info(
        "Ce prompt part de tes séances des prochains jours et de ta dépense "
        "récente pour demander des idées de plats faciles. Les contraintes "
        "saisies dans la barre latérale y sont ajoutées. Ce sont des idées de "
        "cuisine, pas un plan diététique : pour un suivi nutritionnel "
        "individualisé, passe par un professionnel."
    )
else:
    st.info(
        "Ce prompt embarque ton contexte (rôle de coach + tes données récentes) et "
        "une demande d'analyse par défaut. Une fois collé dans ton LLM, tu peux "
        "remplacer la question par la tienne pour creuser un point spécifique."
        + (f" Situation rejouée au {asof.strftime('%d/%m/%Y')}." if asof < _today else "")
    )

prompt = _build_prompt(context, request, system_prompt)
st.code(prompt, language="markdown")
st.download_button(
    "⬇️ Télécharger le prompt (.txt)",
    data=prompt,
    file_name="coach_repas.txt" if _is_nutrition else "coach_analyse.txt",
    mime="text/plain",
)

render_garmin_attribution()
