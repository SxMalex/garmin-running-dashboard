"""
Identité visuelle « jour de course » : le dossard, la jauge de fraîcheur et
les couloirs de piste.

Principes (ne pas diluer) :
- UNE pièce forte par page : le dossard de la séance du jour. Papier blanc,
  numéro condensé, épingles aux coins, bande haute à la couleur du verdict
  (couleurs de statut : ce sont des ÉTATS). Tout le reste reste sobre.
- Les indicateurs sont séparés par des lignes de couloir (filets verticaux),
  pas enfermés dans des cartes : la piste, pas le kit SaaS.
- Une seule animation, à l'arrivée du dossard ; coupée si l'utilisateur
  demande moins de mouvement.
- Palette : `chart_theme` (validée) ; la police des titres est déclarée dans
  `.streamlit/config.toml` et servie localement (aucun CDN).
"""

from __future__ import annotations

import html
import math

import plotly.graph_objects as go
import streamlit as st

import chart_theme as ct

PAPER = "#f3f2ee"        # dossard : papier, pas blanc pur
PAPER_INK = "#1a1c21"    # encre du dossard
PAPER_MUTED = "#5d616b"
DISPLAY = "'Barlow Condensed', 'Arial Narrow', sans-serif"

# Bande du dossard = état du verdict. Le niveau « normal » est un neutre (le
# bleu est l'entité « allure » dans toute l'app, pas un état).
VERDICT_BAND = {2: ct.GOOD, 1: ct.INK_MUTED, 0: ct.SERIOUS}


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
    """Encre la plus lisible (blanc ou encre du dossard) sur un fond donné."""
    return max(("#ffffff", PAPER_INK), key=lambda ink: contrast(background, ink))


def _one_line(value) -> str:
    """Un champ sur une ligne : une ligne vide fermerait le bloc HTML et la
    suite serait interprétée comme du Markdown (liens, images distantes)."""
    return " ".join(str(value).split())

_CSS = f"""
<style>
/* Couloirs : lignes de piste entre les colonnes des rangées marquées
   (st.container(key="lanes-…")) — explicite, pour ne pas toucher d'autres mises en page. */
[class*="st-key-lanes"] [data-testid="stHorizontalBlock"]
  > [data-testid="stColumn"] + [data-testid="stColumn"] {{
  border-left: 1px solid {ct.BASELINE};
  padding-left: 1rem;
}}
@media (max-width: 640px) {{
  [class*="st-key-lanes"] [data-testid="stHorizontalBlock"]
    > [data-testid="stColumn"] + [data-testid="stColumn"] {{
    border-left: 0; padding-left: 0; border-top: 1px solid {ct.BASELINE}; padding-top: .5rem;
  }}
}}
[data-testid="stMetricValue"] {{
  font-family: {DISPLAY};
  font-weight: 700;
  letter-spacing: .01em;
}}

.gd-bib {{
  position: relative;
  background: {PAPER};
  color: {PAPER_INK};
  border-radius: 6px;
  padding: 0 28px 20px;
  box-shadow: 0 10px 28px rgba(0, 0, 0, .45);
  overflow: hidden;
  animation: gd-bib-drop .55s cubic-bezier(.2, .9, .3, 1.15) both;
}}
.gd-bib::before, .gd-bib::after, .gd-bib .gd-pin-b::before, .gd-bib .gd-pin-b::after {{
  content: ""; position: absolute; width: 9px; height: 9px; border-radius: 50%;
  background: {ct.SURFACE}; box-shadow: inset 0 1px 2px rgba(0,0,0,.6);
}}
.gd-bib::before {{ top: 60px; left: 12px; }}
.gd-bib::after {{ top: 60px; right: 12px; }}
.gd-bib .gd-pin-b::before {{ bottom: 12px; left: 12px; }}
.gd-bib .gd-pin-b::after {{ bottom: 12px; right: 12px; }}
.gd-bib-band {{
  margin: 0 -28px 14px; padding: 6px 28px;
  /* Grand texte au sens WCAG (≥ 18,66 px gras) : la bande reste lisible (AA 3:1)
     sur toutes les couleurs de phase, y compris le magenta à 4,3:1. */
  font-family: {DISPLAY}; font-weight: 700; font-size: 1.25rem; letter-spacing: .02em;
}}
.gd-bib-row {{ display: flex; align-items: flex-end; gap: 18px; flex-wrap: wrap; }}
.gd-bib-number {{
  font-family: {DISPLAY}; font-weight: 700; line-height: .85;
  font-size: clamp(4.2rem, 11vw, 7.5rem); letter-spacing: -.01em;
}}
.gd-bib-unit {{ font-family: {DISPLAY}; font-weight: 600; font-size: 1.6rem; margin-left: 4px; }}
.gd-bib-title {{ font-family: {DISPLAY}; font-weight: 700; font-size: 1.9rem; line-height: 1.05; }}
.gd-bib-target {{ font-size: 1.05rem; color: {PAPER_INK}; margin-top: 4px; }}
.gd-bib-why {{ color: {PAPER_MUTED}; margin-top: 12px; max-width: 62ch; font-size: .98rem; }}
.gd-bib-when {{ color: {PAPER_MUTED}; font-size: .95rem; }}
@keyframes gd-bib-drop {{
  from {{ transform: translateY(-14px) rotate(-1.2deg); opacity: 0; }}
  to   {{ transform: none; opacity: 1; }}
}}
@media (prefers-reduced-motion: reduce) {{ .gd-bib {{ animation: none; }} }}
</style>
"""


