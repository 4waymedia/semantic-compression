"""
verbalizer.py
EloAI — System 2, semantic-meanings package

Semantic field expander. Given any compressed semantic position expressed as
an EPA coordinate, expands outward into the space of meaningful interpretations
the position implies.

Output is a SemanticField — a structured map of the semantic territory around
the input coordinate, with every node grounded in a dictionary phrase atom.

Design principles (locked):
  1. NO LLM AT INFERENCE TIME — pure arithmetic over the semantic machine
  2. SEMANTIC FIELD IS THE OUTPUT — not a string
  3. VECTOR ADDITION IS THE CORE OPERATION
  4. FACETS DRIVE EXPANSION — not just EPA proximity
  5. THE VERBALIZER DOES NOT DECIDE PROBABILITY — it enumerates the field
  6. BOTH POLES ALWAYS PRESENT for each dominant axis
  7. OUTPUT IS RE-ENCODABLE as .elo stream

Spec: R-D-concepts/verbalizer/VERBALIZER.md
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Optional

import faiss
import lmdb
import numpy as np

try:                                  # shared read-only env cache (avoids
    from lmdb_cache import get_env    # "already open in this process" collisions
except ImportError:                   # when the dict is also read by the
    from semantic_compression.lmdb_cache import get_env  # surfacer / fingerprint

# ---------------------------------------------------------------------------
# Paths (defaults relative to semantic_compression/)
# ---------------------------------------------------------------------------

_HERE           = Path(__file__).parent
DEFAULT_DICT_DB = _HERE / 'db' / 'dictionary.lmdb'
DEFAULT_EPA_DB  = _HERE / '..' / 'Memory' / 'data' / 'epa_substrate.lmdb'
DEFAULT_FAISS   = _HERE / 'db' / 'dictionary.faiss.index'
DEFAULT_SURFACES = _HERE / 'db' / 'dictionary.faiss.surfaces.json'
# Optional DENOTATION channel (768-d meaning index from a dictionary family, e.g.
# db/builds/<name>/). Distinct from DEFAULT_FAISS above, which is EPA/AFFECT.
# Point at a build dir to give the verbalizer meaning-based word selection.
DEFAULT_DENOTATIVE_DIR: Path | None = None


class ArtifactDriftError(RuntimeError):
    """The FAISS index does not match the EPA substrate it was built from.

    The index is a SECOND ARTIFACT derived from the first. Nothing in the
    filesystem keeps them aligned, so the binding is checked at load. Loading a
    drifted index is worse than failing: `faiss_query` returns row indices, those
    rows are mapped through a stale surfaces list, and the verbalizer silently
    expands into the wrong semantic field. No exception, no log, wrong answers.
    """


# Binding enforcement. `ELO_FAISS_BINDING` = strict | degrade | off
#
#   strict   unverified index -> ArtifactDriftError at construction.
#   degrade  unverified index -> the substrate LOADS, EPA and dictionary lookups work,
#            and `faiss_query()` returns NOTHING. You get no answer, never a wrong one.
#            Warned once, loudly. THIS IS THE DEFAULT while epa/faiss are being refactored.
#   off      no check at all. Do not use outside a debugger.
#
# The rule is "refuse rather than degrade", and `degrade` honours it where it matters:
# a stale index maps faiss rows through a moved surfaces list, so every neighbour is the
# wrong word. Returning [] is a refusal. Returning the wrong word is the bug.
#
# TODO(refactor): flip the default to "strict" once faiss_builder.py v2 has run and
# tools/verify_substrate_chain.py reports the chain intact.
BINDING_MODE = os.environ.get('ELO_FAISS_BINDING', 'degrade').lower()


def _verify_index_binding(faiss_idx: Path, epa_db: Path, *, strict: bool | None = None) -> dict:
    """Compare the fingerprint recorded at build time against the live substrate.

    Returns the sidecar dict on success. On failure: raises in `strict`, returns {} in
    `degrade` (caller must then disable faiss_query), skips entirely in `off`.
    """
    import warnings

    mode = BINDING_MODE if strict is None else ('strict' if strict else 'degrade')
    if mode == 'off':
        return {'_unchecked': True}

    meta_path = Path(faiss_idx).with_suffix('.json')
    if not meta_path.exists():
        msg = f'no sidecar {meta_path.name}; index binding is unrecorded'
    else:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        if meta.get('faiss_format_version', 1) < 2:
            msg = (f'{meta_path.name} predates the content fingerprint (v1 hashed '
                   f'data.mdb bytes, unverifiable). Rebuild: python faiss_builder.py')
        else:
            from artifact_identity import _epa_fingerprint
            live = _epa_fingerprint(Path(epa_db))
            recorded = meta.get('epa_fingerprint')
            if live is None:
                msg = f'cannot fingerprint live substrate {epa_db}'
            elif live == recorded:
                return meta                      # bound, verified
            else:
                msg = (f'FAISS index was built against EPA substrate {recorded}, '
                       f'live substrate is {live}. Rebuild: python faiss_builder.py')

    if mode == 'strict':
        raise ArtifactDriftError(msg)
    warnings.warn(
        f'[verbalizer] UNVERIFIED index binding -- faiss_query() is DISABLED.\n'
        f'  {msg}\n'
        f'  EPA + dictionary lookups still work. Set ELO_FAISS_BINDING=strict to hard-fail.',
        RuntimeWarning, stacklevel=3)
    return {}

# ---------------------------------------------------------------------------
# Stage → process semantics map (Surov stages 1–6)
# ---------------------------------------------------------------------------

STAGE_SEMANTICS = {
    1: 'initiation / potential / opening',
    2: 'engagement / effort / activation',
    3: 'conflict / tension / opposition',
    4: 'resolution / release / pivot',
    5: 'consolidation / reflection / integration',
    6: 'completion / transition / closure',
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WARRINER_MID    = 0.0   # EloAI ±4 scale (0 = neutral, not 4.5)
DOMINANT_THRESH = 0.3   # |intensity| > this → axis is dominant
EXPANSION_SIGMA = 1.5   # shift in EPA units for pole expansion
STAGE_HARD_TOL  = 1.5   # abs(stage_diff) > this → exclude candidate
K_ANCHOR        = 30    # FAISS top-K for anchor retrieval (EPA/affect)
K_DENOT         = 8     # denotation neighbours per seed (768-d meaning channel)
K_POLE          = 15    # FAISS top-K per pole expansion

# Axis labels
AXES = ('E', 'P', 'A')

# Polarity codes (from vfacet_builder)
POL  = {'NEUTRAL': 0, 'POSITIVE': 1, 'NEGATIVE': 2, 'BIPOLAR': 3}
TMP  = {'UNKNOWN': 0, 'STATE': 1, 'PROCESS': 2, 'EVENT': 3, 'OUTCOME': 4, 'CONDITION': 5}
DOM  = {'GENERAL': 0, 'CODING': 1, 'MEDICAL': 2, 'LEGAL': 3, 'FINANCE': 4,
        'SCIENCE': 5, 'PSYCHOLOGY': 6, 'MILITARY': 7, 'CULINARY': 8, 'EDUCATION': 9}
AGN  = {'UNKNOWN': 0, 'SELF': 1, 'OTHER': 2, 'SYSTEM': 3}
DIR  = {'UNKNOWN': 0, 'TOWARD': 1, 'AWAY': 2, 'STABLE': 3, 'REVERSAL': 4, 'NEUTRAL': 5}

POL_NAME = {v: k for k, v in POL.items()}
TMP_NAME = {v: k for k, v in TMP.items()}
DOM_NAME = {v: k for k, v in DOM.items()}
AGN_NAME = {v: k for k, v in AGN.items()}
DIR_NAME = {v: k for k, v in DIR.items()}

_EPA_STRUCT    = struct.Struct('<fff')
_VFACET_STRUCT = struct.Struct('<BB')


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class VerbalizerInput:
    E:            float
    P:            float
    A:            float
    stage:        int           = 1
    seed_class:   str           = 'UNKNOWN'
    anchor_ids:   list          = field(default_factory=list)
    spread:       float         = 1.0
    axis_weights: tuple         = (1.0, 1.0, 1.0)
    source_type:  str           = 'vector'
    raw_ids:      list          = field(default_factory=list)

    @classmethod
    def from_vector(cls, E: float, P: float, A: float, **kw) -> 'VerbalizerInput':
        return cls(E=E, P=P, A=A, source_type='vector', **kw)


@dataclass
class SemanticNode:
    phrase_atom:   str
    token_id:      bytes
    score:         float
    pole_signature: dict        # {E: '+'/'-'/'0', P: '+'/'-'/'0', A: '+'/'-'/'0'}
    facet_profile: dict         # {agency, direction, temporal, domain, polarity}
    stage_fit:     float
    epa_distance:  float
    is_boundary:   bool         = False
    is_cross_axis: bool         = False


@dataclass
class AxisProfile:
    axis:         str
    intensity:    float         # distance from 0, normalized to [0, 1] over ±4
    dominant_pole: str          # '+' or '-' or '0'
    spread:       float


@dataclass
class SemanticField:
    # Input summary
    centroid:      tuple
    stage:         int
    seed_class:    str
    spread:        float
    dominant_axes: list
    source_type:   str

    # The field
    nodes:         list[SemanticNode]
    anchor_nodes:  list[SemanticNode]
    pole_nodes:    dict                 # {'+E': [...], '-E': [...], '0E': [...], ...}
    cross_nodes:   list[SemanticNode]

    # Metadata
    axis_profiles: dict
    computed_from: str
    node_count:    int

    def render(self, fmt: str = 'compact') -> str:
        lines = [
            f'SemanticField  centroid=({self.centroid[0]:+.2f}, {self.centroid[1]:+.2f}, {self.centroid[2]:+.2f})'
            f'  stage={self.stage}  dominant={self.dominant_axes}  nodes={self.node_count}',
        ]
        if fmt == 'compact':
            for n in self.nodes[:10]:
                sig = ''.join(f'{ax}{self.pole_signature_str(n, ax)}' for ax in AXES)
                lines.append(f'  [{sig}] {n.phrase_atom!r:<30}  score={n.score:.3f}  '
                              f'pol={n.facet_profile["polarity"]:<8}  dist={n.epa_distance:.3f}')
        elif fmt == 'full':
            for n in self.nodes:
                lines.append(f'  {n.phrase_atom!r}')
        return '\n'.join(lines)

    @staticmethod
    def pole_signature_str(node: SemanticNode, axis: str) -> str:
        return node.pole_signature.get(axis, '0')

    def encode(self) -> list:
        """Return list of token_id bytes — the field as an ID sequence."""
        return [n.token_id for n in self.nodes]


# ---------------------------------------------------------------------------
# Substrate loader (singleton per process)
# ---------------------------------------------------------------------------

class VerbalizerSubstrate:
    """
    Loads and holds all read-only resources the verbalizer needs.
    Create once; share across all Verbalizer instances.
    """

    def __init__(
        self,
        dict_db:   Path = DEFAULT_DICT_DB,
        epa_db:    Path = DEFAULT_EPA_DB,
        faiss_idx: Path = DEFAULT_FAISS,
        surfaces_path: Path = DEFAULT_SURFACES,
        strict: bool | None = None,   # None -> ELO_FAISS_BINDING (default: degrade)
        denotative_dir: Path | None = DEFAULT_DENOTATIVE_DIR,  # 768-d meaning index (family)
    ):
        self._dict_env   = get_env(dict_db)
        # Verify the faiss<->substrate binding FIRST. _epa_fingerprint() opens the
        # substrate lmdb momentarily, and LMDB forbids two concurrent opens of one env
        # per process — so this must run BEFORE we hold epa_db open (below), else a
        # v2 sidecar (verifiable) raises "already open in this process".
        meta = _verify_index_binding(faiss_idx, epa_db, strict=strict)
        # {} means "could not verify" -> the index loads (cheap) but is never queried.
        self.index_verified = bool(meta)
        self._index      = faiss.read_index(str(faiss_idx))

        with open(surfaces_path, encoding='utf-8') as f:
            self._surfaces: list[str] = json.load(f)

        # faiss_query() maps row -> self._surfaces[row]. If these disagree the
        # expander silently returns the WRONG WORD for every neighbour.
        if len(self._surfaces) != self._index.ntotal:
            self.index_verified = False
            msg = (f'surfaces list ({len(self._surfaces):,}) != index rows '
                   f'({self._index.ntotal:,}); rebuild with faiss_builder.py')
            if BINDING_MODE == 'strict' or strict:
                raise ArtifactDriftError(msg)
            import warnings
            warnings.warn(f'[verbalizer] {msg} -- faiss_query() DISABLED',
                          RuntimeWarning, stacklevel=2)

        # Pre-build EPA lookup: surface → (E, P, A). Open the substrate NOW (after the
        # binding check has opened+closed it), and hold it for the substrate's lifetime.
        self._epa_env    = get_env(epa_db)
        self._epa: dict[str, tuple] = {}
        with self._epa_env.begin() as txn:
            db = self._epa_env.open_db(b'epa', txn=txn)
            cur = txn.cursor(db=db)
            for k, v in cur.iternext():
                raw = k.decode('utf-8')
                surface = raw[3:] if raw.startswith('en|') else raw
                self._epa[surface] = _EPA_STRUCT.unpack(v)

        # Open sub-dbs (no txn arg — handles persist for env lifetime)
        self._db_fwd  = self._dict_env.open_db(b'forward')
        self._db_rev  = self._dict_env.open_db(b'reverse')
        try:
            self._db_vf = self._dict_env.open_db(b'vfacets')      # verbalizer facets
        except Exception:
            self._db_vf = None            # dict built without vfacet_builder -> degrade,
            import warnings               # not crash (lookup_vfacet returns None)
            warnings.warn('[verbalizer] no vfacets sub-DB in this dictionary; run '
                          'vfacet_builder.py on it for full facet constraints',
                          RuntimeWarning, stacklevel=2)

        # Optional DENOTATION channel: the 768-d meaning index from a dictionary
        # family. This is what gives word selection MEANING (car -> truck), distinct
        # from the EPA/affect faiss above (car -> credentials). Opt-in + graceful.
        self.denotative = None
        if denotative_dir is not None:
            try:
                from denotative_index import DenotativeIndex   # noqa: PLC0415
                self.denotative = DenotativeIndex.load(denotative_dir)
            except Exception as e:
                import warnings
                warnings.warn(f'[verbalizer] denotative index not loaded from '
                              f'{denotative_dir}: {e}', RuntimeWarning, stacklevel=2)

    def lookup_id(self, surface: str) -> Optional[bytes]:
        with self._dict_env.begin() as txn:
            return txn.get(surface.encode('utf-8'), db=self._db_fwd)

    def lookup_surface(self, id_bytes: bytes) -> Optional[str]:
        with self._dict_env.begin() as txn:
            v = txn.get(id_bytes, db=self._db_rev)
            return v.decode('utf-8') if v else None

    @staticmethod
    def _fold_key(s: str) -> str:
        """Dedup key: lowercase + drop possessive 's, so Car / CAR / car's collapse to
        one concept. Deliberately does NOT strip a plural 's — that folds news->new,
        lens->len, series->serie (false merges of distinct words). Plural surface
        forms are kept distinct; a lexicon-free suffix rule cannot tell them apart."""
        return s.lower().replace("'s", "").replace("’s", "")

    def denotative_neighbours(self, surface: str, k: int = 8, *,
                              dedupe: bool = True) -> list[tuple[str, float]]:
        """Meaning-nearest words for a surface (car -> automobile/vehicle), from the
        768-d family index. [] if no denotation channel is loaded or the surface is
        absent. Distinct from faiss_query(), which returns EPA/AFFECT neighbours.
        With dedupe=True (default) case + plural variants collapse to distinct
        concepts, and the query's own casings are dropped."""
        if self.denotative is None or not self.denotative.has(surface):
            return []
        if not dedupe:
            return self.denotative.search(surface, pool=k)
        qk = self._fold_key(surface)
        seen, out = {qk}, []
        for s, sim in self.denotative.search(surface, pool=max(k * 4, 32)):
            fk = self._fold_key(s)
            if fk in seen:
                continue
            seen.add(fk)
            out.append((s, sim))
            if len(out) >= k:
                break
        return out

    def lookup_vfacet(self, id_bytes: bytes) -> Optional[dict]:
        if self._db_vf is None:                 # dict has no vfacets -> degrade
            return None
        with self._dict_env.begin() as txn:
            v = txn.get(id_bytes, db=self._db_vf)
            if not v:
                return None
            b0, b1 = _VFACET_STRUCT.unpack(v)
            return {
                'agency':    (b0 & 0b11000000) >> 6,
                'direction': (b0 & 0b00111000) >> 3,
                'temporal':  (b0 & 0b00000111),
                'domain':    (b1 & 0xF0) >> 4,
                'polarity':  (b1 & 0b00001100) >> 2,
            }

    def lookup_vfacet_named(self, id_bytes: bytes) -> Optional[dict]:
        raw = self.lookup_vfacet(id_bytes)
        if not raw:
            return None
        return {
            'agency':    AGN_NAME.get(raw['agency'],    'UNKNOWN'),
            'direction': DIR_NAME.get(raw['direction'], 'UNKNOWN'),
            'temporal':  TMP_NAME.get(raw['temporal'],  'UNKNOWN'),
            'domain':    DOM_NAME.get(raw['domain'],    'GENERAL'),
            'polarity':  POL_NAME.get(raw['polarity'],  'NEUTRAL'),
        }

    def get_epa(self, surface: str) -> Optional[tuple]:
        return self._epa.get(surface)

    def faiss_query(self, epa_vec: tuple, k: int = K_ANCHOR) -> list[tuple[str, float]]:
        """Return [(surface, l2_dist)] nearest to epa_vec.

        Returns [] when the index binding could not be verified. A stale index maps its
        row numbers through a surfaces list that has since moved, so every neighbour is
        a different word than the one reported. No answer beats a wrong answer.
        """
        if not self.index_verified:
            return []
        q = np.array([[epa_vec[0], epa_vec[1], epa_vec[2]]], dtype=np.float32)
        D, I = self._index.search(q, k)
        return [(self._surfaces[i], float(d)) for d, i in zip(D[0], I[0]) if i >= 0]

    def close(self):
        # dict / epa envs are shared via lmdb_cache; do not close them here.
        pass


