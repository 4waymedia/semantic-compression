"""
tokenizer.py -- Step 6 of System 1

Universal character-class tokenizer.

This module is the format-agnostic boundary between raw source text and
the dictionary/encoder. It takes any UTF-8 string and produces a list of
tokens such that ''.join(tokenize(s)) == s exactly.

The rules are pure character classes — no language model, no NLP. Designed
for direct C translation.

Token classes produced
----------------------
    word        contiguous alnum [+ interior joiners]      "don't", "v1.2.3"
    whitespace  contiguous run of \\s                        " ", "\\n\\n"
    punct       any single non-alnum non-ws character       ".", "{", "|"

Joiner rule
-----------
A "joiner" character stays inside a word token IF it is surrounded by
alphanumeric characters on both sides; otherwise it splits into its own
punct token.

    Joiner char     Left context required    Right context required
    -----------     ---------------------    ----------------------
        '           letter                   letter
        .           alnum                    alnum
        _           alnum                    alnum

Examples
    "don't"         -> ["don't"]                  ' between letters
    "'hello'"       -> ["'", "hello", "'"]        outer ' have no letter on
                                                  one side -> standalone
    "3.14"          -> ["3.14"]                   . between digits
    "rabbits."      -> ["rabbits", "."]           trailing . splits
    "my_var"        -> ["my_var"]                 _ between letters
    "_leading"      -> ["_", "leading"]           leading _ splits
    "v1.2.3-rc1"    -> ["v1.2.3", "-", "rc1"]     - never joins
    "  \\n  "         -> ["  \\n  "]                 entire whitespace run

Invariant
---------
    ''.join(tokenize(s)) == s   for any string s

This is the only correctness criterion. Verified exhaustively in
verify_tokenizer.py against text, code, JSON, markdown, YAML samples.

C-portability notes
-------------------
- Pure character predicates (isalpha, isdigit, isspace) — direct mapping.
- Single forward scan with bounded look-back/look-ahead by 1 char.
- No allocations beyond the output token list.
- Stateless — same input always yields same output.
"""

from typing import Iterator

# Joiner characters — interior to a word when surrounded by qualifying chars.
APOSTROPHE = "'"
PERIOD     = "."
UNDERSCORE = "_"


# ---------------------------------------------------------------------------
# Character class predicates
# ---------------------------------------------------------------------------

def _is_alpha(c: str) -> bool:
    """ASCII letter or unicode letter."""
    return c.isalpha()


def _is_digit(c: str) -> bool:
    return c.isdigit()


def _is_alnum(c: str) -> bool:
    return c.isalpha() or c.isdigit()


def _is_space(c: str) -> bool:
    return c.isspace()


# ---------------------------------------------------------------------------
# Joiner predicate — does this character stay inside the current word?
# ---------------------------------------------------------------------------

def _is_interior_joiner(text: str, j: int) -> bool:
    """
    True if text[j] is a joiner character with valid context on both sides.

    Assumes the caller has already verified text[j] is non-alnum non-space.
    Bounds-checked — returns False at start or end of string.
    """
    if j == 0 or j >= len(text) - 1:
        return False

    c = text[j]
    if c not in (APOSTROPHE, PERIOD, UNDERSCORE):
        return False

    left  = text[j - 1]
    right = text[j + 1]

    if c == APOSTROPHE:
        return _is_alpha(left) and _is_alpha(right)
    # PERIOD and UNDERSCORE — any alnum on both sides
    return _is_alnum(left) and _is_alnum(right)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """
    Split text into tokens. Invariant: ''.join(tokenize(text)) == text.

    Token classes:
        - whitespace run     (one or more consecutive whitespace chars)
        - word               (alnum run with interior joiners)
        - single punct char  (anything else)

    Empty input returns an empty list.
    """
    tokens: list[str] = []
    n = len(text)
    i = 0

    while i < n:
        c = text[i]

        # --- whitespace run ---
        if _is_space(c):
            j = i + 1
            while j < n and _is_space(text[j]):
                j += 1
            tokens.append(text[i:j])
            i = j
            continue

        # --- alnum word (with interior joiners) ---
        if _is_alnum(c):
            j = i + 1
            while j < n:
                ch = text[j]
                if _is_alnum(ch):
                    j += 1
                elif _is_interior_joiner(text, j):
                    j += 1
                else:
                    break
            tokens.append(text[i:j])
            i = j
            continue

        # --- single-char punctuation/symbol ---
        tokens.append(c)
        i += 1

    return tokens


def itokenize(text: str) -> Iterator[str]:
    """
    Generator variant — yields tokens one at a time without building a list.
    Useful for streaming very large inputs.
    """
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if _is_space(c):
            j = i + 1
            while j < n and _is_space(text[j]):
                j += 1
            yield text[i:j]
            i = j
        elif _is_alnum(c):
            j = i + 1
            while j < n:
                ch = text[j]
                if _is_alnum(ch) or _is_interior_joiner(text, j):
                    j += 1
                else:
                    break
            yield text[i:j]
            i = j
        else:
            yield c
            i += 1


# ---------------------------------------------------------------------------
# Token class introspection (for downstream encoders / format detection)
# ---------------------------------------------------------------------------

CLASS_WORD       = 'word'
CLASS_WHITESPACE = 'whitespace'
CLASS_PUNCT      = 'punct'
CLASS_EMPTY      = 'empty'


def classify(token: str) -> str:
    """Return the token's class. Single forward scan, no allocations."""
    if not token:
        return CLASS_EMPTY
    if _is_space(token[0]):
        return CLASS_WHITESPACE
    if _is_alnum(token[0]):
        return CLASS_WORD
    return CLASS_PUNCT
