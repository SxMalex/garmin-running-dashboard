"""
Thème graphique central — palette validée (méthode dataviz, six checks
exécutés au validateur contre la surface sombre #0e1117, juillet 2026).

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

Importer ce module suffit à activer le template Plotly `gar_dark`.
"""

import plotly.graph_objects as go
import plotly.io as pio

# ---------------------------------------------------------------------------
# Chrome / encre (surface Streamlit sombre #0e1117)
# ---------------------------------------------------------------------------
SURFACE = "#0e1117"
SURFACE_2 = "#161a23"        # cartes, tooltips
INK = "#e6e8ee"              # texte principal
INK_SECONDARY = "#c6c8ce"    # texte des graphes
INK_MUTED = "#8b8f98"        # axes, labels discrets
GRID = "#232833"             # hairline de grille
BASELINE = "#3a3f4a"         # axe / séparateurs

# ---------------------------------------------------------------------------
# Catégoriel — ordre validé (ne pas réordonner sans re-passer le validateur)
# ---------------------------------------------------------------------------
CAT = [
    "#3987e5",  # 1 bleu
    "#008300",  # 2 vert
    "#d55181",  # 3 magenta
    "#c98500",  # 4 jaune
    "#199e70",  # 5 aqua
    "#d95926",  # 6 orange
    "#9085e9",  # 7 violet
    "#e66767",  # 8 rouge
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
GOOD = "#0ca30c"
WARNING = "#fab219"
SERIOUS = "#ec835a"
CRITICAL = "#d03b3b"

# ---------------------------------------------------------------------------
# Séquentiel (bleu, une teinte) — colorscales continues
# ---------------------------------------------------------------------------
SEQ_COLORSCALE = [
    [0.0, "#9ec5f4"], [0.25, "#6da7ec"], [0.5, "#3987e5"],
    [0.75, "#256abf"], [1.0, "#104281"],
]
# Calendrier/heatmap sur fond sombre : le zéro recule vers la surface
SEQ_DARK_SCALE = ["#151b26", "#104281", "#1c5cab", "#3987e5", "#6da7ec", "#9ec5f4"]

# ---------------------------------------------------------------------------
# Zones FC
# ---------------------------------------------------------------------------
# Marques catégorielles (camembert) : rampe ordinale bleue validée (--ordinal)
ZONE_RAMP = ["#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]
# Teinte redondante seulement (bandes de fond, barres à valeur sur axe) :
# convention cardio bleu→rouge — ne passe pas le plancher vision-normale,
# donc jamais seule porteuse de l'identité.
ZONE_HEAT = ["#3987e5", "#199e70", "#c98500", "#d95926", "#e66767"]

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

pio.templates["gar_dark"] = go.layout.Template(
    layout=dict(
        colorway=CAT,
        font=dict(
            family='system-ui, -apple-system, "Segoe UI", sans-serif',
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
pio.templates.default = "gar_dark"
