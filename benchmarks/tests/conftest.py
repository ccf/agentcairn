# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # benchmarks/ -> import cairn_bench

import json

import pytest

FIX = Path(__file__).parent.parent / "fixtures" / "synthetic"


@pytest.fixture
def lme_instances():
    return json.loads((FIX / "longmemeval_synth.json").read_text())


@pytest.fixture
def locomo_samples():
    return json.loads((FIX / "locomo_synth.json").read_text())
