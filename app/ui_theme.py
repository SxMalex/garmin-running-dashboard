"""
Identité visuelle « Piste claire » : fond papier, cartes blanches, encre
presque noire, un seul accent « volt » posé en aplat.

Principes (ne pas diluer) :
- UNE pièce forte par page : la carte de la séance (gros chiffre condensé,
  une ligne « Pourquoi »). Le reste reste calme : cartes blanches à filet fin,
  pas d'ombre, pas de dégradé.
- L'accent volt (`ct.ACCENT`) n'est JAMAIS du texte sur fond clair (1,1:1) :
  aplats (pastille de zone, jour courant, onglet actif) avec l'encre dessus.
- Les états (bon / attention / sérieux) passent par des pastilles
  `ct.STATUS_TEXT` sur `ct.STATUS_BG` (≥ 4,5:1), jamais par la couleur seule :
  chaque pastille porte aussi un mot.
- Tout texte venant de Garmin ou d'un plan est échappé et remis sur une ligne
  (`_one_line`) : une ligne vide fermerait le bloc HTML et la suite serait lue
  comme du Markdown (liens, images distantes).
- Polices déclarées dans `.streamlit/config.toml`, servies localement.
"""

from __future__ import annotations

import html

import streamlit as st

import chart_theme as ct
# Zones de la jauge : les seuils du verdict de forme, pas une copie.
from forme_logic import TSB_FATIGUE, TSB_FRESH

DISPLAY = "'Barlow Condensed', 'Arial Narrow', sans-serif"

# Niveau du verdict de forme (forme_logic) → clé d'état des pastilles.
# « Normal » est un neutre : le bleu est l'entité « allure », pas un état.
VERDICT_STATUS = {2: "good", 1: "neutral", 0: "serious"}



def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def contrast(a: str, b: str) -> float:
    """Rapport de contraste WCAG entre deux couleurs hex."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ink_on(background: str) -> str:
    """Encre la plus lisible (blanc ou encre) sur un fond donné."""
    return max(("#ffffff", ct.INK), key=lambda ink: contrast(background, ink))


def _one_line(value) -> str:
    return " ".join(str(value).split())


def esc(value) -> str:
    """Texte externe → HTML sûr, sur une ligne."""
    return html.escape(_one_line(value))


# ---------------------------------------------------------------------------
# Feuille de style commune
# ---------------------------------------------------------------------------
_CSS = f"""
<style>
:root {{
  --gd-paper: {ct.SURFACE}; --gd-card: {ct.SURFACE_2}; --gd-line: {ct.LINE};
  --gd-ink: {ct.INK}; --gd-ink2: {ct.INK_SECONDARY}; --gd-muted: {ct.INK_MUTED};
  --gd-accent: {ct.ACCENT}; --gd-display: {DISPLAY};
}}
/* Barre d'outils Streamlit : transparente ET traversable. Fixée en haut de page,
   elle recouvre la 1re ligne de l'en-tête du dashboard et en avalait les clics
   (pôles, Light/Pro, Actualiser) : seuls ses propres boutons restent cliquables. */
[data-testid="stHeader"], [data-testid="stHeader"] * {{ pointer-events: none; }}
[data-testid="stHeader"] button, [data-testid="stHeader"] a,
[data-testid="stHeader"] [role="button"] {{ pointer-events: auto; }}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 1.2rem; max-width: 1320px; }}

/* Cartes : st.container(key="card-…") */
[class*="st-key-card"] {{
  background: var(--gd-card); border: 1px solid var(--gd-line);
  border-radius: 24px; padding: 24px 26px;
}}

/* Graphiques : le frontend Streamlit force le fond Plotly au gris des champs
   (secondaryBackgroundColor) par-dessus le template `gar` ; on le rend transparent
   pour que les graphes se fondent dans la carte ou le papier. */
[data-testid="stPlotlyChart"] .main-svg {{ background: transparent !important; }}
[data-testid="stPlotlyChart"] rect.bg {{ fill: transparent !important; }}

