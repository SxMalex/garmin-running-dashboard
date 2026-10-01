"""Chaque page se rend sans exception avec un compte Garmin factice."""


import pytest
from conftest import PAGES


@pytest.mark.parametrize("name", ["main.py", *PAGES])
def test_page_renders(logged_in, fake_api, name):
    at = logged_in(name).run()
    assert not at.exception, [e.value for e in at.exception]
    # safe_load_activities transforme les exceptions en message : sans ces
    # assertions, un chargement cassé laisserait la suite verte.
    assert "get_activities" in fake_api.calls
    # (st.error sert aussi de tuile de légende, ex. « TSB < −20 » sur Forme.)
    errors = [e.value for e in at.error if "Erreur" in e.value or "Garmin" in e.value]
    assert not errors, errors


def test_activities_explorer_every_metric(logged_in):
    """L'explorateur doit tracer chaque indicateur sans planter (colonnes absentes, NaN)."""
    from activities_logic import METRICS
    at = logged_in("1_Activities.py").run()
    for key in METRICS:
        next(w for w in at.button_group if w.label == "Indicateur").set_value(key).run()
        assert not at.exception, (key, [e.value for e in at.exception])
    assert any("en facile" in m.value for m in at.markdown)          # carte 80/20


def test_progress_page_shows_projections(logged_in, fake_api):
    fake_api.with_predictions = True
    at = logged_in("4_Progression.py").run()
    assert not at.exception, [e.value for e in at.exception]
    labels = {m.label: m for m in at.metric}
    assert "dans 90 j" in (labels["10 km"].delta or "")
    assert any(m.label.startswith("Dans 3 mois") for m in at.metric)
    next(w for w in at.button_group if w.label == "Vue").set_value("ensemble").run()
    assert not at.exception, [e.value for e in at.exception]


