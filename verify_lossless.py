"""
verify_lossless.py -- proof-of-losslessness across all 10 v1 file formats

Wires the existing pieces together inline (no Step 9 compressor yet) and
runs decode(encode(file_bytes)) == file_bytes on every sample file.

Pieces under test:
    format_adapters    read_file  / write_file
    tokenizer          tokenize   (universal char-class)
    dictionary.lmdb    forward + reverse lookup
    caps_codec         encode_oov / decode_oov / encode_caps / decode_caps

Stream format proven here:
    in-vocab lowercase token:  <ID>
    in-vocab cased token:      <cap>:<ID>             (cap is base64 chars, no ':')
    OOV token:                 OOV:<cap>:<lowered>    (caps_codec.encode_oov)
    tokens separated by:       '|'  (PIPE_BYTE)

Round-trip is verified at the BYTE level, not just the text level.

If this passes for all 10 sample files, the foundation is byte-exact lossless
and Step 9 (compressor.py) is just packaging.
"""

import sys
from pathlib import Path

sys.path.insert(0, '.')

import lmdb

from semantic_compression.config import STREAM_ENCODING
from semantic_compression.caps_codec import (
    encode_caps, decode_caps,
    encode_oov, decode_oov, is_oov_token,
    OOV_SENTINEL, OOV_SEP,
)
from semantic_compression.tokenizer import tokenize
from semantic_compression.format_adapters import read_file

LMDB_PATH = Path('semantic_compression/db/dictionary.lmdb')

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

PIPE = '|'


# ---------------------------------------------------------------------------
# Inline minimal compressor (will become compressor.py in Step 9)
# ---------------------------------------------------------------------------

class _Stats:
    def __init__(self):
        self.tier_hits  = {'tier0': 0, 'tier1': 0, 'tier2': 0, 'tier3': 0, 'oov': 0, 'cap_prefix': 0}
        self.total_tokens = 0


def _encode_token(token: str, txn, fwd_db, stats: _Stats) -> str:
    """Encode one source token into its stream representation."""
    stats.total_tokens += 1

    # Try in-vocab lookup, case-folded
    lower = token.lower()
    bytes_id = txn.get(lower.encode(STREAM_ENCODING), db=fwd_db)

    if bytes_id is not None:
        token_id = bytes_id.decode(STREAM_ENCODING)

        # Count tier
        n = len(token_id)
        if   n == 1: stats.tier_hits['tier0'] += 1
        elif n == 2: stats.tier_hits['tier1'] += 1
        elif n == 3: stats.tier_hits['tier2'] += 1
        elif n == 4: stats.tier_hits['tier3'] += 1

        if token == lower:
            return token_id   # fast path: no case info needed

        # Build cap prefix
        _, cap = encode_caps(token)
        if cap == 'A':   # all-lowercase fast path inside encode_caps
            return token_id   # shouldn't happen since token != lower, but safe
        stats.tier_hits['cap_prefix'] += 1
        return f"{cap}{OOV_SEP}{token_id}"

    # Not in dictionary — OOV
    stats.tier_hits['oov'] += 1
    return encode_oov(token)


def _decode_token(stream_token: str, txn, rev_db) -> str:
    """Decode one stream token back to its source string."""
    # OOV path
    if is_oov_token(stream_token):
        return decode_oov(stream_token)

    # In-vocab — may have cap prefix
    if OOV_SEP in stream_token:
        cap, token_id = stream_token.split(OOV_SEP, 1)
        bytes_word = txn.get(token_id.encode(STREAM_ENCODING), db=rev_db)
        if bytes_word is None:
            raise ValueError(f"Unknown ID after cap prefix: {token_id!r}")
        word = bytes_word.decode(STREAM_ENCODING)
        return decode_caps(word, cap)

    # No cap prefix — direct lookup
    bytes_word = txn.get(stream_token.encode(STREAM_ENCODING), db=rev_db)
    if bytes_word is None:
        raise ValueError(f"Unknown stream token: {stream_token!r}")
    return bytes_word.decode(STREAM_ENCODING)


