"""
Step 6 verification -- tokenizer.py

The single acceptance criterion: ''.join(tokenize(s)) == s for any string s.

Tested against:
  1. Hand-crafted edge cases (boundary chars, joiners, contractions)
  2. Synthetic samples for each of the 10 v1 formats
  3. Real corpus chunks from Resources/transcripts
  4. Tokenization behaviour spot-checks (verify the rules actually fire)
"""
import sys
sys.path.insert(0, '.')

import json
from pathlib import Path

from semantic_compression.tokenizer import (
    tokenize, itokenize, classify,
    CLASS_WORD, CLASS_WHITESPACE, CLASS_PUNCT, CLASS_EMPTY,
)

EDGE_CASES = [
    # empty + trivial
    '',
    'a',
    ' ',
    '.',
    "'",
    # English contractions — must stay whole
    "don't",
    "it's",
    "we're",
    "wouldn't",
    "i've",
    "world's",
    # Apostrophes at boundary — must split
    "'hello'",
    "'hi",
    "hi'",
    "boys'",
    "she said 'no'",
    # Numbers + version strings
    "3.14",
    "2.71828",
    "0xFF",
    "v1.2.3",
    "v1.2.3-rc1",
    "1.2.3.4.5",
    # Trailing punctuation
    "rabbits.",
    "fertility?",
    "really!",
    '"chris,',
    # Code samples
    "if (x == 'hello') { return; }",
    "my_var = 42",
    "_leading underscore",
    "trailing_",
    "snake_case_identifier",
    # JSON
    '{"name": "Alice", "age": 30}',
    '{"key": "don\'t"}',
    # Whitespace runs
    '  ',
    '\n',
    '\n\n',
    '\t',
    'a\nb',
    'a  b',
    'a\n\nb',
    'line1\nline2\n\nline3',
    # Mixed
    "Hello, world!  How are you?  I'm fine.",
    'def foo():\n    return 42\n',
    # Unicode
    'café',
    'naïve résumé',
    # Pipe and colon in source (will need encoder care later)
    'a|b',
    'key:value',
    # Empty token edge — repeated punct
    '...',
    '!!!',
    '?!?',
]

print('=== Edge case round-trip ===')
for s in EDGE_CASES:
    tokens = tokenize(s)
    rebuilt = ''.join(tokens)
    assert rebuilt == s, (
        f'ROUND-TRIP FAILED:\n  in:  {s!r}\n  out: {tokens!r}\n  rebuilt: {rebuilt!r}'
    )
print(f'[OK] {len(EDGE_CASES)} edge cases all round-trip exactly')

# Generator variant must produce identical output
print()
print('=== Generator parity ===')
for s in EDGE_CASES:
    assert list(itokenize(s)) == tokenize(s), f'itokenize mismatch for {s!r}'
print(f'[OK] itokenize matches tokenize for all edge cases')


# ---------------------------------------------------------------------------
# Synthetic samples for each v1 format
# ---------------------------------------------------------------------------
SAMPLES = {
    '.txt':  'The quick brown fox jumps over the lazy dog.\n'
             'I said "don\'t do that!" but he ignored me.\n'
             "Pi is 3.14 and e is 2.71828.\n",

    '.md':   '# Heading\n\n'
             'This is **bold** and _italic_.\n\n'
             '- list item one\n'
             '- list item two\n\n'
             '```python\n'
             "x = 'hello'\n"
             '```\n',

    '.json': '{\n'
             '  "version": "1.2.3",\n'
             '  "items": [1, 2, 3],\n'
             '  "msg": "it\'s working"\n'
             '}\n',

    '.csv':  'id,name,email,age\n'
             '1,"Alice Smith",alice@example.com,30\n'
             '2,"Bob Jones",bob@example.com,25\n'
             "3,O'Brien,o.brien@example.com,40\n",

    '.xml':  '<?xml version="1.0" encoding="UTF-8"?>\n'
             '<root>\n'
             '  <item id="1" name="alpha">value 1</item>\n'
             '  <item id="2" name="beta">value 2</item>\n'
             '</root>\n',

    '.html': '<!DOCTYPE html>\n'
             '<html lang="en">\n'
             '<head><title>Test</title></head>\n'
             '<body>\n'
             '  <p>Hello, world!</p>\n'
             '  <a href="https://example.com">link</a>\n'
             '</body>\n'
             '</html>\n',

    '.yaml': 'name: my-service\n'
             'version: 1.2.3\n'
             'config:\n'
             '  port: 8080\n'
             '  hosts:\n'
             '    - host1.example.com\n'
             '    - host2.example.com\n'
             '  message: "it\'s running"\n',

    '.log':  '2026-06-06 10:15:32 INFO  Server started on port 8080\n'
             '2026-06-06 10:15:33 WARN  config.yaml: deprecated option ignored\n'
             '2026-06-06 10:15:34 ERROR Failed to connect: timeout after 30s\n',

    '.srt':  '1\n00:00:01,000 --> 00:00:04,000\n'
             "Hello, this is the first subtitle.\n\n"
             '2\n00:00:04,500 --> 00:00:07,000\n'
             "And here's the second one!\n\n",

    '.vtt':  'WEBVTT\n\n'
             '1\n00:00:01.000 --> 00:00:04.000\n'
             'Hello, this is the first cue.\n\n'
             '2\n00:00:04.500 --> 00:00:07.000\n'
             "And here's the second.\n\n",
}

