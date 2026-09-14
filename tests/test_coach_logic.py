"""
Tests de la logique du coach Garmin (plan adaptatif Garmin Run Coach).
Les fixtures reprennent les formes réelles de l'API (validées en août 2026).
"""

from datetime import date

import pytest

from coach_logic import (
    DEFAULT_SESSION_KEY,
    NUTRITION_FOCUS,
    nutrition_focus,
    active_plan,
    coach_plan_context,
    current_phase,
    estimated_distance_km,
    hard_session_alert,
    merge_coach_into_recommendation,
    next_running_task,
    parse_workout_target,
    plan_phases,
    plan_tasks,
    target_event_date,
    target_label,
    week_schedule,
)

TODAY = date(2026, 8, 13)


def _plan(status="Scheduled", plan_id=46843176, start="2026-05-11T00:00:00.0", name="Programme Marseille-Cassis"):
    return {
        "trainingPlanId": plan_id,
        "name": name,
        "trainingStatus": {"statusId": 1, "statusKey": status},
        "trainingLevel": {"levelKey": "Intermediate"},
        "trainingSubType": {"subTypeKey": "GarminRunningCoachEventBased"},
        "startDate": start,
        "endDate": "2026-10-25T00:00:00.0",
        "durationInWeeks": 24,
        "avgWeeklyWorkouts": 7,
    }


def _task(cdate, name, sport="running", effect="AEROBIC_BASE", description="",
          duration=2520, rest=False, status="NOT_COMPLETE", week=14):
    return {
        "trainingPlanId": 46843176,
        "weekId": week,
        "calendarDate": cdate,
        "taskWorkout": {
            "workoutId": None,
            "sportType": {"sportTypeKey": sport} if sport else None,
            "workoutName": name,
            "workoutDescription": description,
            "scheduledDate": f"{cdate}T08:39:08.0",
            "estimatedDurationInSecs": duration,
            "workoutUuid": f"uuid-{cdate}-{name}",
            "priorityType": "REQUIRED",
            "trainingEffectLabel": effect,
            "workoutPhrase": effect,
            "restDay": rest,
            "adaptiveCoachingWorkoutStatus": status,
        },
    }


@pytest.fixture
def plans_raw():
    return {"trainingPlanList": [
        _plan(status="Completed", plan_id=43470807, start="2025-12-31T00:00:00.0"),
        _plan(),
    ]}


@pytest.fixture
def plan_detail():
    """Plan adaptatif avec la semaine réellement renvoyée par Garmin."""
    return {
        **_plan(),
        "taskList": [
            _task("2026-08-13", "Stabilité abdos 3", sport="strength_training",
                  effect="INVALID", duration=1800, week=13),
            _task("2026-08-14", "Anaérobique", effect="ANAEROBIC_CAPACITY",
                  description="5x1:00@4:15/km", duration=2520),
            _task("2026-08-15", "Repos", sport=None, effect="INVALID",
                  duration=0, rest=True),
            _task("2026-08-16", "Base", description="147bpm", duration=3060),
            _task("2026-08-16", "Circuit corps entier 2", sport="strength_training",
                  effect="INVALID", duration=1500),
            _task("2026-08-18", "Seuil", effect="LACTATE_THRESHOLD",
                  description="3x6:00@5:05/km", duration=2520),
        ],
        "adaptivePlanPhases": [
            {"startDate": "2026-07-08", "endDate": "2026-09-03",
             "trainingPhase": "BUILD", "currentPhase": True},
            {"startDate": "2026-09-04", "endDate": "2026-10-12",
             "trainingPhase": "PEAK", "currentPhase": False},
            {"startDate": "2026-10-25", "endDate": "2026-10-25",
             "trainingPhase": "TARGET_EVENT_DAY", "currentPhase": False},
        ],
    }


# ---------------------------------------------------------------------------
# Plan actif
# ---------------------------------------------------------------------------

class TestActivePlan:
    def test_ignore_les_plans_termines(self, plans_raw):
        plan = active_plan(plans_raw)
        assert plan["plan_id"] == 46843176
        assert plan["name"] == "Programme Marseille-Cassis"
        assert plan["start_date"] == date(2026, 5, 11)

    def test_prend_le_plus_recent_si_plusieurs_actifs(self):
        raw = {"trainingPlanList": [
            _plan(plan_id=1, start="2026-01-01T00:00:00.0"),
            _plan(plan_id=2, start="2026-06-01T00:00:00.0"),
        ]}
        assert active_plan(raw)["plan_id"] == 2

    def test_aucun_plan_actif(self):
        raw = {"trainingPlanList": [_plan(status="Completed")]}
        assert active_plan(raw) is None

    @pytest.mark.parametrize("raw", [None, {}, [], {"trainingPlanList": None}, "bruit"])
    def test_reponses_vides(self, raw):
        assert active_plan(raw) is None

    def test_accepte_une_liste_brute(self):
        assert active_plan([_plan()])["plan_id"] == 46843176


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

