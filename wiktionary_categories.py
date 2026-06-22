"""
wiktionary_categories.py -- Wiktionary category -> word base (authority/coverage).

Builds a topic-labeled WORD BASE by walking a Wiktionary category tree
(e.g. Category:en:Sciences -> Biology -> Genetics -> terms). This is an
AUTHORITY / COVERAGE source, NOT a frequency source: it does not go through
pool_counter. Its job is to *guarantee inclusion* of valuable vocabulary that
frequency sources (transcripts, news) may miss, and to carry a topic label that
also seeds domain/expert dictionaries.

Inclusion contract (how the builder consumes a word base):
  - For each authority term: if it appears in the pooled frequency counts, it
    enters at its real frequency; if it is absent, it enters at a FLOOR frequency
    so it still gets a dictionary entry (at the deepest appropriate tier).
  - Provenance: the entry is tagged source="wiktionary" + its topic path
    (the basis for facets/domain labels + expert-dictionary selection).
  - Authority inclusion never inflates frequency counts (spec §8).

Acquisition:
  - SAMPLE mode (default): reads a bundled category tree (samples/wiktionary/*.json)
    so the harvester runs offline.
  - LIVE mode (--live): walks the real tree via the MediaWiki API
    (en.wiktionary.org/w/api.php?action=query&list=categorymembers...). Run this
    in a network-enabled environment; it is not executed in the R&D sandbox.

Design: docs/compression/spec-corpus-sourcing.md (§2 authority class, §8 inclusion).

Run from the repo root (R-D-concepts/):
    python -m semantic_compression.wiktionary_categories --root en:Sciences
    python -m semantic_compression.wiktionary_categories --live --root en:Sciences   # needs network
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

sys.path.insert(0, ".")

SAMPLE_DIR = Path("semantic_compression/samples/wiktionary")
WORDBASE_OUT = Path("semantic_compression/data/wordbase_wiktionary.tsv")
SOURCE_ID = "wiktionary"
LICENSE = "CC-BY-SA-4.0 (derived headword list)"


@dataclass(frozen=True)
class AuthorityTerm:
    """A coverage term + the topic categories that attest it."""
    term: str
    topics: tuple[str, ...] = field(default_factory=tuple)
    source: str = SOURCE_ID


# ---------------------------------------------------------------------------
# Tree walk (shared by sample + live)
# ---------------------------------------------------------------------------

def _normalize_cat(cat: str) -> str:
    """'Category:en:Biology' / 'en:Biology' -> 'en:Biology'."""
    return cat.split("Category:", 1)[-1].strip()


def walk_categories(get_members, root: str, max_depth: int = 8) -> dict[str, set[str]]:
    """
    Generic recursive walk. `get_members(category) -> (subcats, terms)`.
    Returns term -> set of topic categories it was found under. Cycle-safe.
    """
    root = _normalize_cat(root)
    term_topics: dict[str, set[str]] = {}
    visited: set[str] = set()

    def visit(cat: str, depth: int) -> None:
        cat = _normalize_cat(cat)
        if cat in visited or depth > max_depth:
            return
        visited.add(cat)
        subcats, terms = get_members(cat)
        for t in terms:
            term_topics.setdefault(t, set()).add(cat)
        for sub in subcats:
            visit(sub, depth + 1)

    visit(root, 0)
    return term_topics


# ---------------------------------------------------------------------------
# SAMPLE source
# ---------------------------------------------------------------------------

def _sample_get_members(tree: dict):
    cats = tree["categories"]

    def get(cat: str):
        node = cats.get(cat, {})
        subs = [_normalize_cat(s) for s in node.get("subcategories", [])]
        terms = list(node.get("terms", []))
        return subs, terms
    return get


def harvest_sample(root: str = "en:Sciences",
                   sample_dir: Path = SAMPLE_DIR) -> list[AuthorityTerm]:
    # pick the sample file whose root matches (default sciences.json)
    path = sample_dir / (root.split(":")[-1].lower() + ".json")
    if not path.exists():
        # fall back to any single sample
        cands = sorted(sample_dir.glob("*.json"))
        if not cands:
            raise FileNotFoundError(f"no sample under {sample_dir}")
        path = cands[0]
    tree = json.loads(path.read_text(encoding="utf-8"))
    term_topics = walk_categories(_sample_get_members(tree), tree.get("root", root))
    return _to_terms(term_topics)


# ---------------------------------------------------------------------------
# LIVE source (MediaWiki API) -- runs only with network access
# ---------------------------------------------------------------------------

def _live_get_members(category: str):
    """One API page of members. Network-only; not run in the sandbox."""
    import urllib.parse
    import urllib.request

    api = "https://en.wiktionary.org/w/api.php"
    subs, terms = [], []
    cmcontinue = None
    while True:
        params = {
            "action": "query", "list": "categorymembers",
            "cmtitle": f"Category:{_normalize_cat(category)}",
            "cmlimit": "500", "cmtype": "subcat|page", "format": "json",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue
        url = api + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
            data = json.loads(r.read().decode("utf-8"))
        for m in data.get("query", {}).get("categorymembers", []):
            title = m["title"]
            if title.startswith("Category:"):
                subs.append(title)
            elif ":" not in title:        # mainspace headword (skip Appendix:, etc.)
                terms.append(title.lower())
        cmcontinue = data.get("continue", {}).get("cmcontinue")
        if not cmcontinue:
            break
    return subs, terms


def harvest_live(root: str = "en:Sciences", max_depth: int = 6) -> list[AuthorityTerm]:
    term_topics = walk_categories(_live_get_members, root, max_depth=max_depth)
    return _to_terms(term_topics)


# ---------------------------------------------------------------------------
# Word base
# ---------------------------------------------------------------------------

def _to_terms(term_topics: dict[str, set[str]]) -> list[AuthorityTerm]:
    return [AuthorityTerm(term=t, topics=tuple(sorted(tp)))
            for t, tp in sorted(term_topics.items())]


def write_wordbase(terms: list[AuthorityTerm], path: Path = WORDBASE_OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# wordbase source={SOURCE_ID} license={LICENSE}\n")
        f.write("# term\ttopics\tsource\n")
        for t in terms:
            f.write(f"{t.term}\t{','.join(t.topics)}\t{t.source}\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Harvest a Wiktionary category word base")
    p.add_argument("--root", default="en:Sciences")
    p.add_argument("--live", action="store_true",
                   help="walk the real MediaWiki category tree (needs network)")
    p.add_argument("--out", default=str(WORDBASE_OUT))
    args = p.parse_args()

    terms = harvest_live(args.root) if args.live else harvest_sample(args.root)
    write_wordbase(terms, Path(args.out))
    topics = sorted({tp for t in terms for tp in t.topics})
    print(f"harvested {len(terms)} terms across {len(topics)} topics "
          f"({'live' if args.live else 'sample'})  ->  {args.out}")
    for t in terms[:8]:
        print(f"  {t.term:<16} {','.join(t.topics)}")


if __name__ == "__main__":
    main()
