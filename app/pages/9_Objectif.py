"""
Page Objectif — une course datée → un plan course + renforcement, expliqué
séance par séance, validé puis poussé (sur confirmation) dans le calendrier
Garmin. Le plan est produit par `race_plan_logic` (déterministe) ; Garmin
Run Coach, s'il est actif, reste la référence de la montre.
"""

import os
import time
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import chart_theme as ct
import goal_store
from physio_logic import decoupling_candidates, decoupling_history
from physio_ui import _load_candidate_streams
from formatting import md_escape, seconds_to_pace_str
from progression_logic import fmt_race_time
from race_plan_logic import (
    DISTANCES,
    PHASE_LABELS,
    SOURCES,
    athlete_baseline,
    build_race_plan,
    parse_race_time,
    plan_brief,
    plan_sessions,
    predictions_by_km,
)
from ui_helpers import (
    athlete_id_is_reliable,
    cache_nonce,
    cached_load_activities,
    get_athlete_id,
    get_garmin_client,
    render_garmin_attribution,
    require_login,
)
from ui_mode import decoupling_params, explain, lock_params
from ui_theme import html_block, session_card
from workout_export import (
    TAG_PREFIX,
    UNVERIFIED_FINGERPRINT,
    coach_state,
    fingerprint,
    future_pushes,
    is_dashboard_workout,
    plan_id_of,
    push_gate,
    pushable_sessions,
    reconciled_fingerprint,
    session_key,
    stale_pushes,
    workout_payload,
    workout_tag,
)

st.set_page_config(page_title="Objectif — Running Dashboard", page_icon="🎯", layout="wide")
require_login()
_athlete_id = get_athlete_id()
if not athlete_id_is_reliable():
    # Garmin n'a pas donné le profileId : l'id de repli rangerait l'objectif, le
    # plan validé et le journal des séances dans un autre dossier que celui relu
    # ensuite (objectif « perdu », retrait du calendrier sans mémoire).
    st.title("Objectif de course")
    st.warning("Garmin n'a pas confirmé l'identifiant de ton compte (réponse lente ou refusée). "
               "Ton objectif et ton plan ne sont ni lus ni modifiés tant que ce n'est pas fait, "
               "pour ne pas les ranger au mauvais endroit : réessaie dans une minute.",
               icon=":material/sync_problem:")
    st.stop()
TODAY = date.today()
WRITE_ENABLED = os.getenv("GARMIN_WRITE_ENABLED", "").lower() in ("1", "true", "yes")

WEEKDAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
KIND_ICONS = {"easy": "🟦", "long": "🟩", "shakeout": "🟦", "strides": "⚡", "tempo": "🔶",
              "interval": "🔺", "race_pace": "🎯", "race": "🏁", "strength": "🏋️"}
# Couleur = identité de la phase, slots catégoriels dans l'ordre des phases.
PHASE_COLORS = dict(zip(["MAINTENANCE", "BASE", "BUILD", "PEAK", "TAPER"], ct.CAT))
# État Run Coach affiché sur la page validée : relu au plus une fois par minute
# et par onglet (cocher une case ne coûte pas un appel Garmin) ; le clic sur
# « Envoyer » le relit toujours.
COACH_STATE_TTL_S = 60


# ---------------------------------------------------------------------------
# Données
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_predictions(athlete_id: int, nonce: int) -> dict:
    return get_garmin_client().get_race_predictions() or {}


def current_coach_state(strict: bool = True) -> tuple[str, str | None]:
    """
    État du plan Garmin Run Coach, sans confondre « aucun » et « inconnu ».
    `strict=True` = lecture fraîche (garde d'écriture, cf. `fresh_coach_state`) ;
    la bannière d'information se contente du cache.
    """
    try:
        plans = get_garmin_client().get_training_plans(strict=strict)
    except Exception:
        return coach_state(None, error=RuntimeError("indisponible")), None
    from coach_logic import active_plan
    plan = active_plan(plans)
    return coach_state(plans), (plan or {}).get("name")


def fresh_coach_state() -> str:
    """État Run Coach relu auprès de Garmin, mémorisé pour `displayed_coach_state`."""
    state, _ = current_coach_state()
    st.session_state["objectif_coach_state"] = (time.monotonic(), state)
    return state


