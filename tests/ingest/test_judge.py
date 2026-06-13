# tests/ingest/test_judge.py
# SPDX-License-Identifier: Apache-2.0
import json

from cairn.ingest.judge import (
    _DURABLE_PROTOTYPES,
    _EPHEMERAL_PROTOTYPES,
    EmbeddingJudge,
    Judgment,
)


class StubEmbedder:
    """Maps durable-ish texts near axis-0, ephemeral-ish near axis-1.
    The FakeEmbedder's hash vectors are NOT semantic, so judge tests use this
    purpose-built stub: prototypes and candidates land on designed clusters."""

    model_id = "stub"
    dim = 2

    def _vec(self, text: str) -> list[float]:
        if text.startswith("D:") or text in _DURABLE_PROTOTYPES:
            return [1.0, 0.05]
        if text.startswith("E:") or text in _EPHEMERAL_PROTOTYPES:
            return [0.05, 1.0]
        return [0.5, 0.5]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def test_judgment_defaults():
    j = Judgment(durability=0.7)
    assert j.title is None and j.distilled is None


def test_embedding_judge_separates_clusters():
    judge = EmbeddingJudge(StubEmbedder())
    out = judge.judge(["D: we decided to always rebase-merge", "E: check CI on PR #76"])
    assert len(out) == 2
    assert out[0].durability > 0.5 > out[1].durability
    # embedding tier never produces title/distilled
    assert out[0].title is None and out[0].distilled is None


def test_embedding_judge_durability_clamped_01():
    judge = EmbeddingJudge(StubEmbedder())
    for j in judge.judge(["D: a", "E: b", "neutral text"]):
        assert 0.0 <= j.durability <= 1.0


def test_embedding_judge_neutral_text_near_half():
    judge = EmbeddingJudge(StubEmbedder())
    (j,) = judge.judge(["neutral text"])
    assert 0.35 <= j.durability <= 0.65  # equidistant -> margin ~0 -> ~0.5


def test_embedding_judge_empty_input():
    assert EmbeddingJudge(StubEmbedder()).judge([]) == []


def test_llm_judge_parses_batched_response(monkeypatch):
    import cairn.ingest.judge as jmod

    def fake_request(payload, api_key, timeout):
        # assert the batch shape: one request, all texts numbered
        body = payload["messages"][0]["content"]
        assert "[0]" in body and "[1]" in body
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        '[{"i": 0, "durability": 0.9, "title": "Rebase-merge convention",'
                        ' "distilled": "Always rebase-merge approved PRs."},'
                        ' {"i": 1, "durability": 0.1, "title": null, "distilled": null}]'
                    ),
                }
            ]
        }

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)
    out = judge.judge(["we always rebase-merge", "check the CI please now"])
    assert out[0].durability == 0.9 and out[0].title == "Rebase-merge convention"
    assert out[0].distilled == "Always rebase-merge approved PRs."
    assert out[1].durability == 0.1 and out[1].title is None


def test_llm_judge_timeout_scales_with_batch_size(monkeypatch):
    """The per-request timeout must scale with the chunk size: a fixed small
    timeout (the old default 10s) cannot cover a full batch of 40 messages
    (~30s on Sonnet), so every batch would time out and degrade silently.
    The effective timeout is at least _TIMEOUT_PER_MSG_S per message."""
    import cairn.ingest.judge as jmod

    seen = {}

    def fake_request(payload, api_key, timeout):
        seen["timeout"] = timeout
        n = payload["messages"][0]["content"].count("\n[")  # rough message count
        return {
            "content": [
                {
                    "type": "text",
                    "text": "["
                    + ",".join(f'{{"i": {i}, "durability": 0.1}}' for i in range(n + 1))
                    + "]",
                }
            ]
        }

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=10.0)  # too-low fixed value
    judge.judge([f"message number {i}" for i in range(20)])
    assert seen["timeout"] >= jmod._TIMEOUT_PER_MSG_S * 20  # scaled past the fixed 10s
    assert seen["timeout"] > 10.0


def test_llm_judge_degrades_on_error(monkeypatch):
    import cairn.ingest.judge as jmod

    def boom(payload, api_key, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr(jmod, "_anthropic_request", boom)
    fallback = EmbeddingJudge(StubEmbedder())
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=1.0, fallback=fallback)
    out = judge.judge(["D: decision text here"])
    assert len(out) == 1 and out[0].durability > 0.5  # fallback judged it
    assert judge.degraded == 1


def test_llm_judge_degrades_on_malformed_json(monkeypatch):
    import cairn.ingest.judge as jmod

    monkeypatch.setattr(
        jmod,
        "_anthropic_request",
        lambda payload, api_key, timeout: {"content": [{"type": "text", "text": "not json"}]},
    )
    judge = jmod.LLMJudge(
        api_key="k", model="m", timeout=1.0, fallback=EmbeddingJudge(StubEmbedder())
    )
    out = judge.judge(["D: decision"])
    assert len(out) == 1 and judge.degraded == 1


def test_llm_judge_discards_overlong_distillation(monkeypatch):
    import cairn.ingest.judge as jmod

    text = "short decision"
    monkeypatch.setattr(
        jmod,
        "_anthropic_request",
        lambda payload, api_key, timeout: {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        [{"i": 0, "durability": 0.8, "title": "T", "distilled": "x" * 500}]
                    ),
                }
            ]
        },
    )

    judge = jmod.LLMJudge(api_key="k", model="m", timeout=1.0)
    (j,) = judge.judge([text])
    assert j.durability == 0.8 and j.distilled is None  # >4x verbatim length -> discarded


def _echo_request(durability=0.5):
    """Fake _anthropic_request answering every numbered input in the prompt."""
    import re

    def fake(payload, api_key, timeout):
        body = payload["messages"][0]["content"]
        idxs = [int(m) for m in re.findall(r"^\[(\d+)\]", body, flags=re.M)]
        items = [{"i": i, "durability": durability, "title": None, "distilled": None} for i in idxs]
        return {"content": [{"type": "text", "text": json.dumps(items)}]}

    return fake


def test_llm_judge_chunks_large_batches(monkeypatch):
    import re

    import cairn.ingest.judge as jmod

    sizes: list[int] = []
    inner = _echo_request(0.9)

    def fake(payload, api_key, timeout):
        body = payload["messages"][0]["content"]
        sizes.append(len(re.findall(r"^\[\d+\]", body, flags=re.M)))
        return inner(payload, api_key, timeout)

    monkeypatch.setattr(jmod, "_anthropic_request", fake)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)
    out = judge.judge([f"text number {n}" for n in range(90)])
    assert len(out) == 90
    bs = jmod._BATCH_SIZE
    expected = [bs] * (90 // bs) + ([90 % bs] if 90 % bs else [])
    assert sizes == expected  # chunked by _BATCH_SIZE, never one giant truncation-prone call
    assert judge.degraded == 0


def test_llm_judge_chunk_failure_degrades_only_that_chunk(monkeypatch):
    import cairn.ingest.judge as jmod

    calls = {"n": 0}
    inner = _echo_request(0.9)

    def flaky(payload, api_key, timeout):
        calls["n"] += 1
        if calls["n"] == 2:
            raise TimeoutError("slow")
        return inner(payload, api_key, timeout)

    monkeypatch.setattr(jmod, "_anthropic_request", flaky)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)  # no fallback -> neutral
    out = judge.judge([f"text number {n}" for n in range(90)])
    bs = jmod._BATCH_SIZE
    assert len(out) == 90
    assert judge.degraded == bs  # ONLY the failed 2nd chunk (indices bs..2*bs)
    assert out[0].durability == 0.9  # chunk 1 judged
    assert out[bs].durability == 0.5  # chunk 2 neutral
    assert out[2 * bs].durability == 0.9  # chunk 3 judged


