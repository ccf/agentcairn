# Redaction over-firing fix — Design

**Status:** Approved (brainstorm) — 2026-06-11
**Scope:** Surgical fix to the entropy heuristic in `src/cairn/ingest/redact.py`. Sequenced **before** the vault rebuild so re-ingested memories are not re-damaged.

## Problem

The vault audit (2026-06-11) found the entropy heuristic over-firing on structured identifiers: **571 `high_entropy` redactions vs 4 named-pattern hits**, with ~320 adjacent to a path/URL/branch and the remainder mostly long hyphenated slugs. Damaged examples from real notes:

- `/Users/ccf/.[REDACTED:high_entropy].1.0/skills/brainstorming` (a plugin cache path)
- `https://github.[REDACTED:high_entropy].md` (a GitHub URL)
- `branch [REDACTED:high_entropy]` (a git branch name)
- `superpowers:[REDACTED:high_entropy]` (the skill slug `subagent-driven-development`)

**Root cause:** `_TOKEN_RE = [A-Za-z0-9+/_-]{24,}` includes `/` and `-`, so an entire path, URL tail, branch name, or hyphenated slug matches as a *single* long token, and `_looks_secret()` then judges it by Shannon entropy — which cannot distinguish "random" from "structured-but-varied". The entropy net is ~99% false-positive on this corpus, while all 4 real secrets were caught by the **named patterns**.

This matters doubly: redaction is the spec's "worst failure mode" subsystem (never leak a credential), so the fix must cut false positives **without** raising leak risk; and the damage is corrupting genuine memories (a memory referencing `feat/v1.1-bitemporal` or a repo URL loses that information).

## Decision (locked in brainstorm)

**Surgical, structure-aware-by-construction:** narrow the candidate token class so paths/URLs/branches/slugs can never form an entropy candidate, and restore the one realistic coverage gap with a dedicated named pattern. Rejected alternative: a "wordiness" segment-exemption heuristic on wide tokens — more moving parts to get subtly wrong in security-critical code.

## The change (`src/cairn/ingest/redact.py`)

1. **Narrow `_TOKEN_RE`** from `[A-Za-z0-9+/_-]{24,}` to `[A-Za-z0-9+_]{24,}` — drop `/` and `-` from the class. Paths, URLs, branches, and hyphenated slugs cannot form a candidate token *by construction*; the entropy net only ever judges contiguous unstructured runs (base64/hex/API-key shapes). `_` and `+` stay: common inside real key material, rare as prose separators at 24+ chars.
2. **New named pattern — bare AWS secret value** (kind `aws_secret_value`): `(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40}(?![A-Za-z0-9+/=])`, additionally **guarded in code by a mixed-character requirement** (the match must contain upper + lower + digit) so 40-char prose-ish or all-lowercase strings don't fire. This restores coverage for the one realistic separator-bearing bare secret (`/`,`+` base64, exactly 40 chars) that the narrowed token class gives up. **It runs immediately after the URL-credential pass and BEFORE the entropy pass** — discovered during implementation: if it ran later, the narrowed entropy net would first consume the ≥24-char segment after a `/` and leak the token's prefix (e.g. `wJalr/` before `[REDACTED:high_entropy]`), leaving no intact 40-char run to match.
3. **Entropy thresholds unchanged** (3.5 mixed-case / 3.8 otherwise) and the hex/git-SHA exemption stays — the fix changes *what gets judged*, not *how*.
4. Everything else untouched: the named-pattern list, URL-credential pass, secret-assignment pass, and the pass ordering (URL-cred → entropy → named patterns).

## Why this is safe (the security argument)

- The 4 real secrets in the audited vault were all caught by **named patterns**; none relied on the entropy net spanning a separator.
- Known vendor key shapes that *contain* `-` or `/` — Anthropic `sk-ant-…`, OpenAI `sk-…`, GitHub `github_pat_…`/`gh*_…`, Slack `xox*-…`, JWTs, private-key blocks, URL credentials, `secret=`-style assignments — are all named-pattern-covered regardless of the entropy change.
- **Residual accepted risk:** an unknown-vendor bare secret whose only tell is high entropy *and* which embeds `-` or `/` *and* is not 40-char base64 would slip the entropy net. This is narrow (no known vendor shape fits it), and the safe-direction posture is preserved for everything the patterns and the narrowed net do cover.

## Validation (prove, don't hope)

1. **Golden corpus stays zero-leakage:** every existing test in `tests/ingest/test_redact.py` must pass unchanged — including the secret-assignment, URL-credential, and existing entropy cases.
2. **New leak tests (must redact):**
   - a bare 40-char AWS secret value with `/` and `+`, no `aws_secret=` prefix → `aws_secret_value`;
   - a bare 32+-char contiguous mixed-case base64 token → still `high_entropy`.
3. **New false-positive tests (must survive unredacted)** — taken from the actual vault damage:
   - `/Users/ccf/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/brainstorming`
   - `https://github.com/ccf/agentcairn/blob/main/CHANGELOG.md`
   - branch `feat/v1.1-bitemporal-validity-and-recall`
   - slug `superpowers:subagent-driven-development`
   - plan filename `2026-06-10-agentcairn-claude-code-plugin.md`
   - a long hyphenated permalink slug (e.g. `all-of-the-above-angles-are-31b5c3dc`)
4. **AWS-pattern guard tests:** a 40-char all-lowercase prose-ish string and a 40-char hex digest (e.g. a git SHA-1) must NOT fire `aws_secret_value` — both fail the mixed-character guard (hex is single-case, so it lacks upper or lower).
5. **Corpus replay (manual, local):** run old-vs-new `redact()` over the authored turns of the real transcripts and report the per-kind delta — expect `high_entropy` ~571 → near-single-digits, every remaining hit reviewable.

## Rollout

- **0.7.1** — behavior fix to redaction; no schema/format change, no migration.
- Then the queued **vault rebuild**: clear the dedup ledger → re-`sweep` (structural ingestion 0.7.0 + this fix) gated on the `cairn ingest --dry-run` verification → memories re-created clean and undamaged.

## Out of scope (YAGNI / later)

- Segment-level "wordiness" exemptions on wide tokens (the rejected alternative; revisit only if a real gap shows up).
- Repairing already-damaged notes in place — the rebuild supersedes it.
- Any change to redaction of *titles/tags* flow or pass ordering.
