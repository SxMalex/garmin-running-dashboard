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