# ---------------------------------------------------------------------------
# Semantic addition
# ---------------------------------------------------------------------------

def semantic_add(substrate: VerbalizerSubstrate, *surfaces: str) -> Optional[VerbalizerInput]:
    """
    Add any number of surfaces together into a combined semantic position.
    Returns a VerbalizerInput representing their centroid.
    """
    es, ps, as_ = [], [], []
    for s in surfaces:
        epa = substrate.get_epa(s)
        if epa:
            es.append(epa[0]); ps.append(epa[1]); as_.append(epa[2])
    if not es:
        return None
    return VerbalizerInput(
        E=sum(es)/len(es), P=sum(ps)/len(ps), A=sum(as_)/len(as_),
        source_type='clump',
        anchor_ids=list(surfaces),
    )


# ---------------------------------------------------------------------------
# Core algorithm helpers
# ---------------------------------------------------------------------------

def _axis_profiles(E: float, P: float, A: float) -> dict:
    vals = {'E': E, 'P': P, 'A': A}
    profiles = {}
    for ax, v in vals.items():
        intensity = abs(v) / 4.0   # normalize to [0,1] over ±4 scale
        profiles[ax] = AxisProfile(
            axis=ax,
            intensity=intensity,
            dominant_pole='+' if v > 0 else ('-' if v < 0 else '0'),
            spread=0.0,  # per-axis spread requires full token set; default 0
        )
    return profiles