print()
print('=== Format sample round-trip ===')
for fmt, sample in SAMPLES.items():
    tokens = tokenize(sample)
    rebuilt = ''.join(tokens)
    assert rebuilt == sample, f'{fmt} round-trip failed'
    print(f'[OK] {fmt:<6} {len(sample):>4} chars -> {len(tokens):>4} tokens   (avg {len(sample)/len(tokens):.2f} chars/token)')


# ---------------------------------------------------------------------------
# Real corpus sample — 5 random transcripts
# ---------------------------------------------------------------------------
print()
print('=== Real corpus round-trip (5 transcripts) ===')
corpus_files = sorted(Path('Resources/transcripts').rglob('*.json'))[:5]
for p in corpus_files:
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f'  skip {p.name} ({e})')
        continue
    chunk_count = 0
    for chunk in data.get('chunks', []):
        text = chunk.get('text', '')
        if not text:
            continue
        tokens = tokenize(text)
        assert ''.join(tokens) == text, f'{p.name} chunk roundtrip failed'
        chunk_count += 1
    print(f'[OK] {p.name:<25} {chunk_count} chunks round-trip exact')


# ---------------------------------------------------------------------------
# Behavioural spot-checks — verify the rules actually do what we think
# ---------------------------------------------------------------------------
print()
print('=== Behavioural spot-checks ===')
checks = [
    ("don't",                   ["don't"]),
    ("it's right",              ["it's", " ", "right"]),
    ("'hello'",                 ["'", "hello", "'"]),
    ("boys'",                   ["boys", "'"]),
    ("rabbits.",                ["rabbits", "."]),
    ("3.14",                    ["3.14"]),
    ("v1.2.3",                  ["v1.2.3"]),
    ("v1.2.3-rc1",              ["v1.2.3", "-", "rc1"]),
    ("my_var",                  ["my_var"]),
    ("_leading",                ["_", "leading"]),
    ("trailing_",               ["trailing", "_"]),
    ('"hello"',                 ['"', "hello", '"']),
    ("a|b",                     ["a", "|", "b"]),
    ("key:value",               ["key", ":", "value"]),
    ("a\n\nb",                  ["a", "\n\n", "b"]),
    ("...",                     [".", ".", "."]),
    ("Hello, world!",           ["Hello", ",", " ", "world", "!"]),
]
for inp, expected in checks:
    got = tokenize(inp)
    assert got == expected, f'\n  in: {inp!r}\n  exp: {expected!r}\n  got: {got!r}'
print(f'[OK] {len(checks)} behavioural spot-checks all pass')


# ---------------------------------------------------------------------------
# classify() helper
# ---------------------------------------------------------------------------
print()
print('=== classify() ===')
assert classify('')        == CLASS_EMPTY
assert classify('hello')   == CLASS_WORD
assert classify("don't")   == CLASS_WORD       # word class even with interior '
assert classify('3.14')    == CLASS_WORD
assert classify(' ')       == CLASS_WHITESPACE
assert classify('\n\n')    == CLASS_WHITESPACE
assert classify('.')       == CLASS_PUNCT
assert classify('{')       == CLASS_PUNCT
assert classify('|')       == CLASS_PUNCT
print('[OK] classify returns correct class for all token types')


print()
print('=== Step 6 verification PASSED ===')