def test_llm_judge_tolerates_missing_index(monkeypatch):
    """If the model returns valid JSON that OMITS an index (seen on large
    antecedent-laden batches), only that item degrades — the rest of the chunk's
    real verdicts survive, and the whole batch is not nuked (the old code raised
    'missing judgment for index N', degrading all 40)."""
    import json as _json

    import cairn.ingest.judge as jmod

    def fake_request(payload, api_key, timeout):
        # respond for indices 0 and 2 only; OMIT index 1
        return {
            "content": [
                {
                    "type": "text",
                    "text": _json.dumps(
                        [
                            {
                                "i": 0,
                                "durability": 0.9,
                                "title": "T0",
                                "distilled": "Decision zero.",
                            },
                            {
                                "i": 2,
                                "durability": 0.8,
                                "title": "T2",
                                "distilled": "Decision two.",
                            },
                        ]
                    ),
                }
            ]
        }

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)  # no fallback -> neutral
    out = judge.judge(["turn zero", "turn one", "turn two"])
    assert len(out) == 3  # every index accounted for, no raise
    assert out[0].distilled == "Decision zero." and not out[0].degraded
    assert out[2].distilled == "Decision two." and not out[2].degraded
    assert out[1].degraded is True and out[1].distilled is None  # the omitted index
    assert judge.degraded == 1  # ONLY the missing one, not all 3


def test_llm_judge_missing_index_uses_fallback(monkeypatch):
    """An omitted index is filled from the fallback judge (marked degraded), not
    just neutral 0.5, when a fallback is available."""
    import json as _json

    import cairn.ingest.judge as jmod
    from cairn.ingest.judge import EmbeddingJudge

    def fake_request(payload, api_key, timeout):
        return {  # respond for index 0 only; OMIT index 1
            "content": [
                {
                    "type": "text",
                    "text": _json.dumps(
                        [{"i": 0, "durability": 0.9, "title": "T", "distilled": "D."}]
                    ),
                }
            ]
        }

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(
        api_key="k", model="m", timeout=5.0, fallback=EmbeddingJudge(StubEmbedder())
    )
    out = judge.judge(["D: a durable decision", "D: another durable decision"])
    assert len(out) == 2
    assert out[0].distilled == "D." and not out[0].degraded
    assert out[1].degraded is True  # filled from the embedding fallback
    assert out[1].durability > 0.5  # the StubEmbedder rates "D:" texts durable
    assert judge.degraded == 1


def test_llm_judge_max_tokens_is_16k(monkeypatch):
    import cairn.ingest.judge as jmod

    seen = {}

    def fake_request(payload, api_key, timeout):
        seen["max_tokens"] = payload["max_tokens"]
        return {"content": [{"type": "text", "text": '[{"i": 0, "durability": 0.1}]'}]}

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    jmod.LLMJudge(api_key="k", model="m", timeout=5.0).judge(["x"])
    assert seen["max_tokens"] == 16384  # raised from 8192 to fit large antecedent batches


def test_llm_judge_tolerates_malformed_item(monkeypatch):
    """Per-item degrade is total: a valid-JSON response where one item is missing
    its `durability` (or has a garbled `i`) degrades only that index, not the
    whole chunk. Only top-level invalid/truncated JSON degrades everything."""
    import json as _json

    import cairn.ingest.judge as jmod

    def fake_request(payload, api_key, timeout):
        return {
            "content": [
                {
                    "type": "text",
                    "text": _json.dumps(
                        [
                            {
                                "i": 0,
                                "durability": 0.9,
                                "title": "T0",
                                "distilled": "Decision zero.",
                            },
                            {"i": 1, "title": "no durability field"},  # malformed item
                        ]
                    ),
                }
            ]
        }

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)
    out = judge.judge(["turn zero", "turn one"])
    assert len(out) == 2
    assert out[0].distilled == "Decision zero." and not out[0].degraded
    assert out[1].degraded is True and out[1].distilled is None  # malformed -> degraded
    assert judge.degraded == 1  # only the malformed item


