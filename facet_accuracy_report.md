# Facet Accuracy Report

- build meta.db: `F:\Script-Projects\elo-dev\elo_dev\R-D-concepts\semantic_compression\db\builds\general_v0.4_char4\meta.db`
- gold rows: 160  ·  scored (in build): 155  ·  coverage: 97%
- gold surfaces missing from build: ..., by contrast, provided that, {, }

## Targets (release gate)

| dimension | score | target | pass |
|---|---:|---:|:--:|
| utility | 1.000 | 0.95 | PASS |
| bucket_content | 1.000 | 0.85 | PASS |
| cue_f1_function | 1.000 | 0.90 | PASS |
| abstraction | 0.960 | 0.90 | PASS |

> abstraction gate ACTIVE (meta_layer=2) — scored in Targets above. Promoted 2026-07-05.

## Per-dimension accuracy (all scored)

| dimension | accuracy |
|---|---:|
| kind | 1.000 |
| bucket | 1.000 |
| utility | 1.000 |
| abstraction | 0.987 |
| temporality | 1.000 |
| scope | 1.000 |
| bucket (content only, n=71) | 1.000 |
| abstraction (labeled only, n=50) | 0.960 |

## Logic-cue set F1

- all: F1=1.000 (P=1.000 R=1.000; tp=73 fp=0 fn=0)
- function subset (n=54): F1=1.000 (P=1.000 R=1.000; tp=68 fp=0 fn=0)
- causality: F1=1.000 (P=1.000 R=1.000; tp=16 fp=0 fn=0)

## Confusion (gold -> predicted)

  bucket:
    CONCEPT     -> CONCEPT:26
    METHOD      -> METHOD:21
    RELATION    -> RELATION:55
    STRUCTURAL  -> STRUCTURAL:19
    TOPIC       -> TOPIC:24
    UNKNOWN     -> UNKNOWN:10
  utility:
    CONTENT     -> CONTENT:71
    FILLER      -> FILLER:11
    FUNCTION    -> FUNCTION:54
    STRUCTURAL  -> STRUCTURAL:19
  abstraction:
    -           -> -:105
    abstract    -> abstract:27
    concrete    -> concrete:21, -:1, abstract:1

## Misses: 2 (written to the misses CSV -> override queue)

