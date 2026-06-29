# SPDX-License-Identifier: Apache-2.0
from cairn.config import (
    resolve_auto_recall,
    resolve_auto_recall_k,
    resolve_auto_recall_scope,
)


def test_auto_recall_default_on():
    assert resolve_auto_recall(env={}) is True


def test_auto_recall_off():
    assert resolve_auto_recall(env={"CAIRN_AUTO_RECALL": "0"}) is False
    assert resolve_auto_recall(env={"CAIRN_AUTO_RECALL": "false"}) is False


def test_auto_recall_bad_value_falls_back_true():
    assert resolve_auto_recall(env={"CAIRN_AUTO_RECALL": "maybe"}) is True


def test_auto_recall_k_default():
    assert resolve_auto_recall_k(env={}) == 3


def test_auto_recall_k_override():
    assert resolve_auto_recall_k(env={"CAIRN_AUTO_RECALL_K": "5"}) == 5


def test_auto_recall_k_bad_falls_back():
    assert resolve_auto_recall_k(env={"CAIRN_AUTO_RECALL_K": "lots"}) == 3


def test_auto_recall_scope_default():
    assert resolve_auto_recall_scope(env={}) == "all"


def test_auto_recall_scope_override_lowercased():
    assert resolve_auto_recall_scope(env={"CAIRN_AUTO_RECALL_SCOPE": "Project"}) == "project"
