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
