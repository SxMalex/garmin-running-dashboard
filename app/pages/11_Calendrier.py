"""
Calendrier — les sorties mois par mois ; un clic sur un jour choisit une sortie,
un second clic une autre, et la page les compare : allure corrigée de la pente et
de la chaleur, gestion de course, et le bloc d'avant (volume, charge la veille,
sommeil, HRV). Courses, entraînements ou les deux. Logique pure : `compare_logic`.
"""

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import chart_theme as ct
from compare_logic import (
    HEALTH_NIGHTS,
    KIND_LABELS,
    KINDS,
    WEEKDAYS_FR,
    compare_runs,
    factor_rows,
    filter_kind,
    health_before,
    month_cells,
    month_label,
    months_with_activities,
    run_summary,
    training_block,
    with_kind,
)
from garmin_client import compute_km_splits
from illness_logic import daily_health_frame
from next_session_logic import compute_pmc_series, reference_threshold_sec
from raceday_logic import fmt_pace, weather_from_garmin
from ui_helpers import (
    cache_nonce,
    cached_load_activities,
    get_athlete_id,
    get_garmin_client,
    render_garmin_attribution,
    require_login,
)
from ui_mode import explain
from ui_theme import chip, esc, html_block

st.set_page_config(page_title="Calendrier — Running Dashboard", page_icon="📅", layout="wide")
require_login()
_athlete_id = get_athlete_id()

KIND_COLORS = {"race": ct.ORANGE, "training": ct.BLUE}
SIDE_COLORS = {"A": ct.BLUE, "B": ct.MAGENTA}


@st.cache_data(ttl=3600, show_spinner="Chargement des streams…")
def load_streams(athlete_id: int, activity_id: int) -> dict:
    return get_garmin_client().get_streams(activity_id)


@st.cache_data(ttl=86400, show_spinner=False)
def _load_weather(athlete_id: int, activity_id: int) -> dict | None:
    return weather_from_garmin(get_garmin_client().get_activity_weather(activity_id, strict=True))


def load_weather(athlete_id: int, activity_id: int) -> dict | None:
    """Météo de la sortie ; un échec passager n'est pas mis en cache (réessayé au rerun)."""
    try:
        return _load_weather(athlete_id, activity_id)
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def load_health(athlete_id: int, day_iso: str, nonce: int) -> tuple[pd.DataFrame, list]:
    """Les nuits qui précèdent la sortie (séries par plage, cache disque)."""
    gc = get_garmin_client()
    start = (date.fromisoformat(day_iso) - timedelta(days=HEALTH_NIGHTS - 1)).isoformat()
    sleep = gc.get_sleep_range(start, day_iso)
    frame = daily_health_frame(sleep, gc.get_hrv_range(start, day_iso),
                               gc.get_resting_hr_range(start, day_iso))
    return frame, sleep


@st.cache_data(ttl=3600, show_spinner=False)
def load_pmc(athlete_id: int, nonce: int) -> tuple[pd.DataFrame, int]:
    """PMC de tout l'historique : le même CTL/TSB que le reste du dashboard."""
    df, _err = cached_load_activities(athlete_id)
    threshold = reference_threshold_sec(df)
    return compute_pmc_series(df, threshold), threshold


st.title("Calendrier")
st.caption("Clique un jour pour choisir une sortie, puis un second pour la comparer : ce qui a "
           "changé entre les deux, à terrain et météo égaux, et ce qui s'est passé avant.")

df, error = cached_load_activities(_athlete_id)
if error:
    st.error(f"**Erreur Garmin :** {error}")
    st.stop()
kinded = with_kind(df)
runs = kinded[kinded["kind"] != "other"] if not kinded.empty else kinded
if runs.empty:
    st.info("Aucune sortie de course chargée — rien à placer dans le calendrier.")
    st.stop()

months = months_with_activities(runs)
for _key, _default in (("cal_kind", "both"), ("cal_month", months[0]), ("cmp_a", None),
                       ("cmp_b", None), ("_cal_ver", 0)):
    if _key not in st.session_state:
        st.session_state[_key] = _default
if st.session_state["cal_month"] not in months:
    st.session_state["cal_month"] = months[0]


def _shift_month(step: int) -> None:
    i = months.index(st.session_state["cal_month"]) + step
    st.session_state["cal_month"] = months[min(max(i, 0), len(months) - 1)]


def _clear_selection() -> None:
    st.session_state["cmp_a"] = st.session_state["cmp_b"] = None
    st.session_state["_cal_last"] = None
    st.session_state["_cal_ver"] += 1          # nouvelle clé de graphe : sélection Plotly remise à zéro


