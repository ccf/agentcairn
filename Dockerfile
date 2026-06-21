# Minimal container that runs the agentcairn MCP server over stdio.
#
# agentcairn is local-first and daemonless — Docker is NOT required to use it
# (install with `uvx agentcairn` or `pip install agentcairn`). This image exists
# so MCP directories such as Glama can verify the server builds, starts, and
# answers tool-introspection (tools/list) requests.
FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

# A writable vault so the server can initialize on first run.
ENV CAIRN_VAULT=/vault
RUN mkdir -p /vault

# stdio MCP server — the `agentcairn` console script (cairn.mcp.server:main),
# the same entrypoint MCP hosts launch via `uvx agentcairn`.
ENTRYPOINT ["agentcairn"]