def displayed_coach_state() -> str:
    """État qui décide d'afficher le formulaire d'envoi : lecture fraîche de moins d'une minute."""
    memo = st.session_state.get("objectif_coach_state")
    if memo and time.monotonic() - memo[0] < COACH_STATE_TTL_S:
        return memo[1]
    return fresh_coach_state()


def recent_long_run_drift(df: pd.DataFrame) -> float | None:
    """Dérive de la dernière sortie longue mesurable (≥ 75 min), pour personnaliser."""
    long_runs = [c for c in decoupling_candidates(df) if c["duration_min"] >= 75][:4]
    if not long_runs:
        return None
    streams, _ = _load_candidate_streams(_athlete_id, tuple(int(c["activityId"]) for c in long_runs))
    hist = decoupling_history([(c, streams.get(int(c["activityId"]))) for c in long_runs],
                              lock_params=lock_params(), **decoupling_params())
    return float(hist["decoupling_pct"].iloc[-1]) if not hist.empty else None


# ---------------------------------------------------------------------------
# En-tête
# ---------------------------------------------------------------------------
st.title("Objectif de course")
st.caption("Choisis ta course : le plan combine course et renforcement, et chaque séance "
           "dit pourquoi elle est là.")

# Messages d'un envoi/retrait précédent : survivent au st.rerun() qui suit.
for level, text in st.session_state.pop("objectif_flash", []):
    getattr(st, level)(text)


def flash(level: str, text: str) -> None:
    st.session_state.setdefault("objectif_flash", []).append((level, text))

df, error = cached_load_activities(_athlete_id)
if error:
    st.error(f"Erreur Garmin : {error}")
    st.stop()

doc = goal_store.load(_athlete_id)
if doc.get("recovered_from_corrupt"):
    st.warning("Le fichier de ton objectif est illisible : il sera mis de côté "
               "(goal.json.bad-…) au prochain enregistrement. Le journal des séances envoyées "
               "est perdu : utilise « Rechercher les séances du dashboard » plus bas pour "
               "retrouver celles qui sont encore dans ton calendrier Garmin.")
goal, prefs = doc.get("goal") or {}, doc.get("prefs") or {}

# Valeurs initiales des widgets depuis l'objectif enregistré (key= sans value=).
# La date enregistrée peut être passée (le lendemain de la course) : le
# widget refuserait une valeur sous `min_value` et la page planterait.
_saved_date = date.fromisoformat(goal["race_date"]) if goal.get("race_date") else None
_defaults = {
    "goal_distance": goal.get("distance", "Semi-marathon"),
    "goal_date": max(_saved_date, TODAY + timedelta(days=1)) if _saved_date
                 else TODAY + timedelta(weeks=12),
    "goal_target": goal.get("target_text", ""),
    "goal_runs": int(prefs.get("runs_per_week", 4)),
    "goal_long_day": WEEKDAYS[int(prefs.get("long_run_weekday", 6))],
    "goal_strength": bool(prefs.get("include_strength", True)),
}
for key, value in _defaults.items():
    st.session_state.setdefault(key, value)
# Un onglet resté ouvert jusqu'au jour J garde l'ancienne date : la recaler à
# chaque run (setdefault ne réécrit pas une valeur déjà présente).
if st.session_state["goal_date"] < TODAY + timedelta(days=1):
    st.session_state["goal_date"] = TODAY + timedelta(days=1)

# Une fois l'objectif posé, le plan passe devant : le formulaire se replie.
with st.expander("✏️ Modifier l'objectif" if goal else "🎯 Ta course", expanded=not goal):
    with st.form("goal_form"):
        c1, c2, c3 = st.columns([2, 2, 2])
        c1.selectbox("Distance", list(DISTANCES), key="goal_distance")
        c2.date_input("Date de la course", key="goal_date", min_value=TODAY + timedelta(days=1),
                      format="DD/MM/YYYY")
        c3.text_input("Temps visé (optionnel)", key="goal_target", placeholder="ex. 1:55:00")
        c4, c5, c6 = st.columns([2, 2, 2])
        c4.slider("Sorties par semaine", 3, 6, step=1, key="goal_runs")
        c5.selectbox("Jour de la sortie longue", WEEKDAYS, key="goal_long_day")
        c6.checkbox("Inclure le renforcement musculaire", key="goal_strength")
        submitted = st.form_submit_button("💾 Enregistrer l'objectif", width="stretch")

