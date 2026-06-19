---
title: capture runs on PreCompact, not only SessionEnd
permalink: precompact-capture
type: memory
---
A PreCompact hook runs the detached cairn sweep so long and resumed sessions are captured at each compaction boundary, instead of only when a session formally ends.
