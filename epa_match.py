"""
epa_match.py -- join the GLOBAL Warriner EPA substrate (System 2 / mneme) to a
dictionary build's Base64 IDs, producing the per-dictionary id-keyed `epa` table.

Build-cascade MATCH step (run after IDs are assigned, like facets):
    words   : exact Warriner lookup           -> id -> (E,P,A)
    phrases : COMPOSE from rated constituents  -> id -> mean(E,P,A)   (Way 1)
    -> package dictionary.lmdb b'epa'  (id_bytes -> '<fff>' 12B)
    + epa_stats.json coverage report.

The global substrate stays word-keyed and shared; only the matched/composed subset
is written into the package, keyed by id_bytes. Composition is the deterministic
"Way 1" of docs (Memory/mneme/docs/phrase-epa-strategy.md); the non-compositional
residual (phrase_uncovered) is what later AI passes handle.
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import lmdb

_here = Path(__file__).resolve()
for _cand in (Path(os.environ["EPA_MNEME_PATH"]) if os.environ.get("EPA_MNEME_PATH") else None,
              _here.parent.parent / "Memory"):
    if _cand and (_cand / "mneme").exists() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

from mneme.substrate.epa_projector import (
    load_warriner, epa_from_fallback, pack_epa, WARRINER_CSV, build_epa_db,
)
try:
    from mneme.substrate.epa_substrate import substrate_norms, SUBSTRATE_PATH
except Exception:
    substrate_norms = None; SUBSTRATE_PATH = None


def _substrate_version(path):
    import lmdb
    env = lmdb.open(str(path), readonly=True, max_dbs=4, lock=False)
    try:
        md = env.open_db(b'meta', create=False)
        with env.begin() as t:
            v = t.get(b'global_epa_version', db=md)
        return v.decode() if v else None
    except Exception:
        return None
    finally:
        env.close()


def _lemma_candidates(w: str):
    """Deterministic regular-inflection lemmas (no NLP dep). Yields candidates to
    try for an EXACT norm hit -- higher confidence than difflib fuzzy fallback."""
    out = []
    def add(x):
        if x and x != w and len(x) >= 2 and x not in out:
            out.append(x)
    if w.endswith("ies") and len(w) > 4: add(w[:-3] + "y")
    if w.endswith("es") and len(w) > 3: add(w[:-2]); add(w[:-1])
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3: add(w[:-1])
    if w.endswith("ing") and len(w) > 5:
        add(w[:-3]); add(w[:-3] + "e"); add(w[:-4])          # make/making, run/running
    if w.endswith("ed") and len(w) > 4:
        add(w[:-2]); add(w[:-1]); add(w[:-3])                # walk/walked, like/liked, stop/stopped
    if w.endswith("est") and len(w) > 4: add(w[:-3]); add(w[:-2])
    if w.endswith("er") and len(w) > 4: add(w[:-2]); add(w[:-1])
    if w.endswith("ly") and len(w) > 4: add(w[:-2])
    return out


def _lookup(word: str, norms: dict):
    """Return (epa, method) via exact then rule-lemma; (None, None) if neither."""
    if word in norms:
        return norms[word], "exact"
    for lem in _lemma_candidates(word):
        if lem in norms:
            return norms[lem], "lemma"
    return None, None


def _classify(s: str) -> str:
    if " " in s:
        return "phrase"
    if any(c.isalpha() for c in s):
        return "word"
    return "other"


def _compose(epas: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """Default composition: elementwise mean over rated constituents.
    (Function is intentionally simple; Way 2 calibrates which function wins.)"""
    n = len(epas)
    return (sum(e[0] for e in epas) / n,
            sum(e[1] for e in epas) / n,
            sum(e[2] for e in epas) / n)


def match_epa(pkg_dir: str | Path, *, compose: bool = True,
              fallback_sample: int = 400, seed: int = 0,
              build_global: bool = True, norms: dict | None = None) -> dict:
    pkg = Path(pkg_dir)
    lm = pkg / "dictionary.lmdb"
    if not lm.exists():
        raise FileNotFoundError(lm)
    global_epa_version = None
    if norms is None and substrate_norms and SUBSTRATE_PATH and Path(SUBSTRATE_PATH).exists():
        norms = substrate_norms(SUBSTRATE_PATH)                 # live versioned substrate
        global_epa_version = _substrate_version(SUBSTRATE_PATH)
    if norms is None:
        if build_global:
            build_epa_db(verbose=False)                         # legacy Warriner-only fallback
        norms = load_warriner(WARRINER_CSV)
    word_list = list(norms.keys())

    env = lmdb.open(str(lm), map_size=2 * 1024 ** 3, max_dbs=8)
    fwd = env.open_db(b"forward")
    epa = env.open_db(b"epa")

    t = {"total": 0, "rated_word": 0, "unrated_word": 0,
         "composed": 0, "composed_all": 0, "composed_partial": 0, "rated_via_lemma": 0, "rated_phrase_direct": 0,
         "phrase_uncovered": 0, "other": 0}
    unrated_words: list[str] = []
    with env.begin(write=True) as txn:
        for k, idb in txn.cursor(db=fwd):
            surface = k.decode("utf-8", "replace")
            low = surface.lower().strip()
            kind = _classify(surface)
            t["total"] += 1
            if kind == "word":
                epa_v, meth = _lookup(low, norms)
                if epa_v is not None:
                    txn.put(idb, pack_epa(*epa_v), db=epa)
                    t["rated_word"] += 1
                    if meth == "lemma":
                        t["rated_via_lemma"] += 1
                else:
                    t["unrated_word"] += 1
                    if len(unrated_words) < 20000:
                        unrated_words.append(low)
            elif kind == "phrase":
                if low in norms:                       # exact MWE rating (e.g. NRC v2)
                    txn.put(idb, pack_epa(*norms[low]), db=epa)
                    t["rated_phrase_direct"] += 1
                elif compose:
                    words = [w for w in low.split() if w]
                    rated = [v for v in (_lookup(w, norms)[0] for w in words) if v is not None]
                    if rated:
                        txn.put(idb, pack_epa(*_compose(rated)), db=epa)
                        t["composed"] += 1
                        if len(rated) == len(words):
                            t["composed_all"] += 1
                        else:
                            t["composed_partial"] += 1
                    else:
                        t["phrase_uncovered"] += 1
                else:
                    t["phrase_uncovered"] += 1
            else:
                t["other"] += 1
    env.close()

    random.seed(seed)
    sample = random.sample(unrated_words, min(fallback_sample, len(unrated_words)))
    recovered = sum(1 for w in sample
                    if epa_from_fallback(w, norms, word_list)[1] is not None)

    content = t["rated_word"] + t["unrated_word"] + t["rated_phrase_direct"] + t["composed"] + t["phrase_uncovered"]
    phrase_total = t["rated_phrase_direct"] + t["composed"] + t["phrase_uncovered"]
    covered = t["rated_word"] + t["rated_phrase_direct"] + t["composed"]
    # Bind stats to the build (read the stamped fingerprint, never type it).
    _dfp_env = lmdb.open(str(lm), readonly=True, lock=False, max_dbs=8)
    _dfp_db = _dfp_env.open_db(b"meta", create=False)      # open handle BEFORE the txn
    with _dfp_env.begin() as _t:
        _dfp = _t.get(b"dictionary_fingerprint", db=_dfp_db)
    _dfp_env.close()
    stats = {
        "dictionary_fingerprint": _dfp.decode() if _dfp else None,
        "global_epa_words": len(norms), "global_epa_version": global_epa_version,
        "warriner_csv": WARRINER_CSV,
        **t,
        "content_entries": content,
        "epa_entries_written": covered,
        "pct_content_covered": round(100 * covered / content, 1) if content else 0,
        "pct_words_rated": round(100 * t["rated_word"] / (t["rated_word"] + t["unrated_word"]), 1)
                           if (t["rated_word"] + t["unrated_word"]) else 0,
        "rated_via_lemma": t["rated_via_lemma"],
        "pct_phrases_covered": round(100 * (t["rated_phrase_direct"] + t["composed"]) / phrase_total, 1) if phrase_total else 0,
        "rated_phrase_direct": t["rated_phrase_direct"],
        "phrase_residual_for_ai": t["phrase_uncovered"],
        "fallback_sample": len(sample), "fallback_recovered": recovered,
        "fallback_recovery_rate_on_unrated_words": round(recovered / len(sample), 3) if sample else 0,
    }
    (pkg / "epa_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


if __name__ == "__main__":
    print(json.dumps(match_epa(sys.argv[1]), indent=2))
