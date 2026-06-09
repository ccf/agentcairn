# SPDX-License-Identifier: Apache-2.0
"""LoCoMo sample -> (Notes, Queries). One note per session_{N}; each turn is a
'## {dia_id}  ({speaker})' header so the native dia_id lands in Hit.heading_path.
Gold turns = qa.evidence (normalized). Category 5 (adversarial) is excluded from
retrieval queries (kept only for the QA-abstention metric, handled elsewhere)."""

from __future__ import annotations

import re

from cairn.vault import Note
from cairn_bench.models import Query

_SESSION_RE = re.compile(r"^session_(\d+)$")
_DIA_RE = re.compile(r"^D(\d+):(\d+)$")


def normalize_dia_id(raw: str) -> str:
    """Strip zero-padding: 'D1:02' -> 'D1:2'. Leaves unrecognized ids unchanged."""
    raw = raw.strip()
    m = _DIA_RE.match(raw)
    if not m:
        return raw
    return f"D{int(m.group(1))}:{int(m.group(2))}"


def _evidence_turns(evidence: list) -> set[str]:
    out: set[str] = set()
    for ev in evidence or []:
        for part in str(ev).split(";"):  # handle semicolon-compound
            part = part.strip()
            if _DIA_RE.match(part):
                out.add(normalize_dia_id(part))
    return out


def adapt(sample: dict) -> tuple[list[Note], list[Query]]:
    sample_id = sample["sample_id"]
    conv = sample["conversation"]
    notes: list[Note] = []
    for key in sorted(conv):
        m = _SESSION_RE.match(key)
        if not m:
            continue  # skips session_N_date_time and speaker_a/b
        n = m.group(1)
        date = conv.get(f"session_{n}_date_time", "")
        lines = []
        for turn in conv[key]:
            did = normalize_dia_id(turn["dia_id"])
            text = turn.get("text", "")
            if turn.get("blip_caption"):
                text = f"{text}\n[image: {turn['blip_caption']}]"
            lines.append(f"## {did}  ({turn.get('speaker', '')})\n\n{text}\n")
        permalink = f"{sample_id}_session_{n}"
        notes.append(
            Note(
                permalink=permalink,
                frontmatter={
                    "title": permalink,
                    "type": "session",
                    "permalink": permalink,
                    "session_date": date,
                },
                body="\n".join(lines),
            )
        )
    queries: list[Query] = []
    for i, qa in enumerate(sample.get("qa", [])):
        cat = qa.get("category")
        if cat == 5:
            # Adversarial (unanswerable) query: emit as abstention with empty gold so
            # the retrieval loop skips it (empty gold → no metric contribution, Zep
            # invariant preserved) while the QA path can judge it via the refusal prompt.
            queries.append(
                Query(
                    qid=f"{sample_id}_q{i}",
                    question=qa["question"],
                    answer=str(qa.get("answer", "")),
                    gold_sessions=set(),
                    gold_turns=set(),
                    category=5,
                    is_abstention=True,
                )
            )
            continue
        gold_turns = _evidence_turns(qa.get("evidence", []))
        gold_sessions = {f"{sample_id}_session_{t.split(':')[0][1:]}" for t in gold_turns}
        queries.append(
            Query(
                qid=f"{sample_id}_q{i}",
                question=qa["question"],
                answer=str(qa.get("answer", "")),
                gold_sessions=gold_sessions,
                gold_turns=gold_turns,
                category=cat,
                is_abstention=False,
            )
        )
    return notes, queries
