"""
Tableau de bord Running — Accueil (cockpit du jour)
Connexion Garmin, état du matin (sommeil, HRV, Body Battery), métriques de la
semaine, séance suggérée et dernière sortie en résumé. Le détail complet des
activités vit sur la page Activités ; la forme sur Forme & Récup.
"""

import os

import streamlit as st
import pandas as pd
from datetime import date, datetime

from garmin_client import (
    clear_tokens,
    complete_mfa,
    login_with_credentials,
)
from coach_logic import (
    hard_session_alert,
    merge_coach_into_recommendation,
    target_label,
)
from formatting import weekday_fr
from forme_logic import compute_forme_verdict, forme_downgrade
from next_session_logic import SESSION_TYPES, compute_tsb, recommend_session
from ui_helpers import (
    cached_coach_context,
    cached_load_activities,
    drop_session,
    get_athlete_id,
    get_garmin_client,
    get_session_api,
    render_activity_map,
    render_refresh_button,
    render_garmin_attribution,
    store_session,
)

# ---------------------------------------------------------------------------
# Configuration de la page
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Running Dashboard",
    page_icon="🏃",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": "Tableau de bord Running — Données Garmin Connect + IA Coach",
    },
)

# ---------------------------------------------------------------------------
# Connexion Garmin — reprise de session ou formulaire de login
# ---------------------------------------------------------------------------
_api = get_session_api()

