"""
Step 7 verification -- format_adapters.py

Tests:
  1. detect_format on all 10 supported extensions + aliases
  2. Unsupported extension raises
  3. Adapter registry has one entry per canonical format
  4. Sample file byte-exact round-trip:
        write(adapter.read(p)) == original bytes
  5. Integration with the universal tokenizer:
        tokenize(read(p)) joined back == read(p) for each sample
"""
import sys
sys.path.insert(0, '.')

from pathlib import Path

from semantic_compression.format_adapters import (
    SUPPORTED_FORMATS, detect_format, get_adapter, read_file,
    write_file, round_trip_bytes, register_adapter, UTF8Adapter,
)
from semantic_compression.tokenizer import tokenize

SAMPLES_DIR = Path('semantic_compression/samples')

SAMPLE_FILES = {
    '.txt':  SAMPLES_DIR / 'plain.txt',
    '.md':   SAMPLES_DIR / 'readme.md',
    '.json': SAMPLES_DIR / 'config.json',
    '.csv':  SAMPLES_DIR / 'contacts.csv',
    '.xml':  SAMPLES_DIR / 'feed.xml',
    '.html': SAMPLES_DIR / 'page.html',
    '.yaml': SAMPLES_DIR / 'service.yaml',
    '.log':  SAMPLES_DIR / 'server.log',
    '.srt':  SAMPLES_DIR / 'episode.srt',
    '.vtt':  SAMPLES_DIR / 'episode.vtt',
}

# ---------------------------------------------------------------------------
# 1. detect_format
# ---------------------------------------------------------------------------
print('=== detect_format ===')
for ext in ['.txt', '.md', '.JSON', '.csv', '.xml', '.HTML', '.htm', '.yml', '.yaml', '.log', '.srt', '.vtt']:
    fmt = detect_format(f'foo{ext}')
    print(f"  foo{ext:<7} -> {fmt}")
# Aliases must canonicalise
assert detect_format('a.htm') == '.html', 'htm should alias to html'
assert detect_format('a.yml') == '.yaml', 'yml should alias to yaml'
assert detect_format('A.HTML') == '.html', 'case insensitive'
print('[OK] detect_format: case-insensitive + alias normalisation correct')

# Unsupported
try:
    detect_format('something.pdf')
    raise AssertionError('PDF should not be supported')
except ValueError:
    print('[OK] unsupported extension (.pdf) raises ValueError')

try:
    detect_format('no_extension')
    raise AssertionError('Missing extension should raise')
except ValueError:
    print('[OK] missing extension raises ValueError')


# ---------------------------------------------------------------------------
# 2. Registry sanity
# ---------------------------------------------------------------------------
print()
print('=== Adapter registry ===')
for ext in SAMPLE_FILES:
    a = get_adapter(ext)
    assert a is not None
    assert isinstance(a, UTF8Adapter)
print(f'[OK] All 10 v1 formats route to UTF8Adapter (currently)')


# ---------------------------------------------------------------------------
# 3. Sample file byte-exact round-trip
# ---------------------------------------------------------------------------
print()
print('=== Sample file round-trip ===')
for ext, path in SAMPLE_FILES.items():
    assert path.exists(), f'Sample missing: {path}'
    original_bytes = path.read_bytes()
    fmt, text = read_file(path)
    assert fmt == ext, f'detect_format mismatch on {path}'
    # Re-encode and compare bytes
    rewritten = text.encode('utf-8')
    assert rewritten == original_bytes, (
        f'Byte round-trip FAILED for {ext}\n'
        f'  original len: {len(original_bytes)}\n'
        f'  rewritten len: {len(rewritten)}'
    )
    print(f'[OK] {ext:<6} {path.name:<22} {len(original_bytes):>5} bytes  byte-exact round-trip')


# ---------------------------------------------------------------------------
# 4. round_trip_bytes() helper
# ---------------------------------------------------------------------------
print()
print('=== round_trip_bytes() helper ===')
for ext, path in SAMPLE_FILES.items():
    assert round_trip_bytes(path), f'{ext} round_trip_bytes failed'
print('[OK] round_trip_bytes returns True for all 10 samples')


# ---------------------------------------------------------------------------
# 5. Tokenizer + adapter integration
# ---------------------------------------------------------------------------
print()
print('=== Tokenizer integration ===')
for ext, path in SAMPLE_FILES.items():
    _, text = read_file(path)
    tokens = tokenize(text)
    rebuilt = ''.join(tokens)
    assert rebuilt == text, f'{ext} tokenizer round-trip failed'
    # Also verify rebuilt-text -> bytes matches original
    assert rebuilt.encode('utf-8') == path.read_bytes(), f'{ext} byte mismatch after tokenize'
    print(f'[OK] {ext:<6} {len(tokens):>4} tokens   tokenize+adapter byte-exact')


# ---------------------------------------------------------------------------
# 6. write_file produces byte-identical output
# ---------------------------------------------------------------------------
print()
print('=== write_file via adapter ===')
tmp_dir = SAMPLES_DIR / '_roundtrip'
tmp_dir.mkdir(exist_ok=True)
try:
    for ext, path in SAMPLE_FILES.items():
        _, text = read_file(path)
        out_path = tmp_dir / path.name
        write_file(out_path, text)
        assert out_path.read_bytes() == path.read_bytes(), f'{ext} write_file byte mismatch'
        out_path.unlink()
    print('[OK] write_file produces byte-identical files for all 10 formats')
finally:
    if tmp_dir.exists():
        # Remove any stragglers
        for p in tmp_dir.iterdir():
            p.unlink()
        tmp_dir.rmdir()


print()
print('=== Step 7 verification PASSED ===')
