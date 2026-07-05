"""
meta_fields.py -- System-1 DETERMINISTIC meta derivation (side annotation).

Extends the frozen 4-byte facet record with richer DETERMINISTIC fields WITHOUT
touching it. The facet stays the compact hot-path subset; this produces the wider
per-surface meta row for the meta DB (see docs/compression/spec-meta-db.md).

Only System-1-legal fields are populated here (rule-based, no model). System-2
fields (epa, polarity, agency, directionality, fine temporal stage) are returned
as RESERVED = None until the EPA layer (semantic-meanings) is finalized.

Provenance: every populated field records its method ('cue' | 'suffix' |
'heuristic' | 'override' | 'provenance') so the accuracy harness + overrides know
what to trust. Surfaces are the stable key (IDs are provisional).
"""
from __future__ import annotations

import sys
sys.path.insert(0, ".")

import config as cfg
from config import utility_of
from facets import assign_facet
from verb_complements import complement_of

# Abstraction tell now lives in config.is_abstract (curated lexicon + abstract
# suffixes), shared with facets so the bucket (abstract -> CONCEPT) and the meta
# abstraction dimension agree. Full concrete/abstract is the S2/EPA layer's job.

# Cue groups that surface the latent causality / temporality / scope axes that
# already live in the logic_cue_mask (we expose them, we don't re-derive).
_CAUSAL_CUES   = ("CAUSE", "CONDITION", "INFERENCE", "EVIDENCE_CUE")
_TEMPORAL_CUES = ("TEMPORAL",)
_SCOPE_CUES    = ("QUANTIFIER",)

# Reserved System-2 fields (populated only after EPA finalizes). Listed here so
# the row shape is stable now and the spec/consumers can rely on the keys.
S2_RESERVED = ("polarity", "epa_e", "epa_p", "epa_a", "agency",
               "directionality", "temporal_stage")


def _cues(cue_mask: int) -> set[str]:
    return {n for n, bit in cfg.LOGIC_CUE.items() if cue_mask & bit}


def derive_meta(surface: str, overrides: dict | None = None,
                *, source_register: str | None = None,
                domain: str | None = None) -> dict:
    """Return the full deterministic meta row for a surface.

    source_register / domain are OPTIONAL provenance inputs (from the build's
    corpus sources / Wiktionary topics). When absent they stay None -- still
    System-1, just not yet attested.
    """
    bucket, cue_mask, flags = assign_facet(surface, overrides)
    cues = _cues(cue_mask)
    is_multiword = " " in surface
    method: dict[str, str] = {}

    # --- abstraction (concrete | abstract | None) -- deterministic suffix tell
    abstraction = None
    if not is_multiword and bucket in (cfg.BUCKET["TOPIC"], cfg.BUCKET["CONCEPT"]):
        if cfg.is_abstract(surface):
            abstraction = "abstract"; method["abstraction"] = "suffix/lexicon"
        # no positive concrete tell from surface alone -> leave None (honest; S2/EPA)

    # --- causality / temporality / scope -- SURFACED from the cue mask
    causality = sorted(c for c in _CAUSAL_CUES if c in cues) or None
    if causality: method["causality"] = "cue"
    temporality = "temporal" if cues & set(_TEMPORAL_CUES) else None
    if temporality: method["temporality"] = "cue"
    scope = "quantified" if cues & set(_SCOPE_CUES) else None
    if scope: method["scope"] = "cue"

    # --- domain / register -- System-1 from provenance when available
    if domain: method["domain"] = "provenance"
    if source_register: method["register"] = "provenance"

    # --- complement -- verb subcategorization (to_infinitive | to_noun | both).
    # The POS-resolution instruction set: lets a consumer disambiguate a "to X"
    # homograph from the GOVERNING verb. Single-word verbs only; None otherwise.
    complement = complement_of(surface) if not is_multiword else None
    if complement: method["complement"] = "lexicon"

    row = {
        # identity / structural (deterministic)
        "surface": surface,
        "kind": "phrase" if is_multiword else "word",
        "bucket": cfg.BUCKET_NAME[bucket],
        "utility": cfg.UTILITY_NAME[utility_of(flags)],
        "logic_cues": sorted(cues) or None,
        "flags": sorted(n for n, b in cfg.FLAG.items() if flags & b) or None,
        # System-1 richer axes
        "abstraction": abstraction,      # concrete/abstract/metaphor(S2)
        "causality":   causality,        # from cues
        "temporality": temporality,      # coarse; fine stage = S2
        "scope":       scope,            # coarse; fine = S2
        "domain":      domain,           # provenance (Wiktionary/source)
        "register":    source_register,  # provenance (source kind)
        "complement":  complement,       # verb subcategorization (POS resolution)
        # System-2 RESERVED (None until EPA finalizes)
        **{k: None for k in S2_RESERVED},
        "_method": method or None,
        "_meta_layer": 1,                # 1 = System-1 fields only populated
    }
    return row


if __name__ == "__main__":
    import json
    samples = ["love", "money", "relationship", "god", "marriage", "profit",
               "freedom", "democracy", "because", "when", "all", "machine learning"]
    try:
        from facets import load_overrides
        ov = load_overrides("data/facet_overrides.tsv")
    except Exception:
        ov = None
    print(f"{'surface':<16}{'bucket':<10}{'abstraction':<12}{'causality':<22}{'temporality':<12}{'scope'}")
    print("-" * 86)
    for s in samples:
        m = derive_meta(s, ov)
        print(f"{s:<16}{m['bucket']:<10}{str(m['abstraction']):<12}"
              f"{str(m['causality']):<22}{str(m['temporality']):<12}{str(m['scope'])}")