if submitted:
    target_text = st.session_state["goal_target"].strip()
    target_s = parse_race_time(target_text, st.session_state["goal_distance"])
    pace = target_s / DISTANCES[st.session_state["goal_distance"]] if target_s else None
    if target_text and target_s is None:
        st.error("Temps visé illisible : utilise h:mm:ss ou mm:ss (ex. 1:55:00).")
    elif pace is not None and not (150 <= pace <= 720):
        st.error(f"Temps visé invraisemblable pour un {st.session_state['goal_distance']} "
                 f"(allure {int(pace // 60)}:{int(pace % 60):02d}/km). Vérifie la saisie "
                 "(h:mm:ss).")
    else:
        goal = {"distance": st.session_state["goal_distance"],
                "race_date": st.session_state["goal_date"].isoformat(),
                "target_text": target_text}
        prefs = {"runs_per_week": st.session_state["goal_runs"],
                 "long_run_weekday": WEEKDAYS.index(st.session_state["goal_long_day"]),
                 "include_strength": st.session_state["goal_strength"]}
        doc = goal_store.save_goal(_athlete_id, goal, prefs)
        st.success("Objectif enregistré.")

if not goal:
    st.info("Renseigne ta course ci-dessus pour générer ton plan.")
    render_garmin_attribution()
    st.stop()

if date.fromisoformat(goal["race_date"]) <= TODAY:
    when = "aujourd'hui" if date.fromisoformat(goal["race_date"]) == TODAY else "passée"
    st.info(f"Ta course du {date.fromisoformat(goal['race_date']).strftime('%d/%m/%Y')} est "
            f"{when}. Bravo ! Enregistre un nouvel objectif ci-dessus, ou repars de zéro.")
    if st.button("🆕 Nouvel objectif"):
        goal_store.clear_goal(_athlete_id)
        st.rerun()
    render_garmin_attribution()
    st.stop()

# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
race_date = date.fromisoformat(goal["race_date"])
baseline = athlete_baseline(df, TODAY, predictions_by_km(load_predictions(_athlete_id, cache_nonce())))
context = {"long_run_decoupling_pct": recent_long_run_drift(df)}
plan = build_race_plan(
    race_date, goal["distance"], baseline, TODAY,
    runs_per_week=prefs.get("runs_per_week", 4),
    long_run_weekday=prefs.get("long_run_weekday", 6),
    target_time_s=parse_race_time(goal.get("target_text"), goal["distance"]),
    include_strength=prefs.get("include_strength", True),
    context=context,
)

# Run Coach actif : la montre suit CE plan-là. Le dire dès l'ouverture, avant
# même la validation (contre-validation : bannière invisible jusqu'ici).
_, coach_name = current_coach_state(strict=False)
if coach_name:
    st.info(f"Plan Garmin Run Coach actif : **{coach_name}**. Ta montre suit ce plan-là : "
            "celui-ci reste consultable, mais ne pourra pas être envoyé tant que Run Coach "
            "est actif.")

plan_id = plan_id_of(goal, prefs)
validated = doc.get("validated") or {}
frozen = validated.get("plan") if validated.get("plan_id") == plan_id else None
live_plan = plan
# Une fois validé, c'est le plan FIGÉ qui s'affiche et s'envoie : recalculé
# à chaque visite, il glisserait (lundi de départ, volume du jour) et le
# calendrier recevrait des séances différentes de celles validées.
plan = frozen or live_plan
sessions = plan_sessions(plan)
summary = plan["summary"]

st.markdown(f"#### {goal['distance']} le {race_date.strftime('%d/%m/%Y')} — "
            f"dans {(race_date - TODAY).days} jours")
k1, k2, k3, k4 = st.container(key="card-goal-kpi").columns(4)
k1.metric("📆 Semaines", summary["n_weeks"])
k2.metric("📈 Volume de pointe", f"{summary['peak_km']:.0f} km/sem",
          delta=f"départ {summary['start_km']:.0f} km", delta_color="off", delta_arrow="off")
_SOURCE_TEXT = {
    "race": "D'après ta dernière compétition (formule de Riegel).",
    "prediction": "D'après les prédictions Garmin, majorées de 3 % (elles sont réputées optimistes).",
    "training": "D'après tes entraînements récents : probablement trop prudent.",
    "default": "Valeur par défaut, faute de données.",
}
k3.metric("⏱️ Temps estimé", fmt_race_time(summary["predicted_time_s"]),
          help=_SOURCE_TEXT.get(summary.get("pace_source"), ""))
