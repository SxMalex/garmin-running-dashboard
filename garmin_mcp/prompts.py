"""
Prompts MCP : des commandes prêtes à l'emploi dans Claude Desktop / Claude Code
(« /bilan_semaine », « /prepa_course »…), remplies avec la situation réelle de
l'athlète au moment où on les lance.

Ils sont **adaptatifs** : avant de rédiger la consigne, `situation()` regarde si
un plan Garmin Run Coach pilote la montre (alors Claude ne doit rien réécrire),
si un plan Objectif est validé dans le dashboard, et ce que dit la veille
santé. Les builders sont des fonctions pures (situation → texte), testées sans
serveur ; `register()` les branche sur FastMCP.

Règle commune : Claude va chercher les chiffres avec les outils du serveur
(mêmes calculs que le dashboard), ne les invente pas, et ne pose pas de
diagnostic médical.
"""

from __future__ import annotations

from datetime import date

NIVEAUX = {
    "debutant": "Explique chaque terme technique (TSB, HRV, dérive…) en une phrase simple, "
                "sans jargon non expliqué. Donne au plus 3 conseils concrets.",
    "confirme": "Tu peux utiliser le vocabulaire technique (CTL/ATL/TSB, IF, Pa:HR, ACWR) sans "
                "le définir. Chiffre tes affirmations et signale les limites des indicateurs.",
}

_COMMUN = (
    "Règles : va chercher les données avec les outils du serveur MCP Garmin (ils "
    "reprennent exactement les calculs du dashboard) et n'invente aucun chiffre ; si un outil "
    "ne renvoie rien, dis-le. Tu n'es pas médecin : en cas de signe de maladie ou de douleur, "
    "conseille de lever le pied et, si ça dure, de consulter. Réponds en français."
)


def situation(gc, today: date | None = None) -> dict:
    """
    Photo de la situation qui change la consigne : plan Run Coach actif, plan
    Objectif du dashboard, niveau de la veille santé. Chaque lecture est
    tolérante : une erreur Garmin donne « inconnu », jamais un prompt en échec.
    """
    import goal_store
    from coach_logic import load_coach_context

    today = today or date.today()
    out = {"coach": None, "goal": None, "health": None}
    try:
        # Lecture stricte d'abord (comme la garde d'écriture de la page Objectif) :
        # load_coach_context avale les erreurs et répondrait « pas de plan » pendant
        # une panne — le prompt proposerait alors de réécrire les séances de Run Coach.
        gc.get_training_plans(strict=True)
        ctx = load_coach_context(gc, today)
        if ctx:
            out["coach"] = {"name": ctx["plan"]["name"], "days_to_event": ctx.get("days_to_event")}
    except Exception:
        out["coach"] = "inconnu"
    try:
        if not getattr(gc, "athlete_id_reliable", True):
            raise LookupError      # id de repli : l'objectif serait lu dans un autre dossier
        doc = goal_store.load(gc.athlete_id)
        goal = doc.get("goal")
        if goal:
            # Même chemin que l'Accueil : un plan dont la course est passée (ou sans
            # séances figées) n'est plus « ce que la montre reçoit ».
            validated = goal_store.validated_sessions(gc.athlete_id, today) is not None
            out["goal"] = {"distance": goal["distance"], "race_date": goal["race_date"],
                           "target": goal.get("target_text") or "", "validated": validated,
                           "past": goal["race_date"] < today.isoformat()}
    except LookupError:
        out["goal"] = "inconnu"
    except Exception:
        pass
    try:
        import insights
        hw = insights.health_watch(gc, today)
        if hw.get("available"):
            out["health"] = {"level": hw["level"], "title": hw["title"]}
    except Exception:
        pass
    return out


