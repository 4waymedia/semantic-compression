"""DEPRECATION STUB — verbalizer_ops.py graduated to `verbalizer.ops` on 2026-08-05.
Kept so `from verbalizer_ops import ...` consumers (08-MCP tool_api) keep working
transitionally. Remove once consumers import `verbalizer.ops` (migration step 4)."""
import sys as _sys, warnings as _w
from pathlib import Path as _P
# Package-first (2026-08-14): resolve the INSTALLED `verbalizer` package; fall
# back to the generated package mirror -- never the dev home (04-Verbalizer).
import importlib.util as _ilu
if _ilu.find_spec("verbalizer") is None:
    _SRC = _P(__file__).resolve().parent.parent / "packages" / "elo-verbalizer" / "src"
    if _SRC.is_dir():
        _sys.path.insert(0, str(_SRC))
from verbalizer.ops import *               # noqa: F401,F403
_w.warn("import verbalizer_ops (semantic_compression) is deprecated -- use verbalizer.ops",
        DeprecationWarning, stacklevel=2)
