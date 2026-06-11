# src/cairn/ingest/__init__.py
# SPDX-License-Identifier: Apache-2.0
from cairn.ingest.dedup import DedupLedger, content_hash
from cairn.ingest.distill import Distiller, ExtractiveDistiller, write_derived_note
from cairn.ingest.events import EventKind, NormalizedEvent
from cairn.ingest.locate import encode_cwd, find_transcripts, parse_transcript
from cairn.ingest.models import (
    Candidate,
    IngestReport,
    RedactionResult,
    Transcript,
)
from cairn.ingest.pipeline import ingest_transcript
from cairn.ingest.redact import redact

__all__ = [
    "Candidate",
    "DedupLedger",
    "Distiller",
    "EventKind",
    "ExtractiveDistiller",
    "IngestReport",
    "NormalizedEvent",
    "RedactionResult",
    "Transcript",
    "content_hash",
    "encode_cwd",
    "find_transcripts",
    "ingest_transcript",
    "parse_transcript",
    "redact",
    "write_derived_note",
]