def test_judged_cache_roundtrip(tmp_path):
    from cairn.ingest.judge import JudgedCache

    p = tmp_path / "judged.jsonl"
    c = JudgedCache(p)  # missing file -> empty
    assert c.get("abc") is None
    j = Judgment(durability=0.25, title="T", distilled="The fact.")
    c.put("abc", j, tier="llm")
    assert c.get("abc") == (j, "llm")
    c.put("abc", j, tier="llm")  # idempotent: no duplicate line
    assert len([ln for ln in p.read_text().splitlines() if ln.strip()]) == 1
    c2 = JudgedCache(p)  # reload from disk
    assert c2.get("abc") == (j, "llm")  # full judgment + tier survive disk roundtrip
    assert c2.get("missing") is None


def test_resolve_judge_modes(monkeypatch):
    from cairn.ingest.judge import LLMJudge, resolve_judge

    # none / no -> None
    assert resolve_judge(env={"CAIRN_JUDGE": "none"}, embedder=StubEmbedder()) is None
    assert resolve_judge(env={"CAIRN_JUDGE": "no"}, embedder=StubEmbedder()) is None
    # embedding (default) -> EmbeddingJudge
    j = resolve_judge(env={}, embedder=StubEmbedder())
    assert isinstance(j, EmbeddingJudge)
    # anthropic without key -> degrades to embedding
    j2 = resolve_judge(env={"CAIRN_JUDGE": "anthropic"}, embedder=StubEmbedder())
    assert isinstance(j2, EmbeddingJudge)
    # anthropic with key -> LLMJudge with embedding fallback
    j3 = resolve_judge(
        env={"CAIRN_JUDGE": "anthropic", "ANTHROPIC_API_KEY": "k"}, embedder=StubEmbedder()
    )
    assert isinstance(j3, LLMJudge)


def test_resolve_judge_no_embedder_is_none():
    from cairn.ingest.judge import resolve_judge

    def broken_loader():
        raise RuntimeError("no model")

    assert resolve_judge(env={}, embedder_loader=broken_loader) is None


def test_embedding_judge_chunks_large_batches():
    """A first-run/rebuild can pend ~1000 candidates; embeds must be chunked
    (one giant embed() batch OOM-killed the process on real data)."""
    sizes = []

    class CountingEmbedder(StubEmbedder):
        def embed(self, texts):
            sizes.append(len(texts))
            return super().embed(texts)

    judge = EmbeddingJudge(CountingEmbedder())
    out = judge.judge([f"text number {i}" for i in range(150)])
    assert len(out) == 150
    # 2 prototype batches at init + chunked candidate batches of <=64
    assert all(s <= 64 for s in sizes)
    assert sizes[2:] == [64, 64, 22]


def test_judge_input_clipped_for_huge_texts():
    """A ~300KB pasted blob OOM-killed the embedder on real data; both judge tiers
    must clip their INPUT (the stored note keeps the full text)."""
    received = []

    class SpyEmbedder(StubEmbedder):
        def embed(self, texts):
            received.extend(texts)
            return super().embed(texts)

    judge = EmbeddingJudge(SpyEmbedder())
    received.clear()  # drop prototype batches
    out = judge.judge(["x" * 300_000])
    assert len(out) == 1
    assert max(len(t) for t in received) <= 2000


def test_llm_prompt_clips_huge_texts(monkeypatch):
    import cairn.ingest.judge as jmod

    seen = {}

    def fake_request(payload, api_key, timeout):
        seen["len"] = len(payload["messages"][0]["content"])
        return {
            "content": [
                {
                    "type": "text",
                    "text": '[{"i": 0, "durability": 0.5, "title": null, "distilled": null}]',
                }
            ]
        }

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)
    judge.judge(["y" * 300_000])
    assert seen["len"] < 5000  # prompt + clipped text, not 300KB


