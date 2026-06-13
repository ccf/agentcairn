# tests/ingest/test_pipeline.py
# SPDX-License-Identifier: Apache-2.0

import json

from cairn.ingest.dedup import DedupLedger
from cairn.ingest.events import EventKind, NormalizedEvent
from cairn.ingest.models import IngestReport, Transcript
from cairn.ingest.pipeline import ingest_transcript

SECRET = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"


def _ev(kind, text, ts="t0"):
    from pathlib import Path

    return NormalizedEvent(
        kind=kind,
        role="user" if kind == EventKind.AUTHORED_USER else "assistant",
        text=text,
        timestamp=ts,
        session_id="sess-1",
        project="proj",
        git_branch="main",
        source_path=Path("/tmp/sess-1.jsonl"),
    )


def _transcript(tmp_path) -> Transcript:
    return Transcript(
        session_id="sess-1",
        cwd="/Users/x/proj",
        git_branch="main",
        path=tmp_path / "sess-1.jsonl",
        events=[
            _ev(EventKind.AUTHORED_USER, "thanks!"),  # authored but trivial -> gated out
            _ev(
                EventKind.AUTHORED_USER,
                f"We decided to always rotate the token; the old one was {SECRET}.",
            ),
            _ev(EventKind.AUTHORED_ASSISTANT, "Understood, rotating now."),  # not a candidate
        ],
    )


def test_pipeline_redacts_before_write_and_gates(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger)

    assert report.candidates == 1  # only the one substantive user turn
    assert report.gated_out == 1  # "thanks!"
    assert len(report.written) == 1
    assert report.redactions >= 1

    # INVARIANT: the secret never reaches disk
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert SECRET not in blob
    assert "[REDACTED" in blob


def test_pipeline_ingests_only_authored_user_events(tmp_path):
    """Tool results, meta injections, summaries, and assistant turns are excluded
    by KIND — no text patterns involved. The per-kind tally is reported."""
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    tr = Transcript(
        session_id="s",
        cwd="/Users/x/proj",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[
            _ev(EventKind.TOOL_RESULT, "Context Usage 49.8k/1m tokens; system prompt 6.7k"),
            _ev(EventKind.META_INJECTION, "<task-notification> background task done"),
            _ev(
                EventKind.COMPACT_SUMMARY,
                "This session is being continued from a previous conversation.",
            ),
            _ev(
                EventKind.AUTHORED_USER, "We decided to always rebase-merge and delete the branch."
            ),
        ],
    )
    report = ingest_transcript(tr, vault_root=vault, ledger=ledger)
    assert report.authored == 1
    assert report.candidates == 1
    assert report.event_kinds == {
        "tool_result": 1,
        "meta_injection": 1,
        "compact_summary": 1,
        "authored_user": 1,
    }
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "rebase-merge" in blob
    assert "task-notification" not in blob and "Context Usage" not in blob


def test_pipeline_dedup_skips_on_second_run(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger)
    report2 = ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger)
    assert report2.deduped == 1
    assert report2.written == []


def test_pipeline_dry_run_writes_nothing(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger, dry_run=True)
    assert report.written == []
    assert list(vault.rglob("*.md")) == []
    # dry-run must not poison the ledger
    assert report.deduped == 0

    # dry-run left the ledger clean: a real run now actually writes.
    real = ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger)
    assert len(real.written) == 1


# ---------------------------------------------------------------------------
# M3 — IngestReport.to_dict() must produce a JSON-serializable dict
# ---------------------------------------------------------------------------


def test_ingest_report_to_dict_is_json_serializable(tmp_path):
    """M3: IngestReport.to_dict() must be JSON-serializable (Paths -> str)."""
    from pathlib import Path

    report = IngestReport(
        candidates=3,
        redactions=1,
        deduped=1,
        gated_out=1,
        written=[Path("/vault/memories/note-abc.md")],
    )
    d = report.to_dict()
    serialized = json.dumps(d)  # must not raise
    parsed = json.loads(serialized)
    assert parsed["candidates"] == 3
    assert parsed["redactions"] == 1
    assert parsed["deduped"] == 1
    assert parsed["gated_out"] == 1
    assert parsed["written"] == ["/vault/memories/note-abc.md"]
    assert parsed["authored"] == 0
    assert parsed["event_kinds"] == {}


