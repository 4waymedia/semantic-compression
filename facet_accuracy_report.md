# Facet Accuracy Report

- build meta.db: `/sessions/gifted-sweet-darwin/mnt/elo_dev/R-D-concepts/semantic_compression/db/builds/general_v0.4_char4/meta.db`
- gold rows: 396  ·  scored (in build): 391  ·  coverage: 99%
- gold surfaces missing from build: ..., by contrast, provided that, {, }

## Targets (release gate)

| dimension | score | target | pass |
|---|---:|---:|:--:|
| utility | 1.000 | 0.95 | PASS |
| bucket_content | 0.995 | 0.85 | PASS |
| cue_f1_function | 1.000 | 0.90 | PASS |
| abstraction | 0.988 | 0.90 | PASS |

> abstraction gate ACTIVE (meta_layer=2) — scored in Targets above. Promoted 2026-07-05.

## Per-dimension accuracy (all scored)

| dimension | accuracy |
|---|---:|
| kind | 1.000 |
| bucket | 0.997 |
| utility | 1.000 |
| abstraction | 0.995 |
| temporality | 1.000 |
| scope | 1.000 |
| bucket (content only, n=211) | 0.995 |
| abstraction (labeled only, n=173) | 0.988 |

## Logic-cue set F1

- all: F1=1.000 (P=1.000 R=1.000; tp=196 fp=0 fn=0)
- function subset (n=134): F1=1.000 (P=1.000 R=1.000; tp=191 fp=0 fn=0)
- causality: F1=1.000 (P=1.000 R=1.000; tp=43 fp=0 fn=0)

## Confusion (gold -> predicted)

  bucket:
    CONCEPT     -> CONCEPT:99
    METHOD      -> METHOD:38
    RELATION    -> RELATION:137
    STRUCTURAL  -> STRUCTURAL:23
    TOPIC       -> TOPIC:73, CONCEPT:1
    UNKNOWN     -> UNKNOWN:20
  utility:
    CONTENT     -> CONTENT:211
    FILLER      -> FILLER:23
    FUNCTION    -> FUNCTION:134
    STRUCTURAL  -> STRUCTURAL:23
  abstraction:
    -           -> -:218
    abstract    -> abstract:100
    concrete    -> concrete:71, -:1, abstract:1

## Misses: 3 (written to the misses CSV -> override queue)