def inject_theme() -> None:
    """Styles communs — à appeler une fois par page (require_login le fait)."""
    st.markdown(_CSS, unsafe_allow_html=True)


def bib(*, band_text: str, band_level: int = 1, number: str, unit: str, title: str,
        target: str = "", when: str = "", why: str = "", band_color: str | None = None,
        aria_label: str = "Séance du jour") -> None:
    """
    Le dossard : la séance du jour comme on épingle son dossard. `number` est
    le chiffre qui compte (distance, durée) ; `band_level` le niveau du
    verdict de forme (2 prêt, 1 normal, 0 lève le pied) qui colore la bande.
    """
    def esc(value) -> str:
        return html.escape(_one_line(value))

    # Couleur de statut (verdict) par défaut ; `band_color` pour une identité
    # (ex. la phase du plan sur la page Objectif). L'encre suit le fond (WCAG).
    band = band_color or VERDICT_BAND.get(band_level, ct.INK_MUTED)
    parts = [
        f'<div class="gd-bib" role="group" aria-label="{esc(aria_label)}">',
        f'<div class="gd-bib-band" style="background:{band};color:{ink_on(band)}">'
        f'{esc(band_text)}</div>',
        '<div class="gd-bib-row">',
        f'<div><span class="gd-bib-number">{esc(number)}</span>'
        f'<span class="gd-bib-unit">{esc(unit)}</span></div>',
        f'<div><div class="gd-bib-title">{esc(title)}</div>'
        + (f'<div class="gd-bib-target">{esc(target)}</div>' if target else "")
        + (f'<div class="gd-bib-when">{esc(when)}</div>' if when else "")
        + "</div>",
        "</div>",
        (f'<div class="gd-bib-why">{esc(why)}</div>' if why else ""),
        '<span class="gd-pin-b"></span>',
        "</div>",
    ]
    st.markdown("".join(parts), unsafe_allow_html=True)


def freshness_gauge(tsb: float | None, height: int = 210) -> go.Figure:
    """
    Jauge de fraîcheur (TSB) : zones de statut en fond, aiguille à l'encre.
    La valeur est portée par le chiffre ; les zones ne sont qu'un repère.
    """
    # La valeur affichée est TOUJOURS le vrai TSB : c'est l'échelle qui s'élargit.
    lo = min(-40.0, math.floor(tsb)) if tsb is not None else -40.0
    hi = max(30.0, math.ceil(tsb)) if tsb is not None else 30.0
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=float(tsb) if tsb is not None else None,
        number={"valueformat": "+.0f", "font": {"size": 44, "color": ct.INK,
                                                 "family": DISPLAY}},
        gauge={
            "shape": "angular",
            "axis": {"range": [lo, hi], "tickvals": [-30, -20, -10, 0, 10, 20],
                     "tickcolor": ct.INK_MUTED, "tickfont": {"color": ct.INK_MUTED}},
            "bar": {"color": ct.INK, "thickness": 0.18},
            "bgcolor": ct.SURFACE,
            "borderwidth": 0,
            "steps": [
                {"range": [lo, -20], "color": ct.rgba(ct.SERIOUS, 0.35)},
                {"range": [-20, 5], "color": ct.rgba(ct.INK_MUTED, 0.18)},
                {"range": [5, hi], "color": ct.rgba(ct.GOOD, 0.32)},
            ],
        },
        domain={"x": [0, 1], "y": [0, 1]},
    ))
    fig.update_layout(height=height, margin=dict(l=18, r=18, t=12, b=0),
                      paper_bgcolor="rgba(0,0,0,0)")
    return fig
