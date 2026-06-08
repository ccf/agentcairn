# Plan 1 — Project Scaffold + `cairn.vault` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `agentcairn` Python project and build a fully-tested `cairn.vault` library that parses and writes the Markdown memory contract (frontmatter, observations, relations, wikilinks, Dataview inline fields) with lossless round-tripping.

**Architecture:** Markdown files are the source of truth (see `docs/specs/2026-06-08-agentcairn-design.md`). `cairn.vault` is the *only* code that reads/writes those files; everything downstream (index, search) consumes its parsed `Note` objects. This plan delivers that library plus a CLI skeleton — no DuckDB, embeddings, or MCP yet (those are Plans 2–5).

**Tech Stack:** Python 3.12+, `uv` (env/deps), `hatchling` (build), `typer` (CLI), `python-frontmatter` (YAML frontmatter), `markdown-it-py` (markdown tokens), `pytest` (tests). Distribution name `agentcairn`; import package `cairn`; CLI command `cairn`.

---

## v1 decomposition note

This is **Plan 1 of 5** (see the roadmap in the spec §15). It must produce working, independently-testable software: the `cairn.vault` library + a `cairn` CLI that can parse a note and print it. Later plans depend on `cairn.vault.models.Note` and the parser/writer defined here — **do not change those public signatures without updating downstream plans.**

## The Markdown contract (locked reference — every parser task uses these exact rules)

A memory note is one Markdown file: YAML frontmatter, then a body. The body may contain:

- **Observations** — list items of the form `- [category] content #tag1 #tag2 (optional context)`.
  - Single square brackets. `category` is the text inside `[...]`. After it: free `content`, optional `#tags`, optional trailing `(context)`.
- **Relations** — list items containing a `[[wikilink]]`: `- rel_type [[Target]]`, or `- "multi word rel" [[Target]]`, or bare `- [[Target]]` (⇒ implicit relation type `links_to`).
  - Double square brackets. Distinguish from observations by checking for `[[` first.
- **Inline fields (Dataview-compatible)** — `key:: value` on its own line, or `[key:: value]` / `(key:: value)` mid-text.
- **Body wikilinks** — any `[[Target]]` or `[[Target|alias]]` anywhere in prose (the deterministic graph edges).

**Locked regexes** (define once in `cairn/vault/patterns.py`, import everywhere — never re-inline):

```python
import re

# Observation: "- [category] content #tag (context)"  (single brackets, NOT [[ )
OBSERVATION_RE = re.compile(r"^\s*[-*]\s+\[(?!\[)([^\]]+)\]\s*(.*)$")
# Relation/link list item: "- rel [[Target]]" / '- "rel" [[Target]]' / "- [[Target]]"
RELATION_RE = re.compile(
    r'^\s*[-*]\s+(?:"([^"]+)"\s+|([A-Za-z][\w -]*?)\s+)?\[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s*$'
)
# Any wikilink in prose: [[Target]] or [[Target|alias]]
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
# Trailing "(context)" at end of an observation's remainder
CONTEXT_RE = re.compile(r"\(([^)]*)\)\s*$")
# Hashtags: "#tag" (word chars, hyphen, slash for nested tags)
TAG_RE = re.compile(r"(?:^|\s)#([\w\-/]+)")
# Inline fields
INLINE_FIELD_BRACKET_RE = re.compile(r"\[([\w-]+)::\s*([^\]]+)\]")
INLINE_FIELD_PAREN_RE = re.compile(r"\(([\w-]+)::\s*([^)]+)\)")
INLINE_FIELD_LINE_RE = re.compile(r"^([\w-]+)::\s+(.+)$")
```

## File structure