k4.metric("🎯 Allure course", seconds_to_pace_str(summary['race_pace_sec']),
          delta=("objectif " + fmt_race_time(summary["target_time_s"])) if summary.get("target_time_s") else None,
          delta_color="off", delta_arrow="off")
for w in plan["warnings"]:
    st.info(w)

# Prochaine séance du plan : même carte que la séance du jour de l'Accueil
_next = next((x for x in sessions if date.fromisoformat(x["date"]) >= TODAY), None)
if _next:
    _day = date.fromisoformat(_next["date"])
    _is_run = _next["kind"] not in ("strength",)
    with st.container(key="card-goal-next"):
        html_block(session_card(
            kicker=f"Prochaine séance · {PHASE_LABELS.get(_next['phase'], '')}",
            number=f"{_next['distance_km']:g}" if _is_run else str(_next["duration_min"]),
            unit="km" if _is_run else "min", title=_next["title"], target=_next.get("target", ""),
            when=("Aujourd'hui" if _day == TODAY else f"{WEEKDAYS[_day.weekday()]} {_day.strftime('%d/%m')}"),
            why=_next.get("why", ""), tag="Renfo" if not _is_run else "",
            aria_label="Prochaine séance du plan"))

# Volume hebdomadaire par phase
fig = go.Figure()
weeks_df = pd.DataFrame([{"start": w["start"], "volume": w["volume_km"], "phase": w["phase"],
                          "label": w["phase_label"], "week": w["week"]} for w in plan["weeks"]])
for phase in PHASE_COLORS:
    part = weeks_df[weeks_df["phase"] == phase]
    if part.empty:
        continue
    fig.add_trace(go.Bar(
        x=part["start"], y=part["volume"], name=PHASE_LABELS[phase],
        marker=dict(color=PHASE_COLORS[phase], line=dict(color=ct.SURFACE, width=2)),
        customdata=part[["week"]].to_numpy(),
        hovertemplate="Semaine %{customdata[0]} · %{y:.0f} km<extra>" + PHASE_LABELS[phase] + "</extra>",
    ))
_race_week = next((w["start"] for w in plan["weeks"]
                   if any(x["kind"] == "race" for x in w["sessions"])), None)
if _race_week:
    fig.add_annotation(x=_race_week, y=0, yshift=-2, text="🏁", showarrow=False,
                       yanchor="top", font=dict(size=16))
fig.update_layout(height=240, barmode="overlay", yaxis=dict(title="km / semaine"),
                  legend=dict(orientation="h", y=1.15), margin=dict(l=0, r=0, t=30, b=0))
st.plotly_chart(fig)

explain("seuil")
with st.expander("🎚️ Tes allures d'entraînement"):
    names = {"easy": "Footing", "long": "Sortie longue", "tempo": "Seuil",
             "interval": "Fractionné VMA", "strides": "Lignes droites"}
    st.table(pd.DataFrame([{"Allure": names[z], "min/km": f"{lo} – {hi}"}
                           for z, (lo, hi) in summary["paces"].items()]))

# ---------------------------------------------------------------------------
# Semaines
# ---------------------------------------------------------------------------
st.subheader("📅 Le plan, semaine par semaine")
current_week = next((w["week"] for w in plan["weeks"]
                     if date.fromisoformat(w["start"]) <= TODAY < date.fromisoformat(w["start"]) + timedelta(days=7)),
                    plan["weeks"][0]["week"])
for w in plan["weeks"]:
    start = date.fromisoformat(w["start"])
    title = (f"S{w['week']} · {w['phase_label']} · {w['volume_km']:.0f} km · "
             f"du {start.strftime('%d/%m')}")
    with st.expander(title, expanded=w["week"] in (current_week, current_week + 1)):
        if not w["sessions"]:
            st.caption("Semaine écoulée.")
        for s in w["sessions"]:
            day = date.fromisoformat(s["date"])
            head = f"{KIND_ICONS.get(s['kind'], '•')} **{WEEKDAYS[day.weekday()]} {day.strftime('%d/%m')} — {s['title']}**"
            detail = s.get("target", "")
            if s["kind"] not in ("strength",):
                detail += f" · {s['distance_km']:.1f} km · ~{s['duration_min']} min"
            else:
                detail += f" · ~{s['duration_min']} min"
            st.markdown(f"{head}  \n{detail}")
            st.caption(f"Pourquoi : {s['why']}")
            if s.get("explain"):
                with st.popover("En savoir plus"):
                    st.write(s["explain"])
                    for key in s.get("sources", []):
                        text, level = SOURCES[key]
                        st.caption(f"📚 {text} (preuve : {level})")