def _contexte(sit: dict) -> str:
    """Paragraphe « ce qu'il faut savoir avant de répondre », selon la situation."""
    lines = []
    coach, goal, health = sit.get("coach"), sit.get("goal"), sit.get("health")
    if isinstance(coach, dict):
        j = f", course dans {coach['days_to_event']} jours" if coach.get("days_to_event") is not None else ""
        lines.append(f"- Un plan Garmin Run Coach est actif (« {coach['name']} »{j}) : c'est lui qui "
                     "pilote la montre. Ne propose PAS de nouvelles séances ni de réécrire les siennes ; "
                     "commente-les et dis comment les aborder.")
    elif coach == "inconnu":
        lines.append("- L'état du plan Garmin Run Coach est inconnu (Garmin n'a pas répondu) : "
                     "sois prudent avant de proposer des changements de séances.")
    if goal == "inconnu":
        lines.append("- Objectif du dashboard non lu : compte Garmin non confirmé (réessaie dans une "
                     "minute). Outil : current_goal.")
    elif goal:
        state = ("terminé (la date de course est passée)" if goal.get("past") else
                 "validé (c'est ce plan que la montre reçoit)" if goal["validated"] else "pas encore validé")
        target = f", objectif {goal['target']}" if goal["target"] else ""
        lines.append(f"- Objectif enregistré : {goal['distance']} le {goal['race_date']}{target}, plan {state}. "
                     "Outil : current_goal.")
    else:
        lines.append("- Aucun objectif de course enregistré dans le dashboard.")
    if health and health["level"] >= 1:
        lines.append(f"- Veille santé : « {health['title']} » (niveau {health['level']}/2). Commence par "
                     "ça (outil health_watch) : la récupération passe avant la performance.")
    return "Contexte actuel :\n" + "\n".join(lines)


def _niveau(niveau: str) -> str:
    return NIVEAUX.get((niveau or "debutant").lower(), NIVEAUX["debutant"])


def bilan_semaine(sit: dict, niveau: str = "debutant") -> str:
    return "\n\n".join([
        "Fais-moi le bilan de ma semaine d'entraînement, comme un coach qui me connaît.",
        _contexte(sit),
        "Données à récupérer : daily_briefing, training_load (days=14), aerobic_trend, "
        "health_watch" + (", current_goal" if sit.get("goal") else "") + ".",
        "Structure : 1) ce qui s'est bien passé, chiffres à l'appui ; 2) ce qui m'inquiète "
        "(charge qui monte trop vite, monotonie, trop peu de facile, signal de santé) ; "
        "3) la semaine prochaine en 3 lignes. Termine par UNE question pour me faire réfléchir.",
        _niveau(niveau), _COMMUN,
    ])


def pourquoi_fatigue(sit: dict) -> str:
    return "\n\n".join([
        "Je me sens fatigué. Aide-moi à comprendre pourquoi, en classant les causes probables "
        "de la plus à la moins vraisemblable.",
        _contexte(sit),
        "Examine dans cet ordre : health_watch (maladie qui couve ?), daily_briefing (HRV, "
        "sommeil, fraîcheur TSB), training_load (days=28 : pic de charge, ACWR, monotonie), "
        "aerobic_trend (efficacité qui baisse ?).",
        "Pour chaque cause : le chiffre qui la soutient, ce qui la contredit, et ce que je peux "
        "faire dès aujourd'hui. Si rien dans les données n'explique la fatigue, dis-le "
        "franchement et évoque ce qu'elles ne voient pas (stress, alimentation, chaleur).",
        NIVEAUX["debutant"], _COMMUN,
    ])


def prepa_course(sit: dict, distance: str, date_course: str, temps_vise: str = "") -> str:
    target = f", avec un objectif de {temps_vise}" if temps_vise else ""
    coach_active = isinstance(sit.get("coach"), dict)
    todo = ("Commente la préparation que Run Coach m'a construite : est-elle cohérente avec ma "
            "forme actuelle ? Ne propose pas de plan concurrent."
            if coach_active else
            f"Appelle race_plan_preview(distance=« {distance} », race_date={date_course}"
            + (f", target_time=« {temps_vise} »" if temps_vise else "") + ") et critique ce plan : "
            "volume de départ réaliste ? séances clés bien placées ? renforcement suffisant ? "
            "objectif atteignable vu mes prédictions actuelles ?")
    return "\n\n".join([
        f"Je prépare un {distance} le {date_course}{target}.",
        _contexte(sit), todo,
        "Dis-moi aussi les 2 risques principaux de cette préparation pour MOI (vu ma charge, mon "
        "historique, ma répartition facile/dur) et comment les éviter. Rappelle que le plan ne part "
        "sur la montre qu'une fois validé dans la page Objectif du dashboard.",
        NIVEAUX["confirme"], _COMMUN,
    ])


def debrief(sit: dict, activity_id: str = "") -> str:
    which = (f"la sortie {activity_id}" if activity_id else
             "ma dernière course à pied (trouve son identifiant avec garmin_call(\"get_activities\", "
             "{\"start\": 0, \"limit\": 5}))")
    return "\n\n".join([
        f"Débriefe {which} comme le ferait un entraîneur après la séance.",
        _contexte(sit),
        "Utilise activity_analysis (qualité du signal cardio, dérive) et daily_briefing (était-ce "
        "la séance prévue ?). Si le capteur optique a décroché (FC calée sur la cadence), écarte "
        "les conclusions tirées de la FC.",
        "Structure : ce que la séance a travaillé, si l'intensité était la bonne pour son objectif, "
        "un point positif, un point à corriger, et quoi faire à la prochaine sortie.",
        NIVEAUX["debutant"], _COMMUN,
    ])


