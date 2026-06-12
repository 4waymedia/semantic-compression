"""
compressor.py -- Step 9 of System 1

Public encode / decode API for the .elo lossless compression format.

This module packages the encode / decode logic already proven byte-exact
in verify_lossless.py into a stable public API with file headers, file I/O,
and a CLI entry point. No design changes vs. the proof.

PUBLIC API
----------
    class Compressor (lmdb_path=...):
        encode_text(text, fmt)       -> str            (header + stream)
        decode_text(elo_text)        -> (fmt, text)
        encode_bytes(src_bytes, fmt) -> bytes          (header + stream as utf-8)
        decode_bytes(elo_bytes)      -> (fmt, src_bytes)
        encode_file(src, dst=None)   -> Path
        decode_file(src, dst=None)   -> Path

    Module-level convenience:
        encode_file / decode_file / encode_text / decode_text

.elo FILE HEADER
----------------
    Single line text prefix, '|' delimited:

        ELO|<format_version>|<source_ext>|<stream>

    Example:
        ELO|1|json|gA|T|q|...|

    Parsing: split('|', 3) yields ['ELO', '<ver>', '<ext>', '<stream>'].
    The stream itself contains additional '|' separators but those are
    consumed only by the inner decode step.

    <source_ext> is the source file extension without the leading '.'
    (e.g. 'json', 'txt', 'md'). Used by decode_file to restore the
    original filename.

CLI
---
    python -m semantic_compression.compressor encode <input> [output]
    python -m semantic_compression.compressor decode <input.elo> [output]
    python -m semantic_compression.compressor verify  -- runs the 10-format
                                                         round-trip proof
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import lmdb

sys.path.insert(0, '.')

from semantic_compression.caps_codec import (
    encode_caps, decode_caps,
    encode_oov, decode_oov, is_oov_token,
    OOV_SEP,
)
from semantic_compression.config import (
    BASE64_CHARS, BASE64_INDEX,
    FORMAT_VERSION, STREAM_ENCODING,
)
from semantic_compression.format_adapters import (
    detect_format, get_adapter, read_file,
)
from semantic_compression.tokenizer import tokenize, classify, CLASS_WORD


# ---------------------------------------------------------------------------
# Implicit whitespace transform (Option 2)
#
# Single space (' ') is ~47% of all tokens in the YouTube corpus. Removing
# it from the stream when it sits between two word-class tokens cuts both
# the token count and per-token overhead substantially.
#
# Rule: drop a single-space token at position i iff
#       tokens[i] == ' ' AND
#       classify(tokens[i-1]) == WORD AND
#       classify(tokens[i+1]) == WORD
#
# Decoder restores by inserting ' ' between any two consecutive word tokens
# that are not already separated by a whitespace token in the stream.
#
# This transform sits BEFORE the token-by-token encoder so it composes
# cleanly with both the text-mode wire format and the binary wire format.
# ---------------------------------------------------------------------------

def _strip_implicit_spaces(tokens: list[str]) -> list[str]:
    """Remove single-space tokens between two word-class tokens."""
    n = len(tokens)
    if n < 3:
        return tokens
    out: list[str] = []
    for i, tok in enumerate(tokens):
        if (
            tok == ' '
            and 0 < i < n - 1
            and classify(tokens[i - 1]) == CLASS_WORD
            and classify(tokens[i + 1]) == CLASS_WORD
        ):
            continue
        out.append(tok)
    return out


def _restore_implicit_spaces(tokens: list[str]) -> list[str]:
    """Insert single space between consecutive word-class tokens."""
    if not tokens:
        return tokens
    out: list[str] = [tokens[0]]
    for tok in tokens[1:]:
        if classify(out[-1]) == CLASS_WORD and classify(tok) == CLASS_WORD:
            out.append(' ')
        out.append(tok)
    return out


# ---------------------------------------------------------------------------
# Longest-match phrase scan (v0.3 -- enables phrase atom IDs)
#
# After tokenization + implicit-space stripping, consecutive WORD tokens
# form a "word run". We greedily match the longest phrase from the
# dictionary that starts at each position in the run.
#
# Phrase keys in the dictionary are space-joined word sequences:
#     'you know'              (2-gram)
#     "i don't know"          (3-gram, apostrophe is an interior joiner)
#     'at the end of the day' (6-gram)
#
# A non-WORD token (whitespace, punctuation, structural) breaks the run;
# we look it up individually as before.
# ---------------------------------------------------------------------------

MAX_PHRASE_TOKENS = 9   # matches ngram_counter NGRAM_MAX


def _longest_match_scan(
    tokens: list[str],
    txn,
    fwd_db,
) -> list[str]:
    """
    Greedy longest-match scan against the v0.3 dictionary.

    Returns a list of "stream tokens" -- surface strings that each have
    a single ID in the dictionary. Multi-word phrases collapse into one
    entry; non-matching word runs decompose to individual word tokens;
    non-WORD tokens pass through unchanged.

    The caller then encodes each stream token via _encode_token.
    """
    out: list[str] = []
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]
        if classify(tok) != CLASS_WORD:
            out.append(tok)
            i += 1
            continue

        # Find extent of the consecutive word run
        j = i
        while j < n and classify(tokens[j]) == CLASS_WORD and (j - i) < MAX_PHRASE_TOKENS:
            j += 1

        # Try matching longest phrase first; fall back to shorter
        matched = False
        for span in range(j - i, 1, -1):
            candidate = ' '.join(tokens[i:i + span]).lower()
            if txn.get(candidate.encode(STREAM_ENCODING), db=fwd_db) is not None:
                out.append(' '.join(tokens[i:i + span]))
                i += span
                matched = True
                break
        if not matched:
            out.append(tok)
            i += 1
    return out


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ELO_MAGIC      = 'ELO'
ELO_DELIMITER  = '|'                                  # PIPE_BYTE = 0x7C
DEFAULT_LMDB   = Path('semantic_compression/db/dictionary.lmdb')
ELO_EXTENSION  = '.elo'

# -- Binary stream constants (Option 1 experiment) ---------------------------
ELO_MAGIC_BIN     = b'ELO'
ELO_BIN_VERSION   = 2
ELO_BIN_EXTENSION = '.eloB'

# Bytes view of BASE64_CHARS — lets the binary decoder build LMDB-key bytes
# without going through Python str. BASE64_CHARS_BYTES[idx:idx+1] gives the
# 1-byte slice for Tier 0; multi-byte IDs concatenate single-byte slices.
BASE64_CHARS_BYTES = BASE64_CHARS.encode('ascii')

# Binary stream tag encoding:
#   0x00-0x3F : Tier 0  -- byte value IS the Base64 char index (0-63)        (1 byte total)
#   0x40-0x7F : Tier 1  -- byte1 low 6 bits = first char index               (2 bytes total)
#                          byte2 = second char index
#   0x80-0xBF : Tier 2  -- byte1 low 6 bits = first char index               (3 bytes total)
#   0xC0-0xFD : Tier 3  -- byte1 low 6 bits = first char index               (4 bytes total)
#               (active first-char range g-z = base64 index 32-51)
#   0xFE      : CAP-PREFIX marker  [0xFE][cap_len][cap_chars...][next token]
#   0xFF      : OOV marker         [0xFF][cap_len][cap_chars...][body_len LE u16][body...]
TAG_T1     = 0x40
TAG_T2     = 0x80
TAG_T3     = 0xC0
TAG_CAP    = 0xFE
TAG_OOV    = 0xFF


# ---------------------------------------------------------------------------
# Stats (for diagnostics)
# ---------------------------------------------------------------------------

@dataclass
class EncodeStats:
    total_tokens: int = 0
    tier0: int        = 0
    tier1: int        = 0
    tier2: int        = 0
    tier3: int        = 0
    cap_prefix: int   = 0
    oov: int          = 0

    def __str__(self) -> str:
        return (
            f"tokens={self.total_tokens}  "
            f"T0={self.tier0} T1={self.tier1} T2={self.tier2} T3={self.tier3}  "
            f"cap={self.cap_prefix}  oov={self.oov}"
        )


# ---------------------------------------------------------------------------
# Compressor
# ---------------------------------------------------------------------------

class Compressor:
    """
    Stateful handle around the LMDB dictionary.

    Open once, reuse for many encode/decode operations. Thread-safety
    matches LMDB's: multiple readers via env.begin() are safe; writes
    are not part of this class.

    Designed as a context manager:
        with Compressor() as c:
            elo = c.encode_file(Path('source.json'))
    """

    def __init__(
        self,
        lmdb_path: Path | str = DEFAULT_LMDB,
        *,
        preload_rev_cache: bool = True,
        preload_fwd_cache: bool = True,
        preload_int_cache: bool = True,
    ) -> None:
        """
        Args:
            lmdb_path:           path to the dictionary LMDB env
            preload_rev_cache:   if True, build an in-memory copy of the
                                 reverse (ID -> surface) dictionary at open()
                                 time. Eliminates LMDB GETs on the decode
                                 hot path. ~4-6 MB RAM, 100-300 ms init.
            preload_fwd_cache:   same, for the forward (surface -> ID)
                                 dictionary used by the encode hot path.
            preload_int_cache:   if True (and preload_rev_cache=True), also
                                 build an int-keyed cache for the binary
                                 decoder fast path. Eliminates per-token
                                 bytes-object allocation. +~30 MB RAM on
                                 top of rev_cache; ~3x decode speedup.
        """
        self.lmdb_path = Path(lmdb_path)
        self._env: Optional[lmdb.Environment] = None
        self._fwd_db = None
        self._rev_db = None
        # Cache configuration
        self._preload_rev_cache = preload_rev_cache
        self._preload_fwd_cache = preload_fwd_cache
        self._preload_int_cache = preload_int_cache
        # Populated by open() when flags above are True
        self._rev_cache: Optional[dict[bytes, bytes]] = None
        self._fwd_cache: Optional[dict[bytes, bytes]] = None
        # Int-keyed reverse cache (decode-spec-v02). Key is the raw stream
        # byte pattern packed as an int — no per-token bytes allocation in
        # the decoder hot path.
        self._id_to_surface: Optional[dict[int, bytes]] = None
        # Telemetry — read by callers that want the metrics
        self._init_elapsed_ms: float = 0.0
        self._rev_cache_bytes: int = 0
        self._fwd_cache_bytes: int = 0
        self._id_cache_bytes: int = 0

    # ------------------------------------------------------------------
    # Resource lifecycle
    # ------------------------------------------------------------------

    def open(self) -> 'Compressor':
        """Open LMDB if not already open. Idempotent."""
        if self._env is None:
            if not self.lmdb_path.exists():
                raise FileNotFoundError(
                    f"Dictionary not found at {self.lmdb_path}. "
                    f"Run dictionary_builder first."
                )
            self._env = lmdb.open(
                str(self.lmdb_path),
                readonly=True,
                max_dbs=2,
                lock=False,
            )
            self._fwd_db = self._env.open_db(b'forward')
            self._rev_db = self._env.open_db(b'reverse')
            self._build_caches()
        return self

    def close(self) -> None:
        """Close LMDB. Safe to call multiple times."""
        if self._env is not None:
            self._env.close()
            self._env = None
            self._fwd_db = None
            self._rev_db = None
        # Caches are tied to the env lifecycle — drop them so a re-open
        # rebuilds against the (possibly fresh) dictionary.
        self._rev_cache = None
        self._fwd_cache = None
        self._id_to_surface = None
        self._init_elapsed_ms = 0.0
        self._rev_cache_bytes = 0
        self._fwd_cache_bytes = 0
        self._id_cache_bytes = 0

    def _build_caches(self) -> None:
        """
        Preload the in-memory caches per the constructor flags. Walks the
        full LMDB once per enabled cache. Records init time + memory used.
        """
        t0 = time.monotonic()
        if self._preload_rev_cache:
            with self._env.begin(db=self._rev_db) as txn:
                entries = [(bytes(k), bytes(v)) for k, v in txn.cursor()]
            self._rev_cache = dict(entries)
            self._rev_cache_bytes = sum(len(k) + len(v) for k, v in entries)
            # If the int-keyed cache is also requested, derive it from the
            # same walked entries — avoids a second LMDB cursor pass.
            if self._preload_int_cache:
                self._id_to_surface = _build_int_keyed_cache(entries)
                # Rough resident estimate: 8B per int key + value bytes.
                self._id_cache_bytes = sum(
                    8 + len(v) for v in self._id_to_surface.values()
                )
        if self._preload_fwd_cache:
            with self._env.begin(db=self._fwd_db) as txn:
                fwd = {bytes(k): bytes(v) for k, v in txn.cursor()}
            self._fwd_cache = fwd
            self._fwd_cache_bytes = sum(len(k) + len(v) for k, v in fwd.items())
        self._init_elapsed_ms = (time.monotonic() - t0) * 1000.0

    @property
    def init_ms(self) -> float:
        """Milliseconds spent preloading caches at open() time."""
        return self._init_elapsed_ms

    @property
    def rev_cache_bytes(self) -> int:
        """Resident bytes of the reverse cache (0 if disabled)."""
        return self._rev_cache_bytes

    @property
    def fwd_cache_bytes(self) -> int:
        """Resident bytes of the forward cache (0 if disabled)."""
        return self._fwd_cache_bytes

    @property
    def id_cache_bytes(self) -> int:
        """Resident bytes of the int-keyed reverse cache (0 if disabled)."""
        return self._id_cache_bytes

    def __enter__(self) -> 'Compressor':
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Token-level encode / decode (internal)
    # ------------------------------------------------------------------

    def _encode_token(self, token: str, txn, stats: EncodeStats) -> str:
        """Encode a single source token into its stream representation."""
        stats.total_tokens += 1
        lower = token.lower()
        key = lower.encode(STREAM_ENCODING)
        if self._fwd_cache is not None:
            bytes_id = self._fwd_cache.get(key)
        else:
            bytes_id = txn.get(key, db=self._fwd_db)

        if bytes_id is not None:
            token_id = bytes_id.decode(STREAM_ENCODING)
            n = len(token_id)
            if   n == 1: stats.tier0 += 1
            elif n == 2: stats.tier1 += 1
            elif n == 3: stats.tier2 += 1
            elif n == 4: stats.tier3 += 1

            if token == lower:
                return token_id   # fast path: no case info needed

            _, cap = encode_caps(token)
            stats.cap_prefix += 1
            return f"{cap}{OOV_SEP}{token_id}"

        # OOV path
        stats.oov += 1
        return encode_oov(token)

    def _decode_token(self, stream_token: str, txn) -> str:
        """Decode one stream token back to its source string."""
        if is_oov_token(stream_token):
            return decode_oov(stream_token)

        rev_cache = self._rev_cache    # local alias for hot path

        if OOV_SEP in stream_token:
            cap, token_id = stream_token.split(OOV_SEP, 1)
            key = token_id.encode(STREAM_ENCODING)
            if rev_cache is not None:
                bytes_word = rev_cache.get(key)
            else:
                bytes_word = txn.get(key, db=self._rev_db)
            if bytes_word is None:
                raise ValueError(f"Unknown ID after cap prefix: {token_id!r}")
            return decode_caps(bytes_word.decode(STREAM_ENCODING), cap)

        key = stream_token.encode(STREAM_ENCODING)
        if rev_cache is not None:
            bytes_word = rev_cache.get(key)
        else:
            bytes_word = txn.get(key, db=self._rev_db)
        if bytes_word is None:
            raise ValueError(f"Unknown stream token: {stream_token!r}")
        return bytes_word.decode(STREAM_ENCODING)

    # ------------------------------------------------------------------
    # Text-level API
    # ------------------------------------------------------------------

    def encode_text(
        self,
        text: str,
        fmt: str = '.txt',
        *,
        stats: EncodeStats | None = None,
    ) -> str:
        """
        Encode text -> .elo string (header + stream).

        Args:
            text:   source text
            fmt:    source format extension (with or without leading '.')
            stats:  optional EncodeStats sink for diagnostics

        Returns:
            Single-line .elo string:
                "ELO|<version>|<ext>|<stream>"
        """
        self.open()
        if stats is None:
            stats = EncodeStats()

        ext = _normalize_ext(fmt)

        tokens = tokenize(text)
        tokens = _strip_implicit_spaces(tokens)        # Option 2

        parts: list[str] = []
        with self._env.begin() as txn:
            scanned = _longest_match_scan(tokens, txn, self._fwd_db)   # v0.3
            for tok in scanned:
                parts.append(self._encode_token(tok, txn, stats))

        stream = ELO_DELIMITER.join(parts)
        return f"{ELO_MAGIC}{ELO_DELIMITER}{FORMAT_VERSION}{ELO_DELIMITER}{ext}{ELO_DELIMITER}{stream}"

    def decode_text(self, elo_text: str) -> tuple[str, str]:
        """
        Decode .elo string -> (source_ext, source_text).

        Returns:
            (ext, text)
              ext: original source extension, including leading '.'
              text: byte-exact source text
        """
        self.open()
        magic, version_str, ext, stream = _parse_header(elo_text)

        if magic != ELO_MAGIC:
            raise ValueError(f"Not an .elo stream: missing {ELO_MAGIC!r} magic")

        try:
            version = int(version_str)
        except ValueError as e:
            raise ValueError(f"Invalid FORMAT_VERSION in header: {version_str!r}") from e

        if version != FORMAT_VERSION:
            raise ValueError(
                f"Unsupported .elo FORMAT_VERSION: file={version} reader={FORMAT_VERSION}"
            )

        out: list[str] = []
        with self._env.begin() as txn:
            # Empty stream -> empty source
            if stream:
                for st in stream.split(ELO_DELIMITER):
                    out.append(self._decode_token(st, txn))

        out = _restore_implicit_spaces(out)            # Option 2

        return _denormalize_ext(ext), ''.join(out)

    # ------------------------------------------------------------------
    # Byte-level API
    # ------------------------------------------------------------------

    def encode_bytes(self, src_bytes: bytes, fmt: str) -> bytes:
        """Encode raw source bytes -> .elo bytes (UTF-8 header + stream)."""
        text = src_bytes.decode(STREAM_ENCODING)
        elo_text = self.encode_text(text, fmt=fmt)
        return elo_text.encode(STREAM_ENCODING)

    # ------------------------------------------------------------------
    # BINARY stream API (Option 1 experiment, v2 / .eloB)
    # ------------------------------------------------------------------

    def encode_bytes_binary(
        self,
        src_bytes: bytes,
        fmt: str,
        *,
        stats: EncodeStats | None = None,
    ) -> bytes:
        """Encode raw bytes to compact binary .eloB representation."""
        self.open()
        text = src_bytes.decode(STREAM_ENCODING)
        if stats is None:
            stats = EncodeStats()

        ext = _normalize_ext(fmt)
        ext_bytes = ext.encode(STREAM_ENCODING)
        if len(ext_bytes) > 255:
            raise ValueError(f"format extension too long: {ext!r}")

        # Header
        out = bytearray()
        out += ELO_MAGIC_BIN
        out.append(ELO_BIN_VERSION)
        out.append(len(ext_bytes))
        out += ext_bytes

        # Token stream
        tokens = tokenize(text)
        tokens = _strip_implicit_spaces(tokens)        # Option 2

        with self._env.begin() as txn:
            scanned = _longest_match_scan(tokens, txn, self._fwd_db)   # v0.3
            for tok in scanned:
                _emit_token_binary(
                    tok, out, txn, self._fwd_db, stats,
                    fwd_cache=self._fwd_cache,
                )

        return bytes(out)

    def decode_bytes_binary(self, elo_bytes: bytes) -> tuple[str, bytes]:
        """Decode .eloB bytes -> (source_ext, source_bytes)."""
        self.open()
        if len(elo_bytes) < 5 or elo_bytes[:3] != ELO_MAGIC_BIN:
            raise ValueError("Not an .eloB stream (missing magic)")
        version = elo_bytes[3]
        if version != ELO_BIN_VERSION:
            raise ValueError(
                f"Unsupported .eloB version: file={version} reader={ELO_BIN_VERSION}"
            )
        ext_len = elo_bytes[4]
        ext = elo_bytes[5:5 + ext_len].decode(STREAM_ENCODING)
        stream = elo_bytes[5 + ext_len:]

        # Accumulate token bytes in a list — concatenated at the end. Tokens
        # already arrive as bytes from the cache / LMDB; no str round-trip.
        out_parts: list[bytes] = []
        if self._id_to_surface is not None:
            # Fast path (decode-spec-v02): int-keyed cache, zero bytes
            # allocation per token.
            _consume_stream_binary_int(stream, out_parts, self._id_to_surface)
        elif self._rev_cache is not None:
            # bytes-keyed cache (decode-spec-v01): no LMDB GET, but pays a
            # small bytes-construction cost per token.
            _consume_stream_binary(stream, out_parts, self._rev_cache, None, None)
        else:
            with self._env.begin() as txn:
                _consume_stream_binary(stream, out_parts, None, txn, self._rev_db)

        # Option 2: restore implicit single-spaces between word-class tokens.
        out_parts = _restore_implicit_spaces_bytes(out_parts)

        return _denormalize_ext(ext), b''.join(out_parts)

    def decode_bytes(self, elo_bytes: bytes) -> tuple[str, bytes]:
        """
        Decode .elo bytes -> (source_ext, source_bytes).

        Round-trip guarantee:
            decode_bytes(encode_bytes(b, fmt)) == (fmt, b)
        """
        elo_text = elo_bytes.decode(STREAM_ENCODING)
        ext, text = self.decode_text(elo_text)
        return ext, text.encode(STREAM_ENCODING)

    # ------------------------------------------------------------------
    # File-level API
    # ------------------------------------------------------------------

    def encode_file(
        self,
        src: Path | str,
        dst: Path | str | None = None,
        *,
        stats: EncodeStats | None = None,
    ) -> Path:
        """
        Encode src file -> dst .elo file.

        Args:
            src:   path to a source file (any v1 supported format)
            dst:   path for .elo output. Defaults to src + '.elo'
            stats: optional EncodeStats sink

        Returns:
            Path to the written .elo file.
        """
        src = Path(src)
        dst = Path(dst) if dst is not None else src.with_suffix(src.suffix + ELO_EXTENSION)

        fmt = detect_format(src)
        adapter = get_adapter(fmt)
        text = adapter.read(src)
        elo_text = self.encode_text(text, fmt=fmt, stats=stats)

        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(elo_text.encode(STREAM_ENCODING))

        return dst

    def decode_file(
        self,
        src: Path | str,
        dst: Path | str | None = None,
    ) -> Path:
        """
        Decode .elo file -> original file.

        Args:
            src:  path to a .elo file
            dst:  path for restored output. If None, derive from the
                  source extension stored in the .elo header.

        Returns:
            Path to the restored file.
        """
        src = Path(src)
        with open(src, 'rb') as f:
            elo_bytes = f.read()

        ext, src_bytes = self.decode_bytes(elo_bytes)

        if dst is None:
            # Strip '.elo' suffix and substitute the recorded source ext
            base = src
            if base.suffix == ELO_EXTENSION:
                base = base.with_suffix('')
            # base may already have the source extension because encode_file
            # used src.with_suffix(src.suffix + '.elo') -> 'foo.json.elo'.
            # Stripping '.elo' gave us 'foo.json' which is already correct.
            if base.suffix != ext:
                base = base.with_suffix(ext)
            dst = base
        else:
            dst = Path(dst)

        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(src_bytes)

        return dst


# ---------------------------------------------------------------------------
# Binary stream encoding helpers (Option 1 experiment)
# ---------------------------------------------------------------------------

def _encode_id_to_binary(out: bytearray, token_id: str) -> None:
    """Pack a 1-4 char Base64 token ID into 1-4 binary bytes."""
    n = len(token_id)
    indices = [BASE64_INDEX[c] for c in token_id]
    if n == 1:
        # Tier 0: byte value = base64 index (0-63), top 2 bits already 00
        out.append(indices[0])
    elif n == 2:
        out.append(TAG_T1 | indices[0])     # 0x40-0x7F (low 6 bits = first char)
        out.append(indices[1])
    elif n == 3:
        out.append(TAG_T2 | indices[0])     # 0x80-0xBF
        out.append(indices[1])
        out.append(indices[2])
    elif n == 4:
        out.append(TAG_T3 | indices[0])     # 0xC0-0xFD
        out.append(indices[1])
        out.append(indices[2])
        out.append(indices[3])
    else:
        raise ValueError(f"unexpected token ID length: {token_id!r}")


def _emit_token_binary(
    token: str,
    out: bytearray,
    txn,
    fwd_db,
    stats: EncodeStats,
    *,
    fwd_cache: Optional[dict[bytes, bytes]] = None,
) -> None:
    """Encode one source token directly to the binary stream.

    Uses the in-memory ``fwd_cache`` when provided; otherwise falls back
    to ``txn.get(..., db=fwd_db)``. Both paths produce byte-identical
    output.
    """
    stats.total_tokens += 1
    lower = token.lower()
    key = lower.encode(STREAM_ENCODING)
    if fwd_cache is not None:
        bytes_id = fwd_cache.get(key)
    else:
        bytes_id = txn.get(key, db=fwd_db)

    if bytes_id is not None:
        token_id = bytes_id.decode(STREAM_ENCODING)
        n = len(token_id)
        if   n == 1: stats.tier0 += 1
        elif n == 2: stats.tier1 += 1
        elif n == 3: stats.tier2 += 1
        elif n == 4: stats.tier3 += 1

        if token == lower:
            _encode_id_to_binary(out, token_id)
            return

        # Cap-prefixed in-vocab token
        _, cap = encode_caps(token)
        cap_bytes = cap.encode(STREAM_ENCODING)
        if len(cap_bytes) > 255:
            raise ValueError("cap chars exceed 255 bytes")
        stats.cap_prefix += 1
        out.append(TAG_CAP)
        out.append(len(cap_bytes))
        out += cap_bytes
        _encode_id_to_binary(out, token_id)
        return

    # OOV path  -- store raw lowered word verbatim
    stats.oov += 1
    lower_token, cap = encode_caps(token)
    cap_bytes = cap.encode(STREAM_ENCODING)
    body_bytes = lower_token.encode(STREAM_ENCODING)
    if len(cap_bytes) > 255 or len(body_bytes) > 65535:
        raise ValueError(
            f"OOV token too large: cap_len={len(cap_bytes)} body_len={len(body_bytes)}"
        )
    out.append(TAG_OOV)
    out.append(len(cap_bytes))
    out += cap_bytes
    out.append(len(body_bytes) & 0xFF)
    out.append((len(body_bytes) >> 8) & 0xFF)
    out += body_bytes


def _read_id_from_binary(stream: bytes, i: int) -> tuple[bytes, int]:
    """Read an in-vocab token ID at offset i. Returns (id_bytes, new_offset).

    Returns the LMDB-key bytes form directly (no str round-trip). The bytes
    are slices/constructs over ``BASE64_CHARS_BYTES`` so they're valid
    ASCII and match the byte form LMDB stores keys as.
    """
    cb = BASE64_CHARS_BYTES   # local alias for hot path
    tag = stream[i]
    if tag < TAG_T1:                                    # Tier 0
        return cb[tag:tag + 1], i + 1
    if tag < TAG_T2:                                    # Tier 1
        return bytes((cb[tag & 0x3F], cb[stream[i + 1]])), i + 2
    if tag < TAG_T3:                                    # Tier 2
        return bytes((cb[tag & 0x3F], cb[stream[i + 1]], cb[stream[i + 2]])), i + 3
    if tag < TAG_CAP:                                   # Tier 3
        return bytes((
            cb[tag & 0x3F],
            cb[stream[i + 1]],
            cb[stream[i + 2]],
            cb[stream[i + 3]],
        )), i + 4
    raise ValueError(f"unexpected ID tag byte 0x{tag:02X} at offset {i}")


def _build_int_keyed_cache(
    entries: list[tuple[bytes, bytes]],
) -> dict[int, bytes]:
    """Convert (LMDB key bytes -> surface bytes) entries into an int-keyed
    cache.

    The integer key is the raw STREAM byte pattern of the token, packed
    little-end-first. Because the tag bits in the high byte differentiate
    tier ranges, the four tiers occupy disjoint integer regions:

        Tier 0 (1B)   0x00 .. 0x3F
        Tier 1 (2B)   0x4000 .. 0x7FFF
        Tier 2 (3B)   0x800000 .. 0xBFFFFF
        Tier 3 (4B)   0xC000_0000 .. 0xFDFF_FFFF

    The LMDB key is the Base64 ID *string* (e.g. b'T', b'gB'). Each char
    corresponds to a 6-bit Base64 index that we recover via BASE64_INDEX.
    """
    out: dict[int, bytes] = {}
    bi = BASE64_INDEX     # local alias
    for k, v in entries:
        n = len(k)
        if n == 1:
            # Tier 0: stream byte IS the Base64 index (0-63).
            int_key = bi[chr(k[0])]
        elif n == 2:
            c1 = bi[chr(k[0])]
            c2 = bi[chr(k[1])]
            # Tier 1: byte0 = 0x40 | c1, byte1 = c2.
            int_key = ((0x40 | c1) << 8) | c2
        elif n == 3:
            c1 = bi[chr(k[0])]
            c2 = bi[chr(k[1])]
            c3 = bi[chr(k[2])]
            # Tier 2: byte0 = 0x80 | c1.
            int_key = ((0x80 | c1) << 16) | (c2 << 8) | c3
        elif n == 4:
            c1 = bi[chr(k[0])]
            c2 = bi[chr(k[1])]
            c3 = bi[chr(k[2])]
            c4 = bi[chr(k[3])]
            # Tier 3: byte0 = 0xC0 | c1.
            int_key = ((0xC0 | c1) << 24) | (c2 << 16) | (c3 << 8) | c4
        else:
            raise ValueError(
                f"unexpected ID length {n} in reverse dictionary: {k!r}"
            )
        out[int_key] = v
    return out


def _consume_stream_binary_int(
    stream: bytes,
    out_parts: list[bytes],
    id_to_surface: dict[int, bytes],
) -> None:
    """Decode the binary stream using the int-keyed reverse cache.

    Hot path is allocation-free: no bytes objects constructed for token
    IDs, no string-class lookups, just integer arithmetic + a single
    dict GET per token. Cap-prefix and OOV branches keep the existing
    str-decode behavior because they're rare.

    Pure-Python micro-optimizations applied:
        - Method references bound to locals (``id_to_surface.__getitem__``
          and ``out_parts.append``) — saves one attribute lookup per token.
        - Tag-range constants hoisted to locals (CPython resolves locals
          faster than module-level names).
        - Tier 0 (most common in English text) is the first branch.

    Produces byte-identical output to ``_consume_stream_binary`` when
    given the same input. Verified by ``test_decoder_cache``.
    """
    n = len(stream)
    i = 0
    # Local aliases — CPython resolves locals (LOAD_FAST) faster than globals.
    get   = id_to_surface.__getitem__
    push  = out_parts.append
    _T1   = TAG_T1
    _T2   = TAG_T2
    _T3   = TAG_T3
    _CAP  = TAG_CAP
    _OOV  = TAG_OOV
    _enc  = STREAM_ENCODING
    while i < n:
        tag = stream[i]
        # Tier 0 — single-byte ID. Most common case in English text; first.
        if tag < _T1:
            push(get(tag))
            i += 1
            continue
        # Tier 1 — 2 bytes.
        if tag < _T2:
            push(get((tag << 8) | stream[i + 1]))
            i += 2
            continue
        # Tier 2 — 3 bytes.
        if tag < _T3:
            push(get(
                (tag << 16) | (stream[i + 1] << 8) | stream[i + 2]
            ))
            i += 3
            continue
        # Tier 3 — 4 bytes.
        if tag < _CAP:
            push(get(
                (tag << 24) | (stream[i + 1] << 16)
                | (stream[i + 2] << 8) | stream[i + 3]
            ))
            i += 4
            continue
        # Cap-prefix slow path (rare): construct the int ID then look up.
        if tag == _CAP:
            cap_len = stream[i + 1]
            cap = stream[i + 2:i + 2 + cap_len].decode(_enc)
            i += 2 + cap_len
            tag2 = stream[i]
            if tag2 < _T1:
                int_id = tag2
                i += 1
            elif tag2 < _T2:
                int_id = (tag2 << 8) | stream[i + 1]
                i += 2
            elif tag2 < _T3:
                int_id = (tag2 << 16) | (stream[i + 1] << 8) | stream[i + 2]
                i += 3
            else:
                int_id = (
                    (tag2 << 24) | (stream[i + 1] << 16)
                    | (stream[i + 2] << 8) | stream[i + 3]
                )
                i += 4
            decoded = decode_caps(get(int_id).decode(_enc), cap)
            push(decoded.encode(_enc))
            continue
        # OOV slow path.
        if tag == _OOV:
            cap_len = stream[i + 1]
            cap = stream[i + 2:i + 2 + cap_len].decode(_enc)
            j = i + 2 + cap_len
            body_len = stream[j] | (stream[j + 1] << 8)
            body = stream[j + 2:j + 2 + body_len].decode(_enc)
            push(decode_caps(body, cap).encode(_enc))
            i = j + 2 + body_len
            continue
        raise ValueError(f"unexpected ID tag byte 0x{tag:02X} at offset {i}")


def _classify_bytes_token(b: bytes) -> str:
    """Bytes-aware mirror of ``classify()`` — looks at the first character.

    Fast path for ASCII (vast majority of surfaces). Falls back to a real
    UTF-8 decode only for tokens whose first byte is non-ASCII, which is
    rare and only happens via OOV bodies.
    """
    if not b:
        return CLASS_EMPTY
    first = b[0]
    if first < 0x80:
        return classify(chr(first))
    # Non-ASCII first byte — decode the full token to read first char safely.
    return classify(b.decode(STREAM_ENCODING, errors='replace'))


def _restore_implicit_spaces_bytes(parts: list[bytes]) -> list[bytes]:
    """Bytes-aware version of ``_restore_implicit_spaces``.

    Inserts a single ``b' '`` between two consecutive tokens whose class is
    CLASS_WORD. Mirrors ``_restore_implicit_spaces`` exactly on the str
    side — the only difference is the byte representation throughout.
    """
    if not parts:
        return parts
    out: list[bytes] = [parts[0]]
    last_class = _classify_bytes_token(parts[0])
    for p in parts[1:]:
        c = _classify_bytes_token(p)
        if last_class == CLASS_WORD and c == CLASS_WORD:
            out.append(b' ')
        out.append(p)
        last_class = c
    return out


def _consume_stream_binary(
    stream: bytes,
    out_parts: list[bytes],
    rev_cache: Optional[dict[bytes, bytes]],
    txn,
    rev_db,
) -> None:
    """Walk the binary stream and append decoded source bytes to out_parts.

    Uses ``rev_cache`` when not None — eliminates the LMDB GET per token.
    Falls back to ``txn.get(..., db=rev_db)`` when ``rev_cache`` is None.
    Both paths produce byte-identical output.

    Output parts are bytes (not str) so the caller can ``b''.join(parts)``
    without a str → bytes round-trip.
    """
    # Hoist the lookup once so the hot loop avoids a per-token branch.
    if rev_cache is not None:
        rev_get = rev_cache.get
    else:
        def rev_get(key, _t=txn, _d=rev_db):
            return _t.get(key, db=_d)

    n = len(stream)
    i = 0
    while i < n:
        tag = stream[i]
        if tag == TAG_CAP:
            # Cap-prefix wrapping the next in-vocab ID — slow path,
            # str round-trip is acceptable here (rare).
            cap_len = stream[i + 1]
            cap = stream[i + 2:i + 2 + cap_len].decode(STREAM_ENCODING)
            i += 2 + cap_len
            token_id, i = _read_id_from_binary(stream, i)
            bw = rev_get(token_id)
            if bw is None:
                raise ValueError(f"Unknown ID after cap prefix: {token_id!r}")
            decoded = decode_caps(bw.decode(STREAM_ENCODING), cap)
            out_parts.append(decoded.encode(STREAM_ENCODING))
            continue

        if tag == TAG_OOV:
            cap_len = stream[i + 1]
            cap = stream[i + 2:i + 2 + cap_len].decode(STREAM_ENCODING)
            j = i + 2 + cap_len
            body_len = stream[j] | (stream[j + 1] << 8)
            body = stream[j + 2:j + 2 + body_len].decode(STREAM_ENCODING)
            decoded = decode_caps(body, cap)
            out_parts.append(decoded.encode(STREAM_ENCODING))
            i = j + 2 + body_len
            continue

        # In-vocab token, no cap prefix — HOT PATH.
        # token_id is already bytes; rev_get returns bytes; append directly.
        token_id, i = _read_id_from_binary(stream, i)
        bw = rev_get(token_id)
        if bw is None:
            raise ValueError(f"Unknown stream token: {token_id!r}")
        out_parts.append(bw)


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------

def _normalize_ext(fmt: str) -> str:
    """Format extension without leading '.'  ('.json' -> 'json', 'txt' -> 'txt')."""
    return fmt[1:] if fmt.startswith('.') else fmt


def _denormalize_ext(ext: str) -> str:
    """Format extension with leading '.' ('json' -> '.json')."""
    return ext if ext.startswith('.') else '.' + ext


def _parse_header(elo_text: str) -> tuple[str, str, str, str]:
    """
    Split the .elo header from its stream body.

    Returns (magic, version, source_ext, stream).
    Raises ValueError on malformed input.
    """
    parts = elo_text.split(ELO_DELIMITER, 3)
    if len(parts) < 4:
        # No stream -> still expect 3 header parts
        if len(parts) < 3:
            raise ValueError(f"Malformed .elo header: {elo_text[:64]!r}")
        return parts[0], parts[1], parts[2], ''
    return parts[0], parts[1], parts[2], parts[3]


# ---------------------------------------------------------------------------
# Module-level convenience API
# ---------------------------------------------------------------------------

def encode_text(text: str, fmt: str = '.txt') -> str:
    with Compressor() as c:
        return c.encode_text(text, fmt=fmt)


def decode_text(elo_text: str) -> tuple[str, str]:
    with Compressor() as c:
        return c.decode_text(elo_text)


def encode_file(src: Path | str, dst: Path | str | None = None) -> Path:
    with Compressor() as c:
        return c.encode_file(src, dst)


def decode_file(src: Path | str, dst: Path | str | None = None) -> Path:
    with Compressor() as c:
        return c.decode_file(src, dst)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_encode(args: argparse.Namespace) -> int:
    src = Path(args.input)
    dst = Path(args.output) if args.output else None
    stats = EncodeStats() if args.stats else None
    with Compressor(lmdb_path=args.lmdb) as c:
        out_path = c.encode_file(src, dst, stats=stats)
    src_size = src.stat().st_size
    out_size = out_path.stat().st_size
    ratio = src_size / out_size if out_size else float('inf')
    print(f"encoded  {src}  ->  {out_path}")
    print(f"  {src_size:,} bytes  ->  {out_size:,} bytes   (ratio {ratio:.2f}x)")
    if stats is not None:
        print(f"  stats: {stats}")
    return 0


def _cmd_decode(args: argparse.Namespace) -> int:
    src = Path(args.input)
    dst = Path(args.output) if args.output else None
    with Compressor(lmdb_path=args.lmdb) as c:
        out_path = c.decode_file(src, dst)
    print(f"decoded  {src}  ->  {out_path}")
    print(f"  {src.stat().st_size:,} bytes  ->  {out_path.stat().st_size:,} bytes")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    """Round-trip every sample file via Compressor and compare bytes."""
    from semantic_compression.verify_lossless import SAMPLES

    pass_count = 0
    fail_count = 0

    with Compressor(lmdb_path=args.lmdb) as c:
        for path_str in SAMPLES:
            path = Path(path_str)
            if not path.exists():
                print(f"  MISSING  {path.name}")
                fail_count += 1
                continue

            original = path.read_bytes()
            fmt = detect_format(path)
            elo_bytes = c.encode_bytes(original, fmt=fmt)
            ext, recovered = c.decode_bytes(elo_bytes)

            if ext != fmt or recovered != original:
                print(f"  FAIL     {path.name}   ext={ext!r}  bytes_match={recovered == original}")
                fail_count += 1
                continue

            ratio = len(original) / len(elo_bytes) if elo_bytes else float('inf')
            print(f"  PASS     {path.name:<22} {len(original):>5} -> {len(elo_bytes):>5} bytes  ({ratio:.2f}x)")
            pass_count += 1

    print()
    if fail_count == 0:
        print(f"Compressor round-trip: {pass_count}/{pass_count} formats PASS byte-exact.")
        return 0
    print(f"Compressor round-trip: {pass_count} PASS / {fail_count} FAIL")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='compressor', description='EloAI .elo compressor (System 1)')
    parser.add_argument('--lmdb', default=str(DEFAULT_LMDB), help='Path to LMDB dictionary')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_enc = sub.add_parser('encode', help='Encode a source file to .elo')
    p_enc.add_argument('input', help='Source file path')
    p_enc.add_argument('output', nargs='?', default=None, help='Output .elo path (default: <input>.elo)')
    p_enc.add_argument('--stats', action='store_true', help='Print encode statistics')
    p_enc.set_defaults(func=_cmd_encode)

    p_dec = sub.add_parser('decode', help='Decode an .elo file back to source')
    p_dec.add_argument('input', help='.elo file path')
    p_dec.add_argument('output', nargs='?', default=None, help='Restored output path')
    p_dec.set_defaults(func=_cmd_decode)

    p_ver = sub.add_parser('verify', help='Round-trip the 10 v1 sample files')
    p_ver.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