if _api is None:
    st.markdown("""
    <div style="text-align:center; padding: 48px 32px;">
        <h1 style="margin:0; font-size: 2.5rem;">🏃 Running Dashboard</h1>
        <p style="margin: 12px 0 0; color: #888; font-size: 1.1rem;">
            Connecte ton compte Garmin pour accéder à ton tableau de bord
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.divider()
    col_a, col_b, col_c = st.columns([1, 2, 1])
    with col_b:
        # Étape MFA en attente ? (login précédent a demandé un code)
        if "garmin_mfa_pending" in st.session_state:
            st.info("Un code de vérification t'a été envoyé par Garmin (email).")
            with st.form("mfa_form"):
                mfa_code = st.text_input("Code MFA", max_chars=10)
                submitted = st.form_submit_button("Valider le code", width="stretch")
            if submitted and mfa_code.strip():
                try:
                    api = complete_mfa(
                        st.session_state["garmin_mfa_pending"], mfa_code.strip()
                    )
                    del st.session_state["garmin_mfa_pending"]
                    store_session(api)
                    st.rerun()
                except Exception as e:
                    st.error(f"Code refusé : {e}")
            if st.button("↩️ Recommencer la connexion"):
                del st.session_state["garmin_mfa_pending"]
                st.rerun()
            st.stop()

        # GARMIN_PASSWORD n'est jamais utilisé ici : en `value=`, Streamlit
        # l'enverrait au navigateur ; en repli côté serveur, n'importe quel
        # visiteur pourrait déclencher un vrai login (MFA, blocage du compte)
        # en soumettant le formulaire vide. Le mot de passe se tape une fois,
        # les tokens tiennent ensuite ~1 an.
        with st.form("login_form"):
            email = st.text_input(
                "Email Garmin", value=os.getenv("GARMIN_EMAIL", "")
            )
            password = st.text_input("Mot de passe", type="password")
            submitted = st.form_submit_button("🔗 Se connecter à Garmin", width="stretch")

        if submitted:
            if not email or not password:
                st.error("Renseigne l'email et le mot de passe Garmin.")
            else:
                with st.spinner("Connexion à Garmin Connect..."):
                    try:
                        status, payload = login_with_credentials(email, password)
                        if status == "needs_mfa":
                            st.session_state["garmin_mfa_pending"] = payload
                            st.rerun()
                        else:
                            store_session(payload)
                            st.rerun()
                    except Exception as e:
                        st.error(f"Connexion refusée : {e}")

        st.caption(
            "La session est mémorisée sur le serveur (tokens valides ~1 an) : "
            "la prochaine ouverture de l'app se connectera automatiquement."
        )
    st.stop()

_athlete_id = get_athlete_id()


# ---------------------------------------------------------------------------
# Données de profil et du jour (cachées)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=86400, show_spinner=False)
def load_shoes(athlete_id: int) -> list:
    return get_garmin_client().get_shoes()


@st.cache_data(ttl=86400, show_spinner=False)
def load_full_name(athlete_id: int) -> str:
    return get_garmin_client().get_full_name()


@st.cache_data(ttl=3600, show_spinner=False)
def load_today(athlete_id: int, cdate: str) -> dict:
    """Sommeil, HRV et stats quotidiennes du jour — pour le bandeau du matin."""
    client = get_garmin_client()
    hrv_raw = client.get_hrv(cdate)
    hrv = hrv_raw[0] if isinstance(hrv_raw, list) and hrv_raw else (hrv_raw or {})
    sleep_raw = client.get_sleep(cdate) or {}
    daily_raw = client.get_daily_stats(cdate)
    daily = daily_raw[0] if isinstance(daily_raw, list) and daily_raw else (daily_raw or {})
    return {
        "hrv_summary": (hrv.get("hrvSummary") or {}) if isinstance(hrv, dict) else {},
        "sleep_dto": (sleep_raw.get("dailySleepDTO") or {}) if isinstance(sleep_raw, dict) else {},
        "daily": daily if isinstance(daily, dict) else {},
    }


@st.cache_data(ttl=3600, show_spinner=False)
def load_streams(athlete_id: int, activity_id: int) -> dict:
    return get_garmin_client().get_streams(activity_id)


# ---------------------------------------------------------------------------
# Barre latérale
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## ⚙️ Paramètres")

    render_refresh_button()

    if st.button("🚪 Déconnexion", width='stretch'):
        clear_tokens()
        drop_session()
        st.rerun()

    st.divider()
    st.caption(f"Dernière mise à jour : {datetime.now().strftime('%H:%M:%S')}")


# ---------------------------------------------------------------------------
# Chargement des données
# ---------------------------------------------------------------------------
# Tout l'historique : le TSB et la séance suggérée ci-dessous en dépendent, et un
# curseur de profondeur les faisait varier d'un réglage à l'autre.
df, error = cached_load_activities(_athlete_id)

# ---------------------------------------------------------------------------
# En-tête principal
# ---------------------------------------------------------------------------
st.title("🏃 Running Dashboard")
_name = load_full_name(_athlete_id)
st.caption(
    "Analyse de tes performances de course — Données Garmin Connect"
    + (f" · {_name}" if _name else "")
)

# ---------------------------------------------------------------------------
# Gestion des erreurs Garmin
# ---------------------------------------------------------------------------
if error:
    st.error(f"**Erreur de connexion Garmin**\n\n{error}")
    if st.button("🔄 Reconnecter à Garmin"):
        drop_session()
        st.rerun()
    st.stop()

if df.empty:
    st.warning("Aucune activité trouvée. Vérifie ton compte Garmin Connect.")
    st.stop()

# Chaussures dans la sidebar — chargé ici, après vérification de la connexion
with st.sidebar:
    st.divider()
    shoes = load_shoes(_athlete_id)
    st.markdown("### 👟 Chaussures")
    if not shoes:
        st.caption("Aucune chaussure configurée dans ton profil Garmin.")
    else:
        for shoe in shoes:
            km = shoe["distance_km"]
            label = shoe["name"]
            if shoe["retired"]:
                st.caption(f"~~{label}~~ — {km} km *(retirée)*")
            else:
                st.caption(f"**{label}** — {km} km")
                st.progress(min(km / 800, 1.0), text=f"{min(round(km / 8), 100)} %")
        st.caption("Objectif indicatif : 800 km")

running_df = df[df["activityType"] == "running"]
client = get_garmin_client()

# ---------------------------------------------------------------------------
# Aujourd'hui — sommeil, HRV, Body Battery, FC repos + verdict de forme
# ---------------------------------------------------------------------------
st.subheader("🌅 Aujourd'hui")

today = load_today(_athlete_id, date.today().isoformat())
sleep_dto = today["sleep_dto"]
hrv_summary = today["hrv_summary"]
daily = today["daily"]

sleep_sec = sleep_dto.get("sleepTimeSeconds")
sleep_score = ((sleep_dto.get("sleepScores") or {}).get("overall") or {}).get("value")
hrv_status = hrv_summary.get("status")
hrv_last = hrv_summary.get("lastNightAvg")

# Toutes les activités, pas seulement la course : le wing, le vélo ou la
# muscu fatiguent aussi (cf. compute_pmc_series).
_, _, tsb = compute_tsb(df) if not df.empty else (0.0, 0.0, None)
verdict = compute_forme_verdict(tsb, hrv_status, sleep_score)

t1, t2, t3, t4, t5 = st.columns(5)

if sleep_sec:
    h, m = divmod(int(sleep_sec) // 60, 60)
    t1.metric("😴 Sommeil", f"{h}h{m:02d}",
              delta=f"Score {sleep_score}" if sleep_score is not None else None,
              delta_color="off")
else:
    t1.metric("😴 Sommeil", "—")

t2.metric("💓 HRV nuit", f"{int(hrv_last)} ms" if hrv_last else "—",
          delta=(hrv_status or "").capitalize() or None, delta_color="off")

bb_high = daily.get("bodyBatteryHighestValue")
t3.metric("🔋 Body Battery", f"{int(bb_high)}" if bb_high is not None else "—")

rhr = daily.get("restingHeartRate")
t4.metric("❤️ FC repos", f"{int(rhr)} bpm" if rhr else "—")

t5.metric("🎯 Forme", f"{verdict['icon']} {verdict['label']}",
          delta=f"TSB {tsb:+.0f}" if tsb is not None else None, delta_color="off")

st.page_link("pages/3_Forme.py", label="Voir le détail de la forme", icon="⚡")

st.divider()

# ---------------------------------------------------------------------------
# Métriques de la semaine / du mois
# ---------------------------------------------------------------------------
metrics = client.get_summary_metrics(df)

col1, col2, col3, col4, col5, col6, col7 = st.columns(7)

_now = datetime.now()
_month_mask = running_df["startTimeLocal"] >= _now.replace(
    day=1, hour=0, minute=0, second=0, microsecond=0
)
_dplus_mois = int(running_df.loc[_month_mask, "elevationGain"].fillna(0).sum())

metric_data = [
    (col1, metrics["km_semaine"], "km", "Cette semaine", "🗓️"),
    (col2, metrics["km_mois"], "km", "Ce mois", "📅"),
    (col3, metrics["nb_sorties_semaine"], "", "Sorties / semaine", "👟"),
    (col4, metrics["nb_sorties_mois"], "", "Sorties / mois", "📊"),
    (col5, metrics["pace_moyen"], "", "Allure moyenne", "⏱️"),
    (col6, metrics["hr_moyen"], "", "FC moyenne", "❤️"),
    (col7, _dplus_mois, "m", "D+ ce mois", "⛰️"),
]

for col, value, unit, label, icon in metric_data:
    with col:
        st.metric(label=f"{icon} {label}", value=f"{value} {unit}".strip())

st.divider()

# ---------------------------------------------------------------------------
# Séance suggérée (teaser — le détail et le parcours sur Prochaine sortie)
# ---------------------------------------------------------------------------
if len(running_df) >= 3:
    # Même chaîne que la page Prochaine sortie : plan Garmin d'abord, logique
    # interne en repli, modulée par la récupération du jour.
    _downgrade = forme_downgrade(hrv_status, sleep_score)
    _coach = cached_coach_context(_athlete_id)
    rec = merge_coach_into_recommendation(
        recommend_session(running_df, downgrade=_downgrade, load_df=df), _coach
    )
    s = SESSION_TYPES[rec["session_key"]]
    _task = rec.get("coach_task")

    st.subheader("🗓️ Séance du coach" if _task else "🗓️ Séance suggérée")

    if _task:
        _when = (
            "Aujourd'hui" if _task["date"] == date.today()
            else weekday_fr(_task["date"]).capitalize() + _task["date"].strftime(" %d/%m")
        )
        r1, r2, r3, r4 = st.columns([2, 1, 1, 1])
        r1.metric(f"{s['icon']} Séance", _task["name"])
        r2.metric("🎯 Cible", target_label(_task))
        r3.metric("⏱️ Durée", f"{_task['duration_min']} min")
        r4.metric("📅 Quand", _when)

        _plan_bits = [_coach["plan"]["name"]]
        if _coach["phase"]:
            _plan_bits.append(f"phase {_coach['phase']['label']}")
        if _coach["days_to_event"] is not None:
            _plan_bits.append(f"J−{_coach['days_to_event']}")
        st.caption(" · ".join(_plan_bits))

        _alert = hard_session_alert(_coach, _downgrade)
        if _alert:
            st.warning(_alert, icon="🛟")
    else:
        r1, r2, r3, r4 = st.columns([2, 1, 1, 1])
        r1.metric(f"{s['icon']} Type", s["label"])
        r2.metric("📏 Distance", f"{rec['target_dist_km']} km")
        r3.metric("🐇 Allure", rec["target_pace_str"])
        r4.metric("📅 Quand", rec["suggested_date_str"])

    st.page_link(
        "pages/5_Next_Session.py",
        label="Générer le parcours et l'export GPX",
        icon="🗺️",
    )
    st.divider()

# ---------------------------------------------------------------------------
# Dernière sortie (résumé — le détail complet est sur la page Activités)
# ---------------------------------------------------------------------------
st.subheader("🏅 Dernière sortie")

if not running_df.empty:
    last = running_df.sort_values("startTimeLocal", ascending=False).iloc[0]
    date_fmt = pd.to_datetime(last["startTimeLocal"]).strftime("%A %d %B %Y à %H:%M")

    st.markdown(f"#### {last['activityName']}")
    st.caption(f"📅 {date_fmt}")

    m1, m2, m3, m4, m5, m6, m7, m8 = st.columns(8)
    m1.metric("📏 Distance",  f"{last['distance_km']:.2f} km")
    m2.metric("⏱️ Durée",     f"{int(last['duration_min'])} min")
    m3.metric("🐇 Allure",    last["avgPace"])
    m4.metric("❤️ FC moy",    f"{int(last['avgHR'])} bpm"       if pd.notna(last.get("avgHR"))       else "—")
    m5.metric("❤️‍🔥 FC max",   f"{int(last['maxHR'])} bpm"       if pd.notna(last.get("maxHR"))       else "—")
    m6.metric("🦶 Cadence",   f"{int(last['avgCadence'])} spm"  if pd.notna(last.get("avgCadence"))  else "—")
    m7.metric("🔥 Calories",  f"{int(last['calories'])} kcal"   if pd.notna(last.get("calories"))    else "—")
    m8.metric("⛰️ D+",        f"{int(last['elevationGain'])} m" if pd.notna(last.get("elevationGain")) else "—")

    streams = load_streams(_athlete_id, int(last["activityId"]))
    render_activity_map(streams, height=320)

    st.page_link(
        "pages/1_Activities.py",
        label="Splits, streams et détail complet sur la page Activités",
        icon="📋",
    )
else:
    st.info("Aucune activité de course trouvée dans les données chargées.")

# ---------------------------------------------------------------------------
# Pied de page
# ---------------------------------------------------------------------------
st.divider()
if not running_df.empty:
    total_km = running_df["distance_km"].sum()
    _since = running_df["startTimeLocal"].min().strftime("%m/%Y")
    st.caption(
        f"Historique complet : {total_km:.0f} km sur {len(running_df)} sorties "
        f"depuis {_since}"
    )
render_garmin_attribution()
