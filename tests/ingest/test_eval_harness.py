# tests/ingest/test_eval_harness.py
# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from eval_judge import auc, pr_at  # noqa: E402


def test_auc_perfect_separation():
    assert auc([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1]) == 1.0


def test_auc_random_is_half():
    assert auc([1, 0], [0.5, 0.5]) == 0.5


def test_pr_at_threshold():
    p, r = pr_at([1, 1, 0], [0.9, 0.4, 0.6], 0.5)
    assert p == 0.5 and r == 0.5