```
agentcairn/
├── pyproject.toml                  # project metadata, deps, CLI entry point
├── src/cairn/
│   ├── __init__.py                 # __version__
│   ├── cli.py                      # Typer app: `cairn --version`, `cairn parse <file>`
│   └── vault/
│       ├── __init__.py             # re-exports: Note, Observation, Relation, parse_note, write_note
│       ├── models.py               # dataclasses: Note, Observation, Relation, Wikilink
│       ├── patterns.py             # the locked regexes above
│       ├── parse.py                # markdown -> Note
│       └── write.py                # Note -> markdown (lossless round-trip)
└── tests/
    ├── test_cli.py
    └── vault/
        ├── test_models.py
        ├── test_parse_observations.py
        ├── test_parse_relations.py
        ├── test_parse_inline_fields.py
        ├── test_parse_note.py
        └── test_roundtrip.py
```

---

### Task 1: Project scaffold (uv + hatchling + Typer)

**Files:**
- Create: `pyproject.toml`
- Create: `src/cairn/__init__.py`
- Create: `src/cairn/cli.py`
- Create: `tests/test_cli.py`
- Create: `.gitignore`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
.pytest_cache/
dist/
build/
*.egg-info/
.duckdb
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "agentcairn"
version = "0.0.1"
description = "Local-first agent memory: an Obsidian markdown vault as source of truth, with a rebuildable DuckDB index."
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.12"
authors = [{ name = "Charles C. Figueiredo", email = "ccf@ccf.io" }]
dependencies = [
    "typer>=0.12",
    "python-frontmatter>=1.1",
    "markdown-it-py>=3.0",
]

[project.scripts]
cairn = "cairn.cli:app"

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/cairn"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 3: Create `src/cairn/__init__.py`**

```python
# SPDX-License-Identifier: Apache-2.0
"""agentcairn — local-first agent memory (import package `cairn`)."""

__version__ = "0.0.1"
```

- [ ] **Step 4: Write the failing CLI test** — `tests/test_cli.py`

```python
# SPDX-License-Identifier: Apache-2.0
from typer.testing import CliRunner

from cairn.cli import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.0.1" in result.stdout
```

- [ ] **Step 5: Run it to confirm it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.cli'` (sync deps first with `uv sync` if needed).

- [ ] **Step 6: Implement `src/cairn/cli.py`**

```python
# SPDX-License-Identifier: Apache-2.0
"""The `cairn` command-line interface."""

from __future__ import annotations

import typer

from cairn import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False, help="agentcairn — local-first agent memory.")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """agentcairn — local-first agent memory."""
```

- [ ] **Step 7: Run the test to confirm it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore src/cairn/__init__.py src/cairn/cli.py tests/test_cli.py
git commit -m "feat: project scaffold + cairn CLI skeleton (--version)"
```

---

### Task 2: Vault data models

**Files:**
- Create: `src/cairn/vault/__init__.py`
- Create: `src/cairn/vault/models.py`
- Test: `tests/vault/test_models.py`

- [ ] **Step 1: Write the failing test** — `tests/vault/test_models.py`

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault.models import Note, Observation, Relation


def test_observation_holds_fields():
    obs = Observation(category="method", content="Pour over highlights flavor", tags=["brewing"], context="manual")
    assert obs.category == "method"
    assert obs.tags == ["brewing"]
    assert obs.context == "manual"


def test_relation_defaults_to_links_to():
    rel = Relation(rel_type="links_to", target="Chocolate")
    assert rel.rel_type == "links_to"
    assert rel.target == "Chocolate"


def test_note_aggregates_parts():
    note = Note(
        permalink="coffee",
        frontmatter={"title": "Coffee", "type": "note", "tags": ["drinks"]},
        body="hello",
        observations=[Observation("method", "x", [], None)],
        relations=[Relation("links_to", "Tea")],
        wikilinks=["Tea"],
    )
    assert note.permalink == "coffee"
    assert note.frontmatter["title"] == "Coffee"
    assert note.observations[0].category == "method"
    assert note.wikilinks == ["Tea"]
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/vault/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.vault'`.

- [ ] **Step 3: Implement `src/cairn/vault/models.py`**

