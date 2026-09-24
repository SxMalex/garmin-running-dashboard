"""
Glossaire du mode Light : ce que veut dire chaque terme technique, et
surtout pourquoi il compte pour un coureur. Données pures (pas de Streamlit).

Règle d'écriture : une phrase « c'est quoi », une phrase « pourquoi ça
compte », une phrase « comment le lire ». Rester honnête sur les limites
(ex. l'ACWR est un indicateur discuté).
"""

TERMS = {
    "ctl": {
        "label": "Forme (CTL)",
        "short": "Charge moyenne des 6 dernières semaines.",
        "light": "La CTL résume ce que ton corps a encaissé ces ~6 semaines : c'est ta forme "
                 "de fond. Elle monte lentement quand tu t'entraînes régulièrement et baisse "
                 "doucement au repos. Une CTL qui grimpe progressivement = tu construis.",
    },
    "atl": {
        "label": "Fatigue (ATL)",
        "short": "Charge moyenne des 7 derniers jours.",
        "light": "L'ATL mesure la fatigue récente : elle réagit vite à une grosse semaine. "
                 "C'est normal qu'elle dépasse la CTL pendant un bloc d'entraînement.",
    },
    "tsb": {
        "label": "Fraîcheur (TSB)",
        "short": "Forme − fatigue (CTL − ATL).",
        "light": "La TSB, c'est l'écart entre ta forme de fond et ta fatigue récente. Négative : "
                 "tu es en train de charger (normal en préparation). Autour de 0 à +10 : "
                 "frais, bon moment pour une course. Sous −20 : attention à l'accumulation.",
    },
    "tss": {
        "label": "Charge d'une séance (TSS)",
        "short": "Durée × intensité² d'une séance.",
        "light": "Chaque séance reçoit un score : une heure à ton allure seuil vaut 100. Une "
                 "heure facile vaut beaucoup moins, un fractionné intense un peu plus. C'est "
                 "l'unité qui alimente CTL, ATL et TSB.",
    },
    "hrv": {
        "label": "Variabilité cardiaque (HRV)",
        "short": "Irrégularité naturelle entre deux battements, mesurée la nuit.",
        "light": "Un cœur reposé varie beaucoup d'un battement à l'autre ; stressé ou fatigué, "
                 "il devient régulier. Garmin la compare à TA normale : « équilibrée » = "
                 "récupéré, « basse » plusieurs jours = lève le pied.",
    },
    "decoupling": {
        "label": "Dérive cardiaque",
        "short": "Hausse de la FC en fin de sortie à allure égale.",
        "light": "Sur une sortie longue et régulière, si ton cœur doit battre de plus en plus "
                 "vite pour la même allure, c'est que l'endurance fatigue. Moins de 5 % : "
                 "endurance solide. Plus de 10 % : c'est là que tu as le plus à gagner, avec "
                 "des sorties longues faciles. Elle ne se mesure que si l'allure reste "
                 "constante (ni fractionné, ni accélération finale).",
        "source": "Friel / TrainingPeaks",
    },
    "ef": {
        "label": "Efficacité aérobie",
        "short": "Mètres parcourus par minute pour chaque battement.",
        "light": "Diviser ta vitesse par ta FC donne ton efficacité : combien de mètres ton "
                 "cœur « achète » à chaque battement. Si elle monte au fil des semaines, tu "
                 "vas plus vite pour le même effort — c'est le signe le plus fiable de "
                 "progrès en endurance.",
    },
    "cadence_lock": {
        "label": "FC calée sur la cadence",
        "short": "Le capteur au poignet confond pouls et foulée.",
        "light": "Le capteur optique lit le pouls à travers la peau ; le balancement des bras "
                 "au rythme de la foulée peut le tromper, et la FC affichée recopie alors ta "
                 "cadence (~170-180). On le repère quand la FC saute d'un coup au niveau de "
                 "la cadence sans que l'effort change. Bracelet serré ou ceinture cardio.",
    },
    "vo2max": {
        "label": "VO2max",
        "short": "Consommation maximale d'oxygène estimée par la montre.",
        "light": "C'est la taille de ton moteur aérobie. Le fractionné VMA l'élève ; la montre "
                 "l'estime à partir de l'allure et de la FC, donc elle fluctue avec la "
                 "chaleur ou la fatigue — regarde la tendance, pas un jour isolé.",
    },
    "seuil": {
        "label": "Allure seuil",
        "short": "Allure tenable environ une heure.",
        "light": "Au-dessous, l'effort est durable ; au-dessus, la fatigue s'accumule vite. "
                 "Travailler juste sous ce seuil repousse la limite : ton allure de 10 km et "
                 "de semi devient plus facile.",
    },
    "acwr": {
        "label": "Ratio charge aiguë / chronique (ACWR)",
        "short": "Charge des 7 derniers jours ÷ moyenne des 28.",
        "light": "Compare ta semaine à ton habitude du mois. Autour de 0,8-1,3 : progression "
                 "raisonnable. Au-delà de 1,5 : hausse brutale, associée à plus de blessures "
                 "dans certaines études. Indicateur discuté (Impellizzeri 2020) : c'est un "
                 "signal d'alerte, pas une prédiction.",
        "source": "Gabbett 2016 ; critique Impellizzeri 2020",
    },
    "monotony": {
        "label": "Monotonie",
        "short": "Charge moyenne ÷ écart-type sur 7 jours.",
        "light": "Une semaine où tous les jours se ressemblent (pas de vrai repos, pas de "
                 "vraie séance dure) fatigue plus qu'il n'y paraît. Au-dessus de 2, alterne "
                 "davantage jours durs et jours faciles.",
        "source": "Foster 1998",
    },
    "strength": {
        "label": "Renforcement musculaire",
        "short": "Charges lourdes et exercices ciblés, 1 à 2 fois par semaine.",
        "light": "Des muscles et tendons plus forts rendent chaque foulée moins coûteuse "
                 "(économie de course) et encaissent mieux les impacts. L'effet demande 8 à 12 "
                 "semaines, et chez le coureur la protection contre les blessures n'est nette "
                 "que si les mouvements sont bien exécutés.",
        "source": "Balsalobre-Fernández 2016 ; Eihara 2022 ; Wu 2024",
    },
    "taper": {
        "label": "Affûtage",
        "short": "Baisse du volume avant la course, intensité conservée.",
        "light": "Les dernières semaines, on court moins mais on garde un peu de vitesse : la "
                 "fatigue s'efface plus vite que la forme. On arrive frais ET affûté.",
        "source": "Smyth & Lawlor 2021",
    },
}


def term(key: str) -> dict:
    """Entrée du glossaire (KeyError si le terme n'existe pas : faute de frappe)."""
    return TERMS[key]
