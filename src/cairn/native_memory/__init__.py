# SPDX-License-Identifier: Apache-2.0
"""One-way bridges from host-native memory into the AgentCairn vault."""

from cairn.native_memory.claude_code import ClaudeCodeMemorySource
from cairn.native_memory.importer import apply_import_plan, plan_import
from cairn.native_memory.models import (
    NativeMemoryAction,
    NativeMemoryDiscovery,
    NativeMemoryDocument,
    NativeMemoryPlan,
    NativeMemoryReport,
    NativeMemorySource,
)

__all__ = [
    "ClaudeCodeMemorySource",
    "NativeMemoryAction",
    "NativeMemoryDiscovery",
    "NativeMemoryDocument",
    "NativeMemoryPlan",
    "NativeMemoryReport",
    "NativeMemorySource",
    "apply_import_plan",
    "plan_import",
]
