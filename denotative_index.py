"""
denotative_index.py -- the 768-d semantic (denotative) index, built INCREMENTALLY
in frequency order.

Counterpart to faiss_builder.py (3-d EPA/affect). EPA cannot encode denotation
(car -> credentials); distributional embeddings can (car -> truck). all-mpnet-base-v2
(768-d) over L2-normalised vectors with IndexFlatIP == cosine.

WHY FREQUENCY-ORDERED + BATCHED (measured on general_v0.4_char4):
  first 10,000 words  = 95.7% of all real usage
  first 50,000 words  = 99.3%
  the 233k char-4 tail = 95% of words but <5% of usage -- and is where the ASR
  corruptions live (rabbit->rabbitant:0.80, biger, recolleation). So embed the
  clean common core first; ship from any checkpoint; the noisy tail is optional.

PERSISTENCE (resumable): vectors append to a raw f32 file after every batch, with a
progress.json checkpoint. A crash at word 130k resumes at 130k, not zero. `finalize`
builds the FAISS index from the accumulated vectors at any point.

  # 1. plan (embeds nothing)
  python denotative_index.py plan  --meta db\builds\general_v0.4_char4\meta.db
  # 2. embed the common core first, 10k at a time (resumable; re-run to continue)
  python denotative_index.py embed --meta ... --out db --batch 10000 --limit 20000 --device cuda
  # 3. build the searchable index from whatever is embedded so far
  python denotative_index.py finalize --meta ... --out db
  # (repeat 2 with a higher --limit, or drop --limit for the whole tail)

Heavy deps (sentence-transformers, faiss) import INSIDE the functions that need
them, so `plan`, `finalize`'s reader, and DenotativeIndex.load stay importable
without them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import numpy as np

EMBEDDING_MODEL = "all-mpnet-base-v2"
EMBEDDING_DIM = 768
MAX_SURFACE_LEN = 40

INDEX_NAME = "dictionary.denotative.index"
SURFACES_NAME = "dictionary.denotative.surfaces.json"
SIDECAR_NAME = "dictionary.denotative.json"
VECS_NAME = "dictionary.denotative.vecs.f32"        # raw N x 768 float32, append-only
PROGRESS_NAME = "dictionary.denotative.progress.json"


# ---------------------------------------------------------------------------
# Pool selection (deterministic, frequency-ordered; no heavy deps)
# ---------------------------------------------------------------------------

def is_embeddable(s: str) -> bool:
    """Drop what a 768-d encoder can't represent. NOTE: this does NOT catch
    alphabetic ASR corruptions (rabbitant, biger) -- those live in the rare tail
    and are handled by building common-first + a lowercase dedupe on neighbours."""
    return bool(s) and bool(s.strip()) and len(s) <= MAX_SURFACE_LEN and any(c.isalpha() for c in s)


def select_ranked(meta_db: str | Path) -> list[str]:
    """Single-word CONTENT surfaces, ordered by DESCENDING frequency (ties broken
    by surface, so the order is reproducible). Frequency-order = usage-order, so a
    truncated build still covers the most-used words. Deterministic."""
    con = sqlite3.connect(str(meta_db))
    try:
        rows = con.execute(
            "SELECT surface, COALESCE(frequency, 0) FROM meta "
            "WHERE kind='word' AND utility='CONTENT'").fetchall()
    finally:
        con.close()
    pool = [(s, f) for s, f in rows if is_embeddable(s)]
    pool.sort(key=lambda t: (-t[1], t[0]))
    return [s for s, _ in pool]


def coverage(meta_db: str | Path, n: int) -> float:
    """Fraction of total corpus occurrences covered by the first n ranked words."""
    con = sqlite3.connect(str(meta_db))
    try:
        rows = con.execute(
            "SELECT surface, COALESCE(frequency,0) FROM meta "
            "WHERE kind='word' AND utility='CONTENT'").fetchall()
    finally:
        con.close()
    freqs = sorted((f for s, f in rows if is_embeddable(s)), reverse=True)
    tot = sum(freqs) or 1
    return sum(freqs[:n]) / tot


def surfaces_fingerprint(surfaces: list[str]) -> str:
    h = hashlib.sha256()
    for s in surfaces:
        h.update(s.encode("utf-8")); h.update(b"\n")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Incremental embed (requires sentence-transformers)
# ---------------------------------------------------------------------------

def _progress_path(out_dir): return Path(out_dir) / PROGRESS_NAME
def _vecs_path(out_dir):     return Path(out_dir) / VECS_NAME


def _load_progress(out_dir, fp):
    p = _progress_path(out_dir)
    if not p.exists():
        return 0
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("surfaces_fingerprint") != fp:
        raise RuntimeError(
            "plan changed since the last run (surfaces_fingerprint mismatch). The "
            "meta.db or filter differs. Delete the .vecs.f32 + .progress.json to "
            "rebuild, or point at the original meta.db.")
    return int(d["embedded"])


def embed(meta_db, out_dir, *, batch=10000, limit=None, device=None,
          model_name=EMBEDDING_MODEL, fresh=False) -> dict:
    """Embed ranked surfaces in batches, appending to a raw f32 file. Resumable:
    re-run to continue from the checkpoint. `--limit` stops early (e.g. 20000 =
    the ~98%-usage core). `fresh=True` clears any prior vectors+checkpoint first --
    use when the dictionary/vocab changed (old vectors are for a dead vocab), so the
    surfaces_fingerprint guard can't abort the rebuild."""
    from sentence_transformers import SentenceTransformer   # noqa: PLC0415

    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    if fresh:
        for p in (_vecs_path(out_dir), _progress_path(out_dir)):
            if p.exists():
                p.unlink()
        print("fresh: cleared prior vectors + checkpoint")
    surfaces = select_ranked(meta_db)
    fp = surfaces_fingerprint(surfaces)
    done = _load_progress(out_dir, fp)
    target = len(surfaces) if limit is None else min(int(limit), len(surfaces))

    vecs = _vecs_path(out_dir)
    # guard: the vecs file must hold exactly `done` rows
    if vecs.exists():
        have = vecs.stat().st_size // (EMBEDDING_DIM * 4)
        if have != done:
            raise RuntimeError(
                f"{vecs} holds {have} rows but progress says {done}. Corrupt "
                "checkpoint -- delete both and rebuild.")
    if done >= target:
        print(f"nothing to do: {done:,} already embedded (target {target:,}).")
        return {"embedded": done}

    print(f"embed: {done:,} -> {target:,}  ({len(surfaces):,} total, batch {batch})")
    model = SentenceTransformer(model_name, device=device)
    t0 = time.time()
    with open(vecs, "ab") as fh:
        for lo in range(done, target, batch):
            hi = min(lo + batch, target)
            emb = model.encode(surfaces[lo:hi], batch_size=256, convert_to_numpy=True,
                               normalize_embeddings=True, show_progress_bar=False)
            emb = np.ascontiguousarray(emb, dtype="float32")
            if emb.shape[1] != EMBEDDING_DIM:
                raise RuntimeError(f"dim {emb.shape[1]} != {EMBEDDING_DIM}")
            fh.write(emb.tobytes()); fh.flush()
            _progress_path(out_dir).write_text(json.dumps({
                "embedded": hi, "target": target, "total": len(surfaces),
                "surfaces_fingerprint": fp, "dim": EMBEDDING_DIM,
                "model": model_name, "meta_db": str(meta_db),
            }), encoding="utf-8")
            rate = (hi - done) / max(time.time() - t0, 1e-6)
            print(f"  {hi:>7,}/{target:,}  (+{hi-lo})  {rate:6.0f} w/s  "
                  f"usage-coverage {coverage(meta_db, hi)*100:5.1f}%")
    print(f"done: {target:,} embedded in {time.time()-t0:.0f}s")
    return {"embedded": target}