```python
# SPDX-License-Identifier: Apache-2.0
"""Parsed representations of a memory note. These are the public types the
rest of agentcairn consumes; keep their signatures stable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    category: str
    content: str
    tags: list[str] = field(default_factory=list)
    context: str | None = None


@dataclass
class Relation:
    rel_type: str
    target: str  # the [[Target]] name; may not yet exist (forward reference)


@dataclass
class Note:
    permalink: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    observations: list[Observation] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    wikilinks: list[str] = field(default_factory=list)  # all [[targets]] found in body
    inline_fields: dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 4: Implement `src/cairn/vault/__init__.py`**

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault.models import Note, Observation, Relation

__all__ = ["Note", "Observation", "Relation"]
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `uv run pytest tests/vault/test_models.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cairn/vault/__init__.py src/cairn/vault/models.py tests/vault/test_models.py
git commit -m "feat(vault): Note/Observation/Relation data models"
```

---

### Task 3: Locked regex patterns

**Files:**
- Create: `src/cairn/vault/patterns.py`
- Test: `tests/vault/test_patterns.py`

- [ ] **Step 1: Write the failing test** — `tests/vault/test_patterns.py`

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault.patterns import OBSERVATION_RE, RELATION_RE, WIKILINK_RE


def test_observation_matches_single_bracket_not_double():
    assert OBSERVATION_RE.match("- [method] pour over #brewing (manual)")
    assert OBSERVATION_RE.match("- [[Target]]") is None  # double bracket = relation, not observation


def test_relation_matches_double_bracket_forms():
    assert RELATION_RE.match("- [[Tea]]").group(3) == "Tea"
    assert RELATION_RE.match("- pairs_with [[Tea]]").group(2) == "pairs_with"
    assert RELATION_RE.match('- "pairs well with" [[Tea]]').group(1) == "pairs well with"


def test_wikilink_extracts_target_without_alias():
    assert WIKILINK_RE.findall("see [[Tea|the tea note]] and [[Coffee]]") == ["Tea", "Coffee"]
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/vault/test_patterns.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.vault.patterns'`.

- [ ] **Step 3: Implement `src/cairn/vault/patterns.py`** (copy the locked regexes verbatim from the "Markdown contract" section above)

```python
# SPDX-License-Identifier: Apache-2.0
"""Locked parsing patterns for the markdown memory contract. Single source of
truth — import these; never re-inline a regex elsewhere."""

import re

OBSERVATION_RE = re.compile(r"^\s*[-*]\s+\[(?!\[)([^\]]+)\]\s*(.*)$")
RELATION_RE = re.compile(
    r'^\s*[-*]\s+(?:"([^"]+)"\s+|([A-Za-z][\w -]*?)\s+)?\[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s*$'
)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
CONTEXT_RE = re.compile(r"\(([^)]*)\)\s*$")
TAG_RE = re.compile(r"(?:^|\s)#([\w\-/]+)")
INLINE_FIELD_BRACKET_RE = re.compile(r"\[([\w-]+)::\s*([^\]]+)\]")
INLINE_FIELD_PAREN_RE = re.compile(r"\(([\w-]+)::\s*([^)]+)\)")
INLINE_FIELD_LINE_RE = re.compile(r"^([\w-]+)::\s+(.+)$")
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/vault/test_patterns.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/vault/patterns.py tests/vault/test_patterns.py
git commit -m "feat(vault): locked markdown-contract regex patterns"
```

---

### Task 4: Parse observations

**Files:**
- Create: `src/cairn/vault/parse.py`
- Test: `tests/vault/test_parse_observations.py`

- [ ] **Step 1: Write the failing test** — `tests/vault/test_parse_observations.py`

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault.parse import parse_observation_line


def test_full_observation():
    obs = parse_observation_line("- [method] Pour over highlights flavor #brewing #coffee (slow)")
    assert obs.category == "method"
    assert obs.content == "Pour over highlights flavor"
    assert obs.tags == ["brewing", "coffee"]
    assert obs.context == "slow"