# ---------------------------------------------------------------------------
# M2 — Plan-5 seams must be exported from cairn.ingest
# ---------------------------------------------------------------------------


def test_ingest_package_exports_plan5_seams():
    """M2: cairn.ingest must export redact, RedactionResult, DedupLedger,
    content_hash, Distiller, ExtractiveDistiller, write_derived_note."""
    import cairn.ingest as pkg

    expected = [
        "redact",
        "RedactionResult",
        "DedupLedger",
        "content_hash",
        "Distiller",
        "ExtractiveDistiller",
        "write_derived_note",
    ]
    for name in expected:
        assert hasattr(pkg, name), f"cairn.ingest missing export: {name!r}"
        assert name in pkg.__all__, f"{name!r} not in cairn.ingest.__all__"


# ---------------------------------------------------------------------------
# Layer B — batched ingest with one judge call per run
# ---------------------------------------------------------------------------


def test_ingest_transcripts_judges_once_and_gates_by_combined_score(tmp_path):
    """One judge call per run across transcripts; combined = 0.5*heuristic+0.5*durability."""
    from cairn.ingest.judge import Judgment
    from cairn.ingest.pipeline import ingest_transcripts

    calls = []

    class SpyJudge:
        def judge(self, texts, *, contexts=None):
            calls.append(list(texts))
            # first candidate durable, second ephemeral
            return [
                Judgment(durability=1.0, title="Durable decision", distilled="The decision.")
                if "decided" in t
                else Judgment(durability=0.0)
                for t in texts
            ]

    t1 = Transcript(
        session_id="s1",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s1.jsonl",
        events=[
            _ev(
                EventKind.AUTHORED_USER,
                "We decided to always rebase-merge approved PRs because it is important.",
            )
        ],
    )
    t2 = Transcript(
        session_id="s2",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s2.jsonl",
        events=[
            _ev(
                EventKind.AUTHORED_USER,
                "Check the CI status on PR #76 and merge it if everything is green "
                "because we should ship.",
            )
        ],
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts([t1, t2], vault_root=vault, ledger=ledger, judge=SpyJudge())

    assert len(calls) == 1 and len(calls[0]) == 2  # ONE batched call for both transcripts
    # durable: 0.5*h + 0.5*1.0 >= 0.5 -> written; ephemeral: 0.5*h + 0.5*0 < 0.5 -> gated
    assert len(report.written) == 1
    assert report.gated_out >= 1
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "- [context] The decision. #ingested" in blob
    assert "- [verbatim] We decided" in blob
    assert "CI status" not in blob


def test_ingest_transcripts_without_judge_matches_legacy_behavior(tmp_path):
    from cairn.ingest.pipeline import ingest_transcripts

    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts(
        [_transcript(tmp_path)], vault_root=vault, ledger=ledger, judge=None
    )
    assert report.judge_tier == "none"
    assert len(report.written) == 1  # same as today's singular behavior


def test_ingest_transcript_singular_still_works(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger)
    assert len(report.written) == 1  # unchanged public API


def test_ingest_transcripts_survives_judge_raise(tmp_path):
    """Phase B must NEVER raise: a judge whose judge() blows up (e.g. embedder
    runtime failure) degrades to heuristic-only gating and counts in judge_degraded."""
    from cairn.ingest.pipeline import ingest_transcripts

    class BoomJudge:
        def judge(self, texts):
            raise RuntimeError("embedder blew up at runtime")

    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts(
        [_transcript(tmp_path)], vault_root=vault, ledger=ledger, judge=BoomJudge()
    )
    # judgments treated as absent -> heuristic-only gating, same as judge=None
    assert len(report.written) == 1
    assert report.gated_out == 1
    assert report.judge_degraded == 2  # both pending candidates fell to heuristic


# ---------------------------------------------------------------------------
# Layer B — judged-durability cache (gated-out candidates never re-hit the LLM)
# ---------------------------------------------------------------------------


def _ephemeral_transcript(tmp_path) -> Transcript:
    return Transcript(
        session_id="s-eph",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s-eph.jsonl",
        events=[
            _ev(
                EventKind.AUTHORED_USER,
                "Check the CI status on PR #76 and merge it if everything is green "
                "because we should ship.",
            )
        ],
    )


def test_judged_cache_skips_rejudging_gated_candidates(tmp_path):
    from cairn.ingest.judge import JudgedCache, Judgment
    from cairn.ingest.pipeline import ingest_transcripts

    calls: list[list[str]] = []

    class SpyJudge:
        def judge(self, texts, *, contexts=None):
            calls.append(list(texts))
            return [Judgment(durability=0.0) for _ in texts]  # everything gated out

    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    cache = JudgedCache(tmp_path / "judged.jsonl")

    rep1 = ingest_transcripts(
        [_ephemeral_transcript(tmp_path)],
        vault_root=vault,
        ledger=ledger,
        judge=SpyJudge(),
        judged_cache=cache,
    )
    assert rep1.gated_out == 1 and rep1.written == []
    assert len(calls) == 1 and len(calls[0]) == 1  # judged once

    # Second run (gated candidates are NOT ledgered, so they come back as pending):
    # the cache must answer instead of the judge — ZERO texts reach judge().
    rep2 = ingest_transcripts(
        [_ephemeral_transcript(tmp_path)],
        vault_root=vault,
        ledger=ledger,
        judge=SpyJudge(),
        judged_cache=JudgedCache(tmp_path / "judged.jsonl"),  # reload from disk
    )
    assert len(calls) == 1  # no second judge call at all
    assert rep2.gated_out == 1 and rep2.written == []  # cached durability still gates


def test_judged_cache_hit_still_flows_through_phase_c(tmp_path):
    """A cached durability is attached as a real Judgment: combined gating applies,
    and a durable cached candidate writes WITHOUT any judge call."""
    from cairn.ingest.dedup import content_hash
    from cairn.ingest.judge import JudgedCache
    from cairn.ingest.pipeline import ingest_transcripts

    class NeverCalledJudge:
        def judge(self, texts, *, contexts=None):
            raise AssertionError(f"judge must not be called, got {texts!r}")

    text = "We decided to always rebase-merge approved PRs because it is important."
    cache = JudgedCache(tmp_path / "judged.jsonl")
    from cairn.ingest.judge import Judgment

    cache.put(content_hash(text), Judgment(durability=1.0))
    t = Transcript(
        session_id="s-dur",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s-dur.jsonl",
        events=[_ev(EventKind.AUTHORED_USER, text)],
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts(
        [t], vault_root=vault, ledger=ledger, judge=NeverCalledJudge(), judged_cache=cache
    )
    assert len(report.written) == 1  # 0.5*h + 0.5*1.0 >= threshold


def test_report_judge_tier_recorded(tmp_path):
    from cairn.ingest.judge import EmbeddingJudge
    from cairn.ingest.pipeline import ingest_transcripts
    from tests.ingest.test_judge import StubEmbedder

    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts(
        [_transcript(tmp_path)],
        vault_root=vault,
        ledger=ledger,
        judge=EmbeddingJudge(StubEmbedder()),
    )
    assert report.judge_tier == "embedding"


def test_dry_run_does_not_write_judged_cache(tmp_path):
    """Bugbot (PR #57): a dry run deliberately downgrades the judge tier, so
    persisting its durabilities would make later REAL runs cache-hit and skip
    the LLM. Dry runs must leave the judged cache untouched (like the ledger)."""
    from cairn.ingest.judge import JudgedCache, Judgment
    from cairn.ingest.pipeline import ingest_transcripts

    class LowJudge:
        def judge(self, texts, *, contexts=None):
            return [Judgment(durability=0.0) for _ in texts]  # everything gates out

    vault = tmp_path / "vault"
    vault.mkdir()
    cache_path = tmp_path / "judged.jsonl"
    cache = JudgedCache(cache_path)
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts(
        [_transcript(tmp_path)],
        vault_root=vault,
        ledger=ledger,
        judge=LowJudge(),
        judged_cache=cache,
        dry_run=True,
    )
    assert report.gated_out >= 1  # the judge did gate candidates out
    assert not cache_path.exists()  # but NOTHING was persisted
    # and a fresh cache instance sees no entries
    assert JudgedCache(cache_path).get("anything") is None


def test_judged_cache_preserves_llm_distillation(tmp_path):
    """Bugbot (PR #57): a gated LLM judgment must cache title+distilled too —
    if a later run passes the gate (lower threshold), the cache-hit note must
    still get the distillation format, not a durability-only judgment."""
    from cairn.ingest.judge import JudgedCache, Judgment
    from cairn.ingest.pipeline import ingest_transcripts

    class LLMishJudge:
        degraded = 0

        def judge(self, texts, *, contexts=None):
            return [
                Judgment(durability=0.4, title="Rotation policy", distilled="Always rotate tokens.")
                for _ in texts
            ]

    vault = tmp_path / "vault"
    vault.mkdir()
    cache = JudgedCache(tmp_path / "judged.jsonl")
    ledger = DedupLedger(tmp_path / "led.sha256")
    # Run 1: threshold 0.9 gates everything out; full judgment cached.
    r1 = ingest_transcripts(
        [_transcript(tmp_path)],
        vault_root=vault,
        ledger=ledger,
        judge=LLMishJudge(),
        judged_cache=cache,
        threshold=0.9,
    )
    assert r1.written == [] and r1.gated_out >= 1

    class MustNotBeCalled:
        degraded = 0

        def judge(self, texts, *, contexts=None):
            raise AssertionError(f"LLM re-judged cached texts: {texts}")

    # Run 2: lower threshold; cache hits must pass the gate WITH distillation.
    r2 = ingest_transcripts(
        [_transcript(tmp_path)],
        vault_root=vault,
        ledger=DedupLedger(tmp_path / "led.sha256"),
        judge=MustNotBeCalled(),
        judged_cache=JudgedCache(tmp_path / "judged.jsonl"),
        threshold=0.3,
    )
    assert len(r2.written) >= 1
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "- [context] Always rotate tokens. #ingested" in blob  # distilled survived the cache
    assert "- [verbatim]" in blob
    assert "Rotation policy" in blob  # title survived too


def test_llm_run_ignores_embedding_cache_entry(tmp_path, monkeypatch):
    """An embedding-tier cache entry must NOT suppress an available LLM tier (the
    key-less-window poisoning bug from the 0.9.0 dogfood). Uses a real LLMJudge
    so the pipeline tags the run tier as "llm"."""
    import json as _json

    import cairn.ingest.judge as jmod
    from cairn.ingest.dedup import content_hash
    from cairn.ingest.judge import JudgedCache, Judgment, LLMJudge
    from cairn.ingest.pipeline import ingest_transcripts

    text = "We decided to always rebase-merge approved PRs because it is important."
    JudgedCache(tmp_path / "j.jsonl").put(
        content_hash(text), Judgment(durability=0.1), tier="embedding"
    )

    monkeypatch.setattr(
        jmod,
        "_anthropic_request",
        lambda payload, api_key, timeout: {
            "content": [
                {
                    "type": "text",
                    "text": _json.dumps(
                        [
                            {
                                "i": 0,
                                "durability": 0.9,
                                "title": "Rebase policy",
                                "distilled": "Always rebase-merge.",
                            }
                        ]
                    ),
                }
            ]
        },
    )
    judge = LLMJudge(api_key="k", model="m", timeout=5.0)

    vault = tmp_path / "v"
    vault.mkdir()
    tr = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[_ev(EventKind.AUTHORED_USER, text)],
    )
    ingest_transcripts(
        [tr],
        vault_root=vault,
        ledger=DedupLedger(tmp_path / "l.sha256"),
        judge=judge,
        judged_cache=JudgedCache(tmp_path / "j.jsonl"),
    )
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "Always rebase-merge." in blob  # LLM re-judged despite the embedding cache entry


def test_llm_tier_gates_on_durability_not_heuristic_blend(tmp_path, monkeypatch):
    """On the LLM tier the judge's durability gates the keep — a lexically long,
    marker-heavy turn the LLM rates ephemeral (durability ~0, null distilled) is
    DROPPED, even though 0.5*heuristic+0.5*durability would have kept it.
    Dogfood finding: the 50/50 blend was diluting the paid LLM verdict."""
    import json as _json

    import cairn.ingest.judge as jmod
    from cairn.ingest.judge import LLMJudge
    from cairn.ingest.pipeline import ingest_transcripts

    # long + marker-heavy -> high heuristic score; LLM says ephemeral.
    ephemeral = (
        "Ok so we should probably always remember to check the CI status on the pull "
        "request and then merge it because we decided that is important, but honestly "
        "this is really just routine coordination and process chatter that we do every "
        "single time after pushing any change to the branch."
    )
    monkeypatch.setattr(
        jmod,
        "_anthropic_request",
        lambda payload, api_key, timeout: {
            "content": [
                {
                    "type": "text",
                    "text": _json.dumps(
                        [{"i": 0, "durability": 0.05, "title": None, "distilled": None}]
                    ),
                }
            ]
        },
    )
    vault = tmp_path / "v"
    vault.mkdir()
    tr = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[_ev(EventKind.AUTHORED_USER, ephemeral)],
    )
    rep = ingest_transcripts(
        [tr],
        vault_root=vault,
        ledger=DedupLedger(tmp_path / "l.sha256"),
        judge=LLMJudge(api_key="k", model="m", timeout=5.0),
        judged_cache=None,
    )
    assert rep.judge_tier == "llm"
    assert rep.written == []  # dropped: durability 0.05 < 0.5 (NOT 0.5*high_heuristic+0.5*0.05)
    assert rep.gated_out == 1


