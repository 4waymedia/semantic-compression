"""
wordclass_builder.py -- derive the `wordclass` channel: what part of speech is this surface?

THE GAP THIS CLOSES (measured on elo-browser-v04, 2026-08-28):
    requires EBy TOPIC/CONTENT      dock QBu TOPIC/CONTENT
    uses     Ehf TOPIC/CONTENT      psu  AMSm TOPIC/CONTENT
Bit-identical facet records for verbs and nouns. METHOD holds 95 of 437,995 entries
(0.02%) and only because facets.py has a seed list. The dictionary has no word-class
AXIS at all -- not a missing value, a missing dimension. Composition therefore cannot
choose a predicate, inflect it, or tell `requires` from `psu` when building a clause.
Contract + rationale: handoffs/2026-08-28-dictionary-lane-wordclass-accepted.md

RECORD (3 bytes, id-keyed, sub-db b'wordclass') -- format v2:
    byte 0  [7:5] DOMINANT class  0 UNKNOWN 1 NOUN 2 VERB 3 MOD 4 FUNCTION 5 NAME
                                  6 NUMERAL 7 OTHER
            [4:3] confidence 0 UNKNOWN 1 HEURISTIC 2 CORPUS 3 ADJUDICATED
            [2]   AMBIVALENT (>1 bit set in the mask)
            [1:0] reserved
    byte 1  CLASS MASK -- one bit per class, THE SET OF CLASSES THIS SURFACE CAN TAKE
            bit0 NOUN  bit1 VERB  bit2 MOD  bit3 FUNCTION  bit4 NAME  bit5 NUMERAL
            bit6 OTHER  bit7 reserved
    byte 2  [7:6] countability 0 UNKNOWN 1 COUNT 2 MASS 3 BOTH
            [5:4] inherent_number 0 UNKNOWN 1 SG 2 PL 3 INVARIANT
            [3]   proper   [2] requires_determiner   [1:0] reserved

WHY A MASK AND NOT JUST A DOMINANT CLASS (Paul, 2026-08-28): English is massively
class-ambiguous -- `fight`/`run` are N|V, `dark` is MOD|N, `use` is N|V. A dominant
class plus an "ambivalent" flag tells a consumer that the answer is unreliable but not
WHAT THE ALTERNATIVES ARE, which is exactly what a generator needs: the realizer asking
"may I use this as a predicate?" needs `VERB in mask`, not `dominant == VERB`. The mask
answers both questions (dominant for ranking, membership for licensing) and costs one
byte. A dominant-only record would have forced every consumer to re-derive the set.

FOUR EVIDENCE LAYERS, ranked by confidence, first decisive one wins. Each was measured
before it was written -- the numbers are why the ladder is in this order:

  1. CLOSED CLASS (function words, pronouns, determiners) -- exact, tiny, certain.
  2. INFLECTIONAL PARADIGM (the lever that actually works): if `X`+ing and (`X`+ed or
     `X`+s) exist, X is a verb. Catches all four words the composition lane named
     (requires/uses/measured/reached) where suffix rules catch NONE of them.
     BUT dictionary-presence alone admits ASR junk -- 'systemed'/'systeming' are in the
     437,995 (they were spoken/typed somewhere), which made `system` look verbal. So
     presence is only CORPUS-confidence when the inflections carry corpus frequency;
     presence-without-frequency degrades to HEURISTIC, never silently to fact.
  3. DERIVATIONAL MORPHOLOGY (-tion/-ness -> N, -ly/-ous -> MOD, -ize/-ify -> V):
     decides 36,931 single-word surfaces (18%) and is stem-independent.
  4. CORPUS CONTEXT (determiner->N, modal/'to'->V, degree/copula->MOD): 11,948 surfaces
     carry >=1 cue in the sampled corpus; 35% reach a margin verdict. Weakest signal,
     used last, and the one that sets AMBIVALENT when evidence genuinely splits.

UNKNOWN IS 0 EVERYWHERE. An unstated class is ABSENT, never a default -- a consumer must
be able to tell "not measured" from "measured as OTHER" (the rule every channel in this
build follows, and the one the EPA/facets work was corrected for).

    python wordclass_builder.py --db db/builds/<name>/dictionary.lmdb --corpus <freq.txt>
    python wordclass_builder.py --db ... --dry-run       # census only, writes nothing
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import time
from collections import Counter
from pathlib import Path

import lmdb

WORDCLASS_DB = b'wordclass'
REC = struct.Struct('<BBB')

# Layer-4 evidence floors (2026-08-29). Layer 4 now DECIDES nominal class, so a
# single occurrence must not count: `the think` appears in 19 MB of books, and a
# no-floor rule would make `think` a noun. A cue must clear MIN_CUES absolutely
# AND MIN_SHARE of the surface's strongest cue to be recorded as a real reading.
MIN_CUES = 4
MIN_SHARE = 0.15
# DOMINANCE MARGIN. The top class must beat the runner-up by this factor to be called
# dominant. Below it the two readings are not distinguishable by the evidence we have,
# and the honest record is dominant=UNKNOWN with the mask populated -- consumers use the
# mask for licensing anyway. An unstated dominance is ABSENT, and absence widens; a
# default that names one class is the thing to avoid.
DOMINANCE_MARGIN = 1.5
# The attested degree form must clear this corpus frequency. Measured 2026-08-29: real
# evidence is `fastest` 993, `strongest` 989, `nearer` 41; the noise that produced false
# positives is `proter` 1, `protr` 3, `moder` 3. A 437,995-entry ASR-derived vocabulary
# contains enough garbage surfaces that mere PRESENCE is not evidence -- the same floor
# argument as MIN_CUES.
DEGREE_MIN_FREQ = 10

CLASS = {'UNKNOWN': 0, 'NOUN': 1, 'VERB': 2, 'MOD': 3, 'FUNCTION': 4,
         'NAME': 5, 'NUMERAL': 6, 'OTHER': 7}
CLASS_NAME = {v: k for k, v in CLASS.items()}
CONF = {'UNKNOWN': 0, 'HEURISTIC': 1, 'CORPUS': 2, 'ADJUDICATED': 3}
CONF_NAME = {v: k for k, v in CONF.items()}
AMBIVALENT = 0b100
# byte 1: one bit per class -- the SET of classes a surface can take.
MASK_BIT = {'NOUN': 1 << 0, 'VERB': 1 << 1, 'MOD': 1 << 2, 'FUNCTION': 1 << 3,
            'NAME': 1 << 4, 'NUMERAL': 1 << 5, 'OTHER': 1 << 6}


COUNTABILITY = {'UNKNOWN': 0, 'COUNT': 1, 'MASS': 2, 'BOTH': 3}
NUMBER = {'UNKNOWN': 0, 'SG': 1, 'PL': 2, 'INVARIANT': 3}
# TRI-STATE, 2 bits each (format v2, 2026-08-29). Both were single bits, which cannot
# express UNKNOWN -- so `requires_determiner` reported 437,995 confident falses while
# never being assigned at all, and `proper` reported 410,277 while being derived from
# capitalisation shape. The record spec's property #1 is "UNKNOWN is 0 everywhere"; a
# boolean bit cannot honour it. The two reserved bits in byte 2 pay for this exactly.
TRI = {'UNKNOWN': 0, 'NO': 1, 'YES': 2}
PROPER_SHIFT = 2;  PROPER_MASK = 0b00001100
REQDET_SHIFT = 0;  REQDET_MASK = 0b00000011


def unpack_wordclass(rec: bytes) -> dict:
    """The reader every consumer should use -- never hand-decode the bits."""
    b0, mask, b2 = REC.unpack(rec)
    return {
        'dominant': CLASS_NAME[(b0 >> 5) & 0b111],
        'confidence': CONF_NAME[(b0 >> 3) & 0b11],
        'ambivalent': bool(b0 & AMBIVALENT),
        'classes': {n for n, bit in MASK_BIT.items() if mask & bit},
        'countability': (b2 >> 6) & 0b11,
        'inherent_number': (b2 >> 4) & 0b11,
        # tri-state ints, NOT bools: 0 UNKNOWN / 1 NO / 2 YES. A consumer that wants a
        # boolean must decide what to do with UNKNOWN rather than have it decided here.
        'proper': (b2 & PROPER_MASK) >> PROPER_SHIFT,
        'requires_determiner': (b2 & REQDET_MASK) >> REQDET_SHIFT,
    }

# ---------------------------------------------------------------------------
# Layer 1 -- closed class. Exact membership, highest certainty available offline.
# ---------------------------------------------------------------------------
DETERMINERS = {'the', 'a', 'an', 'this', 'that', 'these', 'those', 'my', 'your', 'his',
               'her', 'its', 'our', 'their', 'some', 'any', 'no', 'every', 'each',
               'either', 'neither', 'both', 'all', 'another', 'much', 'many'}
PRONOUNS = {'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'us', 'them',
            'myself', 'yourself', 'himself', 'herself', 'itself', 'ourselves',
            'yourselves', 'themselves', 'who', 'whom', 'whose', 'which', 'what',
            'mine', 'yours', 'hers', 'ours', 'theirs'}
AUX_MODAL = {'is', 'are', 'was', 'were', 'be', 'been', 'being', 'am', 'do', 'does',
             'did', 'have', 'has', 'had', 'will', 'would', 'can', 'could', 'shall',
             'should', 'may', 'might', 'must', 'ought'}
PREP_CONJ = {'of', 'in', 'on', 'at', 'to', 'for', 'with', 'from', 'by', 'about',
             'into', 'over', 'under', 'after', 'before', 'between', 'through',
             'during', 'without', 'within', 'against', 'among', 'and', 'or', 'but',
             'if', 'because', 'while', 'although', 'though', 'unless', 'until',
             'since', 'so', 'yet', 'nor', 'than', 'as', 'not'}
CLOSED = {w: 'FUNCTION' for w in DETERMINERS | PRONOUNS | AUX_MODAL | PREP_CONJ}

# ---------------------------------------------------------------------------
# Layer 3 -- derivational suffix families (stem-independent).
# ---------------------------------------------------------------------------
N_SUF = ('tion', 'sion', 'ment', 'ness', 'ity', 'ship', 'ance', 'ence', 'hood',
         'dom', 'ist', 'ism', 'age', 'ery', 'or', 'er')
MOD_SUF = ('ful', 'ous', 'ive', 'less', 'ish', 'able', 'ible', 'al', 'ic', 'ary')
V_SUF = ('ize', 'ise', 'ify', 'ate', 'en')

# ---------------------------------------------------------------------------
# Layer 4 -- corpus context cues.
# ---------------------------------------------------------------------------
DEGREE = {'very', 'so', 'too', 'quite', 'really', 'extremely', 'rather', 'pretty',
          'more', 'most', 'less', 'least'}
TO_MODAL = {'to', 'will', 'would', 'can', 'could', 'should', 'must', 'may', 'might',
            'shall', 'do', 'does', 'did', "don't", 'not'}
COPULA = {'is', 'are', 'was', 'were', 'be', 'been', 'being', 'am', 'seems', 'looks'}

_WORD = re.compile(r"[a-z][a-z'-]*")


def inflections(stem: str) -> tuple[set, set, set]:
    """(s-forms, ing-forms, ed-forms) with the standard orthographic variants."""
    e = stem[:-1] if stem.endswith('e') else stem
    dbl = (stem + stem[-1]
           if len(stem) > 2 and stem[-1] not in 'aeiouy' and stem[-2] in 'aeiou'
           else stem)
    s = {stem + 's', stem + 'es', (stem[:-1] + 'ies') if stem.endswith('y') else stem + 's'}
    ing = {stem + 'ing', e + 'ing', dbl + 'ing'}
    ed = {stem + 'ed', e + 'ed', dbl + 'ed',
          (stem[:-1] + 'ied') if stem.endswith('y') else stem + 'ed'}
    return s, ing, ed


def stems_of(w: str) -> set:
    """Candidate STEMS for an inflected surface -- the inverse of inflections().

    Measured 2026-08-28: without this, `requires`/`measured`/`reached` (the exact
    words the composition lane cited) all came back UNKNOWN, because the paradigm
    test classifies a STEM and they are inflected forms. A dictionary is mostly
    inflected forms, so a stem-only rule leaves the majority of verb tokens dark.
    An inflected surface inherits its stem's verb evidence and carries the
    inflection as its own signal (`-ed`/`-ing` are never nouns)."""
    out = set()
    if w.endswith('ing') and len(w) > 5:
        base = w[:-3]
        out |= {base, base + 'e', base[:-1] if len(base) > 2 and base[-1] == base[-2] else base}
    if w.endswith('ed') and len(w) > 4:
        base = w[:-2]
        out |= {base, base + 'e', base[:-1] if len(base) > 2 and base[-1] == base[-2] else base}
        if base.endswith('i'):
            out.add(base[:-1] + 'y')
    if w.endswith('es') and len(w) > 4:
        out |= {w[:-2], w[:-1]}
        if w.endswith('ies'):
            out.add(w[:-3] + 'y')
    if w.endswith('s') and not w.endswith('ss') and len(w) > 3:
        out.add(w[:-1])
    return {s for s in out if len(s) > 2}


def _degree_stems(w: str) -> set:
    """Candidate adjective stems for a comparative/superlative surface.

    Covers the three orthographic changes English makes: bare (`fast`+er), doubled
    consonant (`big`+g+er), and dropped final -e (`nice`+r / `nice`+st)."""
    out: set = set()
    for suf in ('er', 'est'):
        if not w.endswith(suf) or len(w) <= len(suf) + 2:
            continue
        base = w[: -len(suf)]
        out.add(base)
        out.add(base + 'e')                                    # nicer  -> nice
        if len(base) > 2 and base[-1] == base[-2]:
            out.add(base[:-1])                                 # bigger -> big
        if base.endswith('i'):
            out.add(base[:-1] + 'y')                           # happier -> happy
    return {s for s in out if len(s) > 1}


def degree_form(w: str, vocab: set | None, freq: dict | None = None) -> str | None:
    """MOD when `w` is a comparative/superlative, else None.

    THE `-er` PROBLEM, and why a suffix rule cannot solve it (2026-08-29):

        baker  = bake + er   agentive     -> NOUN
        faster = fast + er   comparative  -> MOD

    Identical shape, opposite classes. The old rule listed `-er` under N_SUF and so
    returned NOUN for `faster` -- a WRONG answer, not a missing one, and the reason
    B5 was scheduled first.

    The discriminator is the COMPARATIVE PAIR, which is already in the vocabulary: a
    gradable adjective admits BOTH `-er` and `-est` on one stem (fast/faster/fastest),
    while an agentive noun admits no superlative (`*bakest`). So the test is whether
    the stem's OTHER degree form exists -- exactly the shape of the verb-paradigm test,
    and for the same reason.

    This is the temporality lesson again: `stones`/`thinks` could not be told apart by
    spelling, and neither can `baker`/`faster`. Both need a second co-occurring form.
    Without a vocabulary to consult there is no evidence, so this returns None rather
    than guessing."""
    if not vocab:
        return None
    freq = freq or {}
    for stem in _degree_stems(w):
        if stem not in vocab:
            # The stem must be a real word. Without this, `interest` decomposes to the
            # non-word `inter` and any coincidence downstream counts as evidence.
            continue
        # A VERB-STEM EXCLUSION WAS TRIED HERE AND REMOVED. The idea -- comparison is a
        # property of adjectives, so a verb stem taking -er is agentive -- is sound in
        # linguistics and wrong on this data, in both directions:
        #
        #   * `fast`, `near`, `strong` are legitimately BOTH verb and adjective, so the
        #     exclusion vetoed `faster`, `nearest`, `stronger` -- real comparatives with
        #     evidence forms at frequency 993, 41 and 989.
        #   * the veto also fired on pure ASR noise: `stronged`, `stronging`, `fastes`
        #     are in the vocabulary and made `strong` look like a verb.
        #
        # Frequency separates the real cases cleanly and the part-of-speech test does
        # not, so the floor below does the work instead.
        cmp_forms = {stem + 'er', stem + 'r'}
        sup_forms = {stem + 'est', stem + 'st'}
        if len(stem) > 2 and stem[-1] not in 'aeiou':
            cmp_forms.add(stem + stem[-1] + 'er')              # big -> bigger
            sup_forms.add(stem + stem[-1] + 'est')
        if stem.endswith('y'):
            cmp_forms.add(stem[:-1] + 'ier')
            sup_forms.add(stem[:-1] + 'iest')
        # `w` ITSELF IS NOT EVIDENCE. The first version tested `sup_forms & vocab` for a
        # word ending in -est -- but that set contains `w`, which is in the vocabulary by
        # definition, so the test could not fail. `interest` -> stem `inter` -> "interest
        # is in vocab" -> MOD. Same for forest, protest, earnest, modest.
        #
        # A degree form supplies ONE half of the pair; the evidence is the OTHER half.
        cmp_forms.discard(w)
        sup_forms.discard(w)
        need = cmp_forms if w.endswith('est') else sup_forms
        if any(freq.get(f, 0) >= DEGREE_MIN_FREQ for f in (need & vocab)):
            return 'MOD'
    return None


def morph_class(w: str, vocab: set | None = None, freq: dict | None = None) -> str | None:
    """Derivational class from shape. `vocab` enables the degree test (see degree_form).

    ORDER MATTERS: the degree test runs BEFORE the suffix families, because `-er` is
    listed under N_SUF and would otherwise claim every comparative as a noun."""
    if w.endswith('ly') and len(w) > 4:
        return 'MOD'
    deg = degree_form(w, vocab, freq)
    if deg:
        return deg
    for suf in N_SUF:
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return 'NOUN'
    for suf in MOD_SUF:
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return 'MOD'
    for suf in V_SUF:
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return 'VERB'
    return None


def load_corpus_freq(path: Path | None) -> Counter:
    """Surface -> count, from a word_frequencies-style file ('count\\tsurface')."""
    freq: Counter = Counter()
    if not path or not Path(path).exists():
        return freq
    for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) >= 2 and parts[0].strip().isdigit():
            freq[parts[1].strip().lower()] += int(parts[0])
    return freq


def _iter_context_sources(corpus_text: Path | None):
    """Yield every raw-text file layer 4 should read.

    A DIRECTORY is expanded (`Resources/books/*.txt`), a FILE is used as-is. This
    was single-file-only and defaulted to a 427k-token sample, which is why the
    layer was the weakest: 11,948 surfaces with any cue at all. The build's own
    lexical corpus is 19 MB of books -- the evidence already existed, it just was
    not being read."""
    if corpus_text is None:
        return
    p = Path(corpus_text)
    if p.is_dir():
        yield from sorted(p.rglob('*.txt'))
    elif p.exists():
        yield p


def load_context(corpus_text: Path | None) -> dict:
    """surface -> Counter of contextual class votes (layer 4).

    determiner->NOUN · to/modal->VERB · degree/copula->MOD.

    WHY THIS LAYER CARRIES THE DECIDING VOTE NOW (2026-08-29). The paradigm layer
    cannot separate `stone` from `think`: both inflect -s/-ing/-ed, so both look
    like verbs, and `stones`/`thinks` are the SAME SHAPE -- a 3rd-person singular
    is indistinguishable from a plural by morphology alone. No shape rule can fix
    that; it is not a heuristic that needs tuning, it is information the surface
    form does not contain. The distinction is DISTRIBUTIONAL -- `the stone` occurs,
    `the think` does not -- and determiner context is precisely that measurement.

    So layer 4 stops being a tie-breaker and becomes the nominal evidence of record.
    Because it now decides rather than decorates, cues are required to CLEAR A
    FLOOR (>= MIN_CUES occurrences) before they count -- one stray `the think` in
    19 MB must not mint a noun."""
    ev: dict = {}
    from collections import defaultdict
    ev = defaultdict(Counter)
    files = list(_iter_context_sources(corpus_text))
    if not files:
        return ev
    for f in files:
        try:
            txt_raw = f.read_text(encoding='utf-8', errors='replace')
            txt = txt_raw.lower()
        except Exception:
            continue
        # A SECOND tokenisation that PRESERVES CASE. `_WORD` lowercases, which is why
        # proper-noun evidence was unavailable and a shape rule got used instead.
        for m in _WORD_CASED.finditer(txt_raw):
            w_raw = m.group(0)
            if w_raw[:1].isupper() and not _sentence_initial(txt_raw, m.start()):
                ev[w_raw.lower()]['_proper'] += 1
            elif w_raw.islower():
                ev[w_raw]['_lower'] += 1
        toks = _WORD.findall(txt)
        for i in range(1, len(toks)):
            prev, w = toks[i - 1], toks[i]
            if prev in DETERMINERS:
                ev[w]['NOUN'] += 1
                # C1/C2 evidence, 2026-08-29. WHICH determiner appeared is the signal:
                #   'much'/'some'/'less' + X   -> X is MASS   ("much water")
                #   'a'/'an'/'every'/'each'    -> X is COUNT and takes a determiner
                # English marks the count/mass distinction in the determiner system, so
                # this is a measurement rather than a heuristic -- and the counts were
                # already being gathered and thrown away.
                if prev in _MASS_DET:
                    ev[w]['_mass'] += 1
                elif prev in _COUNT_DET:
                    ev[w]['_count'] += 1
            # C4 evidence: capitalised in NON-INITIAL position is the only reliable
            # written signal of a proper noun, and it is corpus evidence rather than
            # shape. `i > 1` skips sentence-initial capitals, which is exactly what the
            # old shape rule could not do.
            if prev in TO_MODAL:
                ev[w]['VERB'] += 1
            if prev in DEGREE or prev in COPULA:
                ev[w]['MOD'] += 1
    return ev





# Determiners that select for MASS vs COUNT. English encodes the distinction here, which
# is why it is measurable at all: *"much dog" and *"a water" are both ungrammatical.
_MASS_DET = {'much', 'less', 'some'}
_COUNT_DET = {'a', 'an', 'every', 'each', 'another', 'many'}
_WORD_CASED = re.compile(r"[A-Za-z][A-Za-z']+")


def _sentence_initial(text: str, pos: int) -> bool:
    """True when the token at `pos` opens a sentence.

    The whole point of the C4 fix: a capital at the start of a sentence carries NO
    information about proper-noun status, and counting it is what made `Abate`, `Abhor`
    and `Able` proper nouns. Only a capital in mid-sentence position is evidence."""
    j = pos - 1
    while j >= 0 and text[j] in ' \t\r\n"\'(':
        j -= 1
    return j < 0 or text[j] in '.!?:;'


def _corpus_provenance(corpus_freq, corpus_text) -> dict:
    """Corpus inputs as REPO-RELATIVE paths plus a content hash (NLG/NLU ask 2).

    Absolute paths were recorded, which are useless to anyone on another machine and
    actively misleading in this repo -- a previous agent session committed
    `/sessions/gifted-sweet-darwin/...` into a source file, dead everywhere. Relative
    to the repo root, a path means the same thing to every reader.

    The HASH is the load-bearing half. `corpus_text` is now a DIRECTORY (Resources/
    books, 19 MB), and the class assignments depend on what is in it -- layer 4's
    determiner counts are the deciding evidence for noun/verb. Two builds naming the
    same directory are not thereby the same build. The hash is over the sorted
    (relative name, size, mtime_ns) triples rather than file contents: cheap on 19 MB,
    and it changes whenever the corpus does. It detects change; it does not prove
    identity across machines, and is labelled `_manifest_hash` to say so."""
    import hashlib
    root = Path(__file__).resolve().parent.parent

    def rel(p):
        if p is None:
            return None
        p = Path(p)
        try:
            return str(p.resolve().relative_to(root)).replace('\\', '/')
        except ValueError:
            # Outside the repo root. Reported as given AND flagged, rather than
            # silently absolute: an absolute path in a stats file is only meaningful
            # on the machine that wrote it, and a reader must be able to tell
            # "relative, portable" from "absolute, local" without guessing.
            return str(p)

    def manifest_hash(p):
        if p is None:
            return None
        p = Path(p)
        if not p.exists():
            return None
        h = hashlib.sha256()
        files = sorted(p.rglob('*.txt')) if p.is_dir() else [p]
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            h.update(f.name.encode())
            h.update(str(st.st_size).encode())
            h.update(str(st.st_mtime_ns).encode())
        return h.hexdigest()[:16]

    def outside(p):
        if p is None:
            return None
        try:
            Path(p).resolve().relative_to(root); return False
        except ValueError:
            return True

    return {
        'corpus_freq_file': rel(corpus_freq),
        'corpus_text_file': rel(corpus_text),
        # True => the path above is ABSOLUTE and machine-local, because the corpus sits
        # outside the repo root on this machine (a separate mount). Not an error; the
        # hash below is what makes the build identifiable either way.
        'corpus_text_outside_repo': outside(corpus_text),
        'corpus_freq_outside_repo': outside(corpus_freq),
        'corpus_text_is_dir': bool(corpus_text and Path(corpus_text).is_dir()),
        'corpus_text_file_count': (len(list(Path(corpus_text).rglob('*.txt')))
                                   if corpus_text and Path(corpus_text).is_dir() else None),
        'corpus_freq_manifest_hash': manifest_hash(corpus_freq),
        'corpus_text_manifest_hash': manifest_hash(corpus_text),
    }


def _singulars_of(low: str) -> set:
    """Candidate singular forms for a plural surface."""
    out: set = set()
    if low.endswith('ies') and len(low) > 4:
        out.add(low[:-3] + 'y')
    if low.endswith('es') and len(low) > 3:
        out.add(low[:-2]); out.add(low[:-1])
    if low.endswith('s') and not low.endswith('ss') and len(low) > 2:
        out.add(low[:-1])
    return out


def _singular_is_ambivalent(surface: str, resolved: dict) -> bool:
    """True when this surface's singular resolved to more than one class."""
    for sg in _singulars_of(surface.lower()):
        src = resolved.get(sg)
        if src and bin(src[1]).count('1') > 1:
            return True
    return False


