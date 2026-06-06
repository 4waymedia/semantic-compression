"""
format_adapters.py -- Step 7 of System 1

Per-format read/write adapters. Each adapter is a small (read, write) pair
that knows how to move a file's bytes into and out of a Python string.

For v1, all 10 supported formats use the same underlying UTF-8 adapter
because the universal tokenizer already handles format content. Per-format
adapters exist so future format-specific behaviour (CDATA splitting in XML,
fenced-code awareness in Markdown, etc.) has a place to live without
disrupting the rest of the system.

Contract per adapter
--------------------
    read(path)  -> str       file bytes -> text suitable for tokenizer
    write(path, text)        text -> file bytes
    Round-trip:  write(p, read(p)) produces byte-identical file on disk

Universal vs per-format
-----------------------
Today all 10 formats route to UTF8Adapter. Tomorrow if YAML needs special
indentation handling or HTML needs entity-aware splitting, we replace the
adapter for that extension without touching anything else.

C-portability notes
-------------------
- All adapters are stateless functions with explicit byte contracts.
- UTF-8 byte handling is locale-independent.
- File extension detection is pure string suffix matching.
"""

import logging
from pathlib import Path
from typing import Callable

from semantic_compression.config import STREAM_ENCODING

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supported formats — v1 scope
# ---------------------------------------------------------------------------

SUPPORTED_FORMATS: tuple[str, ...] = (
    '.txt',   # plain text
    '.md',    # markdown
    '.json',  # structured
    '.csv',   # tabular
    '.xml',   # structured
    '.html',  # structured + text
    '.htm',   # alias for .html
    '.yaml',  # config
    '.yml',   # alias for .yaml
    '.log',   # operational
    '.srt',   # subtitle
    '.vtt',   # subtitle
)

# Canonical normalisation -- file extension aliases.
_FORMAT_ALIASES: dict[str, str] = {
    '.htm':  '.html',
    '.yml':  '.yaml',
}


# ---------------------------------------------------------------------------
# Universal UTF-8 adapter
# ---------------------------------------------------------------------------

class Adapter:
    """
    Base class. Subclasses override read / write for format-specific
    behaviour. Default behaviour is UTF-8 byte round-trip.
    """
    extension: str | None = None

    def read(self, path: Path) -> str:
        """Read file as bytes and decode UTF-8."""
        with open(path, 'rb') as f:
            raw = f.read()
        return raw.decode(STREAM_ENCODING)

    def write(self, path: Path, text: str) -> None:
        """Encode text as UTF-8 and write bytes."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(text.encode(STREAM_ENCODING))


class UTF8Adapter(Adapter):
    """Identity adapter. Used by every v1 format today."""
    def __init__(self, extension: str | None = None):
        self.extension = extension


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Canonical extensions (post-alias) -> Adapter instance.
_REGISTRY: dict[str, Adapter] = {}

def _register_default_adapters() -> None:
    canonical = set(SUPPORTED_FORMATS) - set(_FORMAT_ALIASES)
    for ext in canonical:
        _REGISTRY[ext] = UTF8Adapter(ext)

_register_default_adapters()


def register_adapter(extension: str, adapter: Adapter) -> None:
    """
    Register a custom adapter for a given extension. Used for testing or
    future format-specific behaviour overrides.
    """
    ext = _normalize_extension(extension)
    _REGISTRY[ext] = adapter


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _normalize_extension(ext: str) -> str:
    """Lowercase, ensure leading dot, resolve aliases."""
    if not ext.startswith('.'):
        ext = '.' + ext
    ext = ext.lower()
    return _FORMAT_ALIASES.get(ext, ext)


def detect_format(path: Path | str) -> str:
    """
    Return the canonical extension for a file path.

    Raises ValueError if the extension is outside the v1 supported set.
    """
    suffix = Path(path).suffix.lower()
    if not suffix:
        raise ValueError(f'No file extension on path: {path!r}')
    if suffix not in SUPPORTED_FORMATS:
        raise ValueError(
            f'Unsupported format {suffix!r}. '
            f'V1 supports: {", ".join(sorted(set(SUPPORTED_FORMATS) - set(_FORMAT_ALIASES)))}'
        )
    return _normalize_extension(suffix)


def get_adapter(fmt: str) -> Adapter:
    """Return the registered adapter for a canonical extension."""
    ext = _normalize_extension(fmt)
    if ext not in _REGISTRY:
        raise KeyError(f'No adapter registered for {ext!r}')
    return _REGISTRY[ext]


def read_file(path: Path | str) -> tuple[str, str]:
    """
    Detect format from extension and read file via its adapter.

    Returns:
        (format, text)
    """
    path = Path(path)
    fmt = detect_format(path)
    text = get_adapter(fmt).read(path)
    return fmt, text


def write_file(path: Path | str, text: str, fmt: str | None = None) -> None:
    """
    Write text to path via the adapter for the given format.
    If fmt is None, detect from path extension.
    """
    path = Path(path)
    if fmt is None:
        fmt = detect_format(path)
    get_adapter(fmt).write(path, text)


def round_trip_bytes(path: Path | str) -> bool:
    """
    Verify that adapter.write(p, adapter.read(p)) yields byte-identical
    file content. Returns True on match.

    This proves the adapter layer is lossless before the
    tokenizer/encoder pipeline adds its work.
    """
    path = Path(path)
    original = path.read_bytes()
    fmt = detect_format(path)
    adapter = get_adapter(fmt)
    text = adapter.read(path)
    # Write to a sibling temp file and compare
    tmp_path = path.with_suffix(path.suffix + '.adapter_test')
    try:
        adapter.write(tmp_path, text)
        rewritten = tmp_path.read_bytes()
        return rewritten == original
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