def test_observation_without_tags_or_context():
    obs = parse_observation_line("- [fact] Water boils at 100C")
    assert obs.category == "fact"
    assert obs.content == "Water boils at 100C"
    assert obs.tags == []
    assert obs.context is None


def test_non_observation_returns_none():
    assert parse_observation_line("- [[Tea]]") is None
    assert parse_observation_line("just text") is None
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/vault/test_parse_observations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.vault.parse'`.

- [ ] **Step 3: Implement `parse_observation_line` in `src/cairn/vault/parse.py`**

```python
# SPDX-License-Identifier: Apache-2.0
"""Parse markdown text into Note objects per the locked contract."""

from __future__ import annotations

from cairn.vault.models import Observation
from cairn.vault.patterns import CONTEXT_RE, OBSERVATION_RE, TAG_RE


def parse_observation_line(line: str) -> Observation | None:
    m = OBSERVATION_RE.match(line)
    if not m:
        return None
    category = m.group(1).strip()
    remainder = m.group(2).strip()

    context: str | None = None
    ctx = CONTEXT_RE.search(remainder)
    if ctx:
        context = ctx.group(1).strip()
        remainder = remainder[: ctx.start()].strip()

    tags = TAG_RE.findall(remainder)
    content = TAG_RE.sub("", remainder).strip()

    return Observation(category=category, content=content, tags=tags, context=context)
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/vault/test_parse_observations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/vault/parse.py tests/vault/test_parse_observations.py
git commit -m "feat(vault): parse observation lines"
```

---

### Task 5: Parse relations

**Files:**
- Modify: `src/cairn/vault/parse.py`
- Test: `tests/vault/test_parse_relations.py`

- [ ] **Step 1: Write the failing test** — `tests/vault/test_parse_relations.py`

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault.parse import parse_relation_line


def test_bare_link_is_implicit_links_to():
    rel = parse_relation_line("- [[Chocolate Desserts]]")
    assert rel.rel_type == "links_to"
    assert rel.target == "Chocolate Desserts"


def test_typed_relation():
    rel = parse_relation_line("- pairs_with [[Tea]]")
    assert rel.rel_type == "pairs_with"
    assert rel.target == "Tea"


def test_quoted_multiword_relation_and_alias_ignored():
    rel = parse_relation_line('- "pairs well with" [[Dark Chocolate|cocoa]]')
    assert rel.rel_type == "pairs well with"
    assert rel.target == "Dark Chocolate"


def test_non_relation_returns_none():
    assert parse_relation_line("- [method] not a relation") is None
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/vault/test_parse_relations.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_relation_line'`.

- [ ] **Step 3: Add `parse_relation_line` to `src/cairn/vault/parse.py`**

Add these imports to the top (alongside existing imports):

```python
from cairn.vault.models import Observation, Relation
from cairn.vault.patterns import CONTEXT_RE, OBSERVATION_RE, RELATION_RE, TAG_RE
```

Append the function:

```python
def parse_relation_line(line: str) -> Relation | None:
    m = RELATION_RE.match(line)
    if not m:
        return None
    quoted, bare, target = m.group(1), m.group(2), m.group(3)
    rel_type = (quoted or bare or "links_to").strip()
    return Relation(rel_type=rel_type, target=target.strip())
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/vault/test_parse_relations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/vault/parse.py tests/vault/test_parse_relations.py
git commit -m "feat(vault): parse relation lines (typed/quoted/bare)"
```

---

### Task 6: Parse inline fields

**Files:**
- Modify: `src/cairn/vault/parse.py`
- Test: `tests/vault/test_parse_inline_fields.py`

- [ ] **Step 1: Write the failing test** — `tests/vault/test_parse_inline_fields.py`

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault.parse import parse_inline_fields


def test_line_level_field():
    assert parse_inline_fields("rating:: 9") == {"rating": "9"}


def test_bracket_and_paren_fields():
    fields = parse_inline_fields("Great coffee [rating:: 9] and (origin:: Ethiopia).")
    assert fields == {"rating": "9", "origin": "Ethiopia"}


