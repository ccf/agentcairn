# tests/ingest/test_judge.py
# SPDX-License-Identifier: Apache-2.0
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
