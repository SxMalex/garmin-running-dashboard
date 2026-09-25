"""
Mode d'affichage Light / Pro, partagé par toutes les pages.

- Light : explications pédagogiques des termes techniques (`glossary`).
- Pro : pas d'explications, chiffres bruts, indicateurs avancés et réglages
  des seuils des analyses.

Le mode et les réglages vivent dans des clés `session_state` qui ne sont PAS
des clés de widget : Streamlit efface l'état d'un widget absent d'une page,
ce qui ferait retomber un seuil réglé sur la page 1 à sa valeur par défaut
sur la page 4 (deux verdicts différents pour la même sortie). Les widgets
recopient leur valeur via `on_change` ; tout le code lit par les accesseurs.

Les réglages Pro ne portent que sur les analyses physio : jamais sur
CTL/ATL ni sur l'allure seuil, pour garder un seul TSB dans l'app.
"""

import streamlit as st

from glossary import term

MODE_KEY = "ui_mode_value"
PARAMS_KEY = "pro_params_value"
MODES = {"light": "Light", "pro": "Pro"}

# clé → (libellé, défaut, min, max, pas, aide)
PRO_PARAMS = {
    "lock_tol_bpm": ("Lock : écart FC/cadence max (bpm)", 3.0, 1.0, 6.0, 0.5,
                     "Écart sous lequel FC et cadence sont jugées confondues."),
    "lock_min_duration_s": ("Lock : durée minimale (s)", 120, 60, 300, 30,
                            "Durée d'un plateau FC ≈ cadence pour le signaler."),
    "lock_min_jump_bpm": ("Lock : saut de FC minimal (bpm)", 12.0, 6.0, 20.0, 1.0,
                          "Marche brutale qui distingue un lock d'une vraie montée de FC."),
    "decoupling_warmup_min": ("Dérive : échauffement exclu (min)", 10, 0, 20, 1,
                              "Début de sortie ignoré pour la 1re moitié."),
    "decoupling_min_moving_min": ("Dérive : effort minimal (min)", 40, 30, 90, 5,
                                  "En dessous, la dérive n'a pas le temps d'apparaître."),
    "decoupling_max_speed_cv": ("Dérive : irrégularité d'allure max", 0.15, 0.05, 0.30, 0.01,
                                "Coefficient de variation de la vitesse toléré."),
}


def ui_mode() -> str:
    return st.session_state.get(MODE_KEY, "light")


def is_pro() -> bool:
    return ui_mode() == "pro"


def _copy(widget_key: str, store_key: str, sub: str | None = None) -> None:
    value = st.session_state[widget_key]
    if sub is None:
        st.session_state[store_key] = value
    else:
        st.session_state.setdefault(store_key, {})[sub] = value


def render_mode_toggle() -> None:
    """Bascule Light/Pro dans l'en-tête (le routeur la rend une fois par run)."""
    st.session_state["ui_mode_widget"] = ui_mode()
    st.segmented_control(
        "Mode d'affichage", list(MODES), format_func=MODES.get, required=True,
        key="ui_mode_widget", on_change=_copy, args=("ui_mode_widget", MODE_KEY),
        label_visibility="collapsed",
        help="Light explique chaque indicateur ; Pro affiche les chiffres bruts, les "
             "indicateurs avancés et les réglages des seuils.",
    )


def param(key: str):
    """Valeur d'un réglage : celle de l'utilisateur en Pro, le défaut sinon."""
    default = PRO_PARAMS[key][1]
    if not is_pro():
        return default
    return st.session_state.get(PARAMS_KEY, {}).get(key, default)


def render_pro_settings(keys: list[str], title: str = "🔬 Réglages des analyses") -> None:
    """Curseurs des réglages Pro (barre latérale). Rien en mode Light."""
    if not is_pro():
        return
    with st.sidebar.expander(title):
        for key in keys:
            label, default, lo, hi, step, help_ = PRO_PARAMS[key]
            widget_key = f"pro_widget_{key}"
            st.session_state[widget_key] = param(key)
            st.slider(label, min_value=lo, max_value=hi, step=step, key=widget_key,
                      help=help_, on_change=_copy, args=(widget_key, PARAMS_KEY, key))
        if st.button("Valeurs par défaut", key="pro_reset"):
            for key in keys:
                st.session_state.setdefault(PARAMS_KEY, {}).pop(key, None)
            st.rerun()


def lock_params() -> dict:
    return {"tol_bpm": param("lock_tol_bpm"),
            "min_duration_s": float(param("lock_min_duration_s")),
            "min_jump_bpm": param("lock_min_jump_bpm")}


def decoupling_params() -> dict:
    return {"warmup_s": float(param("decoupling_warmup_min")) * 60,
            "min_moving_s": float(param("decoupling_min_moving_min")) * 60,
            "max_speed_cv": param("decoupling_max_speed_cv")}


def explain(key: str, *, expanded: bool = False) -> None:
    """Encadré pédagogique d'un terme — mode Light uniquement."""
    if is_pro():
        return
    t = term(key)
    with st.expander(f"💡 {t['label']} : c'est quoi ?", expanded=expanded):
        st.write(t["light"])
        if t.get("source"):
            st.caption(f"📚 {t['source']}")


def help_text(key: str) -> str:
    """Aide courte (infobulle `help=`), dans les deux modes."""
    return term(key)["short"]
