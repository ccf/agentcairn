# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from cairn_bench.qa.judge import judge
from cairn_bench.qa.provider import FakeProvider


def test_judge_yes_no_parsing():
    p = FakeProvider(reply="Yes, the response is correct.")
    assert (
        judge(
            "Q?",
            gold="Mochi",
            response="The cat is Mochi",
            question_type="multi-session",
            provider=p,
        )
        is True
    )
    p2 = FakeProvider(reply="No.")
    assert (
        judge("Q?", gold="Mochi", response="A dog", question_type="multi-session", provider=p2)
        is False
    )


def test_judge_abstention_routes_to_refusal_prompt():
    p = FakeProvider(reply="yes")
    # for abstention, the prompt asks whether the model correctly refused; provider is fake,
    # so we just assert the abstention path is taken (prompt contains 'unanswerable').
    last = {}
    p.on_prompt = lambda prompt: last.setdefault("p", prompt)
    judge(
        "Q?",
        gold="(unanswerable)",
        response="I don't have that info.",
        question_type="single-session-user",
        is_abstention=True,
        provider=p,
    )
    assert "unanswerable" in last["p"].lower()


def test_judge_integer_question_type_does_not_crash():
    """LoCoMo passes numeric category (int 1–4) as question_type; judge must not AttributeError."""
    p = FakeProvider(reply="yes")
    # int category should fall through to base binary prompt and return True
    result = judge("Q?", gold="x", response="x", question_type=1, provider=p)
    assert result is True

    p2 = FakeProvider(reply="no")
    result2 = judge("Q?", gold="x", response="other", question_type=3, provider=p2)
    assert result2 is False


def test_judge_none_question_type_still_works():
    """question_type=None must fall through to base prompt without error."""
    p = FakeProvider(reply="yes")
    result = judge("Q?", gold="x", response="x", question_type=None, provider=p)
    assert result is True


def test_judge_robust_yes_parse():
    """'yes' must only match as a word boundary at the start; false positives must be rejected."""
    # True positives: starts with "yes" (bare, sentence, padded, qualified).
    for reply in ("yes", "Yes.", "Yes, correct.", "  yes"):
        p = FakeProvider(reply=reply)
        assert judge("Q?", gold="x", response="x", provider=p) is True, (
            f"expected True for reply={reply!r}"
        )
    # False positives that the old substring match incorrectly accepted.
    for reply in ("no", "not yes", "yesterday they met", ""):
        p = FakeProvider(reply=reply)
        assert judge("Q?", gold="x", response="x", provider=p) is False, (
            f"expected False for reply={reply!r}"
        )