class TestPhases:
    def test_phases_normalisees_et_triees(self, plan_detail):
        phases = plan_phases(plan_detail)
        assert [p["phase"] for p in phases] == ["BUILD", "PEAK", "TARGET_EVENT_DAY"]
        assert phases[0]["label"] == "Développement"
        assert phases[0]["start"] == date(2026, 7, 8)

    def test_phase_courante_via_le_drapeau_garmin(self, plan_detail):
        assert current_phase(plan_detail, TODAY)["phase"] == "BUILD"

    def test_phase_courante_par_encadrement_si_drapeau_absent(self, plan_detail):
        for phase in plan_detail["adaptivePlanPhases"]:
            phase["currentPhase"] = False
        assert current_phase(plan_detail, date(2026, 9, 10))["phase"] == "PEAK"

    def test_phase_courante_hors_plan(self, plan_detail):
        for phase in plan_detail["adaptivePlanPhases"]:
            phase["currentPhase"] = False
        assert current_phase(plan_detail, date(2027, 1, 1)) is None

    def test_date_de_lobjectif(self, plan_detail):
        assert target_event_date(plan_detail) == date(2026, 10, 25)

    def test_sans_phases(self):
        assert plan_phases({}) == []
        assert target_event_date({}) is None


# ---------------------------------------------------------------------------
# Cibles chiffrées
# ---------------------------------------------------------------------------

class TestParseWorkoutTarget:
    def test_repetitions_en_temps_avec_allure(self):
        target = parse_workout_target("5x1:00@4:15/km")
        assert target["reps"] == 5
        assert target["rep_duration_s"] == 60
        assert target["pace_sec"] == 255

    def test_seuil(self):
        target = parse_workout_target("3x6:00@5:05/km")
        assert (target["reps"], target["rep_duration_s"], target["pace_sec"]) == (3, 360, 305)

    def test_cible_de_frequence_cardiaque(self):
        target = parse_workout_target("147bpm")
        assert target["hr_bpm"] == 147
        assert target["pace_sec"] is None

    def test_repetitions_en_distance(self):
        target = parse_workout_target("8x400m@4:00/km")
        assert target["reps"] == 8
        assert target["rep_distance_m"] == 400
        assert target["pace_sec"] == 240

    @pytest.mark.parametrize("value", [None, "", "   ", 42])
    def test_descriptions_vides(self, value):
        target = parse_workout_target(value)
        assert target["pace_sec"] is None and target["reps"] is None

    def test_format_inconnu_conserve_la_chaine(self):
        assert parse_workout_target("Fartlek libre")["raw"] == "Fartlek libre"


class TestTargetLabel:
    def test_intervalles(self, plan_detail):
        tasks = plan_tasks(plan_detail)
        anaerobique = next(t for t in tasks if t["name"] == "Anaérobique")
        assert target_label(anaerobique) == "5 × 1:00 à 4:15/km"

    def test_cible_fc(self, plan_detail):
        base = next(t for t in plan_tasks(plan_detail) if t["name"] == "Base")
        assert target_label(base) == "FC cible 147 bpm"

    def test_sans_cible(self):
        assert target_label({"target": parse_workout_target("")}) == "Allure libre"

    def test_task_absente(self):
        assert target_label(None) == "—"


# ---------------------------------------------------------------------------
# Séances programmées
# ---------------------------------------------------------------------------

class TestPlanTasks:
    def test_normalisation(self, plan_detail):
        tasks = plan_tasks(plan_detail)
        assert len(tasks) == 6
        anaerobique = next(t for t in tasks if t["name"] == "Anaérobique")
        assert anaerobique["date"] == date(2026, 8, 14)
        assert anaerobique["duration_min"] == 42
        assert anaerobique["sport"] == "running"
        assert anaerobique["is_hard"] is True
        assert anaerobique["session_key"] == "tempo"

    def test_jour_de_repos(self, plan_detail):
        repos = next(t for t in plan_tasks(plan_detail) if t["rest_day"])
        assert repos["date"] == date(2026, 8, 15)
        assert repos["duration_min"] == 0

    def test_base_nest_pas_une_seance_dure(self, plan_detail):
        base = next(t for t in plan_tasks(plan_detail) if t["name"] == "Base")
        assert base["is_hard"] is False
        assert base["session_key"] == "endurance"

    def test_effet_inconnu_retombe_sur_endurance(self):
        raw = {"taskList": [_task("2026-08-20", "Mystère", effect="NOUVEAU_LABEL")]}
        assert plan_tasks(raw)[0]["session_key"] == DEFAULT_SESSION_KEY

    def test_tri_par_date_puis_course_avant_renfo(self, plan_detail):
        tasks = plan_tasks(plan_detail)
        same_day = [t for t in tasks if t["date"] == date(2026, 8, 16)]
        assert [t["sport"] for t in same_day] == ["running", "strength_training"]

    def test_tache_sans_date_ignoree(self):
        raw = {"taskList": [{"taskWorkout": {"workoutName": "Sans date"}}, "bruit"]}
        assert plan_tasks(raw) == []

    @pytest.mark.parametrize("raw", [None, {}, {"taskList": None}, "bruit"])
    def test_reponses_vides(self, raw):
        assert plan_tasks(raw) == []


