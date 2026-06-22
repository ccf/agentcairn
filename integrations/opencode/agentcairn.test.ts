// Pure-logic tests for the agentcairn OpenCode plugin.
// Run with:  node --test integrations/opencode/agentcairn.test.ts
//
// Node 22+ natively strips TypeScript type annotations (via --experimental-strip-types,
// enabled by default in Node 26).  No build step or extra deps required.

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { buildRecallArgs, formatMemoryBlock } from "./agentcairn.ts";

// ---------------------------------------------------------------------------
// buildRecallArgs
// ---------------------------------------------------------------------------

describe("buildRecallArgs", () => {
  test("returns correct argv for default k=5", () => {
    const args = buildRecallArgs("how do I deploy?");
    assert.deepEqual(args, ["recall", "how do I deploy?", "--json", "--k", "5"]);
  });

  test("respects custom k value", () => {
    const args = buildRecallArgs("auth flow", 10);
    assert.deepEqual(args, ["recall", "auth flow", "--json", "--k", "10"]);
  });

  test("first positional arg is always 'recall'", () => {
    const [cmd] = buildRecallArgs("anything");
    assert.equal(cmd, "recall");
  });

  test("k is always stringified", () => {
    const args = buildRecallArgs("x", 3);
    assert.equal(typeof args[4], "string");
    assert.equal(args[4], "3");
  });
});

// ---------------------------------------------------------------------------
// formatMemoryBlock
// ---------------------------------------------------------------------------

describe("formatMemoryBlock", () => {
  test("empty array returns empty string", () => {
    assert.equal(formatMemoryBlock([]), "");
  });

  test("null/undefined-like array returns empty string", () => {
    // @ts-expect-error intentional runtime check
    assert.equal(formatMemoryBlock(null), "");
    // @ts-expect-error intentional runtime check
    assert.equal(formatMemoryBlock(undefined), "");
  });

  test("array of notes with no text returns empty string", () => {
    assert.equal(formatMemoryBlock([{ title: "only title" }]), "");
    assert.equal(formatMemoryBlock([{ text: "" }, { text: "   " }]), "");
  });

  test("single note — output contains the text and the header", () => {
    const result = formatMemoryBlock([{ text: "make ship" }]);
    assert.ok(result.includes("make ship"), "should contain note text");
    assert.ok(
      result.startsWith("## Relevant memories (agentcairn)"),
      "should start with standard header",
    );
  });

  test("multiple notes are separated by horizontal rule", () => {
    const result = formatMemoryBlock([
      { text: "first fact" },
      { text: "second fact" },
    ]);
    assert.ok(result.includes("first fact"));
    assert.ok(result.includes("second fact"));
    assert.ok(result.includes("---"), "should contain HR separator");
  });

  test("notes with only title and no text are filtered out", () => {
    const result = formatMemoryBlock([
      { title: "T1", text: "" },
      { title: "T2", text: "useful content" },
    ]);
    assert.ok(result.includes("useful content"));
    assert.ok(!result.includes("T1"), "empty-text note should not appear");
  });

  test("title field is unused in output body (text only)", () => {
    const result = formatMemoryBlock([{ title: "MyTitle", text: "body text" }]);
    // Title is not injected into the block — only text is.
    assert.ok(result.includes("body text"));
    assert.ok(!result.includes("MyTitle"), "title should not appear in block");
  });

  test("score field on notes is silently ignored", () => {
    // cairn recall --json includes a `score` field; ensure it doesn't break anything.
    const result = formatMemoryBlock([
      { title: "T", text: "scored note", score: 0.92 } as any,
    ]);
    assert.ok(result.includes("scored note"));
  });
});
