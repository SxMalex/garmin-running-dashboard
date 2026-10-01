"""
Navigation en 4 pôles, une question chacun :

- Aujourd'hui   « Je cours quoi, et je suis en état ? »
- Entraînement  « Qu'est-ce que j'ai fait ? »
- Progrès       « Est-ce que je m'améliore ? »
- Objectif      « Où je vais, et comment ? »

`st.navigation(position="hidden")` : le routeur (`main.py`) dessine lui-même
l'en-tête (pôles + sous-pages du pôle courant) et, sur téléphone, une barre
d'onglets fixe en bas. Tous les liens sont des `st.page_link` : navigation
côté client, la session (connexion, mode Light/Pro, réglages) est conservée.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from ui_mode import render_mode_toggle


@dataclass(frozen=True)
class PageDef:
    path: str
    title: str
    url_path: str
    icon: str


@dataclass(frozen=True)
class Pole:
    key: str
    label: str
    icon: str
    pages: tuple[PageDef, ...]


POLES: tuple[Pole, ...] = (
    Pole("today", "Aujourd'hui", ":material/sunny:", (
        PageDef("pages/0_Accueil.py", "Cockpit", "", ":material/dashboard:"),
        PageDef("pages/3_Forme.py", "Forme & récup", "forme", ":material/battery_charging_full:"),
        PageDef("pages/5_Next_Session.py", "Prochaine sortie", "prochaine-sortie", ":material/route:"),
    )),
    Pole("training", "Entraînement", ":material/directions_run:", (
        PageDef("pages/1_Activities.py", "Activités", "activites", ":material/list:"),
        PageDef("pages/6_Heatmap.py", "Carte", "carte", ":material/map:"),
    )),
    Pole("progress", "Progrès", ":material/trending_up:", (
        PageDef("pages/4_Progression.py", "Tendances", "tendances", ":material/show_chart:"),
        PageDef("pages/2_Stats.py", "Statistiques", "stats", ":material/bar_chart:"),
        PageDef("pages/8_Comparatif.py", "Année contre année", "comparatif", ":material/compare_arrows:"),
    )),
    Pole("goal", "Objectif", ":material/flag:", (
        PageDef("pages/9_Objectif.py", "Mon plan", "objectif", ":material/event:"),
        PageDef("pages/7_AI_Coach.py", "Coach IA", "coach", ":material/auto_awesome:"),
    )),
)

COACH_PAGE = "pages/7_AI_Coach.py"


def build_pages() -> tuple[dict[str, list], dict[str, tuple[Pole, PageDef]]]:
    """Sections pour `st.navigation` + index page → (pôle, définition)."""
    sections: dict[str, list] = {}
    index: dict[str, tuple[Pole, PageDef]] = {}
    for pole in POLES:
        pages = []
        for p in pole.pages:
            page = st.Page(p.path, title=p.title, icon=p.icon,
                           url_path=p.url_path or None, default=not p.url_path)
            pages.append(page)
            index[p.title] = (pole, p)
        sections[pole.label] = pages
    return sections, index


def _link(path: str, label: str, icon: str | None, key: str, on: bool) -> None:
    # La clé du conteneur porte l'état actif : le CSS (ui_theme) s'y accroche.
    with st.container(key=f"{key}-{'on' if on else 'off'}", width="content"):
        st.page_link(path, label=label, icon=icon)


def render_header(current_title: str, index: dict[str, tuple[Pole, PageDef]],
                  on_refresh, on_logout) -> None:
    """En-tête : marque, pôles, bascule Light/Pro, actualiser, Claude ; sous-pages du pôle."""
    pole, _ = index.get(current_title, (POLES[0], POLES[0].pages[0]))

    with st.container(horizontal=True, key="gd-topbar", vertical_alignment="center"):
        # Icône Material via Markdown : st.html retire le SVG en ligne.
        with st.container(key="gd-brand", horizontal=True, width="content",
                          vertical_alignment="center", gap="small"):
            st.markdown(":material/directions_run:")
            with st.container(key="gd-brand-name", width="content"):
                st.markdown("RUNNING DASHBOARD")
        for p in POLES:
            _link(p.pages[0].path, p.label, None, f"gd-pole-{p.key}", p.key == pole.key)
        st.space("stretch")
        render_mode_toggle()
        if st.button("Actualiser", icon=":material/refresh:", key="gd-refresh", type="tertiary",
                     help="Recharge les données Garmin (vide le cache)"):
            on_refresh()
        with st.popover("Compte", icon=":material/person:", type="tertiary"):
            if st.button("Déconnexion", icon=":material/logout:", key="gd-logout"):
                on_logout()
        with st.container(key="gd-claude", width="content"):
            st.page_link(COACH_PAGE, label="Prompt coach IA", icon=":material/auto_awesome:",
                         help="Préparer un prompt pour Claude ou un autre LLM")

    if len(pole.pages) > 1:
        with st.container(horizontal=True, key="gd-subnav"):
            for p in pole.pages:
                _link(p.path, p.title, None, f"gd-sub-{p.url_path or 'home'}", p.title == current_title)

    with st.container(key="gd-bottomnav"):
        for p in POLES:
            _link(p.pages[0].path, p.label, p.icon, f"gd-tab-{p.key}", p.key == pole.key)