def test_forme_page_shows_a_single_tsb(logged_in, monkeypatch):
    """
    Revue #1 : le haut de page et l'onglet Charge affichaient deux TSB. La charge
    est construite pour tomber sur une limite d'arrondi (sinon les deux chiffres
    coïncidaient déjà avec l'ancien code, et le test ne prouvait rien).
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
    import pandas as pd
    import next_session_logic
    from pmc_edge import rounding_edge_daily
    daily, c, a = rounding_edge_daily(pd.Timestamp.now())
    monkeypatch.setattr(next_session_logic, "daily_tss", lambda df, thr: daily.copy())
    at = logged_in("3_Forme.py").run()
    assert not at.exception, [e.value for e in at.exception]
    values = {m.label: m.value for m in at.metric if "TSB" in m.label}
    assert len(values) >= 2, values
    shown = {float(v.replace("+", "")) for v in values.values()}
    assert shown == {round(round(c, 1) - round(a, 1), 1)}, values
    assert round(c - a, 1) not in shown                     # l'ancienne définition aurait différé


def test_home_shows_the_health_alert_first(logged_in, monkeypatch):
    """Veille santé : 2 signaux concordants → carte « Alerte santé » en tête des signaux."""
    import numpy as np
    import pandas as pd
    import illness_logic
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=35, freq="D")
    rng = np.random.default_rng(0)
    frame = pd.DataFrame({"rhr": 48 + rng.normal(0, 1, 35), "hrv": 60 + rng.normal(0, 4, 35),
                          "resp": 14.5 + rng.normal(0, 0.3, 35), "spo2": np.nan}, index=idx)
    frame.iloc[-1, 0], frame.iloc[-1, 2] = 57, 16.5
    monkeypatch.setattr(illness_logic, "load_health_frame", lambda gc, today, days=34: frame)
    at = logged_in("main.py").run()
    assert not at.exception, [e.value for e in at.exception]
    cards = [m.value for m in at.markdown if "gd-signal-title" in m.value]
    assert cards and "Alerte santé" in cards[0] and "FC de repos" in cards[0]


def test_progress_shows_running_form(logged_in):
    at = logged_in("4_Progression.py").run()
    assert not at.exception, [e.value for e in at.exception]
    kickers = " ".join(m.value for m in at.markdown)
    assert "Contact au sol" in kickers and "Puissance" in kickers
    next(w for w in at.button_group if w.label == "Métrique").set_value("avgStride_cm").run()
    assert not at.exception, [e.value for e in at.exception]


def test_race_day_page_flat_course_by_default(logged_in):
    """Jour de course sans GPX (AppTest ne sait pas téléverser ; l'import est couvert
    par tests_e2e) : plan sur parcours plat, bracelet et ravitaillement ≥ 1 h 15."""
    at = logged_in("10_Jour_de_course.py", rd_distance="Semi-marathon", rd_target="1:50:00").run()
    assert not at.exception, [e.value for e in at.exception]
    labels = {m.label: m.value for m in at.metric}
    assert labels["Allure moyenne"].startswith("5:13")
    assert any("gel" in m.value for m in at.markdown)


def test_race_day_band_builds_up_progressively(logged_in):
    """Bug d'origine : sur le plat, le bracelet donnait la même allure à chaque km."""
    at = logged_in("10_Jour_de_course.py", rd_distance="10 km", rd_target="50:00").run()
    assert not at.exception, [e.value for e in at.exception]
    paces = at.dataframe[0].value["allure"].tolist()
    assert paces[0] > paces[len(paces) // 2] > paces[-1]          # "5:04" > "5:00" > "4:55"
    assert any("dernières courses" in m.value for m in at.markdown)   # courses passées relues
    next(w for w in at.button_group if w.label == "Stratégie d'allure").set_value("even").run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.dataframe[0].value["allure"].nunique() == 1


def test_race_day_heat_shifts_the_band(logged_in):
    at = logged_in("10_Jour_de_course.py", rd_distance="10 km", rd_target="50:00",
                   rd_temp=30.0, rd_dew=20.0).run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.metric[0].value.startswith("5:00")              # bracelet calé sur l'objectif corrigé
    at.checkbox(key="rd_heat_apply").uncheck().run()
    assert at.metric[0].value.startswith("5:00")


def test_ai_coach_replays_a_past_day_and_adds_the_session_slot(logged_in):
    from datetime import date, time, timedelta
    past = date.today() - timedelta(days=20)
    slot = date.today() + timedelta(days=2)
    at = logged_in("7_AI_Coach.py", ai_asof=past, ai_slot_on=True, ai_slot_date=slot,
                   ai_slot_time=time(7, 30)).run()
    assert not at.exception, [e.value for e in at.exception]
    prompt = at.code[0].value
    assert f"au {past:%d/%m/%Y}" in prompt
    assert "Prédictions Garmin" not in prompt                      # rien du présent dans le passé
    assert "Prochaine séance prévue" in prompt and "07 h 30" in prompt
    listed = [line[2:12] for line in prompt.splitlines() if line.startswith("- ") and " | " in line]
    assert listed and all(date(int(d[6:]), int(d[3:5]), int(d[:2])) <= past for d in listed)   # …jusqu'à la date


def test_calendar_compares_two_runs(logged_in):
    at = logged_in("11_Calendrier.py", cmp_a=1000, cmp_b=1017).run()
    assert not at.exception, [e.value for e in at.exception]
    text = " ".join(m.value for m in at.markdown)
    assert "De A à B" in text and "conditions égales" in text
    assert "Le bloc d'avant" in text
    block = at.dataframe[0].value
    assert "Fraîcheur (TSB) la veille" in block["Indicateur"].tolist()
    # La plus ancienne devient A partout (listes comprises), quel que soit l'ordre des clics
    assert at.session_state["cmp_a"] == 1017 and at.session_state["cmp_b"] == 1000
    at.button(key="cal_clear").click().run()
    assert not at.exception and not at.dataframe


def test_calendar_kind_filter(logged_in):
    at = logged_in("11_Calendrier.py").run()
    assert not at.exception, [e.value for e in at.exception]
    for choice in ("race", "training", "both"):
        next(w for w in at.button_group if w.label == "Sorties").set_value(choice).run()
        assert not at.exception, (choice, [e.value for e in at.exception])
    at.button(key="cal_prev").click().run()
    assert not at.exception, [e.value for e in at.exception]


def test_race_day_target_follows_the_distance(logged_in):
    """Revue #2 : « 50:00 » restait en passant au semi → ~142 min/km."""
    at = logged_in("10_Jour_de_course.py", rd_distance="10 km", rd_target="50:00").run()
    assert not at.exception, [e.value for e in at.exception]
    at.selectbox(key="rd_distance").set_value("Semi-marathon").run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.text_input(key="rd_target").value != "50:00"
    pace = next(m.value for m in at.metric if m.label == "Allure moyenne")
    assert 3 <= int(pace.split(":")[0]) <= 8, pace                    # une allure de coureur


def test_race_day_refuses_an_absurd_target(logged_in):
    at = logged_in("10_Jour_de_course.py", rd_distance="10 km", rd_target="1:40:00:00").run()
    assert not at.exception
    at = logged_in("10_Jour_de_course.py", rd_distance="Semi-marathon", rd_target="0:05").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("vérifie le format" in w.value for w in at.warning)
    assert not at.dataframe                                            # pas de bracelet absurde


def test_home_green_health_card_does_not_push_out_real_alerts(logged_in, monkeypatch):
    """Revue #2 : la carte « Pas de signe de maladie » passait en tête et chassait une alerte."""
    import numpy as np
    import pandas as pd
    import illness_logic
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=35, freq="D")
    rng = np.random.default_rng(0)
    calm = pd.DataFrame({"rhr": 48 + rng.normal(0, 1, 35), "hrv": 60 + rng.normal(0, 4, 35),
                         "resp": 14.5 + rng.normal(0, 0.3, 35), "spo2": np.nan}, index=idx)
    monkeypatch.setattr(illness_logic, "load_health_frame", lambda gc, today, days=34: calm)
    import home_logic
    alert = {"status": "serious", "level": "Alerte", "title": "Pegasus : 900 km", "body": "Change-les."}
    monkeypatch.setattr(home_logic, "home_signals", lambda *a, **k: [alert, {**alert, "title": "ACWR"}])
    at = logged_in("main.py").run()
    assert not at.exception, [e.value for e in at.exception]
    cards = [m.value for m in at.markdown if "gd-signal-title" in m.value]
    assert "Pegasus" in cards[0]                                     # l'alerte d'abord…
    assert "Pas de signe de maladie" in cards[-1]                    # …la carte verte en queue, pas perdue