/* Chiffres : la condensée partout où un nombre compte. */
[data-testid="stMetricValue"] {{
  font-family: var(--gd-display); font-weight: 700; letter-spacing: .005em;
}}
[data-testid="stMetricLabel"] p {{ color: var(--gd-muted); font-weight: 500; }}

.gd-kicker {{
  font-size: .8rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  color: var(--gd-muted); margin: 0 0 .35rem;
}}
.gd-headline {{
  font-family: var(--gd-display); font-weight: 700; line-height: 1;
  font-size: clamp(2rem, 4.6vw, 3.3rem); letter-spacing: -.01em; margin: .15rem 0 .6rem;
}}
.gd-chip {{
  display: inline-block; padding: .35rem .8rem; border-radius: 999px;
  font-weight: 600; font-size: .88rem; line-height: 1.2; margin: 0 .35rem .35rem 0;
  border: 1px solid transparent;
}}
.gd-chip-neutral {{ background: var(--gd-card); border-color: var(--gd-line); color: var(--gd-ink2); }}
.gd-chip-accent {{ background: var(--gd-accent); color: var(--gd-ink); }}
{"".join(f".gd-chip-{k} {{ background: {ct.STATUS_BG[k]}; color: {ct.STATUS_TEXT[k]}; }}"
         for k in ct.STATUS_BG)}

