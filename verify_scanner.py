"""Step 2 verification — corpus_scanner.py"""
import sys
sys.path.insert(0, ".")

from semantic_compression.corpus_scanner import (
    clean_text, dedup_within_chunk, dedup_boundary, scan_single
)

# ── 1. clean_text ───────────────────────────────────────────────────────────
assert clean_text("Hello  World\t!") == "hello world !"
assert clean_text("  spaces  ") == "spaces"
assert clean_text("UPPER CASE") == "upper case"
print("[OK] clean_text: lowercase + whitespace normalization")

# ── 2. dedup_within_chunk ───────────────────────────────────────────────────

# Synthetic 3× repeat
words = "you get you get you get someone on your team that does someone on your team that does".split()
got = dedup_within_chunk(words)
assert got == "you get someone on your team that does".split(), f"Got: {got}"
print("[OK] dedup_within_chunk: 3× repeat collapsed to 1")

# 2× repeat of a longer phrase
words = "natural leaders out there right natural leaders out there right and uh".split()
got = dedup_within_chunk(words)
assert got == "natural leaders out there right and uh".split(), f"Got: {got}"
print("[OK] dedup_within_chunk: 2× repeat collapsed to 1")

# No repetition — should be unchanged
words = "leadership is what drives the success of an organization".split()
got = dedup_within_chunk(words)
assert got == words, f"Got: {got}"
print("[OK] dedup_within_chunk: no-repetition passthrough")

# ── 3. dedup_boundary ───────────────────────────────────────────────────────

prev  = "throughout their lives they have developed themselves over time right".split()
curr  = "throughout their lives they have developed themselves over time right some people have".split()
got   = dedup_boundary(prev, curr)
assert got == ["some", "people", "have"], f"Got: {got}"
print("[OK] dedup_boundary: tail/head overlap stripped correctly")

# No overlap — should be unchanged
prev = "alpha beta gamma".split()
curr = "delta epsilon zeta".split()
assert dedup_boundary(prev, curr) == curr
print("[OK] dedup_boundary: no overlap passthrough")

# ── 4. scan_single on real transcript ───────────────────────────────────────
sample_path = "Resources/transcripts/jocko_podcast/4thExymE1yY.json"
records = scan_single(sample_path, show_progress=False)

assert len(records) > 0, "No records returned"
video_id, chunk_id, clean, meta = records[0]

assert video_id == "4thExymE1yY"
assert chunk_id == 0
assert clean == clean.lower(), "Text not lowercased"
assert "  " not in clean, "Double spaces present"
print(f"[OK] scan_single: {len(records)} chunks from sample transcript")

# Verify dedup worked — raw first chunk has strong repetition
# After dedup, text should NOT contain "jocko podcast number 366 with echo charles"
# appearing twice consecutively
raw_first_tokens = clean.split()
# Check: raw text repeated the first 5 words — they should appear only once now
first_five = raw_first_tokens[:5]
# Find second occurrence
rest = raw_first_tokens[5:]
assert first_five not in [rest[i:i+5] for i in range(min(50, len(rest)-5))], \
    f"First 5 words still repeat in deduped output: {first_five}"
print("[OK] scan_single: within-chunk dedup removed ASR repetition")

# Metadata fields
assert meta["channel_name"] == "Jocko Podcast"
assert meta["start"] == 2
assert meta["speaker"] == "unknown"
print("[OK] scan_single: metadata fields correct")

# Show compression stats
total_raw_words = sum(
    len(rec[2].split()) for rec in records
)
print(f"\nSample transcript stats:")
print(f"  Chunks: {len(records)}")
print(f"  Total tokens (after dedup): {total_raw_words:,}")
print(f"\nFirst chunk preview:")
print(f"  {records[0][2][:120]}...")
print(f"\nSecond chunk preview:")
print(f"  {records[1][2][:120]}...")

print("\n=== Step 2 verification PASSED ===")
