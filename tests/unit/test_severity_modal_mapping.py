"""Lint: severity is drafted from the sentence's modal, not from the model's mood.

Before this rule the only instruction was "set `severity` when the user said so",
so an unhedged requirement and a nice-to-have both landed at `warn` unless the
user happened to say "hard fail" out loud — two identical sentences could draft
two different severities across sessions. The mapping table is what makes it
reproducible, and the weaker-word rule is what keeps a preference from being
escalated into a build failure.

Structural, not wording-exact, and a ratchet rather than a proof: the CLI accepts
any valid severity, so only this prose decides which one is written.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
CONTRACT_SKILL = PLUGIN_ROOT / "skills" / "contract" / "SKILL.md"


def _text() -> str:
    return re.sub(r"\s+", " ", CONTRACT_SKILL.read_text(encoding="utf-8"))


@pytest.mark.parametrize("modal", ("must", "shall", "never", "hard fail"))
def test_blocking_modals_map_to_fail(modal):
    assert modal in _text(), f"{modal!r} is not mapped to a severity"


@pytest.mark.parametrize("modal", ("should", "can", "nice to have", "just warn me"))
def test_hedged_modals_map_to_warn(modal):
    assert modal in _text(), f"{modal!r} is not mapped to a severity"


def test_the_mapping_is_stated_as_a_rule_and_not_only_as_examples():
    body = _text()
    assert "`severity` follows the modal in the sentence" in body
    assert "Omitted means `warn`" in body, "the absent-severity default was lost"


def test_a_mixed_sentence_resolves_downward():
    """"should never exceed" is a preference, and drafting it `fail` breaks a build
    the user never asked to block."""
    assert "the weaker word governs" in _text()


def test_an_unclear_modal_is_asked_about_rather_than_guessed():
    assert "ask about, not to pick" in _text()
