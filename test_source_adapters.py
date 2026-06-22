"""
test_source_adapters.py -- C1 gate: the SourceAdapter contract + transcriptAdapter.

Proves the transcripts adapter reproduces corpus_scanner.scan_single output exactly
(no behavior change), and that the registry/contract work.

Run from the repo root (R-D-concepts/):
    python -m semantic_compression.test_source_adapters
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

from semantic_compression.config import TRANSCRIPT_DIR
from semantic_compression.corpus_scanner import scan_single
from semantic_compression.source_adapters import (
    ADAPTERS, SourceRecord, SourceAdapter, get_adapter, iter_records,
)

_SAMPLE_N = 3


def _sample_files():
    return sorted(Path(TRANSCRIPT_DIR).rglob("*.json"))[:_SAMPLE_N]


def test_registry_has_transcripts():
    assert "transcripts" in ADAPTERS
    assert issubclass(ADAPTERS["transcripts"], SourceAdapter)
    assert get_adapter("transcripts").license  # tagged


def test_unknown_source_raises():
    try:
        get_adapter("nope")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_transcripts_reproduces_scan_single():
    files = _sample_files()
    assert files, f"no transcripts under {TRANSCRIPT_DIR}"
    # direct pipeline output
    direct = [text for f in files for (_v, _c, text, _m) in scan_single(str(f))]
    # via the adapter
    recs = list(get_adapter("transcripts", limit=_SAMPLE_N).scan())
    assert all(isinstance(r, SourceRecord) for r in recs)
    assert [r.text for r in recs] == direct, "adapter drifted from scan_single"
    # records carry source provenance
    assert all(r.source_id == "transcripts" and r.meta.get("source_id") == "transcripts"
               for r in recs)
    assert all("#" in r.doc_id for r in recs)


def main() -> None:
    tests = [
        test_registry_has_transcripts,
        test_unknown_source_raises,
        test_transcripts_reproduces_scan_single,
    ]
    for t in tests:
        t()
        print(f"[OK] {t.__name__}")
    print(f"\n=== test_source_adapters: {len(tests)}/{len(tests)} PASSED ===")


if __name__ == "__main__":
    main()
