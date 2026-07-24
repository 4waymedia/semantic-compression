"""
verbalizer.py — DEPRECATION SHIM. The implementation graduated to
packages/eloai-verbalizer (import: `eloai_verbalizer`) on 2026-07-12 per
ELO_PACKAGING_STANDARD.md §12.3 ("move the proven code into src/; retire the
incubation copy").

This shim keeps `import verbalizer` / `from verbalizer import ...` consumers
(06-RecalEngine, step14, the MCP gateway bootstrap) working against the ONE
real implementation. New code must import `eloai_verbalizer` directly.

Remove this file once no consumer imports the top-level `verbalizer` name.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

# The reference package source, resolvable from any R&D checkout layout.
_PKG_SRC = Path(__file__).resolve().parent.parent / 'packages' / 'eloai-verbalizer' / 'src'
if _PKG_SRC.is_dir() and str(_PKG_SRC) not in sys.path:
    sys.path.insert(0, str(_PKG_SRC))

from eloai_verbalizer import *          # noqa: F401,F403  (re-export the contract surface)
from eloai_verbalizer import __version__ as _pkg_version   # noqa: F401

warnings.warn(
    "import verbalizer (semantic_compression shim) is deprecated -- "
    "the implementation lives in packages/eloai-verbalizer; "
    "use `import eloai_verbalizer` instead.",
    DeprecationWarning, stacklevel=2)
