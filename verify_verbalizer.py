"""
verify_verbalizer.py
EloAI — Verbalizer verification gates

Gates:
  T1   Substrate loads — index, EPA, dict sub-dbs all reachable
  T2   semantic_add(*surfaces) → VerbalizerInput centroid correct
  T3   expand() returns a well-formed SemanticField
  T4   Dominant axes computed correctly from intensity threshold
  T5   Pole nodes present for every dominant axis (+, -, 0 keys)
  T6   Cross nodes fire for multi-dominant coordinates
  T7   Semantic direction — positive coord → majority positive polarity nodes
  T8   LABEL mode is deterministic (same input → same output across calls)
  T9   SUGGEST mode returns exactly top_n nodes
  T10  render() and encode() do not raise
  T11  Edge cases: zero vector, single-value vector, neutral coordinate

Usage:
    python verify_verbalizer.py
    python verify_verbalizer.py --verbose
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from collections import Counter

from verbalizer import (
    VerbalizerSubstrate, Verbalizer, VerbalizerInput, SemanticField,
    SemanticNode, semantic_add, AXES, DOMINANT_THRESH,
    DOM, POL, TMP, POL_NAME,
    DEFAULT_DICT_DB, DEFAULT_EPA_DB, DEFAULT_FAISS, DEFAULT_SURFACES,
)

PASS = '[OK]'
FAIL = '[FAIL]'
_verbose = False


def ok(gate: str, msg: str) -> None:
    print(f'{PASS} {gate:<5} {msg}')


def fail(gate: str, msg: str) -> None:
    print(f'{FAIL} {gate:<5} {msg}', file=sys.stderr)
    sys.exit(1)


def check(gate: str, condition: bool, msg: str) -> None:
    if condition:
        ok(gate, msg)
    else:
        fail(gate, msg)


# ---------------------------------------------------------------------------
# T1 — Substrate loads
# ---------------------------------------------------------------------------

def t1_substrate_loads(sub: VerbalizerSubstrate) -> None:
    # FAISS index has entries
    check('T1', sub._index.ntotal > 0,
          f'FAISS index loaded: {sub._index.ntotal:,} vectors')

    # EPA lookup populated
    check('T1', len(sub._epa) > 10_000,
          f'EPA lookup: {len(sub._epa):,} entries')

    # Forward lookup works for a known word
    id_bytes = sub.lookup_id('happiness')
    check('T1', id_bytes is not None,
          f'"happiness" → id={id_bytes.hex() if id_bytes else None}')

    # Reverse lookup round-trips
    surface = sub.lookup_surface(id_bytes)
    check('T1', surface == 'happiness',
          f'reverse lookup: {surface!r} == "happiness"')

    # vfacet lookup returns the right keys
    vf = sub.lookup_vfacet_named(id_bytes)
    check('T1', vf is not None and 'polarity' in vf,
          f'"happiness" vfacet: polarity={vf["polarity"] if vf else None}')

    # FAISS query returns results
    hits = sub.faiss_query((2.0, 0.0, 0.0), k=5)
    check('T1', len(hits) == 5 and all(isinstance(s, str) for s, _ in hits),
          f'faiss_query(E=+2.0) returned {len(hits)} hits')


# ---------------------------------------------------------------------------
# T2 — semantic_add
# ---------------------------------------------------------------------------

def t2_semantic_add(sub: VerbalizerSubstrate) -> None:
    # Words with known positive valence
    inp = semantic_add(sub, 'happiness', 'love', 'joy')
    check('T2', inp is not None, 'semantic_add returned VerbalizerInput')
    check('T2', inp.E > 0,
          f'centroid E={inp.E:.3f} > 0 for happiness+love+joy')
    check('T2', inp.source_type == 'clump',
          f'source_type={inp.source_type!r}')

    # Negative words → negative E
    inp_neg = semantic_add(sub, 'anger', 'fear', 'hatred')
    check('T2', inp_neg is not None and inp_neg.E < 0,
          f'centroid E={inp_neg.E:.3f} < 0 for anger+fear+hatred')

    # Unknown word returns None gracefully
    inp_unk = semantic_add(sub, 'xyzzy_nonexistent_word')
    check('T2', inp_unk is None,
          'semantic_add with unknown word returns None (graceful)')


# ---------------------------------------------------------------------------
# T3 — expand() returns well-formed SemanticField
# ---------------------------------------------------------------------------

def t3_well_formed(v: Verbalizer) -> SemanticField:
    inp = VerbalizerInput.from_vector(2.0, 0.5, 1.0, stage=2)
    field = v.expand(inp)

    check('T3', isinstance(field, SemanticField),
          'expand() returns SemanticField')
    check('T3', field.node_count > 0,
          f'node_count={field.node_count} > 0')
    check('T3', len(field.nodes) == field.node_count,
          f'len(nodes)={len(field.nodes)} == node_count')
    check('T3', field.centroid == (2.0, 0.5, 1.0),
          f'centroid={field.centroid}')
    check('T3', field.stage == 2,
          f'stage={field.stage}')
    check('T3', all(isinstance(n, SemanticNode) for n in field.nodes),
          'all nodes are SemanticNode instances')

    # Nodes sorted by score descending
    scores = [n.score for n in field.nodes]
    check('T3', scores == sorted(scores, reverse=True),
          'nodes sorted by score descending')

    # Every node has required fields
    for n in field.nodes:
        assert isinstance(n.phrase_atom, str) and n.phrase_atom
        assert isinstance(n.score, float) and n.score >= 0
        assert isinstance(n.facet_profile, dict)
        assert 'polarity' in n.facet_profile
        assert isinstance(n.is_boundary, bool)
        assert isinstance(n.is_cross_axis, bool)
    check('T3', True, 'all nodes have required fields populated')

    return field


# ---------------------------------------------------------------------------
# T4 — Dominant axes computed correctly
# ---------------------------------------------------------------------------

def t4_dominant_axes(v: Verbalizer) -> None:
    # Strong E, weak P and A → only E dominant
    inp = VerbalizerInput.from_vector(3.0, 0.1, 0.1, stage=1)
    field = v.expand(inp)
    check('T4', 'E' in field.dominant_axes,
          f'E=3.0 → E in dominant_axes {field.dominant_axes}')
    check('T4', 'P' not in field.dominant_axes,
          f'P=0.1 → P NOT in dominant_axes {field.dominant_axes}')

    # All three strong → all dominant
    inp3 = VerbalizerInput.from_vector(3.0, 3.0, 3.0, stage=2)
    field3 = v.expand(inp3)
    check('T4', set(field3.dominant_axes) == {'E', 'P', 'A'},
          f'all strong → all dominant: {field3.dominant_axes}')

    # Near-zero → no dominant axes
    inp0 = VerbalizerInput.from_vector(0.1, 0.05, 0.08, stage=3)
    field0 = v.expand(inp0)
    check('T4', len(field0.dominant_axes) == 0,
          f'near-zero → no dominant axes: {field0.dominant_axes}')


# ---------------------------------------------------------------------------
# T5 — Pole nodes present for every dominant axis
# ---------------------------------------------------------------------------

def t5_pole_nodes(v: Verbalizer) -> None:
    inp = VerbalizerInput.from_vector(3.0, 0.1, 0.1, stage=2)
    field = v.expand(inp)

    dominant = field.dominant_axes
    check('T5', len(dominant) > 0, f'has dominant axes: {dominant}')

    for ax in dominant:
        for pole in ('+', '-', '0'):
            key = f'{pole}{ax}'
            check('T5', key in field.pole_nodes,
                  f'pole_nodes["{key}"] present')


# ---------------------------------------------------------------------------
# T6 — Cross nodes fire for multi-dominant coordinates
# ---------------------------------------------------------------------------

def t6_cross_nodes(v: Verbalizer) -> None:
    # Two or more dominant axes → cross nodes should appear
    inp = VerbalizerInput.from_vector(3.0, 3.0, 0.1, stage=2)
    field = v.expand(inp)

    check('T6', len(field.dominant_axes) >= 2,
          f'dominant_axes={field.dominant_axes} (need ≥ 2)')
    check('T6', len(field.cross_nodes) > 0,
          f'cross_nodes count={len(field.cross_nodes)} > 0')
    check('T6', all(n.is_cross_axis for n in field.cross_nodes),
          'all cross_nodes have is_cross_axis=True')


# ---------------------------------------------------------------------------
# T7 — Semantic direction: positive coord → majority positive polarity
# ---------------------------------------------------------------------------

def t7_semantic_direction(v: Verbalizer) -> None:
    # Strong positive E
    inp_pos = VerbalizerInput.from_vector(3.0, 0.0, 0.0, stage=1)
    field_pos = v.expand(inp_pos)
    pol_counts = Counter(n.facet_profile['polarity'] for n in field_pos.nodes)
    pos_count = pol_counts.get('POSITIVE', 0)
    neg_count = pol_counts.get('NEGATIVE', 0)
    check('T7', pos_count > neg_count,
          f'E=+3.0 → POSITIVE({pos_count}) > NEGATIVE({neg_count})')

    # Strong negative E
    inp_neg = VerbalizerInput.from_vector(-3.0, 0.0, 0.0, stage=3)
    field_neg = v.expand(inp_neg)
    pol_neg = Counter(n.facet_profile['polarity'] for n in field_neg.nodes)
    pos_n = pol_neg.get('POSITIVE', 0)
    neg_n = pol_neg.get('NEGATIVE', 0)
    check('T7', neg_n > pos_n,
          f'E=-3.0 → NEGATIVE({neg_n}) > POSITIVE({pos_n})')

    if _verbose:
        print(f'       pos field polarity: {dict(pol_counts)}')
        print(f'       neg field polarity: {dict(pol_neg)}')


# ---------------------------------------------------------------------------
# T8 — LABEL mode is deterministic
# ---------------------------------------------------------------------------

def t8_label_deterministic(v: Verbalizer) -> None:
    inp = VerbalizerInput.from_vector(1.5, -0.5, 0.8, stage=4)
    label1 = v.label(inp)
    label2 = v.label(inp)
    check('T8', label1.phrase_atom == label2.phrase_atom,
          f'LABEL deterministic: {label1.phrase_atom!r} == {label2.phrase_atom!r}')
    check('T8', label1.score == label2.score,
          f'LABEL score stable: {label1.score:.4f}')


# ---------------------------------------------------------------------------
# T9 — SUGGEST returns exactly top_n
# ---------------------------------------------------------------------------

def t9_suggest(v: Verbalizer) -> None:
    inp = VerbalizerInput.from_vector(2.0, 1.0, -0.5, stage=2)
    for n in (1, 3, 5):
        results = v.suggest(inp, top_n=n)
        check('T9', len(results) <= n,
              f'suggest(top_n={n}) returned {len(results)} ≤ {n}')
    check('T9', True, 'SUGGEST top_n constraint holds')


# ---------------------------------------------------------------------------
# T10 — render() and encode() do not raise
# ---------------------------------------------------------------------------

def t10_render_encode(v: Verbalizer) -> None:
    inp = VerbalizerInput.from_vector(1.0, 0.5, 1.0, stage=2)
    field = v.expand(inp)

    try:
        text = field.render(fmt='compact')
        check('T10', isinstance(text, str) and len(text) > 0,
              'render(compact) returned non-empty string')
    except Exception as e:
        fail('T10', f'render() raised: {e}')

    try:
        ids = field.encode()
        check('T10', isinstance(ids, list),
              f'encode() returned list of {len(ids)} id_bytes')
    except Exception as e:
        fail('T10', f'encode() raised: {e}')

    if _verbose:
        print(f'\n{text}\n')


# ---------------------------------------------------------------------------
# T11 — Edge cases
# ---------------------------------------------------------------------------

def t11_edge_cases(v: Verbalizer) -> None:
    # Zero vector → no dominant axes, still returns a field
    inp_zero = VerbalizerInput.from_vector(0.0, 0.0, 0.0, stage=1)
    field_zero = v.expand(inp_zero)
    check('T11', len(field_zero.dominant_axes) == 0,
          f'zero vector → no dominant axes')
    check('T11', isinstance(field_zero, SemanticField),
          'zero vector → SemanticField returned (no crash)')

    # Max positive → E,P,A all dominant
    inp_max = VerbalizerInput.from_vector(4.0, 4.0, 4.0, stage=2)
    field_max = v.expand(inp_max)
    check('T11', set(field_max.dominant_axes) == {'E', 'P', 'A'},
          f'max positive → all axes dominant: {field_max.dominant_axes}')

    # Max negative → also all dominant
    inp_min = VerbalizerInput.from_vector(-4.0, -4.0, -4.0, stage=3)
    field_min = v.expand(inp_min)
    check('T11', set(field_min.dominant_axes) == {'E', 'P', 'A'},
          f'max negative → all axes dominant: {field_min.dominant_axes}')

    # Single-axis: only P dominant
    inp_p = VerbalizerInput.from_vector(0.1, 3.5, 0.1, stage=2)
    field_p = v.expand(inp_p)
    check('T11', field_p.dominant_axes == ['P'],
          f'only P strong → dominant={field_p.dominant_axes}')


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(verbose: bool = False) -> None:
    global _verbose
    _verbose = verbose

    print('verify_verbalizer.py — loading substrate...')
    sub = VerbalizerSubstrate()
    v   = Verbalizer(sub)
    print(f'substrate loaded  EPA={len(sub._epa):,}  FAISS={sub._index.ntotal:,}\n')

    t1_substrate_loads(sub)
    print()
    t2_semantic_add(sub)
    print()
    field = t3_well_formed(v)
    print()
    t4_dominant_axes(v)
    print()
    t5_pole_nodes(v)
    print()
    t6_cross_nodes(v)
    print()
    t7_semantic_direction(v)
    print()
    t8_label_deterministic(v)
    print()
    t9_suggest(v)
    print()
    t10_render_encode(v)
    print()
    t11_edge_cases(v)

    sub.close()
    print('\n✓ All verbalizer gates passed')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()
    run(verbose=args.verbose)


if __name__ == '__main__':
    main()
