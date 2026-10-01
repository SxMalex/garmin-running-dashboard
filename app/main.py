"""
Routeur du dashboard (point d'entrée Streamlit).

- Non connecté : une seule page, le formulaire de connexion Garmin.
- Connecté : les pages en 4 pôles (`nav.POLES`), l'en-tête commun et, sur
  téléphone, la barre d'onglets du bas. Le thème est injecté ici, une fois
  par run, pour toutes les pages.
"""

import os

import streamlit as st

from formatting import md_escape
from garmin_client import (
    end_session,
    complete_mfa,
    login_with_credentials,
)
from nav import build_pages, render_header
from ui_helpers import drop_session, get_session_api, refresh_data, store_session
from ui_theme import inject_theme

st.set_page_config(
    page_title="Running Dashboard",
    page_icon=":material/directions_run:",
    layout="wide",
    # "auto" : ouverte sur ordinateur, repliée sur téléphone (sinon elle couvre la page).
    initial_sidebar_state="auto",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": "Tableau de bord Running — Données Garmin Connect + IA Coach",
    },
)
inject_theme()


def login_page() -> None:
    """Formulaire de connexion Garmin (et étape MFA)."""
    st.markdown("""
    <div style="text-align:center; padding: 56px 24px 24px;">
        <div class="gd-kicker">Garmin Connect</div>
        <h1 class="gd-headline">Running Dashboard</h1>
        <p style="margin: 0; color: #62666F; font-size: 1.1rem;">
            Connecte ton compte Garmin pour accéder à ton tableau de bord
        </p>
    </div>
    """, unsafe_allow_html=True)
    # Seule page qui demande des identifiants Garmin : dire clairement qui l'on est.
    st.caption(
        "Projet open source **non affilié à Garmin** et non approuvé par Garmin. "
        "Ton mot de passe n'est envoyé qu'à Garmin Connect (via la bibliothèque non "
        "officielle `garminconnect`) ; seuls les jetons de session sont gardés sur ce serveur.",
    )

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
                    st.error(f"Code refusé : {md_escape(e)}")
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
                        st.error(f"Connexion refusée : {md_escape(e)}")

        st.caption(
            "La session est mémorisée sur le serveur (tokens valides ~1 an) : "
            "la prochaine ouverture de l'app se connectera automatiquement."
        )


def _logout() -> None:
    # Pour tous les onglets : la session partagée est neutralisée (plus de
    # réécriture du tokenstore au prochain rafraîchissement) puis effacée.
    end_session()
    drop_session()
    st.rerun()


if get_session_api() is None:
    st.navigation([st.Page(login_page, title="Connexion", icon=":material/login:")],
                  position="hidden").run()
    st.stop()

_sections, _index = build_pages()
_page = st.navigation(_sections, position="hidden")
render_header(_page.title, _index, on_refresh=refresh_data, on_logout=_logout)
_page.run()