def test_no_fields_returns_empty():
    assert parse_inline_fields("plain prose with no fields") == {}
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/vault/test_parse_inline_fields.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_inline_fields'`.

- [ ] **Step 3: Add `parse_inline_fields` to `src/cairn/vault/parse.py`**

Extend the patterns import:

```python
from cairn.vault.patterns import (
    CONTEXT_RE,
    INLINE_FIELD_BRACKET_RE,
    INLINE_FIELD_LINE_RE,
    INLINE_FIELD_PAREN_RE,
    OBSERVATION_RE,
    RELATION_RE,
    TAG_RE,
)
```

Append:

```python
def parse_inline_fields(text: str) -> dict[str, str]:
    """Extract Dataview-style inline fields from a single line of text."""
    fields: dict[str, str] = {}
    line_m = INLINE_FIELD_LINE_RE.match(text.strip())
    if line_m:
        fields[line_m.group(1)] = line_m.group(2).strip()
    for rx in (INLINE_FIELD_BRACKET_RE, INLINE_FIELD_PAREN_RE):
        for key, value in rx.findall(text):
            fields[key] = value.strip()
    return fields
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/vault/test_parse_inline_fields.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/vault/parse.py tests/vault/test_parse_inline_fields.py
git commit -m "feat(vault): parse Dataview inline fields"
```

---

### Task 7: Parse a whole note

**Files:**
- Modify: `src/cairn/vault/parse.py`
- Modify: `src/cairn/vault/__init__.py`
- Test: `tests/vault/test_parse_note.py`

- [ ] **Step 1: Write the failing test** — `tests/vault/test_parse_note.py`

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault import parse_note

SAMPLE = """\
---
title: Coffee
type: note
permalink: coffee
tags: [drinks, morning]
---

Notes about [[Coffee]] brewing. Pairs with [[Tea|green tea]].

- [method] Pour over highlights flavor #brewing (slow)
- pairs_with [[Tea]]
- [[Chocolate]]

rating:: 9
"""


def test_parse_note_extracts_all_parts():
    note = parse_note(SAMPLE)
    assert note.permalink == "coffee"
    assert note.frontmatter["title"] == "Coffee"
    assert note.frontmatter["tags"] == ["drinks", "morning"]
    # observations
    assert len(note.observations) == 1
    assert note.observations[0].category == "method"
    assert note.observations[0].tags == ["brewing"]
    # relations: typed + bare (implicit links_to)
    rels = {(r.rel_type, r.target) for r in note.relations}
    assert ("pairs_with", "Tea") in rels
    assert ("links_to", "Chocolate") in rels
    # body wikilinks (de-duplicated, in order of first appearance)
    assert note.wikilinks == ["Coffee", "Tea", "Chocolate"]
    # inline fields
    assert note.inline_fields["rating"] == "9"


def test_permalink_falls_back_to_none_when_absent():
    note = parse_note("---\ntitle: X\n---\nbody")
    assert note.permalink is None
    assert note.frontmatter["title"] == "X"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/vault/test_parse_note.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_note'`.

- [ ] **Step 3: Add `parse_note` to `src/cairn/vault/parse.py`**

Add imports at top:

```python
import frontmatter

from cairn.vault.models import Note, Observation, Relation
from cairn.vault.patterns import WIKILINK_RE  # add to existing patterns import
```

Append:

```python
def parse_note(text: str) -> Note:
    """Parse a full markdown document into a Note."""
    post = frontmatter.loads(text)
    fm = dict(post.metadata)
    body = post.content

    observations: list[Observation] = []
    relations: list[Relation] = []
    inline_fields: dict[str, str] = {}

    for line in body.splitlines():
        if (obs := parse_observation_line(line)) is not None:
            observations.append(obs)
            continue
        if (rel := parse_relation_line(line)) is not None:
            relations.append(rel)
            continue
        inline_fields.update(parse_inline_fields(line))

    # De-duplicated body wikilinks in first-seen order.
    seen: set[str] = set()
    wikilinks: list[str] = []
    for target in WIKILINK_RE.findall(body):
        if target not in seen:
            seen.add(target)
            wikilinks.append(target)

    return Note(
        permalink=fm.get("permalink"),
        frontmatter=fm,
        body=body,
        observations=observations,
        relations=relations,
        wikilinks=wikilinks,
        inline_fields=inline_fields,
    )
```