def test_llm_tier_keeps_iff_distilled(tmp_path, monkeypatch):
    """LLM tier: the LLM's DISTILL decision is the keep signal, not the durability
    float (which clusters 0.3-0.5). A distilled turn is kept even at low durability;
    a null-distilled turn is dropped even at HIGH durability. Dogfood: a durability
    threshold swept in 277 short junk turns the LLM rated ~0.5 but didn't distill."""
    import json as _json

    import cairn.ingest.judge as jmod
    from cairn.ingest.judge import LLMJudge
    from cairn.ingest.pipeline import ingest_transcripts

    monkeypatch.setattr(
        jmod,
        "_anthropic_request",
        lambda payload, api_key, timeout: {
            "content": [
                {
                    "type": "text",
                    "text": _json.dumps(
                        [
                            {
                                "i": 0,
                                "durability": 0.4,
                                "title": "Real decision",
                                "distilled": "We always rebase.",
                            },
                            {"i": 1, "durability": 0.95, "title": None, "distilled": None},
                        ]
                    ),
                }
            ]
        },
    )
    vault = tmp_path / "v"
    vault.mkdir()
    tr = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[
            _ev(
                EventKind.AUTHORED_USER, "we should always rebase-merge our approved pull requests"
            ),
            _ev(
                EventKind.AUTHORED_USER,
                "ok lets proceed with checking the status of the deploy now",
            ),
        ],
    )
    rep = ingest_transcripts(
        [tr],
        vault_root=vault,
        ledger=DedupLedger(tmp_path / "l.sha256"),
        judge=LLMJudge(api_key="k", model="m", timeout=5.0),
        judged_cache=None,
    )
    assert rep.judge_tier == "llm"
    assert (
        len(rep.written) == 1
    )  # only the distilled one (durability 0.4), NOT the null-distilled 0.95
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "We always rebase." in blob
    assert "proceed with checking" not in blob


def test_degraded_llm_chunk_does_not_poison_cache(tmp_path, monkeypatch):
    """Bugbot #61: when an LLM chunk degrades (API failure -> embedding/neutral
    fallback) the verdict has distilled=None but is NOT a real LLM verdict. It
    must not be cached at tier "llm", or a later SUCCESSFUL run would reuse the
    degraded verdict and permanently drop a durable turn after one transient blip.
    A degraded chunk also gates via the embedding blend, not the LLM keep rule."""
    import json as _json

    import cairn.ingest.judge as jmod
    from cairn.ingest.dedup import content_hash
    from cairn.ingest.judge import JudgedCache, Judgment, LLMJudge
    from cairn.ingest.pipeline import ingest_transcripts

    text = "we should always rebase-merge our approved pull requests"

    # Run 1: the API call fails -> the chunk degrades to the fallback, which rates
    # this turn ephemeral (durability 0.0, no distillation). It gates out.
    monkeypatch.setattr(
        jmod,
        "_anthropic_request",
        lambda payload, api_key, timeout: (_ for _ in ()).throw(RuntimeError("transient")),
    )

    class LowFallback:
        def judge(self, texts, *, contexts=None):
            return [Judgment(durability=0.0) for _ in texts]

    cache_path = tmp_path / "j.jsonl"
    vault = tmp_path / "v"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "l.sha256")
    tr = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[_ev(EventKind.AUTHORED_USER, text)],
    )
    rep1 = ingest_transcripts(
        [tr],
        vault_root=vault,
        ledger=ledger,
        judge=LLMJudge(api_key="k", model="m", timeout=5.0, fallback=LowFallback()),
        judged_cache=JudgedCache(cache_path),
    )
    assert rep1.judge_degraded == 1 and rep1.written == [] and rep1.gated_out == 1
    # The degraded verdict must NOT sit in the cache at tier "llm".
    entry = JudgedCache(cache_path).get(content_hash(text))
    assert entry is None or entry[1] != "llm"

    # Run 2: the API works and distills. The candidate (never ledgered in run 1)
    # must be RE-JUDGED — not blocked by the degraded cache entry — and written.
    monkeypatch.setattr(
        jmod,
        "_anthropic_request",
        lambda payload, api_key, timeout: {
            "content": [
                {
                    "type": "text",
                    "text": _json.dumps(
                        [
                            {
                                "i": 0,
                                "durability": 0.4,
                                "title": "Rebase policy",
                                "distilled": "Always rebase-merge.",
                            }
                        ]
                    ),
                }
            ]
        },
    )
    ingest_transcripts(
        [tr],
        vault_root=vault,
        ledger=ledger,
        judge=LLMJudge(api_key="k", model="m", timeout=5.0),
        judged_cache=JudgedCache(cache_path),
    )
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "Always rebase-merge." in blob  # re-judged after the transient failure