def _dominant_axes(profiles: dict) -> list:
    return [ax for ax, p in profiles.items() if p.intensity > DOMINANT_THRESH]


def _stage_fit(candidate_temporal: int, input_stage: int) -> float:
    """
    Map temporal class → approximate stage affinity, then score against input.
    temporal→stage: PROCESS→2, STATE→1/5, OUTCOME→4/6, EVENT→3, CONDITION→3
    """
    affinities = {
        TMP['UNKNOWN']:   3.5,
        TMP['STATE']:     1.0,
        TMP['PROCESS']:   2.0,
        TMP['EVENT']:     3.0,
        TMP['OUTCOME']:   4.5,
        TMP['CONDITION']: 3.0,
    }
    aff = affinities.get(candidate_temporal, 3.5)
    diff = abs(aff - input_stage)
    if diff > STAGE_HARD_TOL:
        return 0.0
    if diff <= 0.5:  return 1.0
    if diff <= 1.0:  return 0.7
    if diff <= 1.5:  return 0.4
    return 0.0


def _pole_signature(surface_epa: tuple, dominant_axes: list) -> dict:
    sig = {}
    epa_map = {'E': surface_epa[0], 'P': surface_epa[1], 'A': surface_epa[2]}
    for ax in AXES:
        v = epa_map[ax]
        if ax in dominant_axes:
            sig[ax] = '+' if v > 0.3 else ('-' if v < -0.3 else '0')
        else:
            sig[ax] = '0'
    return sig


