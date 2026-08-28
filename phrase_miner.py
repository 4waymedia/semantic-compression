"""
phrase_miner.py -- Step 12 of v0.3

Refines the raw n-gram candidate pool from ngram_counter into a final
ranked list of phrase atoms suitable for promotion to the dictionary.

Pipeline:
  1. Load word frequencies + 2-5 grams + 6-9 grams
  2. Compute PMI for each n-gram candidate
       PMI = log2( P(phrase) / prod(P(word_i)) )
            = log2( freq * total_words^(n-1) / prod(word_counts) )
  3. Apply maximal-phrase filtering:
       A phrase P is "absorbed" when there exists a longer phrase P'
       that contains P as a contiguous substring AND
           freq(P') / freq(P) >= absorption_ratio  (default 0.7)
       Absorbed phrases are dropped (they only occur as part of the longer
       parent, so promoting the parent already covers them).
  4. Score each survivor:
       score = savings_total * (1 + max(0, PMI) / 10)
     where savings_total = freq * (n*1.8 - 3) using v0.2-binary cost model.
  5. Rank by score descending.

NO editorial filtering. Politically/topically charged phrases pass
through to System 2 for contextual resolution per the v0.3 spec.

Inputs:
    data/word_frequencies.txt        (unigram surface frequencies)
    data/ngram_frequencies_2to5.txt  (rank-ordered n-grams, n=2..5)
    data/ngram_frequencies_6to9.txt  (rank-ordered n-grams, n=6..9)

Outputs:
    data/phrase_candidates.txt       all survivors ranked by score
    data/phrase_top_summary.txt      top 100/250/500/1000/5000/10000 reports

Usage:
    python -m semantic_compression.phrase_miner
    python -m semantic_compression.phrase_miner --absorb 0.8       # tighter dedup
    python -m semantic_compression.phrase_miner --min-pmi 1.0      # drop low-PMI
    python -m semantic_compression.phrase_miner --report 10000     # extend summary
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, '.')

from semantic_compression.config import (
    COUNTS_FORMAT_VERSION, FUNCTION_WORDS, UTILITY, UTILITY_MASK, UTILITY_SHIFT,
)

# The hygiene gates need "is this a function word?" — which is the FACETS
# UTILITY question, not raw FUNCTION_WORDS membership. Using the set directly
# under-reports badly: config.FUNCTION_WORDS DELIBERATELY omits conjunctions,
# wh-words and negation (see its own docstring — those are already FUNCTION
# via a LOGIC_SEED cue, so facets has no need to list them). A raw
# `w in FUNCTION_WORDS` test has no access to that cue path, so `and`, `but`,
# `or`, `so`, `if`, `because`, `not` all read as CONTENT to it.
#
# Measured 2026-07-27 against phrase_candidates.lexical.txt (produced by the
# raw-set version of this gate): 18,536 of 50,347 entries — 36.8% — carry one
# of those 40 words on an edge. That is why the "lexical" set still led with
# `and you know`, `and then`, `if you want`, `but you know`. The gate logic was
# right; its membership test was blind to two thirds of the function vocabulary.
#
# facets.assign_facet() is the authoritative answer and already accounts for
# BOTH paths (explicit list + cue). config.py / normalize.py / facets.py are
# pure Python — no lmdb, no model — so this stays a cheap import.
# NOTE ON THE IMPORT PATH: facets.py uses BARE imports (`import config`,
# `from normalize import normalize_surface`), so it is only importable with
# semantic_compression/ ITSELF on sys.path — `from semantic_compression.facets
# import ...` fails, because facets' own `import config` cannot resolve from
# the repo root. (Measured: the first version of this block used exactly that
# and silently fell back — the WARNING in mine() is what surfaced it.) So put
# this module's own directory on the path first.
_PKG_DIR = str(Path(__file__).resolve().parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)
try:
    from facets import assign_facet as _assign_facet
except Exception:                                                # pragma: no cover
    _assign_facet = None

_FN_CACHE: dict = {}


def is_function_word(w: str) -> bool:
    """True if `w` is grammatical glue rather than content, per facets'
    UTILITY axis (FUNCTION / STRUCTURAL / FILLER all count as non-content).

    Falls back to raw FUNCTION_WORDS membership only when facets is
    unimportable — and that fallback is KNOWN to miss conjunctions, wh-words
    and negation (see the note above), so a fallback run does NOT produce a
    clean lexical mine. `mine()` warns when it happens rather than silently
    writing a worse file."""
    hit = _FN_CACHE.get(w)
    if hit is not None:
        return hit
    if _assign_facet is None:
        out = w in FUNCTION_WORDS
    else:
        try:
            flags = _assign_facet(w)[2]
            out = ((flags & UTILITY_MASK) >> UTILITY_SHIFT) != UTILITY['CONTENT']
        except Exception:
            out = w in FUNCTION_WORDS
    _FN_CACHE[w] = out
    return out


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR        = Path('semantic_compression/data')
WORD_FREQ_FILE  = DATA_DIR / 'word_frequencies.txt'
NGRAM_2TO5_FILE = DATA_DIR / 'ngram_frequencies_2to5.txt'
NGRAM_6TO9_FILE = DATA_DIR / 'ngram_frequencies_6to9.txt'

OUT_ALL         = DATA_DIR / 'phrase_candidates.txt'
OUT_SUMMARY     = DATA_DIR / 'phrase_top_summary.txt'


# ---------------------------------------------------------------------------
# Tunable thresholds
# ---------------------------------------------------------------------------

ABSORPTION_RATIO_DEFAULT = 0.7    # drop phrase if a longer parent has freq >= 70% of it
MIN_PMI_DEFAULT          = 0.0    # default: keep all; PMI used as score weight not filter

# v0.2-binary cost model (same as ngram_counter.py)
WORD_AVG_BYTES  = 1.8
PHRASE_COST_BIN = 3      # conservative: assume Tier 2 placement

# --- Phrase hygiene (OPT-IN; OFF by default to preserve the v0.3 candidate contract) --
# The default miner keeps ASR stutter-repeats ("china china") and function-heavy
# discourse n-grams ("how to deal with it") because PMI is only a score weight and
# score is byte-savings-dominant. These gates isolate genuinely LEXICAL phrases for
# domain builds. Enable via --lexical (preset) or the individual flags. See
# docs/compression/spec-phrase-assets.md §4.
LEXICAL_MAX_N     = 4       # lexical phrases are ~2-4 words; longer bands are discourse
LEXICAL_MIN_PMI   = 1.5     # above-chance collocation
LEXICAL_MIN_CONTENT = 1     # at least one content (non-function) word


def _has_adjacent_repeat(words: list[str]) -> bool:
    return any(words[i] == words[i + 1] for i in range(len(words) - 1))


def passes_hygiene(words: list[str], *, drop_repeats: bool, min_content: int,
                   no_edge_function: bool, max_n: int | None) -> bool:
    """True if the n-gram is a plausible LEXICAL phrase under the enabled gates.
    All gates default OFF at the call site; each is independent + deterministic."""
    n = len(words)
    if max_n and n > max_n:
        return False                                    # length sanity
    if drop_repeats and _has_adjacent_repeat(words):
        return False                                    # ASR stutter (china china)
    # `is_function_word`, NOT `in FUNCTION_WORDS` — see the module-level note:
    # the raw set omits conjunctions/wh/negation by design, which let 36.8% of
    # the previous "lexical" set through on an edge.
    if no_edge_function and (is_function_word(words[0]) or is_function_word(words[-1])):
        return False                                    # discourse edges (and ... you know)
    if min_content:
        content = sum(1 for w in words if not is_function_word(w))
        if content < min_content:
            return False                                # function-heavy fragment
    return True


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_word_frequencies(path: Path) -> dict[str, int]:
    """
    Parse word_frequencies.txt.

    Format:
        # comment ...
        <count><TAB><unicode-escape-encoded-token>
    """
    counts: dict[str, int] = {}
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            count_str, escaped = line.rstrip('\n').split('\t', 1)
            token = escaped.encode('ascii').decode('unicode_escape')
            counts[token] = int(count_str)
    return counts


def load_ngrams(path: Path) -> list[dict]:
    """
    Parse ngram_frequencies_NtoM.txt.

    Format:
        # comment ...
        <rank><TAB><n><TAB><freq><TAB><savings_each><TAB><savings_total><TAB><phrase>
    """
    records: list[dict] = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 6:
                continue
            n = int(parts[1])
            freq = int(parts[2])
            phrase = parts[5]
            records.append({'phrase': phrase, 'n': n, 'freq': freq})
    return records


# ---------------------------------------------------------------------------
# PMI
# ---------------------------------------------------------------------------

def compute_pmi(
    phrase: str,
    phrase_freq: int,
    word_freq: dict[str, int],
    total_words: int,
) -> float:
    """
    Pointwise Mutual Information for an n-gram against its constituent words.

        PMI(w1...wn) = log2( P(w1...wn) / prod(P(wi)) )
                     = log2( freq * total^(n-1) / prod(count(wi)) )

    Negative PMI means the phrase co-occurs LESS than chance from individual
    word probabilities (e.g. word repetition collisions like "the the").
    PMI ~ 0 means about chance. High positive PMI is a true semantic atom.
    """
    words = phrase.split(' ')
    n = len(words)
    if n < 2 or phrase_freq <= 0 or total_words <= 0:
        return 0.0

    log_freq         = math.log2(phrase_freq)
    log_total        = math.log2(total_words)
    log_n_minus_1    = (n - 1) * log_total

    log_prod_counts = 0.0
    for w in words:
        c = word_freq.get(w, 1)   # absent word -> 1 to avoid log(0); shouldn't happen
        log_prod_counts += math.log2(c)

    return log_freq + log_n_minus_1 - log_prod_counts


# ---------------------------------------------------------------------------
# Maximal-phrase filtering
# ---------------------------------------------------------------------------

def find_absorbed_phrases(
    candidates: list[dict],
    absorption_ratio: float,
) -> set[str]:
    """
    Identify candidates that are absorbed by a longer parent phrase.

    For each candidate P', enumerate its proper substrings P (length 2..n-1)
    and record P's max parent frequency.

    A phrase P is absorbed iff there exists a longer P' containing P with
        freq(P') / freq(P) >= absorption_ratio.

    Returns the set of absorbed phrase strings.
    """
    freq = {c['phrase']: c['freq'] for c in candidates}
    max_parent: dict[str, int] = {}

    for c in candidates:
        n = c['n']
        if n < 3:
            continue   # 2-grams have no proper substrings worth re-checking
        words = c['phrase'].split(' ')
        parent_freq = c['freq']

        for sub_len in range(2, n):
            for i in range(n - sub_len + 1):
                sub = ' '.join(words[i:i + sub_len])
                if sub in freq:
                    prior = max_parent.get(sub, 0)
                    if parent_freq > prior:
                        max_parent[sub] = parent_freq

    absorbed: set[str] = set()
    for phrase, mpf in max_parent.items():
        if mpf <= 0:
            continue
        if mpf / freq[phrase] >= absorption_ratio:
            absorbed.add(phrase)
    return absorbed


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_candidates(candidates: list[dict]) -> list[dict]:
    """
    Compute byte-savings + score. Returns the list sorted by score desc.
    """
    for c in candidates:
        word_cost = c['n'] * WORD_AVG_BYTES
        c['savings_each']  = word_cost - PHRASE_COST_BIN
        c['savings_total'] = c['freq'] * c['savings_each']
        # Boost score by PMI (capped to be a modifier, not a dominator)
        pmi_boost = max(0.0, c['pmi']) / 10.0
        c['score'] = c['savings_total'] * (1.0 + pmi_boost)

    candidates.sort(key=lambda x: x['score'], reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_all(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"# phrase_candidates.txt  format_version={COUNTS_FORMAT_VERSION}\n")
        f.write("# rank\tn\tfreq\tpmi\tsavings_each\tsavings_total\tscore\tphrase\n")
        for rank, r in enumerate(records, 1):
            f.write(
                f"{rank}\t{r['n']}\t{r['freq']}\t{r['pmi']:.2f}\t"
                f"{r['savings_each']:.2f}\t{int(r['savings_total'])}\t"
                f"{r['score']:.1f}\t{r['phrase']}\n"
            )


def write_summary(records: list[dict], path: Path, top_cuts: tuple[int, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"# phrase_top_summary.txt   format_version={COUNTS_FORMAT_VERSION}\n")
        f.write(f"# survivors: {len(records):,}\n\n")

        for top_n in top_cuts:
            slice_ = records[:top_n]
            if not slice_:
                continue
            total_savings = int(sum(r['savings_total'] for r in slice_))
            f.write(f"=== TOP {top_n} ===\n")
            f.write(f"Cumulative estimated savings: {total_savings:,} bytes "
                    f"({total_savings/1024/1024:.2f} MB)\n\n")
            f.write(f"{'RANK':>5}  {'N':>2}  {'FREQ':>10}  {'PMI':>6}  "
                    f"{'SAVE_TOT':>10}  {'SCORE':>11}  PHRASE\n")
            for rank, r in enumerate(slice_, 1):
                f.write(
                    f"{rank:>5}  {r['n']:>2}  {r['freq']:>10,}  "
                    f"{r['pmi']:>6.2f}  {int(r['savings_total']):>10,}  "
                    f"{r['score']:>11,.0f}  {r['phrase']}\n"
                )
            f.write("\n")


def print_console_report(records: list[dict], stats: dict) -> None:
    print()
    print("=== PHRASE MINING COMPLETE ===")
    print(f"  Initial candidates:     {stats['initial']:>10,}")
    print(f"  After hygiene gates:    {stats['after_hygiene']:>10,}")
    print(f"  After PMI floor:        {stats['after_pmi']:>10,}")
    print(f"  After maximal filter:   {stats['after_absorb']:>10,}")
    print(f"  Final survivors:        {len(records):>10,}")
    print(f"  Words referenced:       {stats['word_count']:>10,}")
    print(f"  Total corpus words:     {stats['total_words']:>10,}")
    print()

    cuts = (100, 250, 500, 1000, 5000)
    print("Cumulative bytes saved by promoting top N (conservative Tier 2 cost):")
    for n in cuts:
        if len(records) < n:
            continue
        s = int(sum(r['savings_total'] for r in records[:n]))
        print(f"  top {n:>5,}:  {s:>15,} bytes  ({s/1024/1024:>6.2f} MB)")

    print()
    print("Top 25 phrases by score (PMI-weighted savings):")
    print(f"  {'RANK':>4}  {'N':>2}  {'FREQ':>10}  {'PMI':>6}  {'SAVED':>10}  PHRASE")
    print(f"  {'-'*65}")
    for rank, r in enumerate(records[:25], 1):
        print(
            f"  {rank:>4}  {r['n']:>2}  {r['freq']:>10,}  "
            f"{r['pmi']:>6.2f}  {int(r['savings_total']):>10,}  {r['phrase']!r}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def mine(
    absorption_ratio: float = ABSORPTION_RATIO_DEFAULT,
    min_pmi: float = MIN_PMI_DEFAULT,
    extra_top_cut: int | None = None,
    *,
    drop_repeats: bool = False,
    min_content: int = 0,
    no_edge_function: bool = False,
    max_n: int | None = None,
    out_all: Path = OUT_ALL,
    out_summary: Path = OUT_SUMMARY,
) -> list[dict]:
    print("Loading frequency tables...")
    word_freq = load_word_frequencies(WORD_FREQ_FILE)
    total_words = sum(word_freq.values())
    print(f"  word_frequencies:        {len(word_freq):>10,} entries  "
          f"(total tokens: {total_words:,})")

    candidates: list[dict] = []
    candidates += load_ngrams(NGRAM_2TO5_FILE)
    print(f"  ngrams_2to5 loaded:      {len(candidates):>10,}")
    short_count = len(candidates)
    candidates += load_ngrams(NGRAM_6TO9_FILE)
    print(f"  ngrams_6to9 loaded:      {len(candidates) - short_count:>10,}")
    print(f"  combined candidates:     {len(candidates):>10,}")

    print()
    print("Computing PMI for each candidate...")
    for c in candidates:
        c['pmi'] = compute_pmi(c['phrase'], c['freq'], word_freq, total_words)

    initial = len(candidates)

    if any((drop_repeats, min_content, no_edge_function, max_n)):
        if _assign_facet is None and (no_edge_function or min_content):
            print("  WARNING: facets.assign_facet unavailable — falling back to raw "
                  "FUNCTION_WORDS membership, which MISSES conjunctions/wh/negation "
                  "(and, but, or, so, if, because, not). This run will NOT be a clean "
                  "lexical mine; ~37% of survivors will still be discourse fragments.")
        before = len(candidates)
        candidates = [c for c in candidates
                      if passes_hygiene(c['phrase'].split(' '), drop_repeats=drop_repeats,
                                        min_content=min_content,
                                        no_edge_function=no_edge_function, max_n=max_n)]
        print(f"  hygiene gates:           dropped {before - len(candidates):>10,}, "
              f"kept {len(candidates):,}")
    after_hygiene = len(candidates)

    if min_pmi > 0.0:
        before = len(candidates)
        candidates = [c for c in candidates if c['pmi'] >= min_pmi]
        print(f"  PMI >= {min_pmi} floor:   dropped {before - len(candidates):,}, "
              f"kept {len(candidates):,}")
    after_pmi = len(candidates)

    print()
    print(f"Running maximal-phrase filter (absorption_ratio = {absorption_ratio})...")
    absorbed = find_absorbed_phrases(candidates, absorption_ratio)
    candidates = [c for c in candidates if c['phrase'] not in absorbed]
    print(f"  absorbed (dropped):      {len(absorbed):>10,}")
    print(f"  survivors:               {len(candidates):>10,}")
    after_absorb = len(candidates)

    print()
    print("Scoring + ranking...")
    candidates = score_candidates(candidates)

    write_all(candidates, out_all)
    cuts = [100, 250, 500, 1000, 5000, 10000]
    if extra_top_cut and extra_top_cut not in cuts:
        cuts.append(extra_top_cut)
        cuts.sort()
    write_summary(candidates, out_summary, tuple(cuts))

    stats = {
        'initial':       initial,
        'after_hygiene': after_hygiene,
        'after_pmi':     after_pmi,
        'after_absorb':  after_absorb,
        'word_count':    len(word_freq),
        'total_words':   total_words,
    }
    print_console_report(candidates, stats)

    print()
    print(f"Wrote: {out_all}")
    print(f"Wrote: {out_summary}")
    return candidates


def _selftest() -> int:
    """Pin the 2026-07-27 regression: the edge/content gates must treat
    conjunctions, wh-words and negation as function words. They are absent
    from config.FUNCTION_WORDS BY DESIGN (already FUNCTION via a LOGIC_SEED
    cue), so a raw membership test silently misses them — which let 36.8% of
    the previous lexical set through with a discourse word on an edge.
    Run: python -m semantic_compression.phrase_miner --selftest"""
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  ok  " if cond else "  FAIL") + " " + msg)
        ok = ok and cond

    check(_assign_facet is not None,
          "facets.assign_facet imported (without it the gates silently degrade)")
    # the exact words that leaked
    for w in ("and", "but", "or", "so", "if", "because", "not", "then",
              "what", "when", "which", "while", "as", "than"):
        check(is_function_word(w), f"is_function_word({w!r}) is True")
    # genuine content must NOT be swept up
    for w in ("car", "wash", "drive", "office", "system", "tape"):
        check(not is_function_word(w), f"is_function_word({w!r}) is False")
    # end-to-end on the phrases that used to top the 'lexical' list
    gates = dict(drop_repeats=True, min_content=LEXICAL_MIN_CONTENT,
                 no_edge_function=True, max_n=LEXICAL_MAX_N)
    for bad in ("and you know", "and then", "if you want", "but you know",
                "so i think"):
        check(not passes_hygiene(bad.split(), **gates),
              f"rejects discourse fragment {bad!r}")
    for good in ("post office", "solar system", "hard drive", "red tape"):
        check(passes_hygiene(good.split(), **gates),
              f"keeps lexical compound {good!r}")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> None:
    p = argparse.ArgumentParser(description="Refine n-gram candidates into phrase atoms")
    p.add_argument('--absorb', type=float, default=ABSORPTION_RATIO_DEFAULT,
                   help=f"absorption ratio for maximal-phrase filter "
                        f"(default {ABSORPTION_RATIO_DEFAULT})")
    p.add_argument('--min-pmi', type=float, default=MIN_PMI_DEFAULT,
                   help=f"drop candidates with PMI below this value "
                        f"(default {MIN_PMI_DEFAULT}; 0 = no filter)")
    p.add_argument('--report', type=int, default=None,
                   help="extend the top-N report to include this size")
    # --- lexical hygiene (OPT-IN; preserves the frozen v0.3 candidate set by default)
    #     see docs/compression/spec-phrase-assets.md §4 ---
    p.add_argument('--lexical', action='store_true',
                   help="preset: drop-repeats + no-edge-function + min-content 1 + "
                        "max-n 4 + min-pmi 1.5 (isolate genuinely lexical phrases)")
    p.add_argument('--drop-repeats', action='store_true',
                   help="drop n-grams with adjacent identical tokens (ASR stutters)")
    p.add_argument('--min-content', type=int, default=0,
                   help="require >= N content (non-function) words")
    p.add_argument('--no-edge-function', action='store_true',
                   help="reject phrases starting/ending with a function word")
    p.add_argument('--max-n', type=int, default=None,
                   help="drop phrases longer than N words")
    p.add_argument('--out', default=None,
                   help="output path for candidates (default: phrase_candidates.txt, or "
                        "phrase_candidates.lexical.txt when hygiene gates are on)")
    p.add_argument('--selftest', action='store_true',
                   help="check the hygiene gates against known cases; mines nothing")
    args = p.parse_args()

    if args.selftest:
        raise SystemExit(_selftest())

    # --lexical fills sensible defaults; explicit flags override upward.
    drop_repeats     = args.drop_repeats or args.lexical
    no_edge_function = args.no_edge_function or args.lexical
    min_content      = args.min_content or (LEXICAL_MIN_CONTENT if args.lexical else 0)
    max_n            = args.max_n or (LEXICAL_MAX_N if args.lexical else None)
    min_pmi          = args.min_pmi
    if args.lexical and min_pmi == 0.0:
        min_pmi = LEXICAL_MIN_PMI

    # SAFETY: any hygiene run writes to a SEPARATE file so the frozen v0.3
    # phrase_candidates.txt is never clobbered. Override with --out.
    hygiene_on = any((drop_repeats, min_content, no_edge_function, max_n))
    if args.out:
        out_all = Path(args.out)
        out_summary = out_all.with_name(out_all.stem + '_summary.txt')
    elif hygiene_on:
        out_all = OUT_ALL.with_name('phrase_candidates.lexical.txt')
        out_summary = OUT_SUMMARY.with_name('phrase_top_summary.lexical.txt')
    else:
        out_all, out_summary = OUT_ALL, OUT_SUMMARY

    mine(
        absorption_ratio=args.absorb,
        min_pmi=min_pmi,
        extra_top_cut=args.report,
        drop_repeats=drop_repeats,
        min_content=min_content,
        no_edge_function=no_edge_function,
        max_n=max_n,
        out_all=out_all,
        out_summary=out_summary,
    )


if __name__ == '__main__':
    main()
