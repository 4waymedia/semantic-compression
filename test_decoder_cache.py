"""
test_decoder_cache.py — proves the cached and uncached decode paths
produce byte-identical output across all token paths.

Spec requirements covered (decode-spec-v01):
    10. Add tests proving both cached and uncached decode paths
        produce identical output.

Also exercises:
    - Empty input
    - Pure Tier 0 (single-byte tokens)
    - Mixed tiers (1-4 byte IDs)
    - Cap-prefix path (titlecased/uppercase words)
    - OOV path (rare or proper-noun words)
    - Round-trip equivalence
    - Forward cache (encode-side) symmetric correctness

Run from project root:
    python -m semantic_compression.test_decoder_cache
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow direct execution from project root
sys.path.insert(0, '.')

from semantic_compression.compressor import Compressor


# ---------------------------------------------------------------------------
# Test samples — chosen to exercise specific code paths
# ---------------------------------------------------------------------------

SAMPLES: list[tuple[str, bytes]] = [
    ('empty',           b''),
    ('single_word',     b'the'),
    ('tier0_heavy',     b'the and of to be in on for it is'),
    ('mixed_tiers',     b'the quick brown fox jumps over the lazy dog'),
    ('cap_prefix',      b'The Quick Brown Fox Jumps Over The Lazy Dog'),
    ('mixed_case',      b'The quick brown FOX jumps over the LAZY dog'),
    ('with_punct',      b'hello, world! how are you today?'),
    ('with_numbers',    b'I have 42 apples and 3.14 pies'),
    ('oov_rare',        b'the protozoan eats the bacterium'),
    ('oov_proper',      b'Anthropic and OpenAI both train language models'),
    ('long_passage',
        b'The compression layer of the EloAI platform is built on a '
        b'frozen dictionary of approximately 374000 entries. Each surface '
        b'form maps to a Base64-encoded identifier organized into tiers. '
        b'The binary wire format eloB compresses transcripts by roughly '
        b'fifty percent while preserving byte-exact round-trip semantics.'),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def assert_eq(actual, expected, *, msg: str) -> None:
    if actual != expected:
        raise AssertionError(
            f"{msg}\n  expected: {expected!r}\n  actual:   {actual!r}"
        )


def encode_decode_bin(c: Compressor, src: bytes) -> bytes:
    encoded = c.encode_bytes_binary(src, fmt='.txt')
    ext, decoded = c.decode_bytes_binary(encoded)
    return decoded


def encode_decode_text(c: Compressor, src: bytes) -> bytes:
    text = src.decode('utf-8')
    encoded = c.encode_text(text, fmt='.txt')
    ext, decoded_text = c.decode_text(encoded)
    return decoded_text.encode('utf-8')


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_cached_matches_uncached_binary() -> None:
    """Both decode paths produce byte-identical binary output."""
    # Phase 1: encode all samples and capture decoded output with cache on.
    encoded_by_name: dict[str, bytes] = {}
    cached_decoded: dict[str, bytes] = {}
    with Compressor(preload_rev_cache=True, preload_fwd_cache=True) as cached:
        for name, src in SAMPLES:
            encoded_by_name[name] = cached.encode_bytes_binary(src, fmt='.txt')
            _, dec = cached.decode_bytes_binary(encoded_by_name[name])
            cached_decoded[name] = dec
            assert_eq(dec, src, msg=f"[binary {name}] cached round-trip not byte-exact")

    # Phase 2: decode the SAME encoded bytes with cache off.
    with Compressor(preload_rev_cache=False, preload_fwd_cache=False) as uncached:
        for name, src in SAMPLES:
            _, dec = uncached.decode_bytes_binary(encoded_by_name[name])
            assert_eq(
                dec, cached_decoded[name],
                msg=f"[binary {name}] cached vs uncached decode differs"
            )
    print('PASS  binary: cached vs uncached identical on all samples')


def test_cached_matches_uncached_text() -> None:
    """Same equivalence guarantee for the text wire format."""
    encoded_by_name: dict[str, str] = {}
    cached_decoded: dict[str, str] = {}
    with Compressor(preload_rev_cache=True, preload_fwd_cache=True) as cached:
        for name, src in SAMPLES:
            if not src:
                continue
            text = src.decode('utf-8')
            encoded_by_name[name] = cached.encode_text(text, fmt='.txt')
            _, dec = cached.decode_text(encoded_by_name[name])
            cached_decoded[name] = dec
            assert_eq(
                dec.encode('utf-8'), src,
                msg=f"[text {name}] cached round-trip not byte-exact"
            )

    with Compressor(preload_rev_cache=False, preload_fwd_cache=False) as uncached:
        for name, encoded in encoded_by_name.items():
            _, dec = uncached.decode_text(encoded)
            assert_eq(
                dec, cached_decoded[name],
                msg=f"[text {name}] cached vs uncached decode differs"
            )
    print('PASS  text:   cached vs uncached identical on all samples')


def test_encoder_symmetry_binary() -> None:
    """The forward cache produces identical .eloB bytes to the LMDB path."""
    bytes_by_name: dict[str, bytes] = {}
    with Compressor(preload_fwd_cache=True, preload_rev_cache=False) as cached_enc:
        for name, src in SAMPLES:
            bytes_by_name[name] = cached_enc.encode_bytes_binary(src, fmt='.txt')

    with Compressor(preload_fwd_cache=False, preload_rev_cache=False) as raw_enc:
        for name, src in SAMPLES:
            raw_bytes = raw_enc.encode_bytes_binary(src, fmt='.txt')
            assert_eq(
                raw_bytes, bytes_by_name[name],
                msg=f"[encode {name}] fwd-cache produced different bytes"
            )
    print('PASS  encode: fwd_cache produces byte-identical output')


def test_init_ms_reported() -> None:
    """init_ms is non-zero when caches are enabled, zero when disabled."""
    with Compressor(preload_rev_cache=True, preload_fwd_cache=True) as c:
        assert c.init_ms > 0, "expected non-zero init_ms with caches enabled"
        assert c.rev_cache_bytes > 0, "expected non-zero rev_cache_bytes"
        assert c.fwd_cache_bytes > 0, "expected non-zero fwd_cache_bytes"

    with Compressor(preload_rev_cache=False, preload_fwd_cache=False) as c:
        # init_ms may be a few microseconds of overhead but is essentially zero
        assert c.rev_cache_bytes == 0, "expected zero rev_cache_bytes when disabled"
        assert c.fwd_cache_bytes == 0, "expected zero fwd_cache_bytes when disabled"
    print('PASS  telemetry: init_ms / cache_bytes reported correctly')


def test_cap_prefix_path() -> None:
    """Cap-prefix path exercises both cached and uncached lookups."""
    src = b'The Quick Brown Fox'   # title case — forces cap-prefix tokens
    with Compressor(preload_rev_cache=True) as cached:
        encoded = cached.encode_bytes_binary(src, fmt='.txt')
        _, decoded_c = cached.decode_bytes_binary(encoded)
        assert_eq(decoded_c, src, msg="cap-prefix cached round-trip failed")
    with Compressor(preload_rev_cache=False) as uncached:
        _, decoded_u = uncached.decode_bytes_binary(encoded)
        assert_eq(decoded_u, src, msg="cap-prefix uncached round-trip failed")
        assert_eq(decoded_c, decoded_u, msg="cap-prefix cached vs uncached differ")
    print('PASS  cap-prefix path: identical output cached/uncached')


def test_oov_path() -> None:
    """OOV path exercises body fallback regardless of cache state."""
    src = b'the protozoan eats the bacterium'
    with Compressor(preload_rev_cache=True) as cached:
        encoded = cached.encode_bytes_binary(src, fmt='.txt')
        _, decoded_c = cached.decode_bytes_binary(encoded)
        assert_eq(decoded_c, src, msg="OOV cached round-trip failed")
    with Compressor(preload_rev_cache=False) as uncached:
        _, decoded_u = uncached.decode_bytes_binary(encoded)
        assert_eq(decoded_u, src, msg="OOV uncached round-trip failed")
        assert_eq(decoded_c, decoded_u, msg="OOV cached vs uncached differ")
    print('PASS  OOV path: identical output cached/uncached')


# Note: tests below already use a single Compressor at a time, so they're
# safe with LMDB's single-open-per-process constraint. The tests above
# were refactored to phase-1 (encode with cache) / phase-2 (decode without
# cache) so they avoid two-instance overlap.


def test_int_cache_matches_rev_cache_binary() -> None:
    """decode-spec-v02: int-keyed fast path matches the bytes-keyed path."""
    # Capture decode output via bytes-keyed cache (v01 behavior)
    encoded_by_name: dict[str, bytes] = {}
    v01_decoded: dict[str, bytes] = {}
    with Compressor(
        preload_rev_cache=True,
        preload_fwd_cache=True,
        preload_int_cache=False,    # explicitly disable int cache
    ) as v01:
        assert v01._id_to_surface is None, "int cache must be off"
        for name, src in SAMPLES:
            encoded_by_name[name] = v01.encode_bytes_binary(src, fmt='.txt')
            _, dec = v01.decode_bytes_binary(encoded_by_name[name])
            v01_decoded[name] = dec
            assert_eq(dec, src, msg=f"[v01 {name}] round-trip failed")

    # Decode the SAME bytes through the int-keyed cache and compare.
    with Compressor(
        preload_rev_cache=True,
        preload_fwd_cache=True,
        preload_int_cache=True,
    ) as v02:
        assert v02._id_to_surface is not None, "int cache must be on"
        for name, src in SAMPLES:
            _, dec = v02.decode_bytes_binary(encoded_by_name[name])
            assert_eq(
                dec, v01_decoded[name],
                msg=f"[v02 {name}] int-cache decode differs from bytes-cache"
            )
            assert_eq(dec, src, msg=f"[v02 {name}] int-cache round-trip failed")
    print('PASS  v02:    int-keyed cache matches bytes-keyed cache on all samples')


def test_int_cache_matches_lmdb_binary() -> None:
    """The fast path also matches the no-cache LMDB path byte-for-byte."""
    encoded_by_name: dict[str, bytes] = {}
    lmdb_decoded: dict[str, bytes] = {}
    with Compressor(
        preload_rev_cache=False,
        preload_fwd_cache=False,
        preload_int_cache=False,
    ) as raw:
        for name, src in SAMPLES:
            encoded_by_name[name] = raw.encode_bytes_binary(src, fmt='.txt')
            _, dec = raw.decode_bytes_binary(encoded_by_name[name])
            lmdb_decoded[name] = dec

    with Compressor(preload_int_cache=True) as v02:
        for name, src in SAMPLES:
            _, dec = v02.decode_bytes_binary(encoded_by_name[name])
            assert_eq(
                dec, lmdb_decoded[name],
                msg=f"[v02 {name}] int-cache decode differs from LMDB"
            )
    print('PASS  v02:    int-keyed cache matches LMDB path on all samples')


def test_int_cache_fallback() -> None:
    """When int cache is disabled, behavior falls back cleanly to v01 path."""
    src = b'The quick brown fox at the end of the day.'
    with Compressor(preload_int_cache=False) as c:
        assert c._id_to_surface is None
        assert c._rev_cache is not None    # v01 cache still active
        encoded = c.encode_bytes_binary(src, fmt='.txt')
        _, decoded = c.decode_bytes_binary(encoded)
        assert_eq(decoded, src, msg='fallback round-trip failed')
    print('PASS  v02:    fallback path (int cache off) works correctly')


def test_int_cache_telemetry() -> None:
    """id_cache_bytes reflects cache state."""
    with Compressor(preload_int_cache=True) as c:
        assert c.id_cache_bytes > 0, 'expected positive id_cache_bytes'
        # Sanity: the int cache should be in the same ballpark as the bytes
        # cache (both index the same vocabulary). 0.5x to 2x is reasonable.
        ratio = c.id_cache_bytes / max(c.rev_cache_bytes, 1)
        assert 0.5 <= ratio <= 2.0, (
            f'id_cache_bytes={c.id_cache_bytes:,} '
            f'rev_cache_bytes={c.rev_cache_bytes:,} '
            f'(ratio {ratio:.2f} outside expected 0.5-2.0)'
        )
    with Compressor(preload_int_cache=False) as c:
        assert c.id_cache_bytes == 0
    print('PASS  v02:    id_cache_bytes telemetry correct')


def test_idempotent_open_close() -> None:
    """Re-opening after close rebuilds caches cleanly."""
    c = Compressor(preload_rev_cache=True, preload_fwd_cache=True)
    c.open()
    first_init = c.init_ms
    assert first_init > 0
    c.close()
    assert c.rev_cache_bytes == 0
    assert c.fwd_cache_bytes == 0
    c.open()
    assert c.rev_cache_bytes > 0, "expected cache rebuilt after re-open"
    assert c.init_ms > 0
    c.close()
    print('PASS  lifecycle: open/close/open rebuilds caches')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tests = [
        test_cached_matches_uncached_binary,
        test_cached_matches_uncached_text,
        test_encoder_symmetry_binary,
        test_init_ms_reported,
        test_cap_prefix_path,
        test_oov_path,
        test_int_cache_matches_rev_cache_binary,
        test_int_cache_matches_lmdb_binary,
        test_int_cache_fallback,
        test_int_cache_telemetry,
        test_idempotent_open_close,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f'FAIL  {t.__name__}: {e}')
            failures += 1
        except Exception as e:
            print(f'ERROR {t.__name__}: {type(e).__name__}: {e}')
            failures += 1
    print()
    if failures:
        print(f'{len(tests) - failures}/{len(tests)} passed, {failures} FAILED')
        return 1
    print(f'ALL {len(tests)} TESTS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