def _score_node(
    epa_dist: float,
    stage_fit_val: float,
    facet: Optional[dict],
    surface: str,
    input_domain: int = DOM['GENERAL'],
) -> float:
    epa_prox = 1.0 / (1.0 + epa_dist)
    id_brevity = 1.0 / (1.0 + len(surface.split()))
    dom_match = 1.0 if (facet and facet['domain'] == input_domain) else \
                (0.7 if (not facet or facet['domain'] == DOM['GENERAL']) else 0.4)
    sf = max(stage_fit_val, 0.01)
    return epa_prox * sf * id_brevity * dom_match


def _build_node(
    surface: str,
    id_bytes: bytes,
    epa_dist: float,
    stage_fit_val: float,
    facet: Optional[dict],
    dominant_axes: list,
    surface_epa: tuple,
    is_boundary: bool = False,
    is_cross_axis: bool = False,
) -> SemanticNode:
    pole_sig = _pole_signature(surface_epa, dominant_axes)
    facet_named = {
        'agency':    AGN_NAME.get(facet['agency'],    'UNKNOWN') if facet else 'UNKNOWN',
        'direction': DIR_NAME.get(facet['direction'], 'UNKNOWN') if facet else 'UNKNOWN',
        'temporal':  TMP_NAME.get(facet['temporal'],  'UNKNOWN') if facet else 'UNKNOWN',
        'domain':    DOM_NAME.get(facet['domain'],    'GENERAL') if facet else 'GENERAL',
        'polarity':  POL_NAME.get(facet['polarity'],  'NEUTRAL') if facet else 'NEUTRAL',
    }
    score = _score_node(epa_dist, stage_fit_val, facet, surface)
    return SemanticNode(
        phrase_atom=surface,
        token_id=id_bytes,
        score=score,
        pole_signature=pole_sig,
        facet_profile=facet_named,
        stage_fit=stage_fit_val,
        epa_distance=epa_dist,
        is_boundary=is_boundary,
        is_cross_axis=is_cross_axis,
    )