def finalize(meta_db, out_dir, model_name=EMBEDDING_MODEL) -> dict:
    """Build the FAISS index from whatever has been embedded so far."""
    import faiss                                   # noqa: PLC0415
    out_dir = Path(out_dir)
    surfaces = select_ranked(meta_db)
    fp = surfaces_fingerprint(surfaces)
    done = _load_progress(out_dir, fp)
    if done == 0:
        raise RuntimeError("nothing embedded yet; run `embed` first.")
    vecs = np.fromfile(_vecs_path(out_dir), dtype="float32").reshape(done, EMBEDDING_DIM)
    index = faiss.IndexFlatIP(EMBEDDING_DIM)
    index.add(np.ascontiguousarray(vecs))
    faiss.write_index(index, str(out_dir / INDEX_NAME))
    covered = surfaces[:done]
    (out_dir / SURFACES_NAME).write_text(json.dumps(covered), encoding="utf-8")
    meta = {"meta_db": str(meta_db), "entry_count": done, "planned_total": len(surfaces),
            "usage_coverage": round(coverage(meta_db, done), 4),
            "dim": EMBEDDING_DIM, "index_type": "FlatIP",
            "metric": "cosine (L2-normalised inner product)", "model": model_name,
            "surfaces_fingerprint": fp, "index_path": str(out_dir / INDEX_NAME),
            "surfaces_path": str(out_dir / SURFACES_NAME),
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (out_dir / SIDECAR_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("entry_count", "planned_total",
                                           "usage_coverage")}, indent=2))
    return meta


