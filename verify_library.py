"""
Step 3 verification — library_builder.py
Runs against a single sample transcript so it completes quickly.
Checks tier distribution, ID format, EPA range, stage validity, DB integrity.
"""
import sys, logging
sys.path.insert(0, ".")
logging.basicConfig(level=logging.WARNING)   # suppress info noise during verify

import sqlite3
import numpy as np
import faiss
from collections import Counter

from semantic_compression.config import BASE64_CHARS, TIER_FREQ_RANK
from semantic_compression.library_builder import (
    init_db_schema, count_frequencies, lemmatize_vocab,
    assign_ids, embed_words, project_epa, classify_stages,
    write_db, build_faiss_index, _encode_id,
    _STAGE_NAMES,
)
from sentence_transformers import SentenceTransformer

SAMPLE_DIR  = "Resources/transcripts/jocko_podcast"
TEST_DB     = "semantic_compression/db/test_canonical.db"
TEST_FAISS  = "semantic_compression/db/test_faiss.index"

# ── 1. ID encoding ──────────────────────────────────────────────────────────
assert _encode_id(1, 0)   == "AA",   f"Got {_encode_id(1, 0)!r}"
assert _encode_id(1, 63)  == "A_",   f"Got {_encode_id(1, 63)!r}"
assert _encode_id(1, 64)  == "BA",   f"Got {_encode_id(1, 64)!r}"
assert _encode_id(2, 0)   == "KAA",  f"Got {_encode_id(2, 0)!r}"
assert _encode_id(3, 0)   == "kAAA", f"Got {_encode_id(3, 0)!r}"
print("[OK] ID encoding: tier-prefix and sequential counters correct")

# ── 2. Tier first-char contract ─────────────────────────────────────────────
tier1_first = set(BASE64_CHARS[0:10])
tier2_first = set(BASE64_CHARS[10:36])
tier3_first = set(BASE64_CHARS[36:62])
for c in (0, 63, 200, 639):
    tid = _encode_id(1, c)
    assert len(tid) == 2 and tid[0] in tier1_first, f"Tier1 ID bad: {tid}"
for c in (0, 100, 1000):
    tid = _encode_id(2, c)
    assert len(tid) == 3 and tid[0] in tier2_first, f"Tier2 ID bad: {tid}"
for c in (0, 500, 10000):
    tid = _encode_id(3, c)
    assert len(tid) == 4 and tid[0] in tier3_first, f"Tier3 ID bad: {tid}"
print("[OK] ID first-char encodes tier correctly for all checked values")

# ── 3. Run build phases on sample transcript ─────────────────────────────────
print("\nRunning build phases on sample transcript (1 file)...")

conn = init_db_schema(TEST_DB)
surface_counts = count_frequencies(SAMPLE_DIR, TEST_DB, file_limit=1)

assert len(surface_counts) > 100, "Too few unique words"
total_tokens = sum(surface_counts.values())
print(f"[OK] Phase 1: {len(surface_counts)} unique surface forms, {total_tokens:,} tokens")

lemma_data = lemmatize_vocab(surface_counts, top_n=5000)
assert len(lemma_data) > 50
print(f"[OK] Phase 2: {len(lemma_data)} unique lemmas")

records = assign_ids(lemma_data, conn)
assert len(records) > 0

# Verify tier distribution
tier_counts = Counter(r['tier'] for r in records)
print(f"[OK] Phase 3: {len(records)} words assigned — " +
      ", ".join(f"T{t}:{n}" for t, n in sorted(tier_counts.items())))

# Verify no duplicate IDs
ids = [r['id'] for r in records]
assert len(ids) == len(set(ids)), "Duplicate IDs found"
print("[OK] Phase 3: no duplicate IDs")

# ── 4. Embedding ─────────────────────────────────────────────────────────────
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("all-mpnet-base-v2")
lemmas = [r['lemma'] for r in records]
embeddings = embed_words(lemmas[:200])   # sample 200 for speed
assert embeddings.shape == (200, 768)
assert abs(np.linalg.norm(embeddings[0]) - 1.0) < 1e-4, "Embeddings not L2-normalised"
print(f"[OK] Phase 4: embeddings shape {embeddings.shape}, L2-normalised")

# ── 5. EPA projection ─────────────────────────────────────────────────────────
epa = project_epa(embeddings, model)
assert epa.shape == (200, 3)
assert epa.min() >= -1.0 and epa.max() <= 1.0
print(f"[OK] Phase 5: EPA shape {epa.shape}, range [{epa.min():.3f}, {epa.max():.3f}]")

# Sanity check: "good" should have positive E, "bad" should have negative E
good_emb = model.encode(["good"], normalize_embeddings=True, convert_to_numpy=True)
bad_emb  = model.encode(["bad"],  normalize_embeddings=True, convert_to_numpy=True)
good_epa = project_epa(good_emb, model)
bad_epa  = project_epa(bad_emb, model)
assert good_epa[0, 0] > bad_epa[0, 0], \
    f"'good' E={good_epa[0,0]:.3f} should > 'bad' E={bad_epa[0,0]:.3f}"
print(f"[OK] Phase 5: 'good' E={good_epa[0,0]:.3f} > 'bad' E={bad_epa[0,0]:.3f}")

# ── 6. Stage classification ───────────────────────────────────────────────────
stages = classify_stages(epa)
assert len(stages) == 200
assert all(s in _STAGE_NAMES for s in stages)
stage_dist = Counter(stages)
print(f"[OK] Phase 6: stage distribution — {dict(stage_dist)}")

# ── 7. DB write ───────────────────────────────────────────────────────────────
for i, r in enumerate(records[:200]):
    r['epa_e'] = float(epa[i, 0])
    r['epa_p'] = float(epa[i, 1])
    r['epa_a'] = float(epa[i, 2])
    r['stage'] = stages[i]
    r['vector_id'] = i

write_db(records[:200], conn)

row_count = conn.execute("SELECT COUNT(*) FROM word_library").fetchone()[0]
reg_count = conn.execute("SELECT COUNT(*) FROM id_registry").fetchone()[0]
assert row_count == 200, f"Expected 200 rows, got {row_count}"
assert reg_count >= 200
print(f"[OK] Phase 7: {row_count} rows in word_library, {reg_count} in id_registry")

# Spot-check a row
sample_row = conn.execute(
    "SELECT id, tier, lemma, epa_e, epa_p, epa_a, stage FROM word_library LIMIT 1"
).fetchone()
assert sample_row[3] is not None, "epa_e is NULL"
print(f"[OK] Phase 7: sample row — id={sample_row[0]!r} tier={sample_row[1]} "
      f"lemma={sample_row[2]!r} stage={sample_row[6]!r}")

# ── 8. FAISS index ───────────────────────────────────────────────────────────
index = build_faiss_index(embeddings, TEST_FAISS)
assert index.ntotal == 200
# Nearest neighbour of a vector should be itself
D, I = index.search(embeddings[:1], k=1)
assert I[0][0] == 0, f"NN of vector 0 should be 0, got {I[0][0]}"
print(f"[OK] Phase 8: FAISS index has {index.ntotal} vectors, self-NN correct")

conn.close()
print("\n=== Step 3 verification PASSED ===")
