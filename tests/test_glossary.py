"""Glossaire du mode Light : entrées complètes, termes utilisés présents."""

import re
from pathlib import Path

import pytest

from glossary import TERMS, term

APP = Path(__file__).resolve().parent.parent / "app"


@pytest.mark.parametrize("key", list(TERMS))
def test_entries_complete(key):
    t = TERMS[key]
    assert t["label"] and t["short"] and len(t["light"]) > 60


def test_unknown_term_fails_loudly():
    with pytest.raises(KeyError):
        term("n_existe_pas")


def test_every_term_used_in_pages_exists():
    used = set()
    for path in APP.rglob("*.py"):
        used |= set(re.findall(r"(?:explain|help_text)\(\s*[\"']([a-z_0-9]+)[\"']", path.read_text()))
    assert used <= set(TERMS), used - set(TERMS)
