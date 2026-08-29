"""The gate's own regression test. The gate needs one more than most code does.

WHY (2026-08-29). While cleaning up false positives I taught the gate to ignore
Python string literals, so a docstring EXPLAINING the old behaviour would stop being
reported as the old behaviour. It went from 11 violations to 0 and looked like a win.

It was the opposite. A hardcoded path IS a string literal, so skipping every
`ast.Constant` str suppressed the entire V1 class -- the gate would have reported a
codebase full of hardcoded paths as clean, forever, and the thing it exists to prevent
would have grown back underneath a green check.

Caught only by re-testing a known-bad line instead of trusting the number. Hence this
file: the gate must be shown to still FAIL on real violations, not just to pass on
clean code. A detector is only worth its false-negative rate.

    python tests/test_dict_resolution_gate.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_dict_resolution import scan          # noqa: E402

# (filename, source, expected {(kind, line)})
CASES = [
    ("real_violations.py", '''"""Docstring naming semantic_compression/db/dictionary.lmdb -- PROSE, not a hit."""
import os
from pathlib import Path
DEFAULT = Path('semantic_compression/db/dictionary.lmdb')
x = os.environ.get('ELO_DICT_DB')
y = os.environ.get('ELO_DICT_LMDB')
''', {("V1", 4), ("V2", 5), ("V2", 6)}),

    # The repointed shape must be silent, INCLUDING its explanatory docstring --
    # otherwise the gate flags its own fix notes and gets switched off.
    ("repointed.py", '''"""Resolve through the package.

    Was: _resolve('ELO_DICT_DB', 'semantic_compression/db/dictionary.lmdb') --
    a path that existed, so it never errored while being two generations stale.
    """
from compression_dictionary import resolve_dictionary
DEFAULT = resolve_dictionary().path
''', set()),

    # A comment naming a path is documentation.
    ("comment_only.py", '''# see semantic_compression/db/dictionary.lmdb for the legacy artifact
X = 1
''', set()),
]


def run() -> int:
    fails = 0
    for name, src, expected in CASES:
        d = Path(tempfile.mkdtemp())
        (d / name).write_text(src, encoding="utf-8")
        got = {(h["kind"], h["line"]) for h in scan([d])}
        ok = got == expected
        print(f"  {'pass' if ok else 'FAIL'}  {name:22} expected {sorted(expected)} got {sorted(got)}")
        fails += (not ok)

    # The load-bearing assertion, stated separately because it is the one that was
    # nearly lost: the gate must not be able to return "clean" for real violations.
    d = Path(tempfile.mkdtemp())
    (d / "z.py").write_text("from pathlib import Path\nP = Path('db/dictionary.lmdb')\n",
                            encoding="utf-8")
    if not scan([d]):
        print("  FAIL  gate reports CLEAN on a hardcoded path -- false-negative regression")
        fails += 1
    else:
        print("  pass  gate still catches a bare hardcoded path")

    print(f"\n{'FAILURES: %d' % fails if fails else 'all gate cases pass'}")
    return 1 if fails else 0


def test_gate():
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
