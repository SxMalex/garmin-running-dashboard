"""
Logique pure de la page Forme & Récupération — verdict croisé charge/récup
et modulation de la recommandation de séance. Testable sans Streamlit.
"""

# Statuts HRV Garmin considérés comme dégradés (sous ou hors baseline)
_HRV_DEGRADED = {"UNBALANCED", "LOW", "POOR"}

# Seuils de fraîcheur (TSB) — UNE définition pour le verdict, la jauge de
# l'Accueil, les métriques de Forme / Prochaine sortie et le glossaire.
TSB_FRESH = 5.0      # au-dessus : frais
TSB_FATIGUE = -20.0  # en dessous : fatigue accumulée
# Statuts HRV Garmin → libellé français. « NONE » (pas encore de baseline, les
# premières semaines ou après une coupure) n'est PAS un statut : parse_recovery
# le ramène à None, sinon il passait pour « HRV dans ta baseline ».
HRV_LABELS = {"BALANCED": "équilibrée", "UNBALANCED": "déséquilibrée", "LOW": "basse",
              "POOR": "faible"}


def hrv_label(hrv_status: str | None) -> str | None:
    """Libellé français d'un statut HRV Garmin (None si pas de statut)."""
    if not hrv_status:
        return None
    return HRV_LABELS.get(hrv_status.upper(), hrv_status.lower())

# Rétrogradation d'un cran d'une séance (utilisée quand la récup est mauvaise)
_SESSION_DOWNGRADE = {
    "sortie_longue": "endurance",
    "tempo": "endurance",
    "endurance": "recuperation",
    "recuperation": "recuperation",
}

VERDICT_LEVELS = {
    2: {
        "key": "performance",
        "label": "Prêt à performer",
        "icon": "🟢",
        "headline": "Charge absorbée et récupération au vert : c'est le moment de pousser.",
    },
    1: {
        "key": "normal",
        "label": "Entraînement normal",
        "icon": "🔵",
        "headline": "Rien à signaler : déroule ton plan habituel.",
    },
    0: {
        "key": "recuperation",
        "label": "Lève le pied",
        "icon": "🟠",
        "headline": "Fatigue ou récupération dégradée : privilégie une séance légère.",
    },
}


def hrv_is_degraded(hrv_status: str | None) -> bool:
    """True si le statut HRV Garmin signale une récupération dégradée."""
    return bool(hrv_status) and hrv_status.upper() in _HRV_DEGRADED


def sleep_quality(sleep_score) -> str | None:
    """Classe un score de sommeil Garmin : good (≥75), medium (≥60), poor (<60)."""
    if sleep_score is None:
        return None
    if sleep_score >= 75:
        return "good"
    if sleep_score >= 60:
        return "medium"
    return "poor"


def compute_forme_verdict(
    tsb: float | None,
    hrv_status: str | None,
    sleep_score=None,
) -> dict:
    """
    Verdict croisé charge (TSB) × récupération (HRV, sommeil).

    Base sur le TSB : > 5 frais (2), < -20 fatigué (0), sinon normal (1).
    Chaque signal de récupération dégradé (HRV hors baseline, sommeil < 60)
    descend d'un niveau. Retourne le dict de VERDICT_LEVELS enrichi de
    `level` et `reasons` (une phrase par facteur pris en compte).
    """
    reasons = []

    if tsb is None:
        base = 1
    elif tsb > TSB_FRESH:
        base = 2
        reasons.append(f"TSB {tsb:+.0f} : tu es frais")
    elif tsb < TSB_FATIGUE:
        base = 0
        reasons.append(f"TSB {tsb:+.0f} : charge récente élevée")
    else:
        base = 1
        reasons.append(f"TSB {tsb:+.0f} : charge normale")

    penalty = 0
    if hrv_is_degraded(hrv_status):
        penalty += 1
        reasons.append(f"HRV {hrv_label(hrv_status)} : récupération en retrait")
    elif hrv_status:
        reasons.append("HRV dans ta baseline")

    quality = sleep_quality(sleep_score)
    if quality == "poor":
        penalty += 1
        reasons.append(f"Sommeil dégradé (score {sleep_score})")
    elif quality == "good":
        reasons.append(f"Bon sommeil (score {sleep_score})")

    level = max(0, min(2, base - penalty))
    verdict = dict(VERDICT_LEVELS[level])
    verdict["level"] = level
    verdict["reasons"] = reasons
    return verdict


def forme_downgrade(hrv_status: str | None, sleep_score=None) -> int:
    """
    Nombre de crans à rétrograder la séance recommandée selon la récupération
    (0 = forme OK, 1 = un signal dégradé, 2 = HRV et sommeil dégradés).
    """
    return int(hrv_is_degraded(hrv_status)) + int(sleep_quality(sleep_score) == "poor")


def downgrade_session(session_key: str, steps: int = 1) -> str:
    """Rétrograde une séance de `steps` crans (tempo → endurance → récup)."""
    key = session_key
    for _ in range(max(0, steps)):
        key = _SESSION_DOWNGRADE.get(key, key)
    return key


def _first_dict(raw) -> dict:
    """Garmin renvoie selon les endpoints un dict ou une liste d'un dict."""
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    return raw if isinstance(raw, dict) else {}


def parse_recovery(hrv_raw, sleep_raw, daily_raw=None) -> dict:
    """
    Récupération du jour à partir des réponses brutes Garmin (HRV, sommeil,
    stats quotidiennes). Point unique de lecture de ces payloads : Accueil,
    Forme, Prochaine sortie et serveur MCP y lisent les mêmes champs.
    """
    hrv_summary = _first_dict(hrv_raw).get("hrvSummary") or {}
    sleep_dto = _first_dict(sleep_raw).get("dailySleepDTO") or {}
    return {
        "hrv_summary": hrv_summary,
        # « NONE » = pas de baseline : aucun statut, pas un statut « normal ».
        "hrv_status": (hrv_summary.get("status")
                       if str(hrv_summary.get("status") or "").upper() not in ("", "NONE")
                       else None),
        "hrv_last": hrv_summary.get("lastNightAvg"),
        "sleep_dto": sleep_dto,
        "sleep_sec": sleep_dto.get("sleepTimeSeconds"),
        "sleep_score": ((sleep_dto.get("sleepScores") or {}).get("overall") or {}).get("value"),
        "daily": _first_dict(daily_raw),
    }