def build(lmdb_path: Path, corpus_freq: Path | None = None,
          dry_run: bool = False, corpus_text: Path | None = None) -> dict:
    t0 = time.perf_counter()
    freq = load_corpus_freq(corpus_freq)
    context = load_context(corpus_text)
    env = lmdb.open(str(lmdb_path), map_size=4 * 1024 ** 3, max_dbs=16)
    fwd = env.open_db(b'forward', create=False)
    with env.begin() as txn:
        pairs = [(k.decode('utf-8', 'replace'), bytes(v)) for k, v in txn.cursor(db=fwd)]
    vocab = {s for s, _ in pairs}
    stats: Counter = Counter()
    stats['total_entries'] = len(pairs)

    out: list[tuple[bytes, bytes]] = []
    resolved: dict = {}      # surface -> (dominant, mask, conf, b2) from pass 1
    idx_of: dict = {}        # surface -> position in `out`, for the pass-2 rewrite
    for surface, idb in pairs:
        low = surface.lower()
        classes: set[str] = set()          # THE SET -- every layer contributes
        conf = 'UNKNOWN'
        b2 = 0

        if ' ' in surface:
            classes, conf = {'OTHER'}, 'HEURISTIC'          # phrases: honest OTHER
        elif surface.strip().isdigit():
            classes, conf = {'NUMERAL'}, 'HEURISTIC'
        elif not low.replace("'", '').replace('-', '').isalpha():
            classes, conf = set(), 'UNKNOWN'                # symbols/structural
        elif low in CLOSED:
            classes, conf = {'FUNCTION'}, 'ADJUDICATED'     # layer 1
        else:
            # layer 2 -- inflectional paradigm (verb evidence)
            s_f, ing_f, ed_f = inflections(low)
            d_ing, d_ed, d_s = (bool(ing_f & vocab), bool(ed_f & vocab), bool(s_f & vocab))
            c_ing = max((freq[x] for x in ing_f), default=0) >= 2
            c_ed = max((freq[x] for x in ed_f), default=0) >= 2
            c_s = max((freq[x] for x in s_f), default=0) >= 2
            if c_ing and (c_ed or c_s):
                classes.add('VERB'); conf = 'CORPUS'
            elif d_ing and (d_ed or d_s):
                classes.add('VERB'); conf = 'HEURISTIC'
            # layer 2b -- INFLECTED FORM: inherit the stem's verb evidence.
            if 'VERB' not in classes:
                for stem in stems_of(low):
                    if stem not in vocab:
                        continue
                    ss, sing, sed = inflections(stem)
                    sc_ing = max((freq[x] for x in sing), default=0) >= 2
                    sc_oth = (max((freq[x] for x in sed), default=0) >= 2
                              or max((freq[x] for x in ss), default=0) >= 2)
                    if sc_ing and sc_oth:
                        classes.add('VERB')
                        conf = 'CORPUS' if conf in ('UNKNOWN', 'HEURISTIC') else conf
                        break
                    if bool(sing & vocab) and (bool(sed & vocab) or bool(ss & vocab)):
                        classes.add('VERB')
                        if conf == 'UNKNOWN':
                            conf = 'HEURISTIC'
                        break
            # layer 3 -- derivational morphology (independent evidence, so it ADDS)
            m = morph_class(low, vocab, freq)
            if m:
                classes.add(m)
                if conf == 'UNKNOWN':
                    conf = 'HEURISTIC'
            # layer 4 -- corpus context cues (adds classes; may confirm or widen)
            ctx = context.get(low)
            if ctx:
                # CLASS cues only. The same Counter also carries byte-2 FEATURE evidence
                # under underscore-prefixed keys (`_proper`, `_mass`, `_count`, `_lower`),
                # and without this filter they were added as CLASSES -- `classes.add
                # ('_proper')` then `CLASS['_proper']` -> KeyError, crashing the build.
                # Two kinds of evidence in one bag needs the bag to say which is which.
                cls_cues = {k: v for k, v in ctx.items() if not k.startswith('_')}
                if cls_cues:
                    top = max(cls_cues.items(), key=lambda kv: kv[1])
                    for name, n in cls_cues.items():
                        # floored: absolute count AND share of the dominant cue
                        if n >= MIN_CUES and n >= MIN_SHARE * top[1]:
                            classes.add(name)
                    if conf in ('UNKNOWN', 'HEURISTIC') and top[1] >= MIN_CUES:
                        conf = 'CORPUS'
            # NOMINAL EVIDENCE -- 2026-08-29. This was a plural-shape test
            # (`low+'s' in vocab` => NOUN). RETIRED, because it is not a weak rule,
            # it is an IMPOSSIBLE one: `stones` and `thinks` are the same shape, so
            # the test made every regular verb a noun (`think`, `destroy`, `analyze`
            # all came back ambivalent). Morphology does not carry this distinction.
            #
            # Nominal evidence is now DISTRIBUTIONAL only -- determiner context from
            # layer 4 above, which is a measurement of `the stone` vs `*the think`.
            # The one shape case kept is the irregular plural, which no verb paradigm
            # produces and which therefore IS unambiguous nominal evidence.
            if 'NOUN' not in classes and low.endswith('y') and (low[:-1] + 'ies') in vocab \
                    and freq.get(low[:-1] + 'ies', 0) >= 2:
                classes.add('NOUN')
                if conf == 'UNKNOWN':
                    conf = 'HEURISTIC'

        # ---- byte 2: LEXICAL FEATURES, derived from evidence (format v2) --------
        _ctx2 = context.get(low) or {}
        _n_proper = _ctx2.get('_proper', 0)      # capitalised mid-sentence
        _n_lower = _ctx2.get('_lower', 0)        # seen lowercase
        _n_mass = _ctx2.get('_mass', 0)          # much/less/some + X
        _n_count = _ctx2.get('_count', 0)        # a/an/every/each + X
        _n_det = _ctx2.get('NOUN', 0)            # any determiner + X

        # C4 PROPER. Was `surface[:1].isupper() and surface[1:].islower()` -- shape, not
        # evidence, and wrong in BOTH directions: it flagged book sentence-initials
        # (`Abate`, `Able`) and could never see `israel`/`washington`, since the
        # transcript corpus is uncapitalised. Now: capitalised in NON-INITIAL position
        # is the signal, and a surface seen lowercase far more often is NOT proper.
        if _n_proper >= MIN_CUES and _n_proper >= 2 * _n_lower:
            b2 |= TRI['YES'] << PROPER_SHIFT
            classes.add('NAME')
            if conf == 'UNKNOWN':
                conf = 'HEURISTIC'
        elif _n_lower >= MIN_CUES:
            b2 |= TRI['NO'] << PROPER_SHIFT      # measured NOT proper -- not merely absent
        # else: leave UNKNOWN. No evidence is a state, not a False.

        if 'NOUN' in classes:
            # C2 COUNTABILITY. Was a blanket COUNT for every noun, so `water` read
            # COUNT and a generator would emit *"a water". English marks the
            # distinction in the DETERMINER system -- *"much dog", *"a water" -- so
            # `much/less/some` vs `a/an/every/each` is a measurement, not a guess.
            if _n_mass >= MIN_CUES and _n_count >= MIN_CUES:
                b2 |= COUNTABILITY['BOTH'] << 6      # 'some water' AND 'a water[fall]'
            elif _n_mass >= MIN_CUES:
                b2 |= COUNTABILITY['MASS'] << 6
            elif _n_count >= MIN_CUES:
                b2 |= COUNTABILITY['COUNT'] << 6
            # NO FALLBACK ON A BARE DETERMINER. A first version added
            # `elif _n_det >= MIN_CUES: COUNT`, which is the same unevidenced assertion
            # this fix removed one line up: "the furniture" and "the dog" are equally
            # grammatical, so a plain determiner does not distinguish count from mass.
            # It made `furniture` and `advice` read COUNT on no diagnostic evidence at
            # all. They are UNKNOWN now, which is the true answer for this corpus.
            # else: UNKNOWN.

            if low.endswith('s') and low[:-1] in vocab:
                b2 |= NUMBER['PL'] << 4

            # C1 REQUIRES_DETERMINER -- the field that was DEFINED, READ, and NEVER SET,
            # reporting 437,995 confident falses. A singular COUNT noun needs one
            # ("*dog barked"); a mass noun or a plural does not. Both answers now come
            # from the countability evidence above rather than from silence.
            _cnt = (b2 >> 6) & 0b11
            _num = (b2 >> 4) & 0b11
            if _cnt == COUNTABILITY['COUNT'] and _num != NUMBER['PL']:
                b2 |= TRI['YES'] << REQDET_SHIFT
            elif _cnt in (COUNTABILITY['MASS'], COUNTABILITY['BOTH']) or _num == NUMBER['PL']:
                b2 |= TRI['NO'] << REQDET_SHIFT
            # else UNKNOWN: countability unknown => determiner requirement unknowable

        # DOMINANT -- argmax over per-class EVIDENCE. Priority is a tie-break only.
        #
        # WHAT THIS REPLACES, and why it was wrong (integration lane, 2026-08-29):
        #
        #     order = ('VERB', 'NOUN', 'MOD', ...)
        #     dominant = next((c for c in order if c in classes), 'UNKNOWN')
        #
        # There is no evidence comparison in that expression. The comment above it
        # claimed "highest-evidence class, ties resolved by fixed priority"; the code
        # implemented priority ALONE. With VERB first, `dominant` collapsed to
        # "VERB if VERB in classes else <next present>" -- carrying almost no
        # information beyond the mask it was derived from.
        #
        # Measured consequence: `dog`, `stone`, `water`, `run`, `house` were BYTE
        # IDENTICAL (54 03 40) = dominant VERB, confidence CORPUS, ambivalent. Four
        # common nouns recorded as verbs, at a confidence that reads as MEASURED. A
        # generator picking a paradigm from `dominant` would inflect them as verbs.
        #
        # The evidence was already here and was being thrown away: layer 4 counts
        # determiner/to-modal/degree cues per class and used them only to ADMIT a class
        # into the set. Now they also RANK.
        #
        # Where evidence cannot separate two readings, this records UNKNOWN rather than
        # picking. Consumers license from the mask; dominance is a claim, and a claim
        # without evidence should not be made.
        order = ('VERB', 'NOUN', 'MOD', 'FUNCTION', 'NAME', 'NUMERAL', 'OTHER')
        ev = Counter()
        _ctx = context.get(low)
        if _ctx:
            for _name, _n in _ctx.items():
                if _name in classes and not _name.startswith('_'):
                    ev[_name] = _n
        if len(classes) == 1:
            dominant = next(iter(classes))          # nothing to choose between
        elif not classes:
            dominant = 'UNKNOWN'
        elif conf == 'ADJUDICATED':
            dominant = next((c for c in order if c in classes), 'UNKNOWN')
        elif len(ev) >= 2:
            _ranked = sorted(ev.items(), key=lambda kv: (-kv[1], order.index(kv[0])))
            (_c1, _n1), (_c2, _n2) = _ranked[0], _ranked[1]
            dominant = _c1 if _n1 >= DOMINANCE_MARGIN * max(_n2, 1) else 'UNKNOWN'
            if dominant == 'UNKNOWN':
                stats['dominant_too_close'] += 1
        elif len(ev) == 1:
            dominant = next(iter(ev))               # only one class has any evidence
        else:
            # multi-class, no distributional evidence at all. The remaining signals
            # (paradigm, suffix) are categorical -- they say a reading is POSSIBLE, not
            # that it is more frequent. Ranking them would be inventing a measurement.
            dominant = 'UNKNOWN'
            stats['dominant_no_evidence'] += 1
        mask = 0
        for c in classes:
            mask |= MASK_BIT.get(c, 0)
        amb = bin(mask).count('1') > 1
        b0 = (CLASS[dominant] << 5) | (CONF[conf] << 3) | (AMBIVALENT if amb else 0)
        out.append((idb, REC.pack(b0, mask, b2)))
        resolved[surface] = (dominant, mask, conf, b2)
        idx_of[surface] = len(out) - 1
        stats[f'class_{dominant}'] += 1
        stats[f'conf_{conf}'] += 1
        if amb:
            stats['ambivalent'] += 1

    # ------------------------------------------------------------------ PASS 2
    # PLURAL INHERITANCE (B3, 2026-08-29). A plural takes the class its SINGULAR
    # resolved to -- it does not read its own suffix.
    #
    # The suffix version of this rule was tried and retired: `stones` and `thinks`
    # are the same shape, so `w[:-1] in vocab -> NOUN` made every regular verb a
    # noun. Morphology does not carry the singular/3sg distinction and no amount of
    # tuning gives it one.
    #
    # Inheritance sidesteps that entirely by reading the singular's ALREADY-RESOLVED
    # class, which pass 1 derived from distributional evidence:
    #
    #     stove is NOUN      -> stoves inherits NOUN
    #     think is VERB only -> thinks inherits nothing        (the trap avoided)
    #     run   is NOUN|VERB -> runs   inherits both           (honest)
    #
    # This needs a second pass: a singular must be resolved before anything can
    # inherit from it, and pass 1's iteration order is the LMDB key order, not any
    # order that would guarantee it.
    #
    # Only surfaces with NO class of their own are touched -- inheritance fills a
    # gap, it never overrides evidence the surface earned itself.
    for surface, idb in pairs:
        cur = resolved.get(surface)
        if not cur:
            continue
        # A surface that already earned a class is normally left alone -- inheritance
        # fills gaps, it does not override evidence. The ONE exception is a plural of an
        # AMBIVALENT singular: `fights` earns VERB from its own paradigm, but `fight` is
        # NOUN|VERB, and that nominal reading is independent evidence about this surface
        # too ("the fights were brutal"). Suppressing it would report a confident VERB
        # for a word that is plainly both. Layer 3 already works this way -- independent
        # evidence ADDS -- so this is the existing rule, not a new one.
        if cur[1] and not _singular_is_ambivalent(surface, resolved):
            continue
        low = surface.lower()
        sings = set()
        if low.endswith('ies') and len(low) > 4:
            sings.add(low[:-3] + 'y')
        if low.endswith('es') and len(low) > 3:
            sings.add(low[:-2]); sings.add(low[:-1])
        if low.endswith('s') and not low.endswith('ss') and len(low) > 2:
            sings.add(low[:-1])
        for sg in sings:
            src = resolved.get(sg)
            if not src or not src[1]:
                continue
            _dom, _mask, _conf, _b2 = src
            # Inherit ONLY the nominal reading. A plural is a noun-number form; the
            # singular's VERB reading says nothing about this surface being a verb
            # (`stoves` is not a verb because `stove` can be one). The exception is
            # an AMBIVALENT singular, where both readings are genuinely live and
            # suppressing one would overclaim.
            inherit = _mask & MASK_BIT['NOUN']
            if bin(_mask).count('1') > 1:
                inherit = _mask
            if not inherit:
                continue
            inherit |= cur[1]          # UNION -- never discard what the surface earned
            dom2 = 'NOUN' if inherit & MASK_BIT['NOUN'] else _dom
            amb2 = bin(inherit).count('1') > 1
            if amb2:
                dom2 = 'UNKNOWN'               # inherited ambivalence is not a claim
            # Keep the surface's own confidence when it had one: it earned that from
            # evidence, and inheritance adding a reading does not weaken it.
            conf2 = cur[2] if cur[1] else 'HEURISTIC'
            b0_2 = ((CLASS[dom2] << 5) | (CONF[conf2] << 3)
                    | (AMBIVALENT if amb2 else 0))
            # BYTE 2 for an inherited plural. Two bugs met here:
            #   * the carried mask was `_b2 & 0b00111100`, written for the v1 layout.
            #     Under v2 those bits are inherent_number + proper, so countability and
            #     requires_determiner were being silently dropped.
            #   * more basic: byte 2 is computed in PASS 1, but a plural only becomes a
            #     NOUN in PASS 2 -- so `dogs` never entered the noun branch at all and
            #     came out with countability and requires_determiner UNKNOWN.
            # A plural is a count noun that does NOT require a determiner ("dogs bark"),
            # and its number is PL by construction, so all three are known here.
            _b2n = _b2 & 0b00001100                       # keep the singular's `proper`
            _b2n |= NUMBER['PL'] << 4
            _sg_cnt = (_b2 >> 6) & 0b11
            if _sg_cnt in (COUNTABILITY['COUNT'], COUNTABILITY['BOTH']):
                _b2n |= COUNTABILITY['COUNT'] << 6
                _b2n |= TRI['NO'] << REQDET_SHIFT         # bare plurals are grammatical
            elif _sg_cnt == COUNTABILITY['MASS']:
                _b2n |= COUNTABILITY['MASS'] << 6
                _b2n |= TRI['NO'] << REQDET_SHIFT
            out[idx_of[surface]] = (idb, REC.pack(b0_2, inherit, _b2n))
            resolved[surface] = (dom2, inherit, conf2, _b2)
            stats['plural_inherited'] += 1
            stats[f'class_{dom2}'] += 1
            stats['class_UNKNOWN'] -= 1
            break

    if not dry_run:
        with env.begin(write=True) as txn:
            db = env.open_db(WORDCLASS_DB, txn=txn, create=True)
            for idb, rec in out:
                txn.put(idb, rec, db=db)
        meta = env.open_db(b'meta', create=False)
        with env.begin() as txn:
            fp = txn.get(b'dictionary_fingerprint', db=meta)
        payload = {
            'dictionary_fingerprint': fp.decode() if fp else None,
            # v2 (2026-08-29): byte 2's `proper` and `requires_determiner` widened from 1 bit to
            # 2, so both can say UNKNOWN. A v1 reader against a v2 record takes `proper`
            # from the wrong bits -- readers MUST check this.
            'wordclass_format_version': 2,
            # DERIVED from the struct, never typed (NLG/NLU ask 1, 2026-08-29). This
            # read `2` while REC is '<BBB' = 3 bytes: the record was widened when the
            # class MASK byte landed and the stat was not. A consumer sizing a buffer
            # from it would have been one byte short per record across 437,995 records.
            # A width that can disagree with its own struct is a restated identity.
            'record_width': REC.size,
            'key_scheme': 'base64_id',
            **_corpus_provenance(corpus_freq, corpus_text),
            # ADJUDICATION -- split into PROCESS and DATA (ask 3), per the vfacets
            # precedent (`llm_pass_ran` vs `llm_enriched`). The single `adjudicated:
            # False` was both wrong and ambiguous: 305 records carry CONF=ADJUDICATED,
            # so "false" contradicted the artifact, while the name could equally mean
            # "an adjudication pass ran" or "any value here is adjudicated".
            #
            # They are different questions with different answers, and only the split
            # makes the honest one sayable: NO adjudication pass has ever run on this
            # channel, AND some records nevertheless hold adjudicated-grade values --
            # because closed-class membership is a curated lexical FACT, not the output
            # of a review process.
            'adjudication_pass_ran': False,
            'adjudicated_entries': int(stats.get('conf_ADJUDICATED', 0)),
            'adjudicated_source': 'closed-class list (CLOSED) -- curated lexical fact, '
                                  'not a review pass',
            'elapsed_s': round(time.perf_counter() - t0, 2),
            **{k: v for k, v in stats.items()},
        }
        (Path(lmdb_path).parent / 'wordclass_stats.json').write_text(
            json.dumps(payload, indent=2), encoding='utf-8')
    env.close()
    stats['elapsed_s'] = round(time.perf_counter() - t0, 2)
    return dict(stats)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--db', required=True,
                    help='build package LMDB, e.g. db/builds/<name>/dictionary.lmdb (REQUIRED)')
    ap.add_argument('--corpus', default=None,
                    help="word_frequencies file ('count\\tsurface') for CORPUS confidence")
    ap.add_argument('--corpus-text', default=None,
                    help='raw text sample for context cues (layer 4)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    s = build(Path(a.db), Path(a.corpus) if a.corpus else None, a.dry_run,
              Path(a.corpus_text) if a.corpus_text else None)
    tot = s['total_entries']
    print(f"[wordclass] {tot:,} entries in {s['elapsed_s']}s"
          + ('  (DRY RUN)' if a.dry_run else ''))
    print('  class:')
    for k in sorted(k for k in s if k.startswith('class_')):
        print(f"    {k[6:]:9} {s[k]:>8,}  {100*s[k]/tot:5.1f}%")
    print('  confidence:')
    for k in sorted(k for k in s if k.startswith('conf_')):
        print(f"    {k[5:]:12} {s[k]:>8,}  {100*s[k]/tot:5.1f}%")


if __name__ == '__main__':
    main()