# ---------------------------------------------------------------------------
# Main Verbalizer
# ---------------------------------------------------------------------------

class Verbalizer:
    """
    Semantic field expander.
    Maps semantic territory — does not navigate it.
    """

    def __init__(self, substrate: VerbalizerSubstrate):
        self._s = substrate

    def expand(self, inp: VerbalizerInput) -> SemanticField:
        """Full 8-step expansion. Returns SemanticField."""
        E, P, A = inp.E, inp.P, inp.A

        # ── Step 1: Axis analysis ─────────────────────────────────────────
        axis_profs = _axis_profiles(E, P, A)
        dominant   = _dominant_axes(axis_profs)

        # ── Step 2: Stage characterization ───────────────────────────────
        # Recorded in stage_semantics; used as filter in node construction

        # ── Step 3: Anchor retrieval ──────────────────────────────────────
        anchor_hits = self._s.faiss_query((E, P, A), k=K_ANCHOR)
        anchor_nodes = self._resolve_nodes(
            anchor_hits, inp, dominant, is_boundary=False, is_cross=False
        )

        # ── Step 3b: DENOTATION expansion (meaning channel) ───────────────
        # Enrich the field with MEANING-neighbours (car -> truck/vehicle) from the
        # family's 768-d index. Seeds: the affect anchors + any surface anchors on
        # the input. Candidates still pass through _resolve_nodes below, so they are
        # scored by affect + facets like every other node — denotation PROPOSES,
        # affect/facets DISPOSE. No-op when no denotation channel is loaded, and the
        # primary source of candidates when the EPA/affect faiss is disabled.
        denot_nodes: list[SemanticNode] = []
        if self._s.denotative is not None:
            seeds, seen_seed = [], set()
            for cand in ([s for s, _ in anchor_hits]
                         + [x for x in (list(inp.anchor_ids) + list(inp.raw_ids))
                            if isinstance(x, str)]):
                if cand not in seen_seed:
                    seen_seed.add(cand); seeds.append(cand)
            for seed in seeds:
                nbrs = self._s.denotative_neighbours(seed, k=K_DENOT)
                hits = [(surf, 1.0 - sim) for surf, sim in nbrs]   # sim -> pseudo-distance
                denot_nodes += self._resolve_nodes(
                    hits, inp, dominant, is_boundary=False, is_cross=False,
                    stage_floor=0.5)           # meaning survives regardless of stage

        # ── Step 4: Axis expansion (both poles + neutral per dominant axis)
        pole_nodes: dict[str, list] = {}
        for ax in dominant:
            ax_idx = AXES.index(ax)
            for pole, direction in [('+', +1), ('-', -1), ('0', 0)]:
                shift = [0.0, 0.0, 0.0]
                if pole == '0':
                    shift[ax_idx] = -[E, P, A][ax_idx]  # shift to neutral
                else:
                    shift[ax_idx] = direction * EXPANSION_SIGMA
                shifted = (E + shift[0], P + shift[1], A + shift[2])
                shifted = tuple(max(-4.0, min(4.0, v)) for v in shifted)
                hits = self._s.faiss_query(shifted, k=K_POLE)
                nodes = self._resolve_nodes(
                    hits, inp, dominant, is_boundary=(pole != '0'), is_cross=False
                )
                key = f'{pole}{ax}'
                pole_nodes[key] = nodes

        # ── Step 5: Cross-axis combinations ──────────────────────────────
        cross_nodes: list[SemanticNode] = []
        if len(dominant) >= 2:
            for i, ax1 in enumerate(dominant):
                for ax2 in dominant[i+1:]:
                    cross_nodes += self._cross_expand(inp, ax1, ax2, dominant)

        # ── Step 6 & 7: Collect all, differentiate by facet, score & rank
        seen_surfaces: set = set()
        all_nodes: list[SemanticNode] = []

        for node_list in [anchor_nodes, denot_nodes, cross_nodes] + list(pole_nodes.values()):
            for n in node_list:
                if n.phrase_atom not in seen_surfaces:
                    seen_surfaces.add(n.phrase_atom)
                    all_nodes.append(n)

        all_nodes.sort(key=lambda n: n.score, reverse=True)

        # ── Step 8: Return SemanticField ──────────────────────────────────
        return SemanticField(
            centroid=(E, P, A),
            stage=inp.stage,
            seed_class=inp.seed_class,
            spread=inp.spread,
            dominant_axes=dominant,
            source_type=inp.source_type,
            nodes=all_nodes,
            anchor_nodes=anchor_nodes,
            pole_nodes=pole_nodes,
            cross_nodes=cross_nodes,
            axis_profiles={ax: vars(p) for ax, p in axis_profs.items()},
            computed_from=inp.source_type,
            node_count=len(all_nodes),
        )

    def suggest(self, inp: VerbalizerInput, top_n: int = 3) -> list[SemanticNode]:
        """SUGGEST mode: top-N anchor nodes as phrase atoms with scores."""
        field = self.expand(inp)
        return field.anchor_nodes[:top_n]

    def label(self, inp: VerbalizerInput) -> SemanticNode:
        """LABEL mode: single best node. Deterministic."""
        field = self.expand(inp)
        if not field.nodes:
            raise ValueError('Empty field — no nodes returned')
        return field.nodes[0]

    # ── Internal ─────────────────────────────────────────────────────────

    def _resolve_nodes(
        self,
        hits: list[tuple[str, float]],
        inp: VerbalizerInput,
        dominant_axes: list,
        is_boundary: bool,
        is_cross: bool,
        stage_floor: float = 0.0,   # >0: keep off-stage nodes (denotation) at this floor
    ) -> list[SemanticNode]:
        nodes = []
        for surface, dist in hits:
            id_bytes = self._s.lookup_id(surface)
            if not id_bytes:
                continue
            facet = self._s.lookup_vfacet(id_bytes)
            sfit  = _stage_fit(facet['temporal'] if facet else TMP['UNKNOWN'], inp.stage)
            if sfit == 0.0 and stage_floor == 0.0:
                continue                       # hard stage gate (affect path only)
            sfit = max(sfit, stage_floor)       # denotation: a synonym is stage-agnostic
            surface_epa = self._s.get_epa(surface) or (0.0, 0.0, 0.0)
            node = _build_node(
                surface, id_bytes, dist, sfit, facet,
                dominant_axes, surface_epa,
                is_boundary=is_boundary, is_cross_axis=is_cross,
            )
            nodes.append(node)
        return nodes

    def _cross_expand(
        self,
        inp: VerbalizerInput,
        ax1: str, ax2: str,
        dominant: list,
    ) -> list[SemanticNode]:
        """Generate cross-axis intersection nodes for a pair of dominant axes."""
        E, P, A = inp.E, inp.P, inp.A
        ax_map = {'E': 0, 'P': 1, 'A': 2}
        results = []
        # Four corners of the ax1-ax2 plane
        for sign1, sign2 in [(+1,+1), (+1,-1), (-1,+1), (-1,-1)]:
            shift = [0.0, 0.0, 0.0]
            shift[ax_map[ax1]] = sign1 * EXPANSION_SIGMA
            shift[ax_map[ax2]] = sign2 * EXPANSION_SIGMA
            shifted = (
                max(-4.0, min(4.0, E + shift[0])),
                max(-4.0, min(4.0, P + shift[1])),
                max(-4.0, min(4.0, A + shift[2])),
            )
            hits = self._s.faiss_query(shifted, k=8)
            nodes = self._resolve_nodes(hits, inp, dominant, is_boundary=True, is_cross=True)
            results.extend(nodes)
        return results


# ---------------------------------------------------------------------------
# Convenience: load default substrate
# ---------------------------------------------------------------------------

_DEFAULT_SUBSTRATE: Optional[VerbalizerSubstrate] = None


def get_substrate(**kw) -> VerbalizerSubstrate:
    """Return the module-level default substrate (lazy load)."""
    global _DEFAULT_SUBSTRATE
    if _DEFAULT_SUBSTRATE is None:
        _DEFAULT_SUBSTRATE = VerbalizerSubstrate(**kw)
    return _DEFAULT_SUBSTRATE


def verbalize(E: float, P: float, A: float, stage: int = 1, **kw) -> SemanticField:
    """One-liner convenience: verbalize a (E, P, A) coordinate."""
    sub = get_substrate()
    v   = Verbalizer(sub)
    inp = VerbalizerInput.from_vector(E, P, A, stage=stage, **kw)
    return v.expand(inp)
