# tests/ingest/test_distill_judged.py
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn.ingest.distill import ExtractiveDistiller, _truncate_title
from cairn.ingest.models import Candidate


def _cand(text: str, **kw) -> Candidate:
    return Candidate(
        text=text,
        session_id="s",
        cwd="/Users/x/proj",
        git_branch="main",
        timestamp="2026-06-12T00:00:00Z",
        source_path=Path("/tmp/s.jsonl"),
        **kw,
    )


def test_truncate_title_word_boundary():
    text = (
        "yes, but also Google's PageSpeed Insights claims our robots.txt is malformed. "
        "Can you look into that?"
    )
    t = _truncate_title(text)
    assert len(t) <= 80
    assert not t.rstrip("…").endswith(" Ca")  # no mid-word fragment
    assert t.endswith("…")


def test_truncate_title_short_text_unchanged():
    assert _truncate_title("short title") == "short title"


def test_long_title_does_not_fold_in_yaml(tmp_path):
    from cairn.ingest.distill import write_derived_note

    text = "a" * 30 + " " + "b" * 30 + " " + "c" * 30  # forces near-80 title
    note = ExtractiveDistiller().distill(_cand(text))
    p = write_derived_note(note, tmp_path)
    raw = p.read_text()
    title_lines = [ln for ln in raw.splitlines() if ln.startswith("title:")]
    assert len(title_lines) == 1
    # the line AFTER title: must be a new key, not a folded continuation
    lines = raw.splitlines()
    idx = lines.index(title_lines[0])
    assert lines[idx + 1].split(":")[0] in {
        "type",
        "permalink",
        "tags",
        "created",
        "source",
        "importance",
    }
