"""Retours mineurs de la revue PR 1 : le nom d'une activité record (4_Progression)
est un texte Garmin libre, rendu en Markdown par st.caption."""

import re

import pytest

# Nom d'activité adverse → ce qui ne doit plus s'interpréter dans la légende.
_NAMES = [
    ("![](https://tiers.example/p.png)", "image distante (fuite d'IP)"),
    ("[gagne](https://tiers.example/x)", "lien"),
    ("**gras** _italique_ ~~barré~~", "mise en forme"),
    ("<img src=x onerror=alert(1)>", "HTML"),
    ("`code` $x^2$ # titre", "code, LaTeX, titre"),
    ("ligne 1\n\n## ligne 2", "retour à la ligne + titre"),
    (":red[alerte] :blue-background[x]", "directives de couleur Streamlit"),
]
# Caractère Markdown actif non précédé d'un antislash. (`:` seul est inerte :
# une directive :red[…] n'agit que si son crochet ne l'est pas.)
_ACTIVE = re.compile(r"(?<!\\)[!\[\]()*_~`<>$#]")
_MARK = "ZQX"          # repère la légende du record parmi les autres légendes


def _records(name):
    return [{"typeId": 3, "value": 1200.0, "activityName": name, "activityId": 7,
             "actStartDateTimeInGMTFormatted": "2026-05-03T08:00:00.0"}]


def _record_caption(logged_in, fake_api, name):
    fake_api.get_personal_record = lambda *a, **k: _records(f"{name} {_MARK}")
    at = logged_in("4_Progression.py").run()
    assert not at.exception, [e.value for e in at.exception]
    captions = [c.value for c in at.caption if _MARK in c.value]
    assert len(captions) == 1, [c.value for c in at.caption]
    return captions[0]


@pytest.mark.parametrize("name,case", _NAMES, ids=[c for _, c in _NAMES])
def test_record_activity_name_is_inert_markdown(logged_in, fake_api, name, case):
    shown = _record_caption(logged_in, fake_api, name)
    assert "\n" not in shown, case
    # Les entités posées par md_escape (« &#58; » pour « : », « &amp; ») sont inertes
    # — rendu vérifié dans le navigateur — : leur « # » n'est pas un titre.
    inert = re.sub(r"&#\d+;|&amp;", "", shown)
    assert not _ACTIVE.findall(inert), (case, shown)
    assert ":" not in shown, (case, shown)          # aucune directive :icône: / :couleur[…] possible


def test_record_activity_name_keeps_plain_text_and_emoji(logged_in, fake_api):
    """Un nom ordinaire n'est pas altéré (pas d'antislash parasite hors Markdown)."""
    shown = _record_caption(logged_in, fake_api, "Sortie 🏃 matinale à Rennes")
    assert shown == f"Sortie 🏃 matinale à Rennes {_MARK}"


def test_activity_name_is_escaped_in_plotly_hover(logged_in, fake_api):
    """Plotly interprète le HTML du survol : un nom Garmin brut y devenait un lien."""
    fake_api.activities[0]["activityName"] = '<a href="https://tiers.example">x</a>'
    at = logged_in("1_Activities.py").run()
    assert not at.exception, [e.value for e in at.exception]
    specs = " ".join(c.proto.spec for c in at.get("plotly_chart"))
    assert "tiers.example" in specs                       # le nom est bien dans un survol
    assert "<a href" not in specs and "\\u003ca href" not in specs
