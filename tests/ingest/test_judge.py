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


def test_resolve_judge_modes(monkeypatch):
    from cairn.ingest.judge import LLMJudge, resolve_judge

    # none -> None
    assert resolve_judge(env={"CAIRN_JUDGE": "none"}, embedder=StubEmbedder()) is None
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