def test_transient_weather_failure_is_retried_not_cached(logged_in, fake_api):
    """Revue #2 : un 429 sur la météo la masquait 24 h (st.cache_data gardait le {})."""
    state = {"fail": True}
    real = fake_api.get_activity_weather

    def flaky(activity_id):
        if state["fail"]:
            fake_api.calls.append("get_activity_weather")
            raise RuntimeError("API Error 429 - Too Many Requests")
        return real(activity_id)

    fake_api.get_activity_weather = flaky
    at = logged_in("11_Calendrier.py", cmp_a=1000, cmp_b=1017).run()
    assert not at.exception, [e.value for e in at.exception]
    state["fail"] = False
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("°C" in c.value for c in at.caption)                  # la météo est revenue


def test_ai_coach_slot_carries_the_planned_session(logged_in, monkeypatch):
    """Revue #2 : l'IA choisissait la séance sans connaître le plan de la montre."""
    from datetime import date, time, timedelta
    import ui_helpers
    slot = date.today() + timedelta(days=2)
    task = {"date": slot, "sport": "running", "name": "Seuil 3 × 8 min", "duration_min": 55,
            "rest_day": False, "target": {"pace_sec": None, "hr_bpm": None, "reps": None},
            "description": "3x8:00@4:30/km"}
    monkeypatch.setattr(ui_helpers, "cached_coach_context",
                        lambda athlete_id, cdate=None: {"tasks": [task], "week": [task]})
    at = logged_in("7_AI_Coach.py", ai_slot_on=True, ai_slot_date=slot, ai_slot_time=time(18, 0)).run()
    assert not at.exception, [e.value for e in at.exception]
    prompt = at.code[0].value
    assert "Séance prévue ce jour-là par le plan" in prompt and "Seuil 3 × 8 min" in prompt
    assert "ne la remplace pas" in prompt


