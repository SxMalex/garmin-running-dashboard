"""Identité visuelle : dossard (échappement, contraste), jauge, Accueil en mode coach."""

from datetime import date, timedelta

import pytest
from streamlit.testing.v1 import AppTest

import chart_theme as ct
import ui_theme
from ui_theme import VERDICT_BAND, contrast, freshness_gauge, ink_on


@pytest.mark.parametrize("band", list(VERDICT_BAND.values()) + ct.CAT[:5])
def test_band_ink_meets_wcag_aa(band):
    """Revue P5 : texte blanc à 2,64:1 sur « Lève le pied ». La bande est du grand
    texte (1,25rem gras ≥ 18,66 px) : seuil AA 3:1 ; les états visent 4,5:1."""
    assert "font-weight: 700; font-size: 1.25rem" in ui_theme._CSS
    assert contrast(band, ink_on(band)) >= 3.0, band
    if band in VERDICT_BAND.values():
        assert contrast(band, ink_on(band)) >= 4.5, band


@pytest.mark.parametrize("tsb", [-55.0, -11.0, 0.0, 42.0])
def test_gauge_shows_the_real_value(tsb):
    fig = freshness_gauge(tsb)
    ind = fig.data[0]
    assert ind.value == tsb
    lo, hi = ind.gauge.axis.range
    assert lo <= tsb <= hi


def test_gauge_without_value():
    assert freshness_gauge(None).data[0].value is None


def _bib_app(title, target):
    from ui_theme import bib
    bib(band_text="Normal", number="10", unit="km", title=title, target=target)


def test_bib_escapes_and_stays_one_block():
    at = AppTest.from_function(
        _bib_app, args=("<script>alert(1)</script>",
                        "15 min\n\n![x](https://tiers.example/pixel.png) **gras**"),
        default_timeout=30).run()
    html = " ".join(m.value for m in at.markdown)
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "\n\n" not in html.split('class="gd-bib"', 1)[1]   # aucune rupture de bloc HTML


def test_home_coach_branch_shows_plan_details(logged_in, monkeypatch):
    """Revue P5 : la branche « coach » (source primaire) n'était pas testée."""
    import ui_helpers
    task = {"date": date.today(), "name": "Semi — seuil 3×10 min", "duration_min": 55,
            "target_text": "3x10:00@4:50/km", "session_key": "tempo"}
    ctx = {"plan": {"name": "Semi Run Coach"}, "phase": {"label": "Build"},
           "days_to_event": 40, "next_run": task, "tasks": [task]}
    monkeypatch.setattr(ui_helpers, "cached_coach_context", lambda *a, **k: ctx)
    at = logged_in("main.py").run()
    assert not at.exception, [e.value for e in at.exception]
    html = " ".join(m.value for m in at.markdown)
    assert "Semi — seuil 3×10 min" in html
    assert "Semi Run Coach" in html and "phase Build" in html and "J−40" in html