# ---------------------------------------------------------------------------
# Filtres et navigation
# ---------------------------------------------------------------------------
with st.container(horizontal=True, vertical_alignment="bottom", key="cal-controls"):
    kind = st.segmented_control("Sorties", list(KINDS), format_func=KINDS.get, key="cal_kind") or "both"
    _i = months.index(st.session_state["cal_month"])
    # Flèches et mois groupés : ils passent ensemble à la ligne sur téléphone.
    with st.container(horizontal=True, vertical_alignment="bottom", width="content", key="cal-month-nav"):
        st.button(":material/chevron_left:", key="cal_prev", help="Mois précédent",
                  on_click=_shift_month, args=(1,), disabled=_i == len(months) - 1)
        st.selectbox("Mois", months, key="cal_month",
                     format_func=lambda ym: month_label(*ym).capitalize(), width=200)
        st.button(":material/chevron_right:", key="cal_next", help="Mois suivant",
                  on_click=_shift_month, args=(-1,), disabled=_i == 0)

by_id = kinded.drop_duplicates("activityId").set_index("activityId")
year, month = st.session_state["cal_month"]
chart_key = f"cal_grid_{year}_{month}_{kind}_{st.session_state['_cal_ver']}"

# Clic sur un jour : remplit A, puis B, puis remplace B. La sélection du graphe
# est lue ici, AVANT de dessiner la grille : la case choisie s'affiche dans le
# même run (un st.rerun de plus faisait perdre un clic rapide). Elle persiste
# d'un run à l'autre : on ne traite un clic qu'une fois.
_event = st.session_state.get(chart_key)
_points = ((_event or {}).get("selection") or {}).get("points") or []
if _points and _points[0].get("customdata"):
    _aid = int(_points[0]["customdata"][0])
    _sig = (chart_key, _aid)
    if _aid > 0 and st.session_state.get("_cal_last") != _sig:
        st.session_state["_cal_last"] = _sig
        if _aid not in (st.session_state["cmp_a"], st.session_state["cmp_b"]):
            slot = "cmp_a" if st.session_state["cmp_a"] is None else "cmp_b"
            st.session_state[slot] = _aid

# A = la plus ancienne, partout (grille, listes, cartes) : on lit de A vers B.
_a, _b = st.session_state["cmp_a"], st.session_state["cmp_b"]
if (_a is not None and _b is not None and _a in by_id.index and _b in by_id.index
        and by_id.loc[_a, "startTimeLocal"] > by_id.loc[_b, "startTimeLocal"]):
    st.session_state["cmp_a"], st.session_state["cmp_b"] = _b, _a

selectable = filter_kind(kinded, kind)
cells = month_cells(selectable, year, month)
picked = {st.session_state["cmp_a"]: "A", st.session_state["cmp_b"]: "B"}
picked.pop(None, None)
next_slot = ("choisir comme A" if st.session_state["cmp_a"] is None else
             "choisir comme B" if st.session_state["cmp_b"] is None else "remplace B")

# ---------------------------------------------------------------------------
# Grille du mois (un point Plotly par jour : cliquable, comme l'explorateur)
# ---------------------------------------------------------------------------
fig = go.Figure()
empty = cells[cells["kind"] == "none"]
fig.add_trace(go.Scatter(
    x=empty["weekday"], y=empty["week"], mode="markers+text", text=[d.day for d in empty["day"]],
    marker=dict(symbol="square", size=40, color=ct.GRID, line=dict(width=0)),
    textfont=dict(color=ct.INK_MUTED, size=12), hoverinfo="skip", showlegend=False,
    customdata=[[-1]] * len(empty),
))
for key, color in KIND_COLORS.items():
    part = cells[cells["kind"] == key]
    if part.empty:
        continue
    ids = [int(i) for i in part["main_id"]]
    tags = [picked.get(i, "") for i in ids]
    fig.add_trace(go.Scatter(
        x=part["weekday"], y=part["week"], mode="markers+text", name=KIND_LABELS[key], showlegend=False,
        text=[f"<b>{t}</b>" if t else str(d.day) for t, d in zip(tags, part["day"])],
        marker=dict(symbol="square", size=40, color=ct.rgba(color, 0.35),
                    line=dict(color=[ct.INK if t else color for t in tags],
                              width=[3 if t else 1.5 for t in tags])),
        textfont=dict(color=ct.INK, size=13),
        customdata=[[i] for i in ids],
        hovertext=[h + ("<br><i>déjà choisie (" + t + ")</i>" if t else f"<br><i>clic : {next_slot}</i>")
                   for h, t in zip(part["hover"], tags)],
        hovertemplate="%{hovertext}<extra></extra>",
    ))