def test_llm_chunk_survives_failing_fallback(monkeypatch):
    """Bugbot (PR #57): if a chunk's LLM call fails AND the fallback raises,
    the exception must not escape — that chunk degrades to neutral judgments
    and earlier chunks' successful results are preserved."""
    import json as _json

    import cairn.ingest.judge as jmod

    calls = {"n": 0}

    def flaky_request(payload, api_key, timeout):
        calls["n"] += 1
        if calls["n"] == 2:  # second chunk's LLM call fails
            raise TimeoutError("slow")
        body = payload["messages"][0]["content"]
        count = sum(1 for line in body.splitlines() if line.startswith("["))
        return {
            "content": [
                {
                    "type": "text",
                    "text": _json.dumps(
                        [
                            {"i": i, "durability": 0.9, "title": None, "distilled": None}
                            for i in range(count)
                        ]
                    ),
                }
            ]
        }

    class ExplodingFallback:
        def judge(self, texts):
            raise RuntimeError("embedder died")

    monkeypatch.setattr(jmod, "_anthropic_request", flaky_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=1.0, fallback=ExplodingFallback())
    bs = jmod._BATCH_SIZE
    texts = [f"text {i}" for i in range(4 * bs)]  # four chunks
    out = judge.judge(texts)
    assert len(out) == 4 * bs  # nothing lost
    assert all(j.durability == 0.9 for j in out[:bs])  # chunk 1 results preserved
    assert all(j.durability == 0.5 for j in out[bs : 2 * bs])  # chunk 2 neutral, not raised
    assert all(j.durability == 0.9 for j in out[2 * bs :])  # chunks 3-4 preserved
    assert judge.degraded == bs  # counted once, not twice


def test_judged_cache_records_tier(tmp_path):
    from cairn.ingest.judge import JudgedCache, Judgment

    c = JudgedCache(tmp_path / "j.jsonl")
    c.put("h1", Judgment(durability=0.3), tier="embedding")
    c.put("h2", Judgment(durability=0.9, title="T", distilled="D."), tier="llm")
    c2 = JudgedCache(tmp_path / "j.jsonl")
    assert c2.get("h1") == (Judgment(durability=0.3), "embedding")
    assert c2.get("h2")[1] == "llm"
    assert c2.get("missing") is None


def test_judged_cache_discards_stale_and_versionless_rows(tmp_path):
    """A judge/prompt change (or a degradation-bug fix) bumps the cache version;
    rows from an older version — and legacy rows with no version at all — are
    discarded on load so the candidate is re-judged, never reused. This is what
    keeps a poisoned cache (e.g. the silent-timeout era) from being carried
    forward forever."""
    import json

    from cairn.ingest.judge import _JUDGE_CACHE_VERSION, JudgedCache

    p = tmp_path / "j.jsonl"
    p.write_text(
        json.dumps({"h": "legacy", "d": 0.4, "tier": "llm"})  # no "v"
        + "\n"
        + json.dumps({"h": "old", "d": 0.9, "tier": "llm", "v": _JUDGE_CACHE_VERSION - 1})
        + "\n"
    )
    c = JudgedCache(p)
    assert c.get("legacy") is None  # version-less -> discarded
    assert c.get("old") is None  # older version -> discarded


def test_judged_cache_roundtrips_current_version(tmp_path):
    from cairn.ingest.judge import JudgedCache, Judgment

    p = tmp_path / "j.jsonl"
    JudgedCache(p).put("h", Judgment(durability=0.9, title="T", distilled="D."), tier="llm")
    # reload: a current-version row survives with its full judgment + tier
    assert JudgedCache(p).get("h") == (Judgment(durability=0.9, title="T", distilled="D."), "llm")


def test_tier_at_least():
    from cairn.ingest.judge import tier_at_least

    assert tier_at_least("llm", "embedding")  # llm entry usable on an embedding run
    assert tier_at_least("embedding", "embedding")
    assert not tier_at_least("embedding", "llm")  # embedding entry NOT usable on an llm run
    assert tier_at_least("llm", "llm")


def test_embedding_judge_accepts_and_ignores_contexts():
    judge = EmbeddingJudge(StubEmbedder())
    texts = ["D: we decided to always rebase-merge", "E: check CI on PR #76"]
    without = judge.judge(texts)
    with_ctx = judge.judge(texts, contexts=["some prior assistant proposal", None])
    # the embedding tier produces no distillation, so the antecedent is irrelevant
    assert [j.durability for j in with_ctx] == [j.durability for j in without]


