# SPDX-License-Identifier: Apache-2.0
"""LongMemEval-S instance -> (Notes, Query). One note per haystack session; each turn
is a '## {session_id}_{turn_idx+1}' header so the positional gold turn id lands in
Hit.heading_path. Gold sessions = answer_session_ids; gold turns = has_answer turns."""

from __future__ import annotations

from cairn.vault import Note
from cairn_bench.models import Query


def adapt(instance: dict) -> tuple[list[Note], list[Query]]:
    sids = instance["haystack_session_ids"]
    dates = instance["haystack_dates"]
    sessions = instance["haystack_sessions"]
    notes: list[Note] = []
    gold_turns: set[str] = set()
    for sid, date, turns in zip(sids, dates, sessions, strict=True):
        lines = []
        for i, turn in enumerate(turns):
            turn_id = f"{sid}_{i + 1}"
            if turn.get("has_answer") is True:
                gold_turns.add(turn_id)
            role = turn.get("role", "user")
            lines.append(f"## {turn_id}  ({role}, {date})\n\n{turn['content']}\n")
        notes.append(
            Note(
                permalink=sid,
                frontmatter={
                    "title": sid,
                    "type": "session",
                    "permalink": sid,
                    "session_date": date,
                    "instance_id": instance["question_id"],
                },
                body="\n".join(lines),
            )
        )
    is_abs = instance["question_id"].endswith("_abs")
    q = Query(
        qid=instance["question_id"],
        question=instance["question"],
        answer=instance.get("answer", ""),
        gold_sessions=set() if is_abs else set(instance.get("answer_session_ids", [])),
        gold_turns=set() if is_abs else gold_turns,
        category=instance.get("question_type"),
        is_abstention=is_abs,
        meta={"question_date": instance.get("question_date")},
    )
    return notes, [q]