# Légende à part (sinon sa pastille reprend le contour noir d'une case choisie),
# et APRÈS les traces cliquables : intercalée, elle décalait le point renvoyé au clic.
for key, color in KIND_COLORS.items():
    if (cells["kind"] == key).any():
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=KIND_LABELS[key],
                                 hoverinfo="skip", marker=dict(symbol="square", size=14,
                                                               color=ct.rgba(color, 0.35),
                                                               line=dict(color=color, width=1.5))))
n_weeks = int(cells["week"].max()) + 1
fig.update_layout(
    height=62 * n_weeks + 70, margin=dict(l=0, r=0, t=40, b=0), dragmode=False,
    clickmode="event+select", legend=dict(orientation="h", y=-0.02, yanchor="top"),
    hovermode="closest", hoverlabel=dict(bgcolor=ct.SURFACE_2, bordercolor=ct.LINE, align="left",
                                         font=dict(color=ct.INK, size=13)),
    xaxis=dict(tickvals=list(range(7)), ticktext=WEEKDAYS_FR, side="top", range=[-0.6, 6.6],
               showgrid=False, zeroline=False, fixedrange=True),
    yaxis=dict(autorange="reversed", visible=False, range=[n_weeks - 0.4, -0.6], fixedrange=True),
)
with st.container(key="card-cal-grid"):
    st.plotly_chart(fig, on_select="rerun", selection_mode="points", key=chart_key,
                    config={"displayModeBar": False})

# ---------------------------------------------------------------------------
# Les deux sorties (aussi choisissables dans une liste)
# ---------------------------------------------------------------------------


def _label(aid) -> str:
    r = by_id.loc[aid]
    return (f"{pd.Timestamp(r['startTimeLocal']):%d/%m/%Y} · {KIND_LABELS[r['kind']]} · "
            f"{r['distance_km']:.1f} km · {r['activityName'] or 'Course'}")


options = [int(i) for i in selectable.sort_values("startTimeLocal", ascending=False)["activityId"]]
for current in (st.session_state["cmp_a"], st.session_state["cmp_b"]):
    if current is not None and current not in options:
        options.append(current)

with st.container(horizontal=True, vertical_alignment="bottom", key="cal-picks"):
    st.selectbox("Sortie A", options, key="cmp_a", format_func=_label, placeholder="Clique un jour…")
    st.selectbox("Sortie B", options, key="cmp_b", format_func=_label, placeholder="Clique un autre jour…")
    st.button("Effacer", icon=":material/close:", key="cal_clear", on_click=_clear_selection,
              type="tertiary")

a_id, b_id = st.session_state["cmp_a"], st.session_state["cmp_b"]
if a_id is None or b_id is None or a_id == b_id:
    st.caption("Choisis deux sorties différentes pour les comparer. La plus ancienne devient A, "
               "la plus récente B : on lit ce qui a changé de l'une à l'autre.")
    explain("comparaison")
    render_garmin_attribution()
    st.stop()

# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------
pmc, threshold = load_pmc(_athlete_id, cache_nonce())


def _side(aid: int) -> dict:
    row = by_id.loc[aid].to_dict() | {"activityId": aid}
    day = row["day"]
    splits = compute_km_splits(load_streams(_athlete_id, aid))
    frame, sleep = load_health(_athlete_id, day.isoformat(), cache_nonce())
    return {"run": run_summary(row, splits, load_weather(_athlete_id, aid)),
            "block": training_block(df, pmc, day, threshold),
            "health": health_before(frame, sleep, day)}


with st.spinner("Comparaison des deux sorties…"):
    verdict = compare_runs(_side(a_id), _side(b_id))
A, B = verdict["a"], verdict["b"]


def _run_card(tag: str, side: dict) -> None:
    r = side["run"]
    with st.container(key=f"card-cmp-{tag.lower()}"):
        html_block(f'<div class="gd-kicker">{chip(tag, "accent")} {r["date"]:%d/%m/%Y} · '
                   f'{esc(KIND_LABELS[r["kind"]])}</div><div><b>{esc(r["name"])}</b></div>')
        m1, m2, m3 = st.columns(3)
        m1.metric("Distance", f"{r['distance_km']:.2f} km")
        m2.metric("Durée", f"{r['duration_min']:.0f} min")
        m3.metric("Allure", f"{fmt_pace(r['pace_s'])}/km" if r["pace_s"] else "—")
        m4, m5, m6 = st.columns(3)
        m4.metric("Allure corrigée", f"{fmt_pace(r['adjusted_s'])}/km" if r["adjusted_s"] else "—",
                  help="Ramenée à un terrain plat (pente, Minetti) et au frais (chaleur, Hadley).")
        m5.metric("FC moyenne", f"{r['hr']:.0f} bpm" if r["hr"] else "—")
        m6.metric("2e moitié", f"{r['split_pct']:+.1f} %" if r["split_pct"] is not None else "—",
                  help="Allure de la 2e moitié contre la 1re, à pente égale. Positif = ralentissement.")
        w = r["weather"]
        if w:
            st.caption(f"{w['temp_c']:.0f} °C" + (f", point de rosée {w['dewpoint_c']:.0f} °C"
                                                  if w.get("dewpoint_c") is not None else "")
                       + (f" · pénalité chaleur ≈ {r['heat']['mid']:.1f} %" if r["heat"] and r["heat"]["mid"]
                          else ""))