# ---------------------------------------------------------------------------
# Read (requires faiss only)
# ---------------------------------------------------------------------------

class DenotativeIndex:
    def __init__(self, index, surfaces):
        self.index, self.surfaces = index, surfaces
        self._idx = {s: i for i, s in enumerate(surfaces)}

    @classmethod
    def load(cls, out_dir):
        import faiss                               # noqa: PLC0415
        out_dir = Path(out_dir)
        p = out_dir / INDEX_NAME
        if not p.exists():
            raise FileNotFoundError(f"{p} not built -- run embed then finalize.")
        return cls(faiss.read_index(str(p)),
                   json.loads((out_dir / SURFACES_NAME).read_text(encoding="utf-8")))

    def has(self, surface): return surface in self._idx

    def search(self, surface, pool=50):
        i = self._idx.get(surface)
        if i is None:
            return []
        q = self.index.reconstruct(i).reshape(1, -1).astype("float32")
        sims, ids = self.index.search(q, pool + 1)
        return [(self.surfaces[j], float(s)) for j, s in zip(ids[0], sims[0])
                if j != i and j >= 0][:pool]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="768-d denotative index (frequency-ordered, resumable)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "embed", "finalize"):
        s = sub.add_parser(name)
        s.add_argument("--meta", required=True)
        s.add_argument("--out", default="db")
        if name == "embed":
            s.add_argument("--batch", type=int, default=10000)
            s.add_argument("--limit", type=int, default=None,
                           help="stop after N ranked words (e.g. 20000 ~ 98%% usage)")
            s.add_argument("--device", default=None)
            s.add_argument("--fresh", action="store_true",
                           help="clear prior vectors+checkpoint before embedding (vocab changed)")
    a = ap.parse_args()

    if a.cmd == "plan":
        surf = select_ranked(a.meta)
        print(f"plan: {len(surf):,} embeddable words, frequency-ordered")
        print(f"  fingerprint {surfaces_fingerprint(surf)}")
        for n in (2000, 10000, 20000, 50000, len(surf)):
            n = min(n, len(surf))
            print(f"  first {n:>7,} -> {coverage(a.meta, n)*100:5.1f}% usage  "
                  f"~{n*EMBEDDING_DIM*4/1e9:.2f} GB")
        print(f"  head: {surf[:8]}")
    elif a.cmd == "embed":
        embed(a.meta, a.out, batch=a.batch, limit=a.limit, device=a.device, fresh=a.fresh)
    elif a.cmd == "finalize":
        finalize(a.meta, a.out)


if __name__ == "__main__":
    main()
