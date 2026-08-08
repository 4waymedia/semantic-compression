"""
verbalizer.py — TRANSITIONAL SHIM (repointed 2026-08-05).

The base `verbalizer` package now OWNS the `verbalizer` import name (renamed from
eloai-verbalizer; SYSTEMS.md §1b — base packages carry the plain name). Because this
shim file is itself named `verbalizer`, it cannot `from verbalizer import *` (that
would import itself). Instead it loads the real base package from packages/elo-verbalizer
and installs it as `verbalizer` in sys.modules, replacing this shim — so consumers
doing `import verbalizer` transparently resolve the base package.

Retire in migration step 5, once consumers resolve the base `verbalizer` package on
their own sys.path and no longer route through semantic_compression.
"""
from __future__ import annotations
import importlib.util as _ilu
import sys as _sys
import warnings as _w
from pathlib import Path as _P

_PKGDIR = _P(__file__).resolve().parent.parent / 'packages' / 'elo-verbalizer' / 'src' / 'verbalizer'
_INIT = _PKGDIR / '__init__.py'
if not _INIT.is_file():
    raise ImportError("base `verbalizer` package not found at %s" % _PKGDIR)

# Load the real base package under the name `verbalizer`, replacing this shim.
_spec = _ilu.spec_from_file_location(
    'verbalizer', _INIT, submodule_search_locations=[str(_PKGDIR)])
_mod = _ilu.module_from_spec(_spec)
_sys.modules['verbalizer'] = _mod          # supersede this shim in the module table
_spec.loader.exec_module(_mod)

_w.warn(
    "import verbalizer via semantic_compression is deprecated -- the base "
    "`verbalizer` package should be on sys.path directly (migration step 5).",
    DeprecationWarning, stacklevel=2)