c_a, c_b = st.columns(2, gap="medium")
with c_a:
    _run_card("A", A)
with c_b:
    _run_card("B", B)

with st.container(key="card-cmp-verdict"):
    label = {"good": "B en progrès", "warning": "B en retrait", "neutral": "Stable"}[verdict["status"]]
    html_block(f'<div class="gd-kicker">De A à B</div><div>{chip(label, verdict["status"])} '
               f'{esc(verdict["headline"])}</div>')
    if verdict["factors"]:
        st.markdown("**Ce qui a changé entre-temps**\n\n" + "\n".join(f"- {f}" for f in verdict["factors"]))
    else:
        st.write("Aucun écart marquant dans le bloc d'avant (volume, charge, sommeil, HRV).")
    for note in verdict["notes"]:
        st.caption(note)

# Allure et FC kilomètre par kilomètre
sa, sb = A["run"]["splits"], B["run"]["splits"]
if not sa.empty and not sb.empty:
    fig_s = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                          row_heights=[0.6, 0.4])
    for tag, side, frame in (("A", A, sa), ("B", B, sb)):
        name = f"{tag} · {side['run']['date']:%d/%m/%Y}"
        fig_s.add_trace(go.Scatter(x=frame["km"], y=frame["gap_s"] / 60, mode="lines+markers", name=name,
                                   line=dict(color=SIDE_COLORS[tag], width=2),
                                   customdata=[fmt_pace(v) for v in frame["gap_s"]],
                                   hovertemplate="km %{x:.1f} · %{customdata}/km<extra>" + tag + "</extra>"),
                        row=1, col=1)
        if frame["hr"].notna().any():
            fig_s.add_trace(go.Scatter(x=frame["km"], y=frame["hr"], mode="lines", name=f"FC {tag}",
                                       line=dict(color=SIDE_COLORS[tag], width=1.5, dash="dot"),
                                       showlegend=False,
                                       hovertemplate="km %{x:.1f} · %{y:.0f} bpm<extra>" + tag + "</extra>"),
                            row=2, col=1)
    # Graduations en mm:ss toutes les 15 s (des minutes décimales se lisent mal : 5,5 = 5:30)
    _all = pd.concat([sa["gap_s"], sb["gap_s"]])
    _ticks = list(range(int(_all.min() // 15 * 15), int(_all.max()) + 15, 15))
    fig_s.update_yaxes(title_text="allure à plat (min/km)", autorange="reversed", row=1, col=1,
                       tickvals=[t / 60 for t in _ticks], ticktext=[fmt_pace(t) for t in _ticks])
    fig_s.update_yaxes(title_text="FC (bpm)", row=2, col=1)
    fig_s.update_xaxes(title_text="km", row=2, col=1)
    fig_s.update_layout(height=440, margin=dict(l=0, r=0, t=30, b=0), legend=dict(orientation="h", y=1.08))
    with st.container(key="card-cmp-splits"):
        st.markdown("#### Kilomètre par kilomètre")
        st.plotly_chart(fig_s, config={"displayModeBar": False})
        st.caption("Allure ramenée au plat (les côtes n'apparaissent plus comme des ralentissements). "
                   "Pointillés : fréquence cardiaque.")
else:
    st.caption("Détail au kilomètre indisponible pour l'une des deux sorties (pas de streams Garmin).")

# Le bloc d'avant
rows = factor_rows(A, B)
table = pd.DataFrame({
    "Indicateur": [("● " if r["notable"] else "") + r["label"] for r in rows],
    f"A · {A['run']['date']:%d/%m}": [r["a"] for r in rows],
    f"B · {B['run']['date']:%d/%m}": [r["b"] for r in rows],
    "Écart": [r["delta"] for r in rows],
})
with st.container(key="card-cmp-block"):
    st.markdown(f"#### Le bloc d'avant ({A['block']['weeks']} semaines, et 7 nuits pour la récupération)")
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption("● = écart assez grand pour compter. Charge la veille de chaque sortie : même CTL/TSB "
               "que les pages Forme et Accueil.")

explain("comparaison")
render_garmin_attribution()