# ---------------------------------------------------------------------------
# Validation, explications, push
# ---------------------------------------------------------------------------
st.divider()

if not frozen:
    if validated:
        st.warning("L'objectif ou les préférences ont changé depuis ta dernière validation : "
                   "voici le nouveau plan.")
    if st.button("✅ Valider ce plan", type="primary"):
        goal_store.validate_plan(_athlete_id, plan_id, live_plan)
        st.rerun()
    render_garmin_attribution()
    st.stop()

from datetime import datetime as _dt  # noqa: E402
st.success(f"Plan validé le {_dt.fromtimestamp(validated['at']).strftime('%d/%m/%Y')} — "
           "c'est ce plan-là qui est affiché et envoyé.")
if st.button("🔄 Recalculer avec ma forme du jour"):
    goal_store.validate_plan(_athlete_id, plan_id, live_plan)
    st.rerun()

st.subheader("💡 Pourquoi ce plan est construit ainsi")
st.markdown(
    "- **Progressivité** : le volume monte d'au plus 10 % par semaine, avec une semaine "
    "allégée toutes les quatre pour assimiler la charge.\n"
    "- **Phases** : la base construit l'endurance, le développement ajoute le seuil et le "
    "fractionné, la phase spécifique travaille l'allure de course, l'affûtage "
    "réduit le volume sans perdre l'intensité pour arriver frais.\n"
    f"- **Renforcement** : {'inclus' if prefs.get('include_strength', True) else 'désactivé'} — "
    "jamais la veille d'une séance clé, arrêté 9 jours avant la course, et toujours après "
    "la course si c'est le même jour.\n"
    "- **Allures** : calculées sur ta forme récente (course, prédictions Garmin), pas sur "
    "tes meilleures années — ou sur ton temps visé s'il est réaliste (moins de 5 % plus "
    "rapide que ta forme estimée)."
)
st.caption("⚠️ Chez le coureur, l'effet anti-blessure du renfo n'est net que si les "
           "mouvements sont bien exécutés (Wu et al., 2024) : fais-toi montrer la technique.")
e1, e2 = st.columns(2)
with e1:
    explain("strength")
with e2:
    explain("taper")

st.subheader("📲 Envoyer dans ton calendrier Garmin")
allowed, reason = push_gate(WRITE_ENABLED, displayed_coach_state())
pushed = goal_store.load(_athlete_id).get("pushed") or {}
stale = stale_pushes(pushed, plan_id, TODAY.isoformat(), sessions)
todo = pushable_sessions(sessions, TODAY.isoformat(), pushed)


def remove_entries(entries: dict) -> None:
    """Retire des séances envoyées ; s'arrête (et le dit) à la première erreur."""
    client = get_garmin_client()
    removed = 0
    for key, entry in sorted(entries.items()):
        # Étiquette complète attendue (plan + jour + créneau) : un journal abîmé
        # ne peut pas faire supprimer une autre séance, même du même plan.
        tag = (workout_tag(entry["plan_id"], entry)
               if entry.get("plan_id") and entry.get("date") and entry.get("kind") else TAG_PREFIX)
        try:
            ok = client.remove_workout(entry["workout_id"], entry.get("schedule_id"),
                                       required_tag=tag)
        except Exception as e:
            flash("error", f"Erreur Garmin en retirant « {md_escape(entry.get('name', key))} » : "
                           f"{md_escape(e)}. "
                           f"{removed} séance(s) retirée(s) avant l'erreur.")
            return
        goal_store.forget_push(_athlete_id, key)
        if not ok:
            flash("warning", f"« {md_escape(entry.get('name', key))} » n'a pas été retirée : elle a été "
                             "renommée dans Garmin, ce n'est plus une séance du dashboard.")
            continue
        removed += 1
    flash("success", f"{removed} séance(s) retirée(s) de ton calendrier.")