/* Carte séance (pièce forte) */
.gd-session-top {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
.gd-session-row {{ display: flex; align-items: flex-end; gap: 12px 36px; flex-wrap: wrap; margin: .6rem 0 1rem; }}
.gd-session-number {{
  font-family: var(--gd-display); font-weight: 700; line-height: .85;
  font-size: clamp(4.5rem, 12vw, 7.5rem);
}}
.gd-session-unit {{ font-family: var(--gd-display); font-weight: 600; font-size: 2rem;
  color: var(--gd-muted); margin-left: 6px; }}
.gd-session-title {{ font-size: 1.55rem; font-weight: 600; line-height: 1.15; }}
.gd-session-target {{ font-size: 1.05rem; color: var(--gd-ink2); margin-top: 4px; }}
.gd-session-when {{ font-size: .95rem; color: var(--gd-muted); margin-top: 2px; }}
.gd-why {{
  background: var(--gd-paper); border-radius: 14px; padding: 14px 18px;
  color: var(--gd-ink2); line-height: 1.5; max-width: 72ch;
}}
.gd-why strong {{ color: var(--gd-ink); }}

/* Jauge de fraîcheur horizontale */
.gd-big {{ font-family: var(--gd-display); font-weight: 700; line-height: .9; font-size: 4.2rem; }}
.gd-track {{ position: relative; display: flex; gap: 3px; height: 12px; margin: 14px 0 8px; }}
.gd-track span {{ display: block; height: 100%; }}
.gd-track .gd-needle {{
  position: absolute; top: -6px; width: 4px; height: 24px; border-radius: 2px;
  background: var(--gd-ink); transform: translateX(-2px);
}}
.gd-track-labels {{ display: flex; justify-content: space-between; font-size: .82rem; color: var(--gd-muted); }}

/* Semaine en 7 cases */
.gd-week {{ display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 8px; }}
.gd-day {{ border-radius: 14px; padding: 10px 10px 12px; background: var(--gd-paper); min-height: 74px; }}
.gd-day-name {{ font-size: .8rem; color: var(--gd-muted); }}
.gd-day-what {{ font-weight: 600; font-size: .92rem; line-height: 1.2; margin-top: 4px; }}
.gd-day-done {{ background: var(--gd-ink); color: #fff; }}
.gd-day-done .gd-day-name {{ color: #C9CCD3; }}
.gd-day-done .gd-day-mark {{ color: var(--gd-accent); font-size: .78rem; margin-top: 4px; }}
.gd-day-today {{ background: var(--gd-accent); outline: 2px solid var(--gd-ink); }}
.gd-day-today .gd-day-name {{ color: var(--gd-ink); font-weight: 700; }}
.gd-day-istoday {{ outline: 2px solid var(--gd-ink); outline-offset: 2px; }}
.gd-day-short {{ display: none; }}
.gd-day-plan {{ background: transparent; border: 1.5px dashed {ct.BASELINE}; }}
@media (max-width: 640px) {{
  .gd-week {{ gap: 4px; }}
  .gd-day {{ padding: 6px 4px; min-height: 56px; text-align: center; }}
  .gd-day-what {{ font-size: .72rem; }}
}}

/* Signaux */
.gd-signal-level {{ font-size: .82rem; font-weight: 600; }}
.gd-signal-title {{ font-size: 1.1rem; font-weight: 600; margin: 4px 0; line-height: 1.25; }}
.gd-signal-body {{ font-size: .92rem; color: var(--gd-muted); line-height: 1.45; }}

/* ---------------- Navigation ---------------- */
/* En-tête : jamais d'ascenseur. Streamlit met `overflow: auto` sur les conteneurs
   horizontaux ; avec la barre latérale ouverte, la largeur utile tombe sous
   1 000 px et la barre débordait (ascenseurs horizontal ET vertical). */
.st-key-gd-topbar {{ align-items: center; gap: .6rem; margin-bottom: .2rem;
  flex-wrap: wrap !important; overflow: visible !important; row-gap: .4rem; }}
.st-key-gd-subnav {{ overflow: visible !important; flex-wrap: wrap !important; }}
/* Moins de place : Actualiser et Compte passent en icône seule (infobulle conservée). */
@media (max-width: 1500px) {{
  .st-key-gd-brand-name {{ display: none; }}
  /* Masqués visuellement seulement : le libellé reste le nom accessible du bouton. */
  .st-key-gd-refresh p, .st-key-gd-topbar [data-testid="stPopover"] button p,
  .st-key-gd-claude [data-testid="stPageLink"] a p {{
    position: absolute !important; width: 1px; height: 1px; overflow: hidden;
    clip: rect(0 0 0 0); white-space: nowrap;
  }}
}}
.st-key-gd-brand span[role="img"] {{
  background: var(--gd-accent); color: var(--gd-ink); border-radius: 9px; padding: 5px; font-size: 1.25rem;
}}
.st-key-gd-brand-name p {{ font-family: var(--gd-display); font-weight: 700; font-size: 1.35rem;
  letter-spacing: .02em; white-space: nowrap; margin: 0; }}
[class*="st-key-gd-pole-"] [data-testid="stPageLink"] a {{
  border-radius: 999px; padding: .45rem 1rem; background: transparent;
}}
[class*="st-key-gd-pole-"] [data-testid="stPageLink"] a p {{ font-weight: 500; color: var(--gd-ink2); font-size: .98rem; }}
[class*="st-key-gd-pole-"][class*="-on"] [data-testid="stPageLink"] a {{ background: var(--gd-ink); }}
[class*="st-key-gd-pole-"][class*="-on"] [data-testid="stPageLink"] a p,
[class*="st-key-gd-pole-"][class*="-on"] [data-testid="stPageLink"] a span {{ color: #fff; font-weight: 600; }}
.st-key-gd-subnav {{ border-bottom: 1px solid var(--gd-line); gap: 1.2rem; margin-bottom: 1rem; }}
.st-key-gd-subnav [data-testid="stPageLink"] a {{ padding: .3rem 0; border-radius: 0; background: transparent; }}
.st-key-gd-subnav [data-testid="stPageLink"] a p {{ color: var(--gd-muted); font-size: .92rem; }}
.st-key-gd-subnav [class*="-on"] [data-testid="stPageLink"] a {{ border-bottom: 2px solid var(--gd-ink); }}
.st-key-gd-subnav [class*="-on"] [data-testid="stPageLink"] a p {{ color: var(--gd-ink); font-weight: 600; }}
.st-key-gd-claude [data-testid="stPageLink"] a {{ background: var(--gd-accent); border-radius: 12px; padding: .45rem .9rem; }}
.st-key-gd-claude [data-testid="stPageLink"] a p {{ color: var(--gd-ink); font-weight: 600; }}

/* Barre d'onglets du bas : téléphone uniquement. */
.st-key-gd-bottomnav {{ display: none; }}
@media (max-width: 640px) {{
  [class*="st-key-gd-pole-"], .st-key-gd-claude, .st-key-gd-brand-name {{ display: none; }}
  .gd-day-full, .gd-day-auj {{ display: none; }}
  .gd-day-short {{ display: inline; }}
  .st-key-gd-bottomnav {{
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
    position: fixed; left: 12px; right: 12px; bottom: 14px; z-index: 999990;
    width: auto !important; box-sizing: border-box;
    background: var(--gd-ink); border-radius: 22px; padding: 6px 4px;
  }}
  .st-key-gd-bottomnav > div {{ width: auto !important; min-width: 0; }}
  /* Le menu « ⋮ » de Streamlit chevaucherait la bascule Light/Pro. */
  [data-testid="stMainMenu"] {{ display: none; }}
  /* Dans les cartes, les chiffres restent deux par ligne au lieu de s'empiler. */
  [class*="st-key-card"] [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; gap: .8rem 1rem; }}
  [class*="st-key-card"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
    flex: 1 1 calc(50% - 1rem) !important; min-width: calc(50% - 1rem) !important; width: auto !important;
  }}
  .st-key-gd-bottomnav [data-testid="stPageLink"] a {{
    flex-direction: column; justify-content: center; gap: 2px; background: transparent; padding: 6px 0;
  }}
  .st-key-gd-bottomnav [data-testid="stPageLink"] a p {{ color: #C9CCD3; font-size: .7rem; }}
  .st-key-gd-bottomnav [data-testid="stPageLink"] a span {{ color: #C9CCD3; }}
  .st-key-gd-bottomnav [class*="-on"] [data-testid="stPageLink"] a p,
  .st-key-gd-bottomnav [class*="-on"] [data-testid="stPageLink"] a span {{ color: var(--gd-accent); font-weight: 600; }}
  .block-container {{ padding-bottom: 110px; }}
  [class*="st-key-card"] {{ padding: 18px; border-radius: 20px; }}
}}
@media (prefers-reduced-motion: no-preference) {{
  .gd-session-number {{ animation: gd-rise .45s cubic-bezier(.2, .9, .3, 1.1) both; }}
}}
@keyframes gd-rise {{ from {{ transform: translateY(8px); opacity: 0; }} to {{ transform: none; opacity: 1; }} }}
</style>
"""


def inject_theme() -> None:
    """Styles communs — une fois par run (le routeur `main.py` s'en charge)."""
    st.html(_CSS)


# ---------------------------------------------------------------------------
# Fragments HTML (tout texte passe par `esc`)
# ---------------------------------------------------------------------------
def chip(text: str, status: str = "neutral") -> str:
    """Pastille d'état ; `status` ∈ good/warning/serious/critical/info/neutral/accent."""
    return f'<span class="gd-chip gd-chip-{status}">{esc(text)}</span>'


def verdict_chip(verdict: dict) -> str:
    return chip("● " + verdict["label"], VERDICT_STATUS.get(verdict.get("level"), "neutral"))


def session_card(*, kicker: str, number: str, unit: str, title: str, target: str = "",
                 when: str = "", why: str = "", tag: str = "",
                 aria_label: str = "Séance du jour") -> str:
    """Carte de la séance : le chiffre qui compte, le titre, la cible et le pourquoi."""
    parts = [
        f'<div role="group" aria-label="{esc(aria_label)}">',
        f'<div class="gd-session-top"><div class="gd-kicker">{esc(kicker)}</div>'
        + (chip(tag, "accent") if tag else "") + "</div>",
        '<div class="gd-session-row">',
        f'<div><span class="gd-session-number">{esc(number)}</span>'
        f'<span class="gd-session-unit">{esc(unit)}</span></div>',
        f'<div><div class="gd-session-title">{esc(title)}</div>'
        + (f'<div class="gd-session-target">{esc(target)}</div>' if target else "")
        + (f'<div class="gd-session-when">{esc(when)}</div>' if when else "")
        + "</div></div>",
        (f'<div class="gd-why"><strong>Pourquoi :</strong> {esc(why)}</div>' if why else ""),
        "</div>",
    ]
    return "".join(parts)


def freshness_bar(tsb: float | None) -> str:
    """
    Jauge de fraîcheur horizontale : trois zones (fatigué / neutre / frais) et
    une aiguille. La valeur est TOUJOURS le vrai TSB : c'est l'échelle qui
    s'élargit pour la contenir, jamais l'aiguille qui se bloque au bord.
    """
    lo = min(-40.0, float(tsb) - 5) if tsb is not None else -40.0
    hi = max(30.0, float(tsb) + 5) if tsb is not None else 30.0
    span = hi - lo
    zones = [(TSB_FATIGUE - lo, ct.STATUS_BG["serious"], "6px 0 0 6px"),
             (TSB_FRESH - TSB_FATIGUE, "#ECE9E1", "0"),
             (hi - TSB_FRESH, ct.STATUS_BG["good"], "0 6px 6px 0")]
    bars = "".join(f'<span style="flex-grow:{w / span:.4f};background:{c};border-radius:{r}"></span>'
                   for w, c, r in zones)
    needle = (f'<span class="gd-needle" style="left:{(float(tsb) - lo) / span * 100:.1f}%"></span>'
              if tsb is not None else "")
    return (f'<div class="gd-track" role="img" aria-label="Fraîcheur '
            f'{"inconnue" if tsb is None else f"{tsb:+.0f}"}">{bars}{needle}</div>'
            '<div class="gd-track-labels"><span>Fatigué</span><span>Neutre</span>'
            '<span>Frais</span></div>')


def tsb_status(tsb: float | None) -> tuple[str, str]:
    """(libellé, état) de la fraîcheur, pour la pastille à côté du chiffre."""
    if tsb is None:
        return "—", "neutral"
    if tsb < TSB_FATIGUE:
        return "Fatigué", "serious"
    if tsb > TSB_FRESH:
        return "Frais", "good"
    return "Neutre", "neutral"


def week_strip(days: list[dict]) -> str:
    """
    Semaine en 7 cases. Chaque jour : {"name", "what", "short", "state",
    "is_today"} avec state ∈ done (fait), today, plan (prévu), rest ; `short`
    remplace `what` sur téléphone.
    """
    cells = []
    for d in days:
        state = d.get("state", "rest")
        mark = '<div class="gd-day-mark">✓ fait</div>' if state == "done" else ""
        today_cls = " gd-day-istoday" if d.get("is_today") else ""
        auj = '<span class="gd-day-auj"> · auj.</span>' if d.get("is_today") else ""
        what = (f'<span class="gd-day-full">{esc(d.get("what", ""))}</span>'
                f'<span class="gd-day-short">{esc(d.get("short", ""))}</span>')
        cells.append(f'<div class="gd-day gd-day-{state}{today_cls}">'
                     f'<div class="gd-day-name">{esc(d["name"])}{auj}</div>'
                     f'<div class="gd-day-what">{what}</div>{mark}</div>')
    return f'<div class="gd-week">{"".join(cells)}</div>'


def signal_card(*, status: str, level: str, title: str, body: str) -> str:
    color = ct.STATUS_TEXT.get(status, ct.INK_SECONDARY)
    return (f'<div class="gd-signal-level" style="color:{color}">● {esc(level)}</div>'
            f'<div class="gd-signal-title">{esc(title)}</div>'
            f'<div class="gd-signal-body">{esc(body)}</div>')


def html_block(markup: str) -> None:
    """Rend un fragment déjà échappé (les helpers ci-dessus)."""
    st.markdown(markup, unsafe_allow_html=True)