# ---------------------------------------------------------------------------
# Antecedent resolution — nearest preceding assistant turn per session
# ---------------------------------------------------------------------------


def _ev_sid(kind, text, sid, ts="t0"):
    """Like _ev but with an explicit session_id (default _ev uses None)."""
    from pathlib import Path

    return NormalizedEvent(
        kind=kind,
        role="user" if kind == EventKind.AUTHORED_USER else "assistant",
        text=text,
        timestamp=ts,
        session_id=sid,
        project="p",
        git_branch="main",
        source_path=Path("/tmp/s.jsonl"),
    )


def test_select_candidates_attaches_nearest_preceding_assistant():
    from cairn.ingest.pipeline import select_candidates

    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=__import__("pathlib").Path("/tmp/s.jsonl"),
        events=[
            _ev(
                EventKind.AUTHORED_ASSISTANT, "I propose approach A: the orderbook representation."
            ),
            _ev(EventKind.TOOL_RESULT, "some tool output"),  # must NOT clear the antecedent
            _ev(EventKind.AUTHORED_USER, "lock A"),
        ],
    )
    (cand,) = select_candidates(t)
    assert cand.text == "lock A"
    assert cand.antecedent == "I propose approach A: the orderbook representation."


def test_select_candidates_no_antecedent_before_any_assistant():
    from cairn.ingest.pipeline import select_candidates

    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=__import__("pathlib").Path("/tmp/s.jsonl"),
        events=[_ev(EventKind.AUTHORED_USER, "first turn, no prior assistant")],
    )
    (cand,) = select_candidates(t)
    assert cand.antecedent is None