if stale:
    st.warning(f"{len(stale)} séance(s) déjà envoyée(s) ne correspondent plus au plan affiché "
               "(ancien plan, plan recalculé, ou séance retrouvée dans Garmin avec un autre "
               "contenu) : retire-les avant d'envoyer la nouvelle "
               "version, sinon ta montre mélangerait les deux.")
    if WRITE_ENABLED and st.button("🧹 Retirer les séances de l'ancien plan"):
        remove_entries(stale)
        st.rerun()
elif not allowed:
    st.warning(reason)
elif not todo:
    st.caption("Rien à envoyer : les séances des 14 prochains jours sont déjà dans ton calendrier.")
else:
    labels = {session_key(s): f"{s['date']} — {s['title']}" for s in todo}
    chosen = st.multiselect("Séances des 14 prochains jours", list(labels),
                            default=list(labels), format_func=labels.get, key="push_choice")
    confirm = st.checkbox(f"J'ajoute {len(chosen)} séance(s) à mon calendrier Garmin "
                          "(elles se synchroniseront sur la montre).", key="push_confirm")
    if st.button("📲 Envoyer", disabled=not (chosen and confirm), type="primary"):
        # Garde relue au clic : l'état affiché peut dater d'une minute.
        allowed, reason = push_gate(WRITE_ENABLED, fresh_coach_state())
        if not allowed:
            flash("warning", reason)
            st.rerun()
        client = get_garmin_client()
        sent = found = mismatched = duplicates = 0
        # Tout l'envoi sous verrou : deux onglets ne poussent pas la même semaine.
        with goal_store.locked(_athlete_id):
            current = goal_store.load(_athlete_id).get("pushed") or {}
            still_todo = {session_key(s): s for s in
                          pushable_sessions(sessions, TODAY.isoformat(), current)}
            try:
                # Réconciliation : une séance déjà créée par ce plan (journal perdu,
                # envoi interrompu) est rattachée au journal, pas recréée.
                library = client.list_workouts()
            except Exception as e:
                flash("error", f"Erreur Garmin : impossible de lire tes séances ({md_escape(e)}). "
                               "Rien n'a été envoyé.")
                library = None
            for key in chosen if library is not None else []:
                session = still_todo.get(key)
                if session is None:
                    continue
                payload = workout_payload(session, plan_id)
                base = {"plan_id": plan_id, "date": session["date"], "kind": session["kind"],
                        "name": payload["workoutName"], "fingerprint": fingerprint(payload)}
                # Par étiquette (plan + jour + créneau) et non par nom complet : un
                # créneau passé de « Seuil » à « Lignes droites » change de nom,
                # et la séance d'avant resterait à côté de la nouvelle.
                tag = workout_tag(plan_id, session)
                candidates = [w for w in library if tag in (w.get("workoutName") or "")]
                if candidates:
                    try:
                        # Même étiquette ≠ même contenu : la séance a pu être créée
                        # par une version du plan recalculée depuis (même créneau,
                        # autres allures), ou copiée dans Garmin Connect (« Copie
                        # de … »). On retient celle dont le contenu est vérifié ;
                        # à défaut la première, non vérifiée : elle n'est pas
                        # planifiée et son empreinte la fait signaler à retirer.
                        existing, fp = candidates[0], None
                        for cand in candidates:
                            cand_fp = reconciled_fingerprint(
                                payload, client.get_workout(int(cand["workoutId"])))
                            if fp is None or cand_fp != UNVERIFIED_FINGERPRINT:
                                existing, fp = cand, cand_fp
                            if cand_fp != UNVERIFIED_FINGERPRINT:
                                break
                        duplicates += len(candidates) - 1
                        wid = int(existing["workoutId"])
                        # Présente dans la bibliothèque ≠ planifiée : la création a pu
                        # réussir sans réponse, et la planification jamais se faire.
                        sid = (client.find_schedule(wid, session["date"])
                               if fp == UNVERIFIED_FINGERPRINT
                               else client.ensure_scheduled(wid, session["date"]))
                    except Exception as e:
                        flash("error", f"Erreur Garmin en rattachant « {labels[key]} », déjà "
                                       f"présente dans Garmin : {md_escape(e)}.")
                        break
                    goal_store.record_push(_athlete_id, key, {
                        # Nom réel dans Garmin (autre titre si le créneau a changé de séance)
                        **base, "name": existing.get("workoutName"), "fingerprint": fp, "workout_id": wid,
                        "schedule_id": sid, "reconciled": True})
                    if fp == UNVERIFIED_FINGERPRINT:
                        mismatched += 1
                    else:
                        found += 1
                    continue
                try:
                    ids = client.push_workout(payload, session["date"])
                except Exception as e:
                    flash("error", f"Erreur Garmin à « {labels[key]} » : {md_escape(e)}. {sent} séance(s) "
                                   "envoyée(s) avant l'erreur, elles sont conservées.")
                    break
                # Journal écrit AVANT tout appel st.* : un rerun (double clic)
                # interromprait le script au prochain appel Streamlit.
                goal_store.record_push(_athlete_id, key, {**ids, **base})
                sent += 1
        if sent:
            flash("success", f"{sent} séance(s) envoyée(s) dans ton calendrier Garmin.")
        if duplicates:
            flash("warning", f"{duplicates} séance(s) du dashboard en double dans Garmin (copiée(s) "
                             "dans Garmin Connect) : « Rechercher les séances du dashboard » plus bas "
                             "permet de retirer les copies.")
        if found:
            flash("info", f"{found} séance(s) déjà présente(s) dans Garmin, rattachée(s) au plan.")
        if mismatched:
            flash("warning", f"{mismatched} séance(s) du dashboard déjà présente(s) dans Garmin "
                             "sur ces créneaux ne correspondent pas au plan affiché (plan recalculé "
                             "depuis, ou séance modifiée dans Garmin) : le dashboard ne les a pas "
                             "planifiées. Retire-les (bouton ci-dessous) puis renvoie-les.")
        st.session_state.pop("push_confirm", None)
        st.rerun()