def test_resolve_only_instruction_present_in_prompt():
    from cairn.ingest.judge import _PROMPT

    assert "PRIOR ASSISTANT MESSAGE" in _PROMPT
    assert "resolve" in _PROMPT.lower()
    assert "acknowledgement" in _PROMPT.lower() or "contentless" in _PROMPT.lower()


def test_llm_judge_renders_prior_assistant_block_only_when_present(monkeypatch):
    import cairn.ingest.judge as jmod

    bodies = []

    def fake_request(payload, api_key, timeout):
        body = payload["messages"][0]["content"]
        bodies.append(body)
        return {
            "content": [
                {
                    "type": "text",
                    "text": '[{"i":0,"durability":0.1,"title":null,"distilled":null},'
                    '{"i":1,"durability":0.1,"title":null,"distilled":null}]',
                }
            ]
        }

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)
    judge.judge(["lock A", "we should always rebase-merge"], contexts=["Propose approach A", None])
    body = bodies[0]
    assert "PRIOR ASSISTANT MESSAGE (context only): Propose approach A" in body
    assert "DEVELOPER MESSAGE: lock A" in body
    assert "[1] we should always rebase-merge" in body  # no antecedent -> rendered plainly


def test_llm_judge_contexts_index_aligned_across_chunks(monkeypatch):
    """contexts must stay aligned with texts within each _BATCH_SIZE chunk."""
    import cairn.ingest.judge as jmod

    seen_blocks = []

    def fake_request(payload, api_key, timeout):
        body = payload["messages"][0]["content"]
        for line in body.splitlines():
            if "PRIOR ASSISTANT MESSAGE" in line:
                seen_blocks.append(line)
        import re

        idxs = [int(m) for m in re.findall(r"^\[(\d+)\]", body, flags=re.M)]
        items = [{"i": i, "durability": 0.1, "title": None, "distilled": None} for i in idxs]
        return {"content": [{"type": "text", "text": __import__("json").dumps(items)}]}

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)
    n = jmod._BATCH_SIZE + 5  # force two chunks
    texts = [f"msg {i}" for i in range(n)]
    contexts = [f"ctx {i}" if i % 2 == 0 else None for i in range(n)]
    out = judge.judge(texts, contexts=contexts)
    assert len(out) == n
    assert len(seen_blocks) == len([c for c in contexts if c])
    assert all(f"ctx {i}" in "\n".join(seen_blocks) for i in range(n) if i % 2 == 0)


def test_distill_ratio_uses_antecedent_length_when_present(monkeypatch):
    """A terse turn ('lock A') resolved against a long antecedent may have a
    distillation far longer than 4x the turn — it must survive the ratio guard.
    Without an antecedent, the guard still discards an overlong distillation."""
    import cairn.ingest.judge as jmod

    long_distilled = (
        "Approach A — the orderbook representation — is the locked direction."  # ~67 chars
    )

    def fake_request(payload, api_key, timeout):
        return {
            "content": [
                {
                    "type": "text",
                    "text": __import__("json").dumps(
                        [{"i": 0, "durability": 0.8, "title": "T", "distilled": long_distilled}]
                    ),
                }
            ]
        }

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)

    # WITH a long antecedent: 67 chars <= 4 * len(antecedent) -> kept
    (with_ctx,) = judge.judge(
        ["lock A"], contexts=["Approach A is the orderbook representation strategy."]
    )
    assert with_ctx.distilled == long_distilled

    # WITHOUT an antecedent: 67 chars > 4 * len("lock A")=24 -> discarded (original guard)
    (no_ctx,) = judge.judge(["lock A"], contexts=None)
    assert no_ctx.distilled is None


def test_judge_cache_version_is_3_and_discards_v2(tmp_path):
    import json

    from cairn.ingest.judge import _JUDGE_CACHE_VERSION, JudgedCache

    assert _JUDGE_CACHE_VERSION == 3  # bumped for the antecedent-resolution prompt
    p = tmp_path / "j.jsonl"
    p.write_text(json.dumps({"h": "old", "d": 0.9, "tier": "llm", "v": 2}) + "\n")
    assert JudgedCache(p).get("old") is None  # v2 verdict discarded, will be re-judged
