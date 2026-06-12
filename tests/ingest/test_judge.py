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
    assert sizes == [40, 40, 10]  # chunked, never one giant truncation-prone call
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
    assert len(out) == 90
    assert judge.degraded == 40  # ONLY the failed middle chunk
    assert out[0].durability == 0.9  # chunk 1 judged
    assert out[40].durability == 0.5  # chunk 2 neutral
    assert out[80].durability == 0.9  # chunk 3 judged


def test_judged_cache_roundtrip(tmp_path):
    from cairn.ingest.judge import JudgedCache

    p = tmp_path / "judged.jsonl"
    c = JudgedCache(p)  # missing file -> empty
    assert c.get("abc") is None
    c.put("abc", 0.25)
    assert c.get("abc") == 0.25
    c.put("abc", 0.25)  # idempotent: no duplicate line
    assert len([ln for ln in p.read_text().splitlines() if ln.strip()]) == 1
    c2 = JudgedCache(p)  # reload from disk
    assert c2.get("abc") == 0.25
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