# Séances du dashboard présentes dans Garmin mais absentes du journal (journal
# perdu ou corrompu, ancien plan oublié) : on peut les retrouver et les retirer.
if WRITE_ENABLED and st.button("🔎 Rechercher les séances du dashboard dans Garmin"):
    try:
        known = {e.get("workout_id") for e in pushed.values()}
        orphans = {}
        for w in get_garmin_client().list_workouts():
            name = w.get("workoutName") or ""
            if not is_dashboard_workout(w) or w.get("workoutId") in known:
                continue
            day = name.split("[GD-")[-1].split("-")[1] if name.count("-") >= 2 else ""
            iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else ""
            if iso and iso >= TODAY.isoformat():
                orphans[f"orphan-{w['workoutId']}"] = {"workout_id": w["workoutId"], "name": name,
                                                       "date": iso}
        st.session_state["objectif_orphans"] = orphans
    except Exception as e:
        st.error(f"Erreur Garmin : impossible de lire tes séances ({md_escape(e)}).")
orphans = st.session_state.get("objectif_orphans") or {}
if orphans:
    st.warning(f"{len(orphans)} séance(s) du dashboard à venir dans Garmin, hors journal : "
               + ", ".join(f"{o['date']} {md_escape(o['name'])}" for o in orphans.values()))
    if st.button("🗑️ Retirer ces séances retrouvées"):
        for key, o in orphans.items():
            goal_store.record_push(_athlete_id, key, o)   # journalisées pour le retrait
        remove_entries(orphans)
        st.session_state.pop("objectif_orphans", None)
        st.rerun()

upcoming = future_pushes(pushed, TODAY.isoformat())
if pushed:
    with st.expander(f"🗓️ {len(upcoming)} séance(s) à venir envoyée(s) par le dashboard"):
        for key, entry in sorted(upcoming.items()):
            st.markdown(f"- {entry.get('date', key)} — {md_escape(entry.get('name', key))}")
        include_past = st.checkbox("Inclure les séances passées (historique du calendrier)",
                                   key="remove_include_past")
        target = pushed if include_past else upcoming
        if WRITE_ENABLED and target and st.button(f"🗑️ Retirer {len(target)} séance(s) de mon calendrier"):
            remove_entries(target)
            st.rerun()

# ---------------------------------------------------------------------------
# Discuter du plan avec Claude
# ---------------------------------------------------------------------------
with st.expander("🤖 Discuter de ce plan avec Claude"):
    st.markdown(
        "Avec ton abonnement Claude, ouvre **Claude Desktop** ou **Claude Code** connecté au "
        "serveur MCP du projet (voir README) : il lit ton plan et tes métriques directement. "
        "Sinon, copie ce résumé dans n'importe quelle conversation :"
    )
    st.code(plan_brief(plan), language=None)

render_garmin_attribution()
