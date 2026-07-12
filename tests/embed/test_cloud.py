import pytest

from cairn.embed._cloud import batched, embed_request

URL = "https://api.example/v1/embeddings"


def fake_post_factory(captured):
    def post(url, payload, headers):
        captured.append((url, payload, headers))
        return {
            "data": [  # out of order on purpose — proves re-sort by index
                {"index": 1, "embedding": [0.2]},
                {"index": 0, "embedding": [0.1]},
            ]
        }

    return post


def test_batched():
    assert list(batched([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_bearer_header_and_ordered_parse():
    cap = []
    vecs = embed_request(
        URL, {"model": "m", "input": ["a", "b"]}, "secret", label="X", post=fake_post_factory(cap)
    )
    assert vecs == [[0.1], [0.2]]  # re-ordered by index
    _, _, headers = cap[0]
    assert headers["Authorization"] == "Bearer secret"
    assert headers["Content-Type"] == "application/json"


def test_redacts_secrets_from_every_cloud_input_before_post():
    captured = []
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    payload = {"model": "m", "input": [f"document {secret}", f"query {secret}"]}

    embed_request(URL, payload, "provider-key", label="X", post=fake_post_factory(captured))

    sent = captured[0][1]["input"]
    assert sent == [
        "document [REDACTED:openai_key]",
        "query [REDACTED:openai_key]",
    ]
    # Sanitizing egress must not mutate the caller's payload in place.
    assert payload["input"] == [f"document {secret}", f"query {secret}"]


def test_missing_key_raises():
    with pytest.raises(RuntimeError, match="API key"):
        embed_request(URL, {"model": "m", "input": ["a"]}, None, label="X", post=lambda *a: {})


def test_empty_data_raises_never_zero():
    with pytest.raises(RuntimeError):
        embed_request(
            URL,
            {"model": "m", "input": ["a"]},
            "k",
            label="X",
            post=lambda *a: {"data": []},
        )


def test_count_mismatch_raises():
    with pytest.raises(RuntimeError):
        embed_request(
            URL,
            {"model": "m", "input": ["a", "b"]},
            "k",
            label="X",
            post=lambda *a: {"data": [{"index": 0, "embedding": [0.1]}]},
        )


def test_retries_then_raises():
    calls = {"n": 0}

    def flaky(url, payload, headers):
        calls["n"] += 1
        raise TimeoutError("boom")

    with pytest.raises(RuntimeError, match="failed"):
        embed_request(URL, {"model": "m", "input": ["a"]}, "k", label="X", post=flaky, retries=3)
    assert calls["n"] == 3


def test_malformed_item_surfaces_as_runtimeerror():
    # a data item missing "embedding" must fail-closed (KeyError -> RuntimeError), never skip
    with pytest.raises(RuntimeError):
        embed_request(
            URL,
            {"model": "m", "input": ["a"]},
            "k",
            label="X",
            post=lambda *a: {"data": [{"index": 0}]},
        )
