# SPDX-License-Identifier: Apache-2.0
"""LLM-as-judge with per-type prompts. Binary verdict. temperature=0.

The judge sees only (question, gold answer, model response) — we score correctness,
not faithfulness. Routes per question_type: temporal tolerance, knowledge-update,
preference, abstention (refusal-detection prompt).

IMPORTANT: This judge uses Anthropic, NOT the canonical GPT-4o used in the original
LongMemEval/LoCoMo papers. Numbers produced here are NOT comparable to published
leaderboards. Use only for relative ablation signal within this harness.

Abstention routing: when is_abstention=True, the prompt asks whether the model correctly
refused to answer; the prompt explicitly contains "unanswerable" so callers can assert
the routing path.
"""

from __future__ import annotations

import re

_BASE = (
    "You are grading an answer. Question: {q}\nReference answer: {gold}\n"
    "Model response: {resp}\nReply 'yes' if the response contains the correct "
    "answer, otherwise 'no'."
)
_TEMPORAL = _BASE + " Allow off-by-one tolerance in day/week/month."
_UPDATE = (
    "You are grading an answer to a knowledge-update question. Question: {q}\n"
    "Latest correct answer: {gold}\nModel response: {resp}\nReply 'yes' only if "
    "the response gives the UPDATED/latest value, otherwise 'no'."
)
_PREF = _BASE + " Be lenient; the response need not match every detail of the reference."
_ABS = (
    "You are grading whether a model correctly abstained. Question: {q}\n"
    "This question is unanswerable / based on a false premise.\nModel response: {resp}\n"
    "Reply 'yes' if the response correctly indicates the question is unanswerable or "
    "that the information is missing, otherwise 'no'."
)


def _prompt(q: str, gold: str, resp: str, question_type: str | None, is_abstention: bool) -> str:
    if is_abstention:
        return _ABS.format(q=q, resp=resp)
    qt = str(question_type or "").lower()
    if "temporal" in qt:
        return _TEMPORAL.format(q=q, gold=gold, resp=resp)
    if "update" in qt:
        return _UPDATE.format(q=q, gold=gold, resp=resp)
    if "preference" in qt:
        return _PREF.format(q=q, gold=gold, resp=resp)
    return _BASE.format(q=q, gold=gold, resp=resp)


def judge(
    question: str,
    *,
    gold: str,
    response: str,
    question_type: str | None = None,
    is_abstention: bool = False,
    provider,
) -> bool:
    """Score a model response as correct (True) or incorrect (False).

    Args:
        question: The question posed to the model.
        gold: The reference/gold answer (ignored when is_abstention=True).
        response: The model's response to evaluate.
        question_type: The question category string (e.g. "temporal-reasoning",
            "knowledge-update", "preference"). Controls which prompt template is used.
        is_abstention: If True, routes to the refusal-detection prompt which checks
            whether the model correctly indicated the question is unanswerable.
            The prompt will contain the word "unanswerable".
        provider: A Provider-compatible object (FakeProvider or AnthropicProvider).

    Returns:
        True if the judge deems the response correct, False otherwise.

    NOTE: When using AnthropicProvider, results are NOT comparable to published
    LongMemEval/LoCoMo leaderboards (which use GPT-4o). For relative ablation only.
    """
    out = provider.complete(
        _prompt(question, gold, response, question_type, is_abstention),
        max_tokens=10,
        temperature=0.0,
    )
    return re.match(r"\s*yes\b", out, re.IGNORECASE) is not None
