"""
denotative_index.py -- the 768-d semantic (denotative) index.

This is the index the architecture specified from day one and never built. It is the
counterpart to `faiss_builder.py` (which builds the 3-d EPA/AFFECT index):

    dictionary.faiss.index        dim 3    FlatL2   AFFECT     (built)
    dictionary.denotative.index   dim 768  FlatIP   DENOTATION (this module)

Why it matters: EPA has 3 dimensions of affect and cannot encode denotation, so EPA
nearest-neighbour returns `car -> credentials, attention, decoration`. Denotative
similarity requires distributional embeddings. `all-mpnet-base-v2` (768-d) over
L2-normalised vectors with `IndexFlatIP` **is cosine similarity** -- the same
construction `library_builder.build_faiss_index` uses.

Pool: single-word CONTENT surfaces from the build's meta.db (`kind='word' AND
utility='CONTENT'`) -- 250,083 for `general_v0.4_char4`. Function words, fillers and
phrases are excluded; they add cost and no denotative signal.

Heavy deps (`sentence-transformers`, `faiss`) are imported INSIDE `build()`, so this
module -- and the reader/`load()` path -- import cleanly without them. Install the
`build` extra to construct the index.

    python denotative_index.py --meta db/builds/general_v0.4_char4/meta.db --dry-run
    python denotative_index.py --meta db/builds/general_v0.4_char4/meta.db --out db

Deterministic: surfaces are sorted before embedding, so row i is stable across runs.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

EMBEDDING_MODEL = "all-mpnet-base-v2"
EMBEDDING_DIM = 768
INDEX_NAME = "dictionary.denotative.index"
SURFACES_NAME = "dictionary.denotative.surfaces.json"
SIDECAR_NAME = "dictionary.denotative.json"


# ---------------------------------------------------------------------------
# Pool selection (deterministic, no heavy deps)
# ---------------------------------------------------------------------------

MAX_SURFACE_LEN = 40


def is_embeddable(surface: str) -> bool:
    """Can a sentence-transformer produce a meaningful vector for this surface?

    Drops what a 768-d encoder cannot represent (measured on general_v0.4_char4:
    4,497 of 250,083 = 1.8%):
      * whitespace-only surfaces            ('\\n\\n', 8)
      * surfaces with NO alphabetic char    ('0', '0.0000', 4,485; 3,340 pure digits)
      * runaway ASR artifacts               ('birthcap.ap.ap.ap.ap...', len > 40)

    NOTE: the surviving pool still contains corpus noise ('000fold', '000galon').
    That is a *dictionary hygiene* problem upstream, not an index problem -- those
    surfaces occupy real vocabulary IDs. Flagged, not silently swallowed here.
    """
    if not surface or not surface.strip():
        return False
    if len(surface) > MAX_SURFACE_LEN:
        return False
    return any(c.isalpha() for c in surface)


def select_surfaces(meta_db: str | Path, *, filtered: bool = True) -> list[str]:
    """Single-word CONTENT surfaces, sorted. Sorted => row index is reproducible.
    `filtered=False` returns the raw pool (for auditing what is dropped)."""
    con = sqlite3.connect(str(meta_db))
    try:
        rows = con.execute(
            "SELECT surface FROM meta WHERE kind='word' AND utility='CONTENT'"
        ).fetchall()
    finally:
        con.close()
    pool = {r[0] for r in rows}
    if filtered:
        pool = {s for s in pool if is_embeddable(s)}
    return sorted(pool)


def surfaces_fingerprint(surfaces: list[str]) -> str:
    h = hashlib.sha256()
    for s in surfaces:
        h.update(s.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Build (requires the `build` extra: sentence-transformers + faiss)
# ---------------------------------------------------------------------------

def build(meta_db: str | Path, out_dir: str | Path,
          model_name: str = EMBEDDING_MODEL, batch_size: int = 256,
          device: str | None = None) -> dict:
    """Embed the pool, L2-normalise, build IndexFlatIP (= cosine), write artifacts."""
    import faiss                                   # noqa: PLC0415
    import numpy as np                             # noqa: PLC0415
    from sentence_transformers import SentenceTransformer   # noqa: PLC0415

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    surfaces = select_surfaces(meta_db)
    if not surfaces:
        raise RuntimeError(f"no CONTENT word surfaces found in {meta_db}")

    t0 = time.time()
    model = SentenceTransformer(model_name, device=device)
    emb = model.encode(surfaces, batch_size=batch_size, convert_to_numpy=True,
                       normalize_embeddings=True, show_progress_bar=True)
    emb = np.ascontiguousarray(emb, dtype="float32")
    if emb.shape[1] != EMBEDDING_DIM:
        raise RuntimeError(f"expected dim {EMBEDDING_DIM}, model gave {emb.shape[1]}")

    index = faiss.IndexFlatIP(emb.shape[1])        # cosine, since rows are unit-norm
    index.add(emb)
    faiss.write_index(index, str(out_dir / INDEX_NAME))
    (out_dir / SURFACES_NAME).write_text(json.dumps(surfaces), encoding="utf-8")

    meta = {
        "meta_db": str(meta_db),
        "entry_count": len(surfaces),
        "dim": int(emb.shape[1]),
        "index_type": "FlatIP",
        "metric": "cosine (L2-normalised inner product)",
        "model": model_name,
        "surfaces_fingerprint": surfaces_fingerprint(surfaces),
        "index_path": str(out_dir / INDEX_NAME),
        "surfaces_path": str(out_dir / SURFACES_NAME),
        "build_elapsed_s": round(time.time() - t0, 2),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out_dir / SIDECAR_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


# ---------------------------------------------------------------------------
# Read (requires faiss only)
# ---------------------------------------------------------------------------

class DenotativeIndex:
    """Loaded 768-d cosine index + its surface list."""

    def __init__(self, index, surfaces: list[str]):
        self.index = index
        self.surfaces = surfaces
        self._idx = {s: i for i, s in enumerate(surfaces)}

    @classmethod
    def load(cls, out_dir: str | Path) -> "DenotativeIndex":
        import faiss                               # noqa: PLC0415
        out_dir = Path(out_dir)
        idx_path = out_dir / INDEX_NAME
        if not idx_path.exists():
            raise FileNotFoundError(
                f"{idx_path} not built. Run: python denotative_index.py "
                f"--meta <build>/meta.db --out {out_dir}   (needs the `build` extra)")
        index = faiss.read_index(str(idx_path))
        surfaces = json.loads((out_dir / SURFACES_NAME).read_text(encoding="utf-8"))
        return cls(index, surfaces)

    def has(self, surface: str) -> bool:
        return surface in self._idx

    def search(self, surface: str, pool: int = 50) -> list[tuple[str, float]]:
        """Top-`pool` denotative neighbours as (surface, cosine). Excludes self."""
        import numpy as np                         # noqa: PLC0415
        i = self._idx.get(surface)
        if i is None:
            return []
        q = self.index.reconstruct(i).reshape(1, -1).astype("float32")
        sims, ids = self.index.search(q, pool + 1)
        out = []
        for j, s in zip(ids[0], sims[0]):
            if j == i or j < 0:
                continue
            out.append((self.surfaces[j], float(s)))
        return out[:pool]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Build the 768-d denotative index")
    p.add_argument("--meta", required=True, help="a build's meta.db")
    p.add_argument("--out", default="db", help="output dir for index + sidecar")
    p.add_argument("--model", default=EMBEDDING_MODEL)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--device", default=None, help="cuda | cpu (default: auto)")
    p.add_argument("--dry-run", action="store_true",
                   help="report the pool + cost, embed nothing")
    a = p.parse_args()

    if a.dry_run:
        raw = select_surfaces(a.meta, filtered=False)
        s = select_surfaces(a.meta)
        dropped = [x for x in raw if not is_embeddable(x)]
        gb = len(s) * EMBEDDING_DIM * 4 / 1e9
        print(f"raw pool      : {len(raw):,} single-word CONTENT surfaces")
        print(f"dropped       : {len(dropped):,} unembeddable "
              f"({100*len(dropped)/max(len(raw),1):.1f}%)")
        print(f"pool          : {len(s):,} to embed")
        print(f"fingerprint   : {surfaces_fingerprint(s)}")
        print(f"index size    : ~{gb:.2f} GB float32 ({EMBEDDING_DIM}-d)")
        print(f"model         : {a.model}")
        print(f"sample        : {s[:6]}")
        n_digit = sum(1 for x in dropped if x.strip().isdigit())
        if n_digit:
            print(f"\n!! {n_digit:,} pure-digit surfaces occupy dictionary IDs "
                  f"(corpus hygiene, upstream of this index)")
        print("\n(dry run -- nothing embedded. Drop --dry-run to build.)")
        return

    meta = build(a.meta, a.out, a.model, a.batch_size, a.device)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
