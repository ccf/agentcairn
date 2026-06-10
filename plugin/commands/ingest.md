---
description: Distill recent sessions into the vault now.
---
Run `uvx --from agentcairn cairn sweep --vault "${CAIRN_VAULT:-$HOME/agentcairn}"` to ingest and reindex recent sessions (the index location is taken from `CAIRN_INDEX`, falling back to the default cache), then report how many memories were written.
