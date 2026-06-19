---
title: the DuckDB index is vault-scoped
permalink: vault-scoped-index
type: memory
---
The index path is derived from a hash of the resolved vault path, so a scratch vault can never pollute the production index. Doctor reports DRIFT when they diverge.
