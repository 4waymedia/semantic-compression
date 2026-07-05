# Vfacet LLM Classification Guide

> How to classify `agency` and `direction` fields in `b'vfacets'` using the two-pass
> pipeline in `vfacet_llm.py`. Pass 1 is deterministic (EPA + heuristics); Pass 2
> sends remaining UNKNOWNs to an LLM in parallel batches.
>
> Companion docs: facets spec → [`spec-facets-db.md`](spec-facets-db.md);
> EPA handbook → [`HANDBOOK-facets-epa.md`](HANDBOOK-facets-epa.md). Last updated: 2026-06-25.

---

## Overview

`vfacet_builder.py` builds the `b'vfacets'` LMDB sub-database with five fields:
`polarity`, `temporal`, `domain`, `agency`, `direction`. The builder fills the first
three deterministically; `agency` and `direction` default to UNKNOWN for most entries.

`vfacet_llm.py` fills those two remaining fields in two passes without touching the
other three.

**Pass 1 — Deterministic** (~2 seconds, full corpus):
- `direction` derived from EPA A/E axes (activity + evaluation → movement vector)
- `agency` derived from curated word lists + morphological heuristics + domain signals
- Covers all EPA-rated entries reliably; ~50% of the corpus

**Pass 2 — LLM** (minutes, UNKNOWN entries only):
- Sends remaining UNKNOWN entries to an LLM in parallel batches
- Supports local llama.cpp or OpenAI-compatible API
- Idempotent: already-classified entries are preserved across runs

---

## Field Definitions

### AGENCY
Who or what is acting.

| Code | Meaning | Examples |
|------|---------|---------|
| SELF | Subject acts on or for themselves | feel, decide, heal, grow |
| OTHER | Subject acts on other people | teach, lead, tell, advise |
| SYSTEM | Automated or institutional process | compile, regulate, compute |
| UNKNOWN | Inanimate noun, abstract concept, indeterminate | table, economy |

### DIRECTION
The movement or discourse stance of the entry.

| Code | Meaning | Examples |
|------|---------|---------|
| TOWARD | Approach, gain, build — or discourse signal offering a claim | pursue, achieve, "here's what", "going to" |
| AWAY | Avoid, reject, escape — or discourse retreat | flee, deny, "no wait", "walking away" |
| STABLE | Persist, hold, anchor — or discourse continuation | remain, maintain, "which means", "it's still" |
| REVERSAL | Change direction, undo, overturn | reverse, pivot, transform, undo |
| NEUTRAL | Filler, pure reference, no directional signal | "you know", "I mean", "a lot of" |
| UNKNOWN | Genuinely indeterminate | — |

---

## Setup

### Environment

Add your API key to `R-D-concepts/.env`:

```
OPENAI_API_KEY=sk-...
```

The script loads this automatically via `_load_dotenv()`. Requires:
```cmd
pip install python-dotenv
```

### Dependencies

```cmd
pip install lmdb
pip install python-dotenv   # for .env loading
```

---

## Running

### Pass 1 only (deterministic, fast)
```cmd
python vfacet_llm.py
```

### Pass 1 + Pass 2 with OpenAI
```cmd
python vfacet_llm.py --llm --openai --openai-model gpt-5.4-mini --parallel 50 --batch-size 200
```

### Pass 1 + Pass 2 with local llama.cpp
```cmd
python vfacet_llm.py --llm --local --local-url http://localhost:8080 --parallel 4 --batch-size 50
```

### Check current distribution
```cmd
python vfacet_llm.py --stats
```

---

## Key Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--llm` | off | Enable Pass 2 LLM classification |
| `--openai` | off | Use OpenAI API (reads `OPENAI_API_KEY`) |
| `--openai-model` | `gpt-5.4-mini` | OpenAI model name |
| `--local` | off | Use local llama.cpp server |
| `--local-url` | `http://localhost:8080` | llama.cpp server URL |
| `--parallel` | 1 | Concurrent batches (match server slots) |
| `--batch-size` | 50 | Entries per LLM request |
| `--max-llm` | 0 (unlimited) | Cap total LLM entries (for testing) |
| `--time-limit` | none | Stop after duration: `2h`, `90m`, `3600` |
| `--dry-run` | off | Count only, no writes |
| `--miss-log` | `llm_misses.txt` | Log unmatched/hallucinated entries |
| `--stats` | off | Print field distributions and exit |

---

## Recommended Settings

### OpenAI (Tier 3+)
```cmd
--parallel 50 --batch-size 200 --openai-model gpt-5.4-mini
```
~150k entries in under 10 minutes. Uses `max_completion_tokens` (required for newer models).

### Local llama.cpp (MoE model, 4 parallel slots)
```cmd
--parallel 4 --batch-size 50
```
Start server with `--parallel 4`. Throughput ~4 items/sec; full corpus ~10 hours.

---

## Pre-filters (applied before LLM)

1. **Numeric fragments** — entries starting with a digit (e.g. `00 at night`) are
   skipped; these are transcript artifacts with no classifiable agency or direction.

2. **Pure stopword n-grams** — phrases composed entirely of stopwords (e.g. `a the of`)
   are written directly as `NEUTRAL` direction without calling the LLM.

---

## Preservation Logic

Pass 1 skips any entry where both `agency` AND `direction` are already non-UNKNOWN.
This means the script is safe to re-run: LLM-classified values from a previous run
are never overwritten.

---

## Hallucination Detection

The LLM is asked to echo each phrase back in its response. If the returned word
doesn't match the sent phrase (after apostrophe normalization), it's logged as a
mismatch and the result is applied positionally (fallback) rather than by name.
Mismatches are written to `--miss-log` with a `[MISMATCH]` prefix for review.

Typical hallucination rate: < 0.1%.

---

## Abort Behavior

If the LLM server returns a 5xx error or connection refused, the run aborts
immediately with `[ABORT] LLM server unavailable`. All entries already written
to LMDB are committed and safe — restart the script to resume.

4xx errors (e.g. bad model name, wrong parameter) log the full error body and
continue to the next batch rather than aborting.

---

## Typical Results (373k corpus)

After a full two-pass run:

| Field | Before | After |
|-------|--------|-------|
| Direction UNKNOWN | ~39% | ~18% |
| Agency UNKNOWN | ~90% | ~70% |
| Direction classified | ~61% | ~82% |

Remaining UNKNOWNs after LLM pass are either genuinely ambiguous entries
or numeric/artifact fragments that can't be meaningfully classified.
