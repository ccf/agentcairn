# tests/ingest/test_importance.py
# SPDX-License-Identifier: Apache-2.0
from cairn.ingest.importance import KEEP_THRESHOLD, is_important, score


def test_trivial_turns_score_low():
    for trivial in ["ok", "thanks!", "yes", "sounds good", "lgtm"]:
        assert score(trivial) < KEEP_THRESHOLD


def test_substantive_decision_scores_high():
    text = (
        "We decided to use DuckDB read-only ATTACH because CREATE MACRO fails on "
        "a read-only connection. Always escape the path before interpolating."
    )
    assert score(text) >= KEEP_THRESHOLD
    assert is_important(text) is True


def test_preference_and_correction_markers_boost():
    assert score("Actually, I prefer plaintext questions instead of the popup.") >= KEEP_THRESHOLD


def test_is_important_respects_explicit_threshold():
    text = "minor note about formatting"
    assert is_important(text, threshold=0.99) is False