class TestNextRunningTask:
    def test_saute_le_renfo_et_prend_la_prochaine_course(self, plan_detail):
        task = next_running_task(plan_tasks(plan_detail), TODAY)
        assert task["name"] == "Anaérobique"
        assert task["date"] == date(2026, 8, 14)

    def test_ignore_les_seances_passees(self, plan_detail):
        task = next_running_task(plan_tasks(plan_detail), date(2026, 8, 17))
        assert task["name"] == "Seuil"

    def test_ignore_les_seances_deja_faites(self):
        tasks = plan_tasks({"taskList": [
            _task("2026-08-14", "Anaérobique", status="COMPLETE"),
            _task("2026-08-16", "Base"),
        ]})
        assert next_running_task(tasks, TODAY)["name"] == "Base"

    def test_ignore_les_jours_de_repos(self):
        tasks = plan_tasks({"taskList": [_task("2026-08-14", "Repos", rest=True)]})
        assert next_running_task(tasks, TODAY) is None

    def test_plus_aucune_seance(self, plan_detail):
        assert next_running_task(plan_tasks(plan_detail), date(2026, 9, 1)) is None


class TestWeekSchedule:
    def test_fenetre_de_sept_jours(self, plan_detail):
        week = week_schedule(plan_tasks(plan_detail), TODAY)
        assert [t["date"] for t in week] == [
            date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 15),
            date(2026, 8, 16), date(2026, 8, 16), date(2026, 8, 18),
        ]

    def test_exclut_le_huitieme_jour(self, plan_detail):
        week = week_schedule(plan_tasks(plan_detail), date(2026, 8, 12), days=2)
        assert [t["name"] for t in week] == ["Stabilité abdos 3"]


# ---------------------------------------------------------------------------
# Contexte complet
# ---------------------------------------------------------------------------

class TestCoachPlanContext:
    def test_contexte(self, plans_raw, plan_detail):
        ctx = coach_plan_context(plans_raw, plan_detail, TODAY)
        assert ctx["plan"]["plan_id"] == 46843176
        assert ctx["phase"]["phase"] == "BUILD"
        assert ctx["event_date"] == date(2026, 10, 25)
        assert ctx["days_to_event"] == 73
        assert ctx["next_run"]["name"] == "Anaérobique"
        assert [t["name"] for t in ctx["today_tasks"]] == ["Stabilité abdos 3"]

    def test_sans_plan_actif(self, plan_detail):
        raw = {"trainingPlanList": [_plan(status="Completed")]}
        assert coach_plan_context(raw, plan_detail, TODAY) is None


# ---------------------------------------------------------------------------
# Estimation de distance et fusion dans la recommandation
# ---------------------------------------------------------------------------

class TestEstimatedDistance:
    def test_duree_et_allure(self):
        # 42 min à 6:19/km (379 s) ≈ 6,6 km
        assert estimated_distance_km(42, 379) == pytest.approx(6.6, abs=0.1)

    @pytest.mark.parametrize("duration,pace", [(0, 300), (40, 0), (40, -1), (None, 300)])
    def test_valeurs_invalides(self, duration, pace):
        assert estimated_distance_km(duration, pace) == 0.0


def _rec() -> dict:
    return {
        "session_key": "endurance",
        "downgraded_from": "tempo",
        "ctl": 28.8, "atl": 35.0, "tsb": -6.2,
        "days_since": 0,
        "target_dist_km": 6.2,
        "target_pace_sec": 398.0,
        "target_pace_str": "6:38/km",
        "target_elev": 54,
        "duration_min": 41,
        "avg_dist": 6.4,
        "avg_pace_sec": 379.0,
        "avg_pace_str": "6:19/km",
        "suggested_date": date(2026, 8, 14),
        "suggested_date_str": "Demain",
    }


class TestMergeCoach:
    def test_le_coach_pilote_la_seance(self, plans_raw, plan_detail):
        ctx = coach_plan_context(plans_raw, plan_detail, TODAY)
        merged = merge_coach_into_recommendation(_rec(), ctx)
        assert merged["session_key"] == "tempo"           # ANAEROBIC_CAPACITY
        assert merged["duration_min"] == 42               # durée Garmin
        assert merged["suggested_date"] == date(2026, 8, 14)
        assert merged["coach_target_label"] == "5 × 1:00 à 4:15/km"
        assert merged["target_dist_km"] == pytest.approx(6.6, abs=0.1)
        assert merged["target_dist_is_estimated"] is True

    def test_conserve_les_metriques_locales(self, plans_raw, plan_detail):
        ctx = coach_plan_context(plans_raw, plan_detail, TODAY)
        merged = merge_coach_into_recommendation(_rec(), ctx)
        assert (merged["ctl"], merged["atl"], merged["tsb"]) == (28.8, 35.0, -6.2)
        assert merged["avg_pace_str"] == "6:19/km"

    def test_allure_du_parcours_reste_lallure_reelle(self, plans_raw, plan_detail):
        # 4:15/km est l'allure des répétitions, pas celle de la séance entière.
        ctx = coach_plan_context(plans_raw, plan_detail, TODAY)
        merged = merge_coach_into_recommendation(_rec(), ctx)
        assert merged["target_pace_sec"] == 379.0

    def test_efface_la_retrogradation_interne(self, plans_raw, plan_detail):
        ctx = coach_plan_context(plans_raw, plan_detail, TODAY)
        assert merge_coach_into_recommendation(_rec(), ctx)["downgraded_from"] is None

    def test_sans_coach_la_reco_interne_est_intacte(self):
        merged = merge_coach_into_recommendation(_rec(), None)
        assert merged["session_key"] == "endurance"
        assert merged["downgraded_from"] == "tempo"
        assert merged["target_dist_km"] == 6.2
        assert merged["coach"] is None

    def test_plan_sans_course_a_venir(self, plans_raw, plan_detail):
        ctx = coach_plan_context(plans_raw, plan_detail, date(2026, 9, 1))
        merged = merge_coach_into_recommendation(_rec(), ctx)
        assert merged["session_key"] == "endurance"
        assert merged["coach"] is None


class TestHardSessionAlert:
    def test_alerte_sur_seance_intense_et_recup_degradee(self, plans_raw, plan_detail):
        ctx = coach_plan_context(plans_raw, plan_detail, TODAY)
        alert = hard_session_alert(ctx, downgrade=1)
        assert "Anaérobique" in alert and "5 × 1:00 à 4:15/km" in alert

    def test_pas_dalerte_si_recup_ok(self, plans_raw, plan_detail):
        ctx = coach_plan_context(plans_raw, plan_detail, TODAY)
        assert hard_session_alert(ctx, downgrade=0) is None

    def test_pas_dalerte_sur_une_seance_facile(self, plans_raw, plan_detail):
        ctx = coach_plan_context(plans_raw, plan_detail, date(2026, 8, 15))
        assert ctx["next_run"]["name"] == "Base"
        assert hard_session_alert(ctx, downgrade=2) is None

    def test_sans_contexte(self):
        assert hard_session_alert(None, downgrade=2) is None


# ---------------------------------------------------------------------------
# Axe nutritionnel (prompt « Idées de repas » de la page IA Coach)
# ---------------------------------------------------------------------------

class TestNutritionFocus:
    def test_seance_intense(self, plan_detail):
        anaerobique = next(t for t in plan_tasks(plan_detail) if t["name"] == "Anaérobique")
        assert "glucides" in nutrition_focus(anaerobique)

    def test_endurance(self, plan_detail):
        base = next(t for t in plan_tasks(plan_detail) if t["name"] == "Base")
        assert "rien de particulier" in nutrition_focus(base)

    def test_jour_de_repos_prioritaire_sur_le_type(self, plan_detail):
        repos = next(t for t in plan_tasks(plan_detail) if t["rest_day"])
        assert "repos" in nutrition_focus(repos)

    def test_sans_seance(self):
        assert "équilibrés" in nutrition_focus(None)

    def test_type_inconnu_retombe_sur_endurance(self):
        assert nutrition_focus({"session_key": "inexistant"}) == NUTRITION_FOCUS["endurance"]

    def test_toutes_les_cles_de_seance_sont_couvertes(self):
        """Chaque type de séance du dashboard doit avoir son axe nutritionnel."""
        from next_session_logic import SESSION_TYPES
        assert set(SESSION_TYPES) <= set(NUTRITION_FOCUS)