Note: `permalink` may be `None`; update `Note.permalink` type to `str | None` in `models.py` (change `permalink: str` to `permalink: str | None = None`).

- [ ] **Step 4: Re-export `parse_note` in `src/cairn/vault/__init__.py`**

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault.models import Note, Observation, Relation
from cairn.vault.parse import parse_note

__all__ = ["Note", "Observation", "Relation", "parse_note"]
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `uv run pytest tests/vault/test_parse_note.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/cairn/vault/parse.py src/cairn/vault/models.py src/cairn/vault/__init__.py tests/vault/test_parse_note.py
git commit -m "feat(vault): parse_note assembles full Note from markdown"
```

---

### Task 8: Write a note back to markdown (lossless round-trip)

**Files:**
- Create: `src/cairn/vault/write.py`
- Modify: `src/cairn/vault/__init__.py`
- Test: `tests/vault/test_roundtrip.py`

- [ ] **Step 1: Write the failing test** — `tests/vault/test_roundtrip.py`

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault import parse_note, write_note

SAMPLE = """\
---
title: Coffee
type: note
permalink: coffee
tags:
- drinks
- morning
---

Notes about [[Coffee]] brewing.

- [method] Pour over highlights flavor #brewing (slow)
- pairs_with [[Tea]]
"""


def test_roundtrip_is_idempotent():
    note = parse_note(SAMPLE)
    out = write_note(note)
    # Parsing the written output yields an equivalent Note (stable fixpoint).
    reparsed = parse_note(out)
    assert reparsed.frontmatter == note.frontmatter
    assert reparsed.body.strip() == note.body.strip()
    # Writing again is byte-identical (idempotent).
    assert write_note(reparsed) == out


def test_write_preserves_frontmatter_keys():
    note = parse_note(SAMPLE)
    out = write_note(note)
    assert "title: Coffee" in out
    assert "permalink: coffee" in out
    assert out.startswith("---")
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/vault/test_roundtrip.py -v`
Expected: FAIL — `ImportError: cannot import name 'write_note'`.

- [ ] **Step 3: Implement `src/cairn/vault/write.py`**

The body is the source of truth for observations/relations/links (they live *in* the body), so `write_note` serializes frontmatter + the stored `body` verbatim. This guarantees lossless round-trip and never clobbers human edits in the body.

```python
# SPDX-License-Identifier: Apache-2.0
"""Serialize a Note back to markdown without clobbering human edits.

The body string is authoritative for observations/relations/wikilinks/inline
fields (they are parsed *from* the body), so we re-emit it verbatim and only
re-render the frontmatter block. This makes parse->write a stable fixpoint."""

from __future__ import annotations

import frontmatter

from cairn.vault.models import Note


def write_note(note: Note) -> str:
    post = frontmatter.Post(note.body, **note.frontmatter)
    # frontmatter.dumps emits "---\n<yaml>---\n\n<body>"; normalize trailing newline.
    text = frontmatter.dumps(post)
    if not text.endswith("\n"):
        text += "\n"
    return text