def encode_text(text: str, env, stats: _Stats) -> str:
    """Compress text → stream string."""
    fwd_db = env.open_db(b'forward')
    parts = []
    with env.begin() as txn:
        for tok in tokenize(text):
            parts.append(_encode_token(tok, txn, fwd_db, stats))
    return PIPE.join(parts)


def decode_text(stream: str, env) -> str:
    """Decompress stream → text."""
    rev_db = env.open_db(b'reverse')
    out = []
    with env.begin() as txn:
        for st in stream.split(PIPE):
            out.append(_decode_token(st, txn, rev_db))
    return ''.join(out)


# ---------------------------------------------------------------------------
# Run round-trip on each sample
# ---------------------------------------------------------------------------

def run():
    if not LMDB_PATH.exists():
        print(f"ERROR: dictionary.lmdb not found at {LMDB_PATH}")
        print("       Run dictionary_builder first.")
        sys.exit(1)

    env = lmdb.open(str(LMDB_PATH), readonly=True, max_dbs=2)

    overall_pass = True
    total_original = 0
    total_stream   = 0

    print(f"{'FILE':<22} {'BYTES':>7} {'STREAM':>8} {'RATIO':>6}  {'TIER0/1/2/3':<18} {'CAP':>4} {'OOV':>4}  STATUS")
    print('-' * 92)

    for path_str in SAMPLES:
        path = Path(path_str)
        if not path.exists():
            print(f"{path.name:<22}   MISSING")
            overall_pass = False
            continue

        original_bytes = path.read_bytes()
        fmt, text = read_file(path)

        # Sanity: adapter must already be byte-exact (Step 7 result)
        if text.encode(STREAM_ENCODING) != original_bytes:
            print(f"{path.name:<22}   FAIL  adapter not byte-exact")
            overall_pass = False
            continue

        stats = _Stats()

        # Encode
        try:
            stream = encode_text(text, env, stats)
        except Exception as e:
            print(f"{path.name:<22}   FAIL  encode error: {e}")
            overall_pass = False
            continue

        # Decode
        try:
            recovered = decode_text(stream, env)
        except Exception as e:
            print(f"{path.name:<22}   FAIL  decode error: {e}")
            overall_pass = False
            continue

        # Byte-exact comparison
        recovered_bytes = recovered.encode(STREAM_ENCODING)
        if recovered_bytes != original_bytes:
            # Find divergence point for diagnostics
            n = min(len(recovered_bytes), len(original_bytes))
            diff_at = next(
                (i for i in range(n) if recovered_bytes[i] != original_bytes[i]),
                n,
            )
            print(
                f"{path.name:<22}   FAIL  byte mismatch at offset {diff_at}\n"
                f"  original  ...{original_bytes[max(0, diff_at-8):diff_at+8]!r}...\n"
                f"  recovered ...{recovered_bytes[max(0, diff_at-8):diff_at+8]!r}..."
            )
            overall_pass = False
            continue

        # Success — report stats
        stream_bytes = len(stream.encode(STREAM_ENCODING))
        ratio = len(original_bytes) / stream_bytes if stream_bytes else float('inf')
        tier_str = (
            f"{stats.tier_hits['tier0']}/"
            f"{stats.tier_hits['tier1']}/"
            f"{stats.tier_hits['tier2']}/"
            f"{stats.tier_hits['tier3']}"
        )
        print(
            f"{path.name:<22} {len(original_bytes):>7} {stream_bytes:>8} "
            f"{ratio:>5.2f}x  {tier_str:<18} "
            f"{stats.tier_hits['cap_prefix']:>4} {stats.tier_hits['oov']:>4}  PASS"
        )
        total_original += len(original_bytes)
        total_stream   += stream_bytes

    env.close()

    print('-' * 92)
    if overall_pass:
        overall_ratio = total_original / total_stream if total_stream else float('inf')
        print(
            f"ALL 10 FORMATS PASS BYTE-EXACT ROUND-TRIP  "
            f"(combined ratio: {overall_ratio:.2f}x  "
            f"{total_original:,} -> {total_stream:,} bytes)"
        )
        print()
        print("Foundation is provably lossless. Step 9 compressor is now packaging only.")
        return 0
    else:
        print("FAILURES DETECTED -- foundation needs fixes before Step 9")
        return 1


if __name__ == '__main__':
    sys.exit(run())
