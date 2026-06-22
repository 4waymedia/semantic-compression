"""
wikipedia_adapter.py -- Wikipedia SourceAdapter (C3).

Adds Wikipedia as a vocabulary source: reads wiki-markup articles, strips markup
to plain text, per-source dedups, and yields SourceRecords for the pool counter.
Registers itself into source_adapters.ADAPTERS on import (no edit to that module).

Design: docs/compression/spec-corpus-sourcing.md (§4 adapter, §7 dedup, license).

This R&D adapter ships a tiny bundled sample (samples/wikipedia/*.wiki) so the
two-source pooled run works offline. A production build points --dump at a real
Wikipedia XML/extracted dump; `clean_wikitext` is the reusable part.

Run from the repo root (R-D-concepts/):
    python -m semantic_compression.wikipedia_adapter --limit 2
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterator

sys.path.insert(0, ".")

from semantic_compression.source_adapters import (
    SourceAdapter, SourceRecord, register, iter_records,
)

SAMPLE_DIR = Path("semantic_compression/samples/wikipedia")


# ---------------------------------------------------------------------------
# Wiki-markup cleaning  (regex-based; reusable for real dumps)
# ---------------------------------------------------------------------------

_RE_COMMENT   = re.compile(r"<!--.*?-->", re.DOTALL)
_RE_REF_PAIR  = re.compile(r"<ref[^>]*>.*?</ref>", re.DOTALL | re.IGNORECASE)
_RE_REF_SELF  = re.compile(r"<ref[^>]*/>", re.IGNORECASE)
_RE_TAG       = re.compile(r"<[^>]+>")                       # any leftover html tag
_RE_TEMPLATE  = re.compile(r"\{\{[^{}]*\}\}")                # {{...}} (non-nested)
_RE_TABLE     = re.compile(r"\{\|.*?\|\}", re.DOTALL)        # {| ... |} tables
_RE_FILE_LINK = re.compile(r"\[\[(?:File|Image):[^\[\]]*\]\]", re.IGNORECASE)
_RE_WIKILINK  = re.compile(r"\[\[([^\[\]|]+)\|([^\[\]]+)\]\]")   # [[target|text]] -> text
_RE_WIKILINK2 = re.compile(r"\[\[([^\[\]]+)\]\]")               # [[target]]      -> target
_RE_EXTLINK   = re.compile(r"\[https?://\S+\s+([^\]]+)\]")      # [url text]      -> text
_RE_EXTLINK2  = re.compile(r"\[https?://\S+\]")                 # [url]           -> ""
_RE_BOLDITAL  = re.compile(r"'{2,5}")                          # ''' '' -> strip
_RE_HEADING   = re.compile(r"^\s*={2,}\s*(.*?)\s*={2,}\s*$", re.MULTILINE)
_RE_BLANKS    = re.compile(r"\n{3,}")


def clean_wikitext(raw: str) -> str:
    """Strip wiki markup to readable plain text. Order matters."""
    s = raw
    s = _RE_COMMENT.sub("", s)
    s = _RE_REF_PAIR.sub("", s)
    s = _RE_REF_SELF.sub("", s)
    s = _RE_TABLE.sub("", s)
    s = _RE_FILE_LINK.sub("", s)
    # templates can nest one level in our samples; run twice
    s = _RE_TEMPLATE.sub("", s)
    s = _RE_TEMPLATE.sub("", s)
    s = _RE_WIKILINK.sub(r"\2", s)
    s = _RE_WIKILINK2.sub(r"\1", s)
    s = _RE_EXTLINK.sub(r"\1", s)
    s = _RE_EXTLINK2.sub("", s)
    s = _RE_HEADING.sub(r"\1", s)
    s = _RE_BOLDITAL.sub("", s)
    s = _RE_TAG.sub("", s)
    s = _RE_BLANKS.sub("\n\n", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

@register
class WikipediaAdapter(SourceAdapter):
    source_id = "wikipedia"
    license = "CC-BY-SA-4.0 (derived counts only)"

    def __init__(self, sample_dir: str | Path = SAMPLE_DIR, limit: int | None = None):
        self.sample_dir = Path(sample_dir)
        self.limit = limit

    def scan(self) -> Iterator[SourceRecord]:
        files = sorted(self.sample_dir.rglob("*.wiki"))
        if self.limit is not None:
            files = files[:self.limit]
        seen: set[str] = set()
        for path in files:
            raw = path.read_text(encoding="utf-8")
            text = clean_wikitext(raw)
            # per-source dedup: drop exact-duplicate paragraphs across articles
            kept = []
            for para in text.split("\n\n"):
                p = para.strip()
                if not p:
                    continue
                key = p.lower()
                if key in seen:
                    continue
                seen.add(key)
                kept.append(p)
            if not kept:
                continue
            yield SourceRecord(
                source_id=self.source_id,
                doc_id=path.stem,
                text="\n\n".join(kept),
                meta={"source_id": self.source_id, "title": path.stem},
            )


def main() -> None:
    p = argparse.ArgumentParser(description="Stream cleaned Wikipedia SourceRecords")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    n = 0
    for rec in iter_records("wikipedia", limit=args.limit):
        if n < 3:
            print(f"[{rec.source_id}] {rec.doc_id}: {rec.text[:90]!r}")
        n += 1
    print(f"... {n} wikipedia records")


if __name__ == "__main__":
    main()