def test_select_candidates_does_not_cross_session_boundary():
    from cairn.ingest.pipeline import select_candidates

    t = Transcript(
        session_id="s1",
        cwd="/Users/x/p",
        git_branch="main",
        path=__import__("pathlib").Path("/tmp/s.jsonl"),
        events=[
            _ev_sid(EventKind.AUTHORED_ASSISTANT, "proposal in session one", "s1"),
            _ev_sid(EventKind.AUTHORED_USER, "user turn in session two", "s2"),
        ],
    )
    (cand,) = select_candidates(t)
    assert cand.antecedent is None  # the s1 proposal must not resolve an s2 turn


def test_select_candidates_keeps_full_antecedent_untruncated():
    """select_candidates stores the FULL antecedent; truncation happens later in
    Phase A AFTER redaction (truncating first could fragment a boundary-straddling
    secret and leak it to the judge)."""
    from cairn.ingest.pipeline import _ANTECEDENT_CHARS, select_candidates

    long_proposal = "x" * (_ANTECEDENT_CHARS + 500)
    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=__import__("pathlib").Path("/tmp/s.jsonl"),
        events=[
            _ev(EventKind.AUTHORED_ASSISTANT, long_proposal),
            _ev(EventKind.AUTHORED_USER, "go with it"),
        ],
    )
    (cand,) = select_candidates(t)
    assert cand.antecedent == long_proposal  # full, untruncated at selection time


