"""
verify_verbalizer.py — FORWARDER. The T1–T11 gates graduated with the
implementation to packages/eloai-verbalizer:

    uv run python -m eloai_verbalizer.verify [--verbose]

plus a pytest port in packages/eloai-verbalizer/tests/test_field.py.
This forwarder keeps the old invocation working from this directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PKG_SRC = Path(__file__).resolve().parent.parent / 'packages' / 'eloai-verbalizer' / 'src'
if _PKG_SRC.is_dir() and str(_PKG_SRC) not in sys.path:
    sys.path.insert(0, str(_PKG_SRC))

from eloai_verbalizer.verify import main

if __name__ == '__main__':
    main()
