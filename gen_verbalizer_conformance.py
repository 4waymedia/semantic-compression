"""DEPRECATION STUB — generator graduated to `verbalizer.gen_conformance` on 2026-08-05.
Remove once no consumer imports `gen_verbalizer_conformance` (migration step 5)."""
import sys as _sys
from pathlib import Path as _P
# Package-first (2026-08-14): resolve the INSTALLED `verbalizer` package; fall
# back to the generated package mirror -- never the dev home (04-Verbalizer).
import importlib.util as _ilu
if _ilu.find_spec("verbalizer") is None:
    _SRC = _P(__file__).resolve().parent.parent / "packages" / "elo-verbalizer" / "src"
    if _SRC.is_dir():
        _sys.path.insert(0, str(_SRC))
from verbalizer.gen_conformance import *   # noqa: F401,F403