def test_select_candidates_consecutive_user_turns_share_antecedent():
    from cairn.ingest.pipeline import select_candidates

    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=__import__("pathlib").Path("/tmp/s.jsonl"),
        events=[
            _ev(EventKind.AUTHORED_ASSISTANT, "I propose approach A."),
            _ev(EventKind.AUTHORED_USER, "lock A"),
            _ev(EventKind.AUTHORED_USER, "and also document it"),
        ],
    )
    c1, c2 = select_candidates(t)
    assert c1.antecedent == "I propose approach A."
    assert c2.antecedent == "I propose approach A."  # a user turn does not clear it


def test_pipeline_passes_antecedent_as_judge_context_and_writes_resolved(tmp_path, monkeypatch):
    """The pipeline feeds each candidate's antecedent to the real LLM judge as
    context (rendered into the prompt), and a resolved distillation is written
    self-contained; [verbatim] stays the user's words."""
    import json as _json

    import cairn.ingest.judge as jmod
    from cairn.ingest.judge import LLMJudge
    from cairn.ingest.pipeline import ingest_transcripts

    seen = {}
    _resolved = "Approach A — the orderbook representation — is the locked direction."

    def fake_request(payload, api_key, timeout):
        seen["body"] = payload["messages"][0]["content"]
        return {
            "content": [
                {
                    "type": "text",
                    "text": _json.dumps(
                        [
                            {
                                "i": 0,
                                "durability": 0.8,
                                "title": "Lock approach A: orderbook representation",
                                "distilled": _resolved,
                            }
                        ]
                    ),
                }
            ]
        }

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[
            _ev(EventKind.AUTHORED_ASSISTANT, "Approach A is the orderbook representation."),
            _ev(EventKind.AUTHORED_USER, "lock A"),
        ],
    )
    vault = tmp_path / "v"
    vault.mkdir()
    rep = ingest_transcripts(
        [t],
        vault_root=vault,
        ledger=DedupLedger(tmp_path / "l.sha256"),
        judge=LLMJudge(api_key="k", model="m", timeout=5.0),
    )
    assert rep.judge_tier == "llm"
    # the antecedent was rendered into the judge prompt as resolution context
    assert "Approach A is the orderbook representation." in seen["body"]
    assert "DEVELOPER MESSAGE: lock A" in seen["body"]
    assert len(rep.written) == 1
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "orderbook representation" in blob  # resolved distillation is self-contained
    assert "- [verbatim] lock A" in blob  # verbatim is still the user's literal turn


