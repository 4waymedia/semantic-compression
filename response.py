"""DEPRECATION STUB — response.py graduated to the base `verbalizer` package
(`verbalizer.response`) on 2026-08-05. Kept so `from response import ...` consumers
(08-MCP tool_api, Reasoning/co_presence, probes) keep working transitionally.
Remove once consumers import `verbalizer.response` directly (migration step 4/5)."""
import sys as _sys, warnings as _w
from pathlib import Path as _P
# Package-first (2026-08-14): resolve the INSTALLED `verbalizer` package; fall
# back to the generated package mirror -- never the dev home (04-Verbalizer).
import importlib.util as _ilu
if _ilu.find_spec("verbalizer") is None:
    _SRC = _P(__file__).resolve().parent.parent / "packages" / "elo-verbalizer" / "src"
    if _SRC.is_dir():
        _sys.path.insert(0, str(_SRC))
from verbalizer.response import *          # noqa: F401,F403
_w.warn("import response (semantic_compression) is deprecated -- use verbalizer.response",
        DeprecationWarning, stacklevel=2)