```

- [ ] **Step 4: Re-export `write_note` in `src/cairn/vault/__init__.py`**

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault.models import Note, Observation, Relation
from cairn.vault.parse import parse_note
from cairn.vault.write import write_note

__all__ = ["Note", "Observation", "Relation", "parse_note", "write_note"]
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `uv run pytest tests/vault/test_roundtrip.py -v`
Expected: PASS. (If the idempotency assertion fails because PyYAML reorders/reflows keys, that is expected behavior to surface here — the fix is to assert the *fixpoint* property as written: first write may normalize, but writing the reparsed note must be byte-identical. The test already encodes this.)

- [ ] **Step 6: Commit**

```bash
git add src/cairn/vault/write.py src/cairn/vault/__init__.py tests/vault/test_roundtrip.py
git commit -m "feat(vault): lossless write_note round-trip"
```

---

### Task 9: Forward-reference tolerance

**Files:**
- Test: `tests/vault/test_forward_refs.py`

- [ ] **Step 1: Write the test** — `tests/vault/test_forward_refs.py`

A note that links to a target that does not exist yet must parse without error (capture never blocks on link integrity — spec §6).

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault import parse_note


def test_links_to_nonexistent_target_parses_cleanly():
    note = parse_note("---\ntitle: A\n---\nSee [[Does Not Exist Yet]].\n\n- depends_on [[Also Missing]]\n")
    assert "Does Not Exist Yet" in note.wikilinks
    assert any(r.target == "Also Missing" and r.rel_type == "depends_on" for r in note.relations)
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/vault/test_forward_refs.py -v`
Expected: PASS (no code change needed — this asserts the parser tolerates dangling links). If it fails, the parser is wrongly validating link targets; remove any such validation.

- [ ] **Step 3: Commit**

```bash
git add tests/vault/test_forward_refs.py
git commit -m "test(vault): forward references parse without error"
```

---

### Task 10: Wire `cairn parse <file>` into the CLI

**Files:**
- Modify: `src/cairn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add the failing test** — append to `tests/test_cli.py`

```python
import json
from pathlib import Path


def test_parse_command_outputs_json(tmp_path: Path):
    note_file = tmp_path / "coffee.md"
    note_file.write_text("---\ntitle: Coffee\npermalink: coffee\n---\n\n- [method] pour over #brewing\n")
    result = runner.invoke(app, ["parse", str(note_file)])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["permalink"] == "coffee"
    assert data["observations"][0]["category"] == "method"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_cli.py::test_parse_command_outputs_json -v`
Expected: FAIL — no `parse` command registered.

- [ ] **Step 3: Add the `parse` command to `src/cairn/cli.py`**

Add imports and command:

```python
import dataclasses
import json
from pathlib import Path

from cairn.vault import parse_note


@app.command()
def parse(file: Path = typer.Argument(..., exists=True, readable=True, help="Markdown note to parse.")) -> None:
    """Parse a markdown note and print its structured form as JSON."""
    note = parse_note(file.read_text())
    typer.echo(json.dumps(dataclasses.asdict(note), indent=2, default=str))
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite + smoke-test the CLI**

Run: `uv run pytest -v` → all green.
Run: `uv run cairn --version` → prints `0.0.1`.

- [ ] **Step 6: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): cairn parse <file> prints structured note JSON"
```

---

## Done criteria

- `uv run pytest -v` is all green.
- `uv run cairn parse some-note.md` prints structured JSON (frontmatter, observations, relations, wikilinks, inline fields).
- `parse_note` / `write_note` round-trip is a stable fixpoint and tolerates forward references.
- `cairn.vault.{Note, Observation, Relation, parse_note, write_note}` are importable — the public surface Plans 2–5 build on.

## Self-review (completed by plan author)

- **Spec coverage (this plan's slice):** markdown contract (frontmatter, observations, relations, bare-link⇒`links_to`, Dataview inline fields, body wikilinks) ✓; lossless/round-trip writer ✓; forward-reference tolerance ✓; CLI skeleton ✓. Deferred to later plans by design: DuckDB index, embeddings, search, ingest/redaction, MCP — **not** in scope here.
- **Placeholder scan:** every code step contains complete, runnable code; no TBD/TODO. ✓
- **Type consistency:** `Note`, `Observation(category, content, tags, context)`, `Relation(rel_type, target)`, `parse_note`, `write_note`, `parse_observation_line`, `parse_relation_line`, `parse_inline_fields` are used identically across Tasks 2–10. `Note.permalink` typed `str | None` (Task 7 fixes the Task 2 default). ✓