def test_phase_a_redacts_antecedent_before_judge(tmp_path):
    """An antecedent containing a secret must be redacted before the judge sees
    it, and the redaction must be counted in report.redactions."""
    from cairn.ingest.judge import Judgment
    from cairn.ingest.pipeline import ingest_transcripts

    seen_contexts = []

    class SpyJudge:
        degraded = 0

        def judge(self, texts, *, contexts=None):
            seen_contexts.extend(contexts or [None] * len(texts))
            return [Judgment(durability=0.0) for _ in texts]  # gate out; we only inspect input

    secret = "sk-ant-api03-" + "A" * 40 + "-deadbeefcafe1234567890AB_cd-ef"
    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[
            _ev(EventKind.AUTHORED_ASSISTANT, f"Use this key: {secret} for option A."),
            _ev(EventKind.AUTHORED_USER, "lock A"),
        ],
    )
    vault = tmp_path / "v"
    vault.mkdir()
    rep = ingest_transcripts(
        [t], vault_root=vault, ledger=DedupLedger(tmp_path / "l.sha256"), judge=SpyJudge()
    )
    assert seen_contexts, "judge received no contexts"
    assert secret not in (seen_contexts[0] or "")  # raw secret never reaches the judge
    assert "[REDACTED:" in (seen_contexts[0] or "")
    assert rep.redactions >= 1  # antecedent redaction counted


def test_antecedent_secret_straddling_truncation_boundary_is_redacted(tmp_path):
    """A secret straddling the _ANTECEDENT_CHARS boundary must be redacted WHOLE
    (redact runs before truncation) — truncating first would fragment it and leak
    the prefix to the judge."""
    from cairn.ingest.judge import Judgment
    from cairn.ingest.pipeline import _ANTECEDENT_CHARS, ingest_transcripts

    seen = []

    class SpyJudge:
        degraded = 0

        def judge(self, texts, *, contexts=None):
            seen.extend(contexts or [None] * len(texts))
            return [Judgment(durability=0.0) for _ in texts]

    key = "sk-ant-api03-" + "Z" * 40 + "-deadbeefcafe1234567890AB_cd-ef"
    # position the key so it starts ~10 chars before the truncation boundary
    antecedent = ("x" * (_ANTECEDENT_CHARS - 10)) + key + " trailing context for option A"
    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[
            _ev(EventKind.AUTHORED_ASSISTANT, antecedent),
            _ev(EventKind.AUTHORED_USER, "lock A"),
        ],
    )
    vault = tmp_path / "v"
    vault.mkdir()
    ingest_transcripts(
        [t], vault_root=vault, ledger=DedupLedger(tmp_path / "l.sha256"), judge=SpyJudge()
    )
    ctx = seen[0] or ""
    assert key not in ctx  # full secret never reaches the judge
    assert "sk-ant" not in ctx  # not even a fragment of it
    assert len(ctx) <= _ANTECEDENT_CHARS  # still truncated, but AFTER redaction
