"""
source_adapters.py -- Multi-source corpus ingest contract (C1).

Generalizes corpus_scanner.py (the transcript source) so every vocabulary source
yields a uniform SourceRecord stream for the counters. Adding a source = adding
an adapter. corpus_scanner stays the transcripts implementation with NO behavior
change; TranscriptAdapter is a thin wrapper over its per-file scan, so the transcripts
token output is byte-identical to today's pipeline.

Design: docs/compression/spec-corpus-sourcing.md (§4 adapter contract).

Run from the repo root (R-D-concepts/), matching corpus_scanner's package imports:
    python -m semantic_compression.source_adapters --source transcripts --limit 5
"""

from __future__ import annotations

import abc
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from semantic_compression.config import TRANSCRIPT_DIR


# ---------------------------------------------------------------------------
# The uniform record every source yields
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceRecord:
    """One unit of source text for vocabulary counting.

    `text` is already cleaned and per-source deduplicated by the adapter; the
    counters tokenize it. `source_id` is carried so the counters can keep
    per-source counts (dispersion + provenance — spec §6).
    """
    source_id: str
    doc_id: str
    text: str
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Adapter contract + registry
# ---------------------------------------------------------------------------

class SourceAdapter(abc.ABC):
    """Contract every vocabulary source implements."""
    source_id: str = "base"
    license: str = "unknown"

    @abc.abstractmethod
    def scan(self) -> Iterator[SourceRecord]:
        """Yield cleaned, per-source-deduped SourceRecords."""
        raise NotImplementedError


ADAPTERS: dict[str, type[SourceAdapter]] = {}


def register(cls: type[SourceAdapter]) -> type[SourceAdapter]:
    if cls.source_id in ADAPTERS:
        raise ValueError(f"duplicate source_id {cls.source_id!r}")
    ADAPTERS[cls.source_id] = cls
    return cls


def get_adapter(source_id: str, **kwargs) -> SourceAdapter:
    if source_id not in ADAPTERS:
        raise KeyError(f"unknown source {source_id!r}; have {sorted(ADAPTERS)}")
    return ADAPTERS[source_id](**kwargs)


def iter_records(source_id: str, **kwargs) -> Iterator[SourceRecord]:
    """Convenience: stream records from a registered source."""
    yield from get_adapter(source_id, **kwargs).scan()


# ---------------------------------------------------------------------------
# transcript — the existing corpus_scanner, wrapped (no behavior change)
# ---------------------------------------------------------------------------

@register
class TranscriptAdapter(SourceAdapter):
    source_id = "transcripts"
    license = "per-channel (derived counts only)"

    def __init__(self, transcript_dir: str | Path = TRANSCRIPT_DIR,
                 limit: int | None = None):
        self.transcript_dir = Path(transcript_dir)
        self.limit = limit

    def scan(self) -> Iterator[SourceRecord]:
        # Per-file scan via corpus_scanner.scan_single → identical tokens to the
        # current pipeline, with no corpus_stats DB side effects.
        from semantic_compression.corpus_scanner import scan_single
        files = sorted(self.transcript_dir.rglob("*.json"))
        if self.limit is not None:
            files = files[:self.limit]
        for path in files:
            for video_id, chunk_id, clean_text, meta in scan_single(str(path)):
                m = dict(meta)
                m["source_id"] = self.source_id
                yield SourceRecord(
                    source_id=self.source_id,
                    doc_id=f"{video_id}#{chunk_id}",
                    text=clean_text,
                    meta=m,
                )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Stream SourceRecords from an adapter")
    p.add_argument("--source", default="transcripts", choices=sorted(ADAPTERS))
    p.add_argument("--limit", type=int, default=3)
    args = p.parse_args()
    n = 0
    for rec in iter_records(args.source, limit=args.limit):
        if n < 3:
            print(f"[{rec.source_id}] {rec.doc_id}  {rec.text[:70]!r}")
        n += 1
    print(f"... {n} records from {args.source!r} "
          f"(license: {get_adapter(args.source).license})")


if __name__ == "__main__":
    main()
