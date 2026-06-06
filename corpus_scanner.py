"""
corpus_scanner.py — Step 2 of System 1

Scans JSON transcript files, removes ASR chunk-boundary duplication,
cleans text, and yields (video_id, chunk_id, clean_text, metadata) tuples.
Tracks processed files in the corpus_stats SQLite table.

YouTube ASR produces two distinct duplication artifacts:
  1. Within-chunk: each phrase repeats 2-3× immediately in place.
     e.g. "you get you get someone on your team that does someone on your team that does"
  2. Cross-chunk: the last N words of chunk K appear again at the start of chunk K+1.
"""

import html
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Generator

from tqdm import tqdm

from semantic_compression.config import DB_PATH, TRANSCRIPT_DIR

log = logging.getLogger(__name__)

# Type alias
ChunkRecord = tuple[str, int, str, dict]  # (video_id, chunk_id, clean_text, metadata)

_WHITESPACE = re.compile(r'\s+')


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS corpus_stats (
            video_id        TEXT PRIMARY KEY,
            channel_name    TEXT,
            chunk_count     INTEGER,
            token_count     INTEGER,
            processed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """
    Minimal cleanup for transcript text — only HTML entity decoding.

    v1 byte-exact policy: do NOT lowercase, do NOT collapse whitespace.
    Case is handled by caps_codec at encode time. Whitespace runs are
    preserved as their own tokens by the universal tokenizer.

    Note: dedup_within_chunk / dedup_boundary below still operate on
    word-level splits and re-join with single spaces. That's acceptable
    for YouTube ASR (which only has single-space separation in source),
    but means this scanner is YouTube-specific. Real file formats use
    format_adapters + tokenize directly.
    """
    return html.unescape(text)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

_DEDUP_K_MAX = 12   # longest phrase length to try matching
_DEDUP_K_MIN = 2    # shortest phrase (2-word immediate repeats are always ASR artifacts)


def dedup_within_chunk(words: list[str]) -> list[str]:
    """
    Remove immediate phrase repetitions from a single chunk's word list.

    YouTube ASR chunks contain rolling windows where every phrase repeats
    2-3× consecutively. Algorithm: at each position i, find the longest
    phrase of length k (3 ≤ k ≤ 12) whose next occurrence starts immediately
    at i+k. Emit the phrase once and advance past all consecutive copies.
    Falls back to emitting words[i] individually when no repeat is found.
    """
    result: list[str] = []
    i = 0
    n = len(words)

    while i < n:
        matched = False
        # Try longest phrases first to avoid partial-match fragmentation
        for k in range(min(_DEDUP_K_MAX, (n - i) // 2), _DEDUP_K_MIN - 1, -1):
            if words[i:i + k] == words[i + k:i + 2 * k]:
                phrase = words[i:i + k]
                result.extend(phrase)
                # Advance past all consecutive copies of this phrase
                j = i + k
                while j + k <= n and words[j:j + k] == phrase:
                    j += k
                i = j
                matched = True
                break

        if not matched:
            result.append(words[i])
            i += 1

    return result


def dedup_boundary(prev_words: list[str], curr_words: list[str], min_overlap: int = 4) -> list[str]:
    """
    Strip the prefix of curr_words that duplicates the tail of prev_words.

    Checks overlaps from longest (up to 60 words) down to min_overlap.
    Returns curr_words unchanged if no overlap is found.
    """
    if not prev_words or not curr_words:
        return curr_words

    max_check = min(len(prev_words), len(curr_words), 60)
    for overlap in range(max_check, min_overlap - 1, -1):
        if prev_words[-overlap:] == curr_words[:overlap]:
            return curr_words[overlap:]

    return curr_words


# ---------------------------------------------------------------------------
# JSON loading
# ---------------------------------------------------------------------------

def _load_transcript(path: Path) -> dict | None:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        log.warning("Skipping %s — %s", path.name, exc)
        return None


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def scan_transcripts(
    transcript_dir: str = TRANSCRIPT_DIR,
    db_path: str = DB_PATH,
    skip_processed: bool = True,
    limit: int | None = None,
    show_progress: bool = True,
) -> Generator[ChunkRecord, None, None]:
    """
    Yield (video_id, chunk_id, clean_text, metadata) for every chunk
    across all transcript JSON files under transcript_dir.

    Processing per file:
      - Within-chunk ASR dedup (removes rolling-window repetition)
      - Cross-chunk boundary dedup (removes inter-chunk tail/head overlap)
      - Text cleaning (lowercase, whitespace normalization)

    Resumable: files whose video_id already appears in corpus_stats are
    skipped when skip_processed=True.
    """
    conn = _init_db(db_path)
    root = Path(transcript_dir)

    files = sorted(root.rglob("*.json"))
    if limit is not None:
        files = files[:limit]

    already_done: set[str] = set()
    if skip_processed:
        already_done = {
            row[0] for row in conn.execute("SELECT video_id FROM corpus_stats")
        }

    iterator = tqdm(files, desc="Scanning", unit="file") if show_progress else files

    for path in iterator:
        data = _load_transcript(path)
        if data is None:
            continue

        video_id: str = data.get("video_id") or path.stem
        if video_id in already_done:
            continue

        channel_name: str = data.get("channel_name", "unknown")
        title: str        = data.get("title", "")
        chunks: list      = data.get("chunks", [])

        prev_words: list[str] = []
        token_count = 0

        for chunk_id, chunk in enumerate(chunks):
            raw = chunk.get("text", "")
            if not raw.strip():
                continue

            words = clean_text(raw).split()

            # Pass 1: remove within-chunk ASR repetition
            words = dedup_within_chunk(words)

            # Pass 2: remove overlap with previous chunk's tail
            words = dedup_boundary(prev_words, words)

            if not words:
                prev_words = []
                continue

            clean = " ".join(words)
            token_count += len(words)
            prev_words = words

            metadata: dict = {
                "video_id":     video_id,
                "channel_name": channel_name,
                "title":        title,
                "chunk_id":     chunk_id,
                "start":        chunk.get("start"),
                "end":          chunk.get("end"),
                "start_hms":    chunk.get("start_hms"),
                "end_hms":      chunk.get("end_hms"),
                "speaker":      chunk.get("speaker", "unknown"),
            }

            yield video_id, chunk_id, clean, metadata

        conn.execute(
            "INSERT OR REPLACE INTO corpus_stats "
            "(video_id, channel_name, chunk_count, token_count) VALUES (?, ?, ?, ?)",
            (video_id, channel_name, len(chunks), token_count),
        )
        conn.commit()
        already_done.add(video_id)

    conn.close()


def scan_single(path: str, show_progress: bool = False) -> list[ChunkRecord]:
    """
    Scan a single transcript file without writing to the DB.
    Useful for unit tests and spot-checks.
    """
    data = _load_transcript(Path(path))
    if data is None:
        return []

    video_id     = data.get("video_id") or Path(path).stem
    channel_name = data.get("channel_name", "unknown")
    title        = data.get("title", "")
    chunks       = data.get("chunks", [])

    records: list[ChunkRecord] = []
    prev_words: list[str] = []

    for chunk_id, chunk in enumerate(chunks):
        raw = chunk.get("text", "")
        if not raw.strip():
            continue

        words = clean_text(raw).split()
        words = dedup_within_chunk(words)
        words = dedup_boundary(prev_words, words)

        if not words:
            prev_words = []
            continue

        clean = " ".join(words)
        prev_words = words

        metadata: dict = {
            "video_id": video_id, "channel_name": channel_name,
            "title": title, "chunk_id": chunk_id,
            "start": chunk.get("start"), "end": chunk.get("end"),
            "start_hms": chunk.get("start_hms"), "end_hms": chunk.get("end_hms"),
            "speaker": chunk.get("speaker", "unknown"),
        }
        records.append((video_id, chunk_id, clean, metadata))

    return records
