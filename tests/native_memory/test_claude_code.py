# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cairn.ingest.harness.claude_code import encode_cwd
from cairn.ingest.redact import redact
from cairn.ingest.sanitize import sanitize_text
from cairn.native_memory import ClaudeCodeMemorySource, apply_import_plan, plan_import
from cairn.temporal import parse_temporal
from cairn.vault import parse_note, write_note

_OPENAI_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"
_T1 = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
_T2 = datetime(2026, 7, 15, 13, 0, tzinfo=UTC)
_T3 = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)
_T4 = datetime(2026, 7, 15, 15, 0, tzinfo=UTC)


def _project(tmp_path: Path, name: str = "project") -> Path:
    project = tmp_path / name
    project.mkdir()
    return project


def _memory_dir(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    root = tmp_path / "claude-memory"
    root.mkdir(parents=True)
    for relative, text in (files or {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _discover(project: Path, root: Path):
    return ClaudeCodeMemorySource(memory_dir=root, env={}).discover(project)


def _parsed(path: Path):
    return parse_note(path.read_text(encoding="utf-8"))


def _one_action(plan, kind: str):
    actions = [action for action in plan.actions if action.kind == kind]
    assert len(actions) == 1
    return actions[0]


def test_explicit_source_discovers_memory_and_topic_files_only(tmp_path):
    project = _project(tmp_path)
    configured = _memory_dir(tmp_path / "configured", {"MEMORY.md": "configured copy"})
    config_root = tmp_path / "claude-config"
    config_root.mkdir()
    (config_root / "settings.json").write_text(
        '{"autoMemoryDirectory": "' + str(configured) + '"}', encoding="utf-8"
    )
    explicit = _memory_dir(
        tmp_path / "explicit",
        {
            "MEMORY.md": "# Index\n\nUse the topic file.",
            "topic.md": "# Topic\n\nA durable fact.",
            "CLAUDE.md": "instructions, not memory",
            "CLAUDE.local.md": "more instructions",
            "notes.txt": "not Markdown",
            "nested/ignored.md": "not a direct topic file",
        },
    )

    discovery = ClaudeCodeMemorySource(
        memory_dir=explicit,
        env={"CLAUDE_CONFIG_DIR": str(config_root)},
    ).discover(project)

    assert discovery.root == explicit.resolve()
    assert [document.relative_path for document in discovery.documents] == [
        "MEMORY.md",
        "topic.md",
    ]
    assert [document.text for document in discovery.documents] == [
        "# Index\n\nUse the topic file.",
        "# Topic\n\nA durable fact.",
    ]


def test_default_discovery_uses_current_project_encoder(tmp_path):
    project = _project(tmp_path, "repo.with spaces!")
    config_root = tmp_path / "claude-config"
    memory = config_root / "projects" / encode_cwd(str(project.resolve())) / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("current encoding", encoding="utf-8")

    discovery = ClaudeCodeMemorySource(env={"CLAUDE_CONFIG_DIR": str(config_root)}).discover(
        project
    )

    assert discovery.root == memory.resolve()
    assert [document.relative_path for document in discovery.documents] == ["MEMORY.md"]


def test_user_custom_memory_directory_is_the_exact_source(tmp_path):
    project = _project(tmp_path)
    custom = _memory_dir(tmp_path / "custom", {"MEMORY.md": "custom memory"})
    config_root = tmp_path / "claude-config"
    config_root.mkdir()
    (config_root / "settings.json").write_text(
        '{"autoMemoryDirectory": "' + str(custom) + '"}', encoding="utf-8"
    )

    discovery = ClaudeCodeMemorySource(env={"CLAUDE_CONFIG_DIR": str(config_root)}).discover(
        project
    )

    assert discovery.root == custom.resolve()


def test_project_memory_redirect_fails_closed_without_explicit_source(tmp_path):
    project = _project(tmp_path)
    settings = project / ".claude"
    settings.mkdir()
    (settings / "settings.local.json").write_text(
        '{"autoMemoryDirectory": "/tmp/project-specific-memory"}', encoding="utf-8"
    )
    config_root = tmp_path / "claude-config"
    config_root.mkdir()

    with pytest.raises(ValueError, match="pass --source"):
        ClaudeCodeMemorySource(env={"CLAUDE_CONFIG_DIR": str(config_root)}).discover(project)


def test_missing_explicit_source_fails_without_a_discovery_snapshot(tmp_path):
    project = _project(tmp_path)

    with pytest.raises(FileNotFoundError, match="auto-memory directory not found"):
        _discover(project, tmp_path / "missing")


def test_current_encoder_handles_punctuation_and_claude_long_path_hash():
    assert (
        encode_cwd("/Users/alice/My Repo/.worktrees/feat_x")
        == "-Users-alice-My-Repo--worktrees-feat-x"
    )

    long_path = "/Users/alice/" + "x" * 240
    assert encode_cwd(long_path) == "-Users-alice-" + "x" * 187 + "-i79aat"


def test_discovery_rejects_markdown_symlink_that_escapes_source(tmp_path):
    project = _project(tmp_path)
    root = _memory_dir(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("must not be imported", encoding="utf-8")
    try:
        (root / "MEMORY.md").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform capability
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(ValueError, match="symlink outside source root"):
        _discover(project, root)


def test_plan_is_write_free_and_redacts_before_hashing_and_provenance(tmp_path):
    project = _project(tmp_path, "acme-api")
    raw = f"# Deployment\n\nThe old key was {_OPENAI_KEY}; rotate it.\n"
    root = _memory_dir(tmp_path, {"MEMORY.md": raw})
    vault = tmp_path / "vault"
    manifest = tmp_path / "cache" / "claude-code.json"

    plan = plan_import(
        _discover(project, root),
        vault_root=vault,
        manifest_path=manifest,
        now=_T1,
    )

    action = _one_action(plan, "added")
    assert plan.discovered == 1
    assert plan.redactions >= 1
    assert not vault.exists()
    assert not manifest.exists()
    assert action.destination is not None and not action.destination.exists()
    assert action.note is not None

    safe_text = redact(sanitize_text(raw)).text
    fm = action.note.frontmatter
    assert action.source_hash == hashlib.sha256(safe_text.encode("utf-8")).hexdigest()
    assert fm["kind"] == "native-memory"
    assert fm["native_source"] == "claude-code"
    assert fm["harness"] == "claude-code"
    assert fm["project"] == "acme-api"
    assert fm["source_path"] == "MEMORY.md"
    assert fm["source_version"] == 1
    assert fm["source_status"] == "current"
    assert fm["model_generated"] is True
    assert fm["read_only_source"] is True
    assert fm["source_hash"] == action.source_hash
    assert "memory://native/claude-code/" in fm["source"]
    assert _OPENAI_KEY not in action.note.body
    assert _OPENAI_KEY not in action.note.permalink
    assert _OPENAI_KEY not in action.destination.name
    assert "[REDACTED:openai_key]" in action.note.body


def test_apply_is_idempotent_and_preserves_manual_edits(tmp_path):
    project = _project(tmp_path)
    root = _memory_dir(tmp_path, {"MEMORY.md": "# Build\n\nUse Python 3.12.\n"})
    vault = tmp_path / "vault"
    manifest = tmp_path / "cache" / "claude-code.json"
    discovery = _discover(project, root)
    first = plan_import(discovery, vault_root=vault, manifest_path=manifest, now=_T1)

    report = apply_import_plan(first, vault_root=vault)

    assert report.added == 1
    destination = _one_action(first, "added").destination
    assert destination is not None and destination.is_file()
    manual_text = destination.read_text(encoding="utf-8") + "\nManual annotation stays.\n"
    destination.write_text(manual_text, encoding="utf-8")
    before = destination.read_bytes()
    before_mtime = destination.stat().st_mtime_ns

    second = plan_import(discovery, vault_root=vault, manifest_path=manifest, now=_T2)
    second_report = apply_import_plan(second, vault_root=vault)

    unchanged = _one_action(second, "unchanged")
    assert unchanged.version == 1
    assert second_report.unchanged == 1
    assert second_report.written == []
    assert destination.read_bytes() == before
    assert destination.stat().st_mtime_ns == before_mtime
    assert "Manual annotation stays." in destination.read_text(encoding="utf-8")


def test_vault_registry_preserves_manual_deletion_after_cache_loss(tmp_path):
    project = _project(tmp_path)
    root = _memory_dir(tmp_path, {"MEMORY.md": "# Build\n\nUse Python 3.12.\n"})
    vault = tmp_path / "vault"
    manifest = tmp_path / "cache" / "claude-code.json"
    discovery = _discover(project, root)
    first = plan_import(discovery, vault_root=vault, manifest_path=manifest, now=_T1)
    apply_import_plan(first, vault_root=vault)
    destination = _one_action(first, "added").destination
    assert destination is not None
    registry = vault / ".agentcairn" / "native-memory" / "claude-code.json"
    assert registry.is_file()

    destination.unlink()
    manifest.unlink()
    retry = plan_import(discovery, vault_root=vault, manifest_path=manifest, now=_T2)

    unchanged = _one_action(retry, "unchanged")
    assert unchanged.version == 1
    apply_import_plan(retry, vault_root=vault)
    assert not destination.exists()


def test_corrupt_canonical_registry_fails_closed(tmp_path):
    project = _project(tmp_path)
    root = _memory_dir(tmp_path, {"MEMORY.md": "Durable source fact.\n"})
    vault = tmp_path / "vault"
    manifest = tmp_path / "cache" / "claude-code.json"
    discovery = _discover(project, root)
    first = plan_import(discovery, vault_root=vault, manifest_path=manifest, now=_T1)
    apply_import_plan(first, vault_root=vault)
    first.registry_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid native-memory registry"):
        plan_import(discovery, vault_root=vault, manifest_path=manifest, now=_T2)


def test_update_delete_and_identical_reappearance_create_v3(tmp_path):
    project = _project(tmp_path)
    v1 = "# Runtime\n\nUse two workers.\n"
    v2 = "# Runtime\n\nUse four workers.\n"
    root = _memory_dir(tmp_path, {"MEMORY.md": v1})
    source_file = root / "MEMORY.md"
    vault = tmp_path / "vault"
    manifest = tmp_path / "cache" / "claude-code.json"

    first = plan_import(_discover(project, root), vault_root=vault, manifest_path=manifest, now=_T1)
    apply_import_plan(first, vault_root=vault)
    v1_action = _one_action(first, "added")
    assert v1_action.destination is not None

    source_file.write_text(v2, encoding="utf-8")
    second = plan_import(
        _discover(project, root), vault_root=vault, manifest_path=manifest, now=_T2
    )
    v2_action = _one_action(second, "updated")
    assert v2_action.version == 2
    apply_import_plan(second, vault_root=vault)
    assert v2_action.destination is not None

    old = _parsed(v1_action.destination)
    current_v2 = _parsed(v2_action.destination)
    assert "Use two workers." in old.body
    assert old.frontmatter["superseded_by"] == current_v2.permalink
    assert old.frontmatter["source_status"] == "superseded"
    assert "Use four workers." in current_v2.body
    assert current_v2.frontmatter["source_status"] == "current"
    assert "superseded_by" not in current_v2.frontmatter

    source_file.unlink()
    deletion = plan_import(
        _discover(project, root), vault_root=vault, manifest_path=manifest, now=_T3
    )
    expired = _one_action(deletion, "expired")
    assert expired.version == 2
    apply_import_plan(deletion, vault_root=vault)

    missing_v2 = _parsed(v2_action.destination)
    assert missing_v2.frontmatter["source_status"] == "missing"
    assert parse_temporal(missing_v2.frontmatter["valid_until"]) == _T3
    assert parse_temporal(missing_v2.frontmatter["source_missing_at"]) == _T3
    assert "Use four workers." in missing_v2.body

    # Reappearing with byte-identical content is a new source incarnation. It must
    # not collide with or reactivate the expired v2 note.
    source_file.write_text(v2, encoding="utf-8")
    reappearance = plan_import(
        _discover(project, root), vault_root=vault, manifest_path=manifest, now=_T4
    )
    v3_action = _one_action(reappearance, "updated")
    assert v3_action.version == 3
    assert v3_action.destination not in {v1_action.destination, v2_action.destination}
    apply_import_plan(reappearance, vault_root=vault)

    assert v3_action.destination is not None
    current_v3 = _parsed(v3_action.destination)
    expired_v2 = _parsed(v2_action.destination)
    assert current_v3.frontmatter["source_version"] == 3
    assert current_v3.frontmatter["source_status"] == "current"
    assert "valid_until" not in current_v3.frontmatter
    assert expired_v2.frontmatter["superseded_by"] == current_v3.permalink
    assert parse_temporal(expired_v2.frontmatter["valid_until"]) == _T3


def test_interrupted_update_is_repaired_on_next_apply(tmp_path):
    from cairn.native_memory.importer import _write_new_note

    project = _project(tmp_path)
    root = _memory_dir(tmp_path, {"MEMORY.md": "Use two workers.\n"})
    source_file = root / "MEMORY.md"
    vault = tmp_path / "vault"
    manifest = tmp_path / "cache" / "claude-code.json"

    first = plan_import(_discover(project, root), vault_root=vault, manifest_path=manifest, now=_T1)
    apply_import_plan(first, vault_root=vault)
    old_action = _one_action(first, "added")
    assert old_action.destination is not None

    source_file.write_text("Use four workers.\n", encoding="utf-8")
    interrupted = plan_import(
        _discover(project, root), vault_root=vault, manifest_path=manifest, now=_T2
    )
    new_action = _one_action(interrupted, "updated")
    # Simulate a process dying after the safe write-first phase but before it
    # demotes the old note or advances the lifecycle manifest.
    _write_new_note(vault.resolve(), new_action)

    retry = plan_import(_discover(project, root), vault_root=vault, manifest_path=manifest, now=_T3)
    repair = _one_action(retry, "repaired")
    assert repair.version == 2
    assert old_action.destination in repair.stale_paths

    apply_import_plan(retry, vault_root=vault)

    old = _parsed(old_action.destination)
    current = _parsed(new_action.destination)
    assert old.frontmatter["superseded_by"] == current.permalink
    assert old.frontmatter["source_status"] == "superseded"
    assert "superseded_by" not in current.frontmatter


def test_failed_demotion_does_not_advance_lifecycle_state(tmp_path):
    project = _project(tmp_path)
    root = _memory_dir(tmp_path, {"MEMORY.md": "Use two workers.\n"})
    source_file = root / "MEMORY.md"
    vault = tmp_path / "vault"
    manifest = tmp_path / "cache" / "claude-code.json"

    first = plan_import(_discover(project, root), vault_root=vault, manifest_path=manifest, now=_T1)
    apply_import_plan(first, vault_root=vault)
    old_path = _one_action(first, "added").destination
    assert old_path is not None
    registry = first.registry_path
    manifest_before = manifest.read_bytes()
    registry_before = registry.read_bytes()

    old = _parsed(old_path)
    old.frontmatter["source_id"] = "human-changed-source-id"
    old_path.write_text(write_note(old), encoding="utf-8")
    source_file.write_text("Use four workers.\n", encoding="utf-8")
    update = plan_import(
        _discover(project, root), vault_root=vault, manifest_path=manifest, now=_T2
    )
    new_action = _one_action(update, "updated")

    with pytest.raises(ValueError, match="changed source identity"):
        apply_import_plan(update, vault_root=vault)

    assert new_action.destination is not None and new_action.destination.is_file()
    assert manifest.read_bytes() == manifest_before
    assert registry.read_bytes() == registry_before


def test_deletion_reconciliation_is_scoped_to_selected_project(tmp_path):
    vault = tmp_path / "vault"
    manifest = tmp_path / "cache" / "claude-code.json"
    project_a = _project(tmp_path, "project-a")
    project_b = _project(tmp_path, "project-b")
    root_a = _memory_dir(tmp_path / "source-a", {"MEMORY.md": "Fact A"})
    root_b = _memory_dir(tmp_path / "source-b", {"MEMORY.md": "Fact B"})

    plan_a = plan_import(
        _discover(project_a, root_a), vault_root=vault, manifest_path=manifest, now=_T1
    )
    apply_import_plan(plan_a, vault_root=vault)
    plan_b = plan_import(
        _discover(project_b, root_b), vault_root=vault, manifest_path=manifest, now=_T2
    )
    apply_import_plan(plan_b, vault_root=vault)
    action_a = _one_action(plan_a, "added")
    action_b = _one_action(plan_b, "added")
    assert action_a.destination is not None and action_b.destination is not None

    (root_a / "MEMORY.md").unlink()
    deletion_a = plan_import(
        _discover(project_a, root_a), vault_root=vault, manifest_path=manifest, now=_T3
    )

    assert deletion_a.count("expired") == 1
    assert {action.source_id for action in deletion_a.actions if action.kind == "expired"} == {
        action_a.source_id
    }
    assert all(action.source_id != action_b.source_id for action in deletion_a.actions)
    apply_import_plan(deletion_a, vault_root=vault)

    note_a = _parsed(action_a.destination)
    note_b = _parsed(action_b.destination)
    assert note_a.frontmatter["source_status"] == "missing"
    assert note_b.frontmatter["source_status"] == "current"
    assert "valid_until" not in note_b.frontmatter
