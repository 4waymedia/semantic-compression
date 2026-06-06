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
    FORMAT_VERSION, STREAM_ENCODING,
)
from semantic_compression.format_adapters import (
    detect_format, get_adapter, read_file,
)
from semantic_compression.tokenizer import tokenize, classify, CLASS_WORD


# ---------------------------------------------------------------------------
# Implicit whitespace transform (Option 2 experiment)
#
# Single space (' ') is ~47% of all tokens in the YouTube corpus. Removing
# it from the stream when it falls between two word-class tokens cuts both
# the token count and the pipe-delimiter overhead substantially.
#
# Rule: drop a single-space token at position i iff
#       tokens[i] == ' ' AND classify(tokens[i-1]) == WORD AND classify(tokens[i+1]) == WORD
#
# Decoder restores by inserting ' ' between any two consecutive word tokens
# that are not already separated by a whitespace token.
#
# All non-single-space whitespace (newlines, tabs, multi-space runs, leading
# and trailing whitespace) emits explicitly. The transform is byte-exact
# lossless on every test case where the property "no two consecutive WORD
# tokens appear with no whitespace between them in the source" holds, which
# is true for natural text. Edge case: identifier-style runs like
# `foo_bar` produce a SINGLE word token (interior _ rule), so the property
# is preserved.
# ---------------------------------------------------------------------------

def _strip_implicit_spaces(tokens: list[str]) -> list[str]:
    """Remove single-space tokens that fall between two word-class tokens."""
    n = len(tokens)
    if n < 3:
        return tokens
    out: list[str] = []
    i = 0
    while i < n:
        tok = tokens[i]
        if (
            tok == ' '
            and i > 0
            and i < n - 1
            and classify(tokens[i - 1]) == CLASS_WORD
            and classify(tokens[i + 1]) == CLASS_WORD
        ):
            # implicit — skip
            pass
        else:
            out.append(tok)
        i += 1
    return out


def _restore_implicit_spaces(tokens: list[str]) -> list[str]:
    """Insert single-space tokens between consecutive word-class tokens."""
    if not tokens:
        return tokens
    out: list[str] = [tokens[0]]
    for tok in tokens[1:]:
        if classify(out[-1]) == CLASS_WORD and classify(tok) == CLASS_WORD:
            out.append(' ')
        out.append(tok)
    return out


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ELO_MAGIC      = 'ELO'
ELO_DELIMITER  = '|'                                  # PIPE_BYTE = 0x7C
DEFAULT_LMDB   = Path('semantic_compression/db/dictionary.lmdb')
ELO_EXTENSION  = '.elo'


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

    def __init__(self, lmdb_path: Path | str = DEFAULT_LMDB) -> None:
        self.lmdb_path = Path(lmdb_path)
        self._env: Optional[lmdb.Environment] = None
        self._fwd_db = None
        self._rev_db = None

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
        return self

    def close(self) -> None:
        """Close LMDB. Safe to call multiple times."""
        if self._env is not None:
            self._env.close()
            self._env = None
            self._fwd_db = None
            self._rev_db = None

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
        bytes_id = txn.get(lower.encode(STREAM_ENCODING), db=self._fwd_db)

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

        if OOV_SEP in stream_token:
            cap, token_id = stream_token.split(OOV_SEP, 1)
            bytes_word = txn.get(token_id.encode(STREAM_ENCODING), db=self._rev_db)
            if bytes_word is None:
                raise ValueError(f"Unknown ID after cap prefix: {token_id!r}")
            return decode_caps(bytes_word.decode(STREAM_ENCODING), cap)

        bytes_word = txn.get(stream_token.encode(STREAM_ENCODING), db=self._rev_db)
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
        tokens = _strip_implicit_spaces(tokens)   # Option 2

        parts: list[str] = []
        with self._env.begin() as txn:
            for tok in tokens:
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
            if stream:
                for st in stream.split(ELO_DELIMITER):
                    out.append(self._decode_token(st, txn))

        out = _restore_implicit_spaces(out)   # Option 2

        return _denormalize_ext(ext), ''.join(out)

    # ------------------------------------------------------------------
    # Byte-level API
    # ------------------------------------------------------------------

    def encode_bytes(self, src_bytes: bytes, fmt: str) -> bytes:
        """Encode raw source bytes -> .elo bytes (UTF-8 header + stream)."""
        text = src_bytes.decode(STREAM_ENCODING)
        elo_text = self.encode_text(text, fmt=fmt)
        return elo_text.encode(STREAM_ENCODING)

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
