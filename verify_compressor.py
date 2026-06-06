"""
Step 9 verification -- compressor.py

Tests:
  1. Module-level encode_text / decode_text round-trip
  2. Compressor class context manager + encode_bytes / decode_bytes
  3. encode_file / decode_file with auto-derived dst paths
  4. encode_file / decode_file with explicit dst paths
  5. .elo file header is correct format and parseable
  6. CLI verify command on all 10 v1 samples
  7. Round-trip every sample at the BYTE level (the v1 acceptance gate)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')

from semantic_compression.compressor import (
    Compressor, EncodeStats,
    encode_text, decode_text, encode_file, decode_file,
    ELO_MAGIC, ELO_DELIMITER, ELO_EXTENSION,
)
from semantic_compression.config import FORMAT_VERSION
from semantic_compression.format_adapters import detect_format

SAMPLES = [
    'semantic_compression/samples/plain.txt',
    'semantic_compression/samples/readme.md',
    'semantic_compression/samples/config.json',
    'semantic_compression/samples/contacts.csv',
    'semantic_compression/samples/feed.xml',
    'semantic_compression/samples/page.html',
    'semantic_compression/samples/service.yaml',
    'semantic_compression/samples/server.log',
    'semantic_compression/samples/episode.srt',
    'semantic_compression/samples/episode.vtt',
]


# ---------------------------------------------------------------------------
# 1. Module-level text round-trip
# ---------------------------------------------------------------------------
print('=== Module-level encode_text / decode_text ===')
TEST_TEXTS = [
    ('Hello, world!',                              '.txt'),
    ('I said "don\'t do that!"',                   '.txt'),
    ('{"name": "Alice", "age": 30}',               '.json'),
    ('  line1\n  line2\n\n  line3',                '.txt'),
    ('',                                            '.txt'),
    ('a',                                           '.txt'),
]
for text, fmt in TEST_TEXTS:
    elo = encode_text(text, fmt=fmt)
    rec_fmt, rec_text = decode_text(elo)
    assert rec_fmt == fmt, f"fmt mismatch: {rec_fmt!r} vs {fmt!r}"
    assert rec_text == text, f"text mismatch for {text[:40]!r}"
    # Header check
    parts = elo.split(ELO_DELIMITER, 3)
    assert parts[0] == ELO_MAGIC
    assert int(parts[1]) == FORMAT_VERSION
    assert '.' + parts[2] == fmt
print(f'[OK] {len(TEST_TEXTS)} text round-trips byte-exact (including empty + single-char)')


# ---------------------------------------------------------------------------
# 2. Compressor class context manager + bytes API
# ---------------------------------------------------------------------------
print()
print('=== Compressor class + bytes API ===')
with Compressor() as c:
    test_bytes = b'Compressor round-trip via byte API.\n'
    elo = c.encode_bytes(test_bytes, fmt='.txt')
    assert isinstance(elo, bytes)
    fmt, recovered = c.decode_bytes(elo)
    assert fmt == '.txt' and recovered == test_bytes
print('[OK] Compressor context manager + encode_bytes / decode_bytes')


# ---------------------------------------------------------------------------
# 3. encode_file / decode_file -- auto-derived dst paths
# ---------------------------------------------------------------------------
print()
print('=== File API: auto-derived destination ===')
with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    for path_str in SAMPLES:
        src = Path(path_str)
        # Copy source into temp dir so encode/decode produces siblings there
        src_copy = tmp / src.name
        src_copy.write_bytes(src.read_bytes())

        elo_path = encode_file(src_copy)
        assert elo_path.suffix == ELO_EXTENSION
        assert elo_path.exists()

        # Move the original aside so decode writes to a clean path
        src_copy.unlink()
        recovered_path = decode_file(elo_path)
        # decode_file should restore something like 'plain.txt' (it strips
        # .elo, then resets the suffix to the recorded format).
        recovered_bytes = recovered_path.read_bytes()
        original_bytes  = src.read_bytes()
        assert recovered_bytes == original_bytes, \
            f"{src.name}: byte mismatch after file round-trip"
print(f'[OK] {len(SAMPLES)} files round-trip via encode_file / decode_file')


# ---------------------------------------------------------------------------
# 4. Explicit dst paths
# ---------------------------------------------------------------------------
print()
print('=== File API: explicit destination ===')
with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    src = Path(SAMPLES[2])  # config.json
    elo_out = tmp / 'custom.elo'
    rec_out = tmp / 'restored.json'
    encode_file(src, elo_out)
    assert elo_out.exists()
    decode_file(elo_out, rec_out)
    assert rec_out.read_bytes() == src.read_bytes()
print('[OK] encode_file/decode_file honour explicit dst paths')


# ---------------------------------------------------------------------------
# 5. Header format
# ---------------------------------------------------------------------------
print()
print('=== .elo header format ===')
elo = encode_text('hello', fmt='.txt')
assert elo.startswith(f'{ELO_MAGIC}{ELO_DELIMITER}{FORMAT_VERSION}{ELO_DELIMITER}txt{ELO_DELIMITER}'), \
    f"Header format wrong: {elo[:30]!r}"
print(f'[OK] Header format: "{ELO_MAGIC}|{FORMAT_VERSION}|<ext>|<stream>"')


# ---------------------------------------------------------------------------
# 6. CLI verify subcommand
# ---------------------------------------------------------------------------
print()
print('=== CLI: compressor verify on 10 v1 samples ===')
from semantic_compression.compressor import main as cli_main
rc = cli_main(['verify'])
assert rc == 0, f"CLI verify exit code = {rc}"
print('[OK] CLI verify returns 0')


# ---------------------------------------------------------------------------
# 7. Byte-exact acceptance gate
# ---------------------------------------------------------------------------
print()
print('=== v1 ACCEPTANCE GATE: 10/10 samples byte-exact ===')
pass_count = 0
with Compressor() as c:
    for path_str in SAMPLES:
        path = Path(path_str)
        original = path.read_bytes()
        fmt = detect_format(path)
        elo = c.encode_bytes(original, fmt=fmt)
        rec_fmt, recovered = c.decode_bytes(elo)
        assert rec_fmt == fmt, f"{path.name}: fmt mismatch"
        assert recovered == original, f"{path.name}: byte mismatch"
        pass_count += 1
        print(f'  PASS  {path.name:<22} {len(original):>5} -> {len(elo):>5} bytes')

print()
print(f'=== Step 9 verification PASSED ({pass_count}/{len(SAMPLES)} byte-exact) ===')