def test_ai_coach_does_not_claim_a_planned_session_on_an_empty_day(logged_in, monkeypatch):
    """Revue : « mon plan la prévoit, ne la remplace pas » alors que le bloc disait « rien ce jour-là »."""
    from datetime import date, time, timedelta
    import ui_helpers
    today = date.today()
    task = {"date": today + timedelta(days=1), "sport": "running", "name": "Footing", "duration_min": 40,
            "rest_day": False, "target": {}, "description": ""}
    monkeypatch.setattr(ui_helpers, "cached_coach_context",
                        lambda athlete_id, cdate=None: {"tasks": [task], "week": [task]})
    at = logged_in("7_AI_Coach.py", ai_slot_on=True, ai_slot_date=today + timedelta(days=10),
                   ai_slot_time=time(7, 0)).run()
    assert not at.exception, [e.value for e in at.exception]
    prompt = at.code[0].value
    assert "Au-delà de l'horizon connu" in prompt and "ne la remplace pas" not in prompt


def test_ai_coach_rest_day_is_not_a_session_to_keep(logged_in, monkeypatch):
    """Contre-revue : un jour de repos déclenchait « ne la remplace pas »."""
    from datetime import date, time, timedelta
    import ui_helpers
    slot = date.today() + timedelta(days=1)
    rest = {"date": slot, "sport": "running", "name": "Repos", "duration_min": 0, "rest_day": True,
            "target": {}, "description": ""}
    monkeypatch.setattr(ui_helpers, "cached_coach_context",
                        lambda athlete_id, cdate=None: {"tasks": [rest], "week": [rest]})
    at = logged_in("7_AI_Coach.py", ai_slot_on=True, ai_slot_date=slot, ai_slot_time=time(7, 0)).run()
    prompt = at.code[0].value
    assert "prévoit du repos" in prompt and "ne la remplace pas" not in prompt


def test_ai_coach_slot_with_the_objectif_plan(logged_in, monkeypatch):
    """Branche plan Objectif (sans Run Coach) : la séance validée du jour est transmise à l'IA."""
    from datetime import date, time, timedelta
    import goal_store
    import ui_helpers
    slot = date.today() + timedelta(days=3)
    sessions = [{"date": slot.isoformat(), "kind": "tempo", "title": "Tempo 3 × 2 km",
                 "distance_km": 10, "target": "4:50/km"},
                {"date": slot.isoformat(), "kind": "strength", "title": "Renfo", "duration_min": 25}]
    monkeypatch.setattr(ui_helpers, "cached_coach_context", lambda athlete_id, cdate=None: None)
    monkeypatch.setattr(goal_store, "validated_sessions", lambda athlete_id, today=None: sessions)
    at = logged_in("7_AI_Coach.py", ai_slot_on=True, ai_slot_date=slot, ai_slot_time=time(12, 0)).run()
    assert not at.exception, [e.value for e in at.exception]
    prompt = at.code[0].value
    assert "Tempo 3 × 2 km — course, 10 km (4:50/km) (plan Objectif validé)" in prompt
    assert "Renfo — renforcement, 25 min" in prompt and "ne la remplace pas" in prompt
    empty_day = logged_in("7_AI_Coach.py", ai_slot_on=True, ai_slot_date=slot + timedelta(days=1),
                          ai_slot_time=time(12, 0)).run()
    assert "Rien de prévu ce jour-là par le plan Objectif" in empty_day.code[0].value


def test_charge_legend_uses_the_shared_tsb_scale(logged_in):
    """Revue #1 : l'encart de l'onglet Charge gardait son échelle (« Sous-entraîné »
    au-delà de 25) sous une métrique qui disait « Bien reposé »."""
    at = logged_in("3_Forme.py").run()
    legend = " ".join(e.value for e in [*at.success, *at.warning, *at.error, *at.info])
    assert "Sous-entraîné" not in legend
    assert "Bien reposé" in legend and "Récupération nécessaire" in legend
