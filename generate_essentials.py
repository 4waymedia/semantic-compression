"""Generate data/essentials-v1.csv from config.PRIMITIVES.

This is the "fifth data artifact" — the frozen mapping of all Tier 0
primitives (SYSTEM + WORD + STRUCTURAL IDs) to their Base64 char IDs,
surface forms, corpus frequencies, and integer LLM ranking.

Consumers should use this CSV rather than importing config.py directly,
to avoid hard-coding Python as the sole source of truth for the 58
Tier 0 primitives.
"""
import csv, gzip, sys
sys.path.insert(0, '.')

from config import WORD_IDS, STRUCTURAL_IDS, SYSTEM_IDS, PRIMITIVES, STREAM_ENCODING

# Merge all primitives into one dict: char_id -> surface
primitive_map = {
    **SYSTEM_IDS,      # e.g. '0': 'STREAM_START'
    **WORD_IDS,        # e.g. 'T': 'the'
    **STRUCTURAL_IDS,  # e.g. 'g': ' '
}
assert len(primitive_map) == 58, f"Expected 58 primitives, got {len(primitive_map)}"
assert len(PRIMITIVES) == 58

# Load word frequencies inline (avoids importing dictionary_builder_v03 which pulls lmdb)
wf = {}
with open('data/word_frequencies.txt', 'r', encoding=STREAM_ENCODING) as f:
    for line in f:
        if line.startswith('#') or not line.strip():
            continue
        count_str, escaped = line.rstrip('\n').split('\t', 1)
        token = escaped.encode('ascii').decode('unicode_escape')
        wf[token] = int(count_str)

# Load existing CSV to get integer IDs (populated after rebuild)
integer_id_of = {}
csv_path = 'data/token-ids-v1.csv.gz'
try:
    with gzip.open(csv_path, 'rt', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            integer_id_of[row[2]] = int(row[0])
except FileNotFoundError:
    pass

# Categories in display order
categories = [
    ('system', SYSTEM_IDS),
    ('word', WORD_IDS),
    ('structural', STRUCTURAL_IDS),
]

out_path = 'data/essentials-v1.csv'
with open(out_path, 'w', encoding='utf-8', newline='') as f:
    writer = csv.writer(f)
    writer.writerow([
        'char_id', 'surface', 'category', 'freq',
        'integer_id', 'profile_tiny', 'profile_compact',
        'profile_standard', 'profile_full', 'profile_reference',
    ])

    for cat, mapping in categories:
        for char_id, surface in mapping.items():
            freq = wf.get(surface, 0)
            int_id = integer_id_of.get(surface, '')
            writer.writerow([char_id, surface, cat, freq, int_id,
                           'Y', 'Y', 'Y', 'Y', 'Y'])

print(f"Wrote {len(primitive_map)} primitives to {out_path}")
print(f"  System:     {len(SYSTEM_IDS)}")
print(f"  Words:      {len(WORD_IDS)}")
print(f"  Structural: {len(STRUCTURAL_IDS)}")
with_ids = sum(1 for s in primitive_map.values() if s in integer_id_of)
print(f"  With integer IDs (from CSV): {with_ids}")
print(f"  Without integer IDs: {len(primitive_map) - with_ids}")
if len(primitive_map) - with_ids == len(primitive_map):
    print("  (Run dictionary_builder_v03.py rebuild first to populate integer IDs)")