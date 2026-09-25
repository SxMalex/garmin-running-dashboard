"""
Thème graphique central — système « Piste claire » (septembre 2026).

Palette dérivée de celle validée en juillet 2026 (méthode dataviz, surface
sombre) : mêmes teintes, même ordre, luminosité baissée juste assez pour
tenir 3:1 contre le fond papier #F5F4EF (marques graphiques, WCAG 1.4.11) —
garde-fou : `tests_ui/test_theme_ui.py`.

Règles :
- l'ordre catégoriel CAT est le mécanisme de sécurité daltonisme : ne jamais
  recycler les teintes ni générer une 9e couleur ;
- la couleur suit l'entité (allure = bleu partout), jamais son rang ;
- les couleurs de statut (GOOD/WARNING/SERIOUS/CRITICAL) sont réservées aux
  états et deltas, jamais utilisées comme série ;
- séquentiel = une seule teinte (bleu), du clair au foncé ;
- ZONE_RAMP pour les zones FC en marques catégorielles (camembert) ;
  ZONE_HEAT (convention cardio bleu→rouge) uniquement en teinte redondante
  (bandes de fond, barres dont la valeur est déjà portée par l'axe).

Importer ce module suffit à activer le template Plotly `gar`.
"""

import plotly.graph_objects as go
import plotly.io as pio

# ---------------------------------------------------------------------------
# Chrome / encre (fond papier, cartes blanches)
# ---------------------------------------------------------------------------
SURFACE = "#F5F4EF"          # fond de page (papier)
SURFACE_2 = "#FFFFFF"        # cartes, tooltips
INK = "#15171C"              # texte principal, boutons principaux
INK_SECONDARY = "#3D4048"    # texte des graphes
INK_MUTED = "#62666F"        # axes, labels discrets (5,6:1 sur papier)
GRID = "#ECE9E1"             # hairline de grille
BASELINE = "#D6D2C7"         # axe / séparateurs
LINE = "#E4E1D8"             # bordure des cartes
ACCENT = "#D2F53C"           # accent « volt » : APLAT seulement, jamais du texte sur clair

# ---------------------------------------------------------------------------
# Catégoriel — ordre validé (ne pas réordonner sans re-passer le validateur)
# ---------------------------------------------------------------------------
CAT = [
    "#3987e5",  # 1 bleu
    "#008300",  # 2 vert
    "#d55181",  # 3 magenta
    "#ba7b00",  # 4 jaune (assombri pour le fond clair)
    "#189a6d",  # 5 aqua
    "#d95926",  # 6 orange
    "#867ae7",  # 7 violet
    "#e45a5a",  # 8 rouge
]
BLUE, GREEN, MAGENTA, YELLOW, AQUA, ORANGE, VIOLET, RED = CAT

# Alias sémantiques — la couleur suit l'entité, partout dans l'app
PACE = BLUE          # allure
HR = RED             # fréquence cardiaque
HR_MAX = ORANGE      # FC max (marqueurs secondaires)
ALTITUDE = VIOLET    # profil altimétrique dans les panneaux multi-streams
CADENCE = AQUA
VOLUME = BLUE
CTL, ATL, TSB = BLUE, ORANGE, AQUA   # charge d'entraînement (3 séries)

# ---------------------------------------------------------------------------
# Statut — réservé aux états/deltas, jamais une série
# ---------------------------------------------------------------------------
GOOD = "#0c9e0c"
WARNING = "#b67d04"
SERIOUS = "#e65b25"
CRITICAL = "#d03b3b"

# Texte et pastilles d'état sur fond clair (4,5:1 minimum sur leur fond pâle)
STATUS_TEXT = {"good": "#13622E", "warning": "#8A4B00", "serious": "#A93A0B",
               "critical": "#A42424", "info": "#1F4FA3"}
STATUS_BG = {"good": "#E3F4E7", "warning": "#FFF4E5", "serious": "#FCE8DF",
             "critical": "#FBE3E3", "info": "#E6EEFB"}

# ---------------------------------------------------------------------------
# Séquentiel (bleu, une teinte) — colorscales continues
# ---------------------------------------------------------------------------
SEQ_COLORSCALE = [
    [0.0, "#9ec5f4"], [0.25, "#6da7ec"], [0.5, "#3987e5"],
    [0.75, "#256abf"], [1.0, "#104281"],
]
# Calendrier/heatmap : le zéro recule vers le fond papier
SEQ_SURFACE_SCALE = ["#EEF1F5", "#C9DBF5", "#9ec5f4", "#6da7ec", "#3987e5", "#104281"]

# ---------------------------------------------------------------------------
# Zones FC
# ---------------------------------------------------------------------------
# Marques catégorielles (camembert) : rampe ordinale bleue validée (--ordinal)
ZONE_RAMP = ["#3987e5", "#2a70cc", "#1f5aae", "#184f95", "#0f3566"]   # ≥ 3:1 sur papier
# Teinte redondante seulement (bandes de fond, barres à valeur sur axe) :
# convention cardio bleu→rouge — ne passe pas le plancher vision-normale,
# donc jamais seule porteuse de l'identité.
ZONE_HEAT = [BLUE, AQUA, YELLOW, ORANGE, RED]   # mêmes teintes que CAT, lisibles sur papier

# Phases de sommeil : du plus profond (foncé) au plus léger, éveil à part.
SLEEP_DEEP, SLEEP_LIGHT, SLEEP_REM, SLEEP_AWAKE = "#184f95", BLUE, VIOLET, RED

# Types de sortie (eventType Garmin) — sous-ensemble de CAT, ordre d'affichage
# alphabétique validé
WORKOUT_COLORS = {
    "Normal": BLUE,
    "Race": RED,
    "Sortie longue": AQUA,
    "Entraînement": YELLOW,
}


def rgba(hex_color: str, alpha: float) -> str:
    """Convertit un token hex en chaîne CSS rgba(r,g,b,a)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ---------------------------------------------------------------------------
# Template Plotly — appliqué par défaut dès l'import de ce module
# ---------------------------------------------------------------------------
_axis = dict(
    gridcolor=GRID,
    linecolor=BASELINE,
    zeroline=False,
    tickfont=dict(color=INK_MUTED, size=11),
    title=dict(font=dict(color=INK_MUTED, size=11)),
)

pio.templates["gar"] = go.layout.Template(
    layout=dict(
        colorway=CAT,
        font=dict(
            family='Barlow, system-ui, -apple-system, "Segoe UI", sans-serif',
            color=INK_SECONDARY,
            size=12,
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        barcornerradius=4,
        hoverlabel=dict(
            bgcolor=SURFACE_2,
            bordercolor=BASELINE,
            font=dict(color=INK, size=12),
        ),
        xaxis=_axis,
        yaxis=_axis,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            font=dict(color=INK_SECONDARY, size=11),
        ),
        margin=dict(l=0, r=0, t=30, b=0),
    )
)
pio.templates.default = "gar"