def ajuste_plan(sit: dict, contrainte: str) -> str:
    coach_active = isinstance(sit.get("coach"), dict)
    if coach_active:
        todo = ("Run Coach pilote la montre : explique-moi comment signaler cette contrainte à Garmin "
                "(jours d'entraînement, pause du plan) et comment adapter les séances sans casser la "
                "logique du plan. Ne réécris pas les séances toi-même.")
    elif sit.get("goal"):
        todo = ("Appelle current_goal, puis propose une version ajustée des semaines concernées : "
                "garde les séances clés, déplace ou réduis le reste, respecte 48 h entre deux séances "
                "dures et pas de renfo la veille d'une séance clé. Dis ce qui change et pourquoi. "
                "Rappelle qu'il faudra régler les préférences dans la page Objectif et revalider.")
    else:
        todo = ("Je n'ai pas encore de plan : propose une organisation de semaine qui tient compte de "
                "la contrainte, à partir de daily_briefing et training_load (days=28).")
    return "\n\n".join([
        f"Ma contrainte : {contrainte}. Aide-moi à adapter mon entraînement.",
        _contexte(sit), todo, NIVEAUX["debutant"], _COMMUN,
    ])


def seance_du_jour(sit: dict) -> str:
    return "\n\n".join([
        "Est-ce que je cours aujourd'hui, et quoi ? Réponds d'abord en une phrase (oui / léger / "
        "repos), puis justifie.",
        _contexte(sit),
        "Appelle daily_briefing et health_watch. Si la veille santé signale 2 signaux concordants, "
        "le repos prime sur le plan. Sinon, décris la séance prévue et comment la courir (allure, "
        "FC, sensations) vu ma récupération de la nuit.",
        NIVEAUX["debutant"], _COMMUN,
    ])


UNKNOWN = {"coach": "inconnu", "goal": None, "health": None}


def safe_situation(get_gc) -> dict:
    """Situation, ou « inconnue » si la connexion Garmin échoue : le prompt doit
    toujours s'afficher (les outils expliqueront l'erreur à Claude)."""
    try:
        return situation(get_gc())
    except Exception:
        return dict(UNKNOWN)


def register(mcp, get_gc) -> None:
    """Déclare les prompts sur le serveur FastMCP (situation lue à chaque appel)."""

    @mcp.prompt(name="bilan_semaine", title="Bilan de ma semaine")
    def bilan_semaine_prompt(niveau: str = "debutant") -> str:
        """Bilan de la semaine d'entraînement (niveau : « debutant » ou « confirme »)."""
        return bilan_semaine(safe_situation(get_gc), niveau)

    @mcp.prompt(name="pourquoi_fatigue", title="Pourquoi je suis fatigué ?")
    def pourquoi_fatigue_prompt() -> str:
        """Causes probables de la fatigue, classées, à partir de la veille santé et de la charge."""
        return pourquoi_fatigue(safe_situation(get_gc))

    @mcp.prompt(name="prepa_course", title="Préparer une course")
    def prepa_course_prompt(distance: str, date_course: str, temps_vise: str = "") -> str:
        """Préparation d'une course (distance « 10 km », « Semi-marathon »… ; date AAAA-MM-JJ)."""
        return prepa_course(safe_situation(get_gc), distance, date_course, temps_vise)

    @mcp.prompt(name="debrief", title="Débrief d'une sortie")
    def debrief_prompt(activity_id: str = "") -> str:
        """Débrief d'une sortie (la dernière course si aucun identifiant n'est donné)."""
        return debrief(safe_situation(get_gc), activity_id)

    @mcp.prompt(name="ajuste_plan", title="Ajuster mon plan")
    def ajuste_plan_prompt(contrainte: str) -> str:
        """Adapter l'entraînement à une contrainte (« vacances du 3 au 10 », « genou sensible »…)."""
        return ajuste_plan(safe_situation(get_gc), contrainte)

    @mcp.prompt(name="seance_du_jour", title="Je cours quoi aujourd'hui ?")
    def seance_du_jour_prompt() -> str:
        """Décision du jour : courir, courir léger ou se reposer, et comment."""
        return seance_du_jour(safe_situation(get_gc))
