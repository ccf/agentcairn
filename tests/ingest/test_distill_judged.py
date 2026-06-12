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


def test_truncate_title_whitespace_only_falls_back():
    assert _truncate_title("") == "memory"
    assert _truncate_title("   \n\t  ") == "memory"


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


def test_distill_with_llm_judgment_writes_distilled_plus_verbatim():
    from cairn.ingest.judge import Judgment

    cand = _cand(
        "we should always run the corpus replay before changing redaction",
        judgment=Judgment(
            durability=0.9,
            title="Corpus replay before redaction changes",
            distilled="Always run the corpus replay before changing redaction.",
        ),
        importance=0.83,
    )
    note = ExtractiveDistiller().distill(cand)
    assert note.frontmatter["title"] == "Corpus replay before redaction changes"
    assert note.frontmatter["importance"] == 0.83
    assert (
        "- [context] Always run the corpus replay before changing redaction. #ingested" in note.body
    )
    assert "- [verbatim] we should always run the corpus replay" in note.body


def test_distill_without_judgment_keeps_verbatim_format():
    cand = _cand("we decided to always do the thing")
    note = ExtractiveDistiller().distill(cand)
    assert note.body.startswith("- [context] we decided to always do the thing")
    assert "[verbatim]" not in note.body


def test_dedup_identity_unchanged_by_judgment():
    from cairn.ingest.dedup import content_hash
    from cairn.ingest.judge import Judgment

    text = "we decided to always do the thing"
    plain = ExtractiveDistiller().distill(_cand(text))
    judged = ExtractiveDistiller().distill(
        _cand(text, judgment=Judgment(durability=0.9, title="T", distilled="D."), importance=0.9)
    )
    # permalink (slug + content hash) is derived from the VERBATIM text only
    assert plain.permalink == judged.permalink
    assert content_hash(text) in plain.permalink or plain.permalink.endswith(content_hash(text)[:8])
