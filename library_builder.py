"""
library_builder.py — Step 3 of System 1

Builds the canonical word library from corpus frequency data.

Phases:
  1. count_frequencies  — stream corpus, tally surface-form counts
  2. lemmatize_vocab    — spaCy batch lemmatization of unique surface forms
  3. assign_ids         — tier assignment by frequency rank + Base64 ID gen
  4. embed_words        — sentence-transformers batch encoding (all-mpnet-base-v2)
  5. project_epa        — cosine projection onto E/P/A seed-word axes
  6. classify_stage     — nearest-centroid stage assignment from EPA values
  7. write_db           — batch insert into word_library + id_registry
  8. build_faiss        — IndexFlatIP over L2-normalised embeddings
"""

import logging
import sqlite3
from collections import Counter
from pathlib import Path

import faiss
import numpy as np
import spacy
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

from semantic_compression.config import (
    BASE64_CHARS, DB_PATH, EMBEDDING_MODEL, FAISS_PATH,
    SPACY_MODEL, TIER_CAPACITY, TIER_FREQ_RANK, TIER_WORD_FIRST_CHARS,
    TRANSCRIPT_DIR,
)
from semantic_compression.corpus_scanner import scan_transcripts

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier ID spaces
# ---------------------------------------------------------------------------

# All tiers use TIER_WORD_FIRST_CHARS (g-z, 20 chars) as first char.
# Tier is distinguished by length: 2=Tier1, 3=Tier2, 4=Tier3.
_TIER_FIRST = TIER_WORD_FIRST_CHARS   # 'ghijklmnopqrstuvwxyz'
_TIER_CAPACITY = TIER_CAPACITY


def _encode_id(tier: int, counter: int) -> str:
    """
    Convert sequential counter to a tier-appropriate Base64 ID.
    All tiers use g-z as first char; length encodes tier (2/3/4).
    """
    length = tier + 1   # tier1→2-char, tier2→3-char, tier3→4-char
    chars = []
    remaining = counter
    for _ in range(length - 1):
        chars.append(BASE64_CHARS[remaining % 64])
        remaining //= 64
    if remaining >= len(_TIER_FIRST):
        raise OverflowError(f"Tier {tier} ID space exhausted (counter={counter})")
    chars.append(_TIER_FIRST[remaining])
    return ''.join(reversed(chars))


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

def init_db_schema(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Create all System 1 tables and indexes if they don't exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS word_library (
            id          TEXT PRIMARY KEY,
            tier        INTEGER NOT NULL,
            surface     TEXT NOT NULL,
            lemma       TEXT NOT NULL,
            frequency   INTEGER DEFAULT 0,
            pos_tag     TEXT,
            role        TEXT,
            epa_e       REAL,
            epa_p       REAL,
            epa_a       REAL,
            stage       TEXT,
            vector_id   INTEGER,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS phrase_library (
            id              TEXT PRIMARY KEY,
            tier            INTEGER NOT NULL,
            canonical_form  TEXT NOT NULL,
            surface_forms   TEXT,
            token_count     INTEGER,
            frequency       INTEGER DEFAULT 0,
            pragmatic_fn    TEXT,
            pragmatic_sub   TEXT,
            stance          TEXT,
            inference       TEXT,
            epa_e           REAL,
            epa_p           REAL,
            epa_a           REAL,
            stage           TEXT,
            vector_id       INTEGER,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS id_registry (
            id          TEXT PRIMARY KEY,
            type        TEXT NOT NULL,
            tier        INTEGER NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS corpus_stats (
            video_id        TEXT PRIMARY KEY,
            channel_name    TEXT,
            chunk_count     INTEGER,
            token_count     INTEGER,
            processed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_word_lemma   ON word_library(lemma);
        CREATE INDEX IF NOT EXISTS idx_word_tier    ON word_library(tier);
        CREATE INDEX IF NOT EXISTS idx_word_freq    ON word_library(frequency DESC);
        CREATE INDEX IF NOT EXISTS idx_phrase_canon ON phrase_library(canonical_form);
        CREATE INDEX IF NOT EXISTS idx_phrase_tier  ON phrase_library(tier);
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Phase 1 — frequency counting
# ---------------------------------------------------------------------------

_ALPHA_MIN_LEN = 2   # ignore single-char tokens (except meaningful ones)

def count_frequencies(
    transcript_dir: str = TRANSCRIPT_DIR,
    db_path: str = DB_PATH,
    file_limit: int | None = None,
) -> Counter:
    """
    Stream all corpus chunks and count surface-form word frequencies.
    Returns Counter({surface_form: count}).
    Filters out tokens that are purely numeric or < 2 chars.
    """
    counts: Counter = Counter()
    for _vid, _cid, text, _meta in scan_transcripts(
        transcript_dir=transcript_dir,
        db_path=db_path,
        skip_processed=False,
        limit=file_limit,
        show_progress=True,
    ):
        for word in text.split():
            if len(word) >= _ALPHA_MIN_LEN and not word.isdigit():
                counts[word] += 1
    log.info("Counted %d unique surface forms", len(counts))
    return counts


# ---------------------------------------------------------------------------
# Phase 2 — lemmatization
# ---------------------------------------------------------------------------

def _get_role(pos: str, dep: str) -> str:
    role_map = {
        'DET': 'determiner', 'CCONJ': 'conjunction', 'SCONJ': 'conjunction',
        'AUX': 'auxiliary',  'VERB': 'verb',          'NOUN': 'noun',
        'PROPN': 'proper_noun', 'ADJ': 'adjective',   'ADV': 'adverb',
        'PRON': 'pronoun',   'ADP': 'preposition',    'PART': 'particle',
        'INTJ': 'interjection', 'NUM': 'numeral',
    }
    return role_map.get(pos, 'other')


def lemmatize_vocab(
    surface_counts: Counter,
    top_n: int = 100_000,
    batch_size: int = 1000,
) -> dict:
    """
    Lemmatize the top_n most frequent surface forms using spaCy.

    Returns:
        {lemma: {'frequency': int, 'pos': str, 'role': str, 'surfaces': [str]}}
    """
    nlp = spacy.load(SPACY_MODEL, disable=['parser', 'ner'])

    # Work with top_n most frequent forms only
    top_surfaces = [w for w, _ in surface_counts.most_common(top_n)]

    lemma_data: dict = {}
    for i in tqdm(range(0, len(top_surfaces), batch_size), desc="Lemmatizing"):
        batch = top_surfaces[i:i + batch_size]
        # Process each word as a single-token doc for accurate lemmatization
        docs = list(nlp.pipe(batch))
        for surface, doc in zip(batch, docs):
            if not doc or not doc[0].is_alpha:
                continue
            token = doc[0]
            lemma = token.lemma_.lower()
            freq = surface_counts[surface]
            if lemma not in lemma_data:
                lemma_data[lemma] = {
                    'frequency': 0,
                    'pos': token.pos_,
                    'role': _get_role(token.pos_, token.dep_),
                    'surfaces': [],
                }
            lemma_data[lemma]['frequency'] += freq
            lemma_data[lemma]['surfaces'].append(surface)

    log.info("Lemmatized to %d unique lemmas", len(lemma_data))
    return lemma_data


# ---------------------------------------------------------------------------
# Phase 3 — tier assignment + ID generation
# ---------------------------------------------------------------------------

def assign_ids(lemma_data: dict, conn: sqlite3.Connection) -> list[dict]:
    """
    Sort lemmas by frequency, assign tiers and Base64 IDs.
    Registers each ID in id_registry (collision prevention).
    Returns list of word record dicts ready for embedding.
    """
    # Load already-registered IDs to avoid collisions
    registered = {row[0] for row in conn.execute("SELECT id FROM id_registry")}

    # Sort by frequency descending
    sorted_lemmas = sorted(lemma_data.items(), key=lambda x: -x[1]['frequency'])

    # Tier rank boundaries — cap Tier 1 at actual ID-space capacity (1,280)
    tier1_max = min(TIER_FREQ_RANK[1][1], _TIER_CAPACITY[1])  # 1,000 (fits in 1,280 slots)
    tier2_max = TIER_FREQ_RANK[2][1]                           # 10000

    counters = {1: 0, 2: 0, 3: 0}
    records = []

    for rank, (lemma, data) in enumerate(tqdm(sorted_lemmas, desc="Assigning IDs"), start=1):
        if rank <= tier1_max:
            tier = 1
        elif rank <= tier2_max:
            tier = 2
        else:
            tier = 3

        # Generate next available ID for this tier
        while True:
            token_id = _encode_id(tier, counters[tier])
            counters[tier] += 1
            if token_id not in registered:
                registered.add(token_id)
                break

        records.append({
            'id':       token_id,
            'tier':     tier,
            'lemma':    lemma,
            'surface':  data['surfaces'][0],   # most frequent surface form
            'frequency': data['frequency'],
            'pos_tag':  data['pos'],
            'role':     data['role'],
        })

    log.info(
        "IDs assigned — Tier1: %d, Tier2: %d, Tier3: %d",
        counters[1], counters[2], counters[3],
    )
    return records


# ---------------------------------------------------------------------------
# Phase 4 — embedding
# ---------------------------------------------------------------------------

def embed_words(
    lemmas: list[str],
    batch_size: int = 256,
) -> np.ndarray:
    """
    Encode lemmas with sentence-transformers and L2-normalise.
    Returns float32 array of shape (N, EMBEDDING_DIM).
    """
    model = SentenceTransformer(EMBEDDING_MODEL)
    embeddings = model.encode(
        lemmas,
        batch_size=batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,   # L2-norm → cosine via inner product
        convert_to_numpy=True,
    )
    return embeddings.astype(np.float32)


# ---------------------------------------------------------------------------
# Phase 5 — EPA projection
# ---------------------------------------------------------------------------

# Seed words that anchor each EPA pole
_EPA_SEEDS = {
    'E+': ["good", "wonderful", "excellent", "pleasant", "positive", "beautiful", "great", "nice"],
    'E-': ["bad", "terrible", "awful", "horrible", "negative", "nasty", "evil", "ugly"],
    'P+': ["strong", "powerful", "dominant", "forceful", "mighty", "potent", "heavy", "bold"],
    'P-': ["weak", "fragile", "powerless", "timid", "frail", "helpless", "small", "feeble"],
    'A+': ["active", "fast", "energetic", "dynamic", "lively", "quick", "vibrant", "alive"],
    'A-': ["passive", "slow", "calm", "inactive", "still", "quiet", "sluggish", "dormant"],
}


def _build_epa_axes(model: SentenceTransformer) -> dict[str, np.ndarray]:
    """Compute unit-vector axes for each EPA dimension from seed embeddings."""
    axes = {}
    for pole, seeds in _EPA_SEEDS.items():
        vecs = model.encode(seeds, normalize_embeddings=True, convert_to_numpy=True)
        centroid = vecs.mean(axis=0)
        centroid /= np.linalg.norm(centroid) + 1e-9
        axes[pole] = centroid.astype(np.float32)
    return axes


def project_epa(
    embeddings: np.ndarray,
    model: SentenceTransformer,
) -> np.ndarray:
    """
    Project L2-normalised word embeddings onto E, P, A axes.
    Each score = cosine_sim(word, pos_pole) - cosine_sim(word, neg_pole),
    clipped to [-1, 1].
    Returns float32 array of shape (N, 3): columns are [epa_e, epa_p, epa_a].
    """
    axes = _build_epa_axes(model)
    epa = np.column_stack([
        embeddings @ axes['E+'] - embeddings @ axes['E-'],  # Evaluation
        embeddings @ axes['P+'] - embeddings @ axes['P-'],  # Potency
        embeddings @ axes['A+'] - embeddings @ axes['A-'],  # Activity
    ])
    return np.clip(epa, -1.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Phase 6 — stage classification
# ---------------------------------------------------------------------------

# EPA centroids for each process stage (Surov 2022 heuristics)
_STAGE_CENTROIDS = {
    'STAGE_PERCEPTION': np.array([ 0.0,  0.0,  0.5]),  # neutral, slightly active
    'STAGE_NOVELTY':    np.array([ 0.5,  0.0,  0.5]),  # positive, active
    'STAGE_GOAL_PLAN':  np.array([ 0.5,  0.5, -0.2]),  # positive, strong, low activity
    'STAGE_ACTION':     np.array([ 0.0,  0.6,  0.6]),  # strong + active
    'STAGE_PROGRESS':   np.array([ 0.3,  0.5,  0.3]),  # moderate all
    'STAGE_RESULT':     np.array([ 0.6,  0.2, -0.3]),  # positive, settled
}

_STAGE_NAMES  = list(_STAGE_CENTROIDS.keys())
_STAGE_MATRIX = np.array([_STAGE_CENTROIDS[s] for s in _STAGE_NAMES], dtype=np.float32)


def classify_stages(epa_scores: np.ndarray) -> list[str]:
    """
    Assign a process stage to each word via nearest-centroid in EPA space.
    Returns list of stage name strings.
    """
    # Euclidean distance to each centroid
    dists = np.linalg.norm(
        epa_scores[:, None, :] - _STAGE_MATRIX[None, :, :], axis=2
    )
    nearest = np.argmin(dists, axis=1)
    return [_STAGE_NAMES[i] for i in nearest]


# ---------------------------------------------------------------------------
# Phase 7 — DB write
# ---------------------------------------------------------------------------

def write_db(records: list[dict], conn: sqlite3.Connection) -> None:
    """Batch insert word records into word_library and id_registry."""
    word_rows = [
        (r['id'], r['tier'], r['surface'], r['lemma'], r['frequency'],
         r['pos_tag'], r['role'], r['epa_e'], r['epa_p'], r['epa_a'],
         r['stage'], r['vector_id'])
        for r in records
    ]
    registry_rows = [(r['id'], 'word', r['tier']) for r in records]

    conn.executemany(
        "INSERT OR REPLACE INTO word_library "
        "(id, tier, surface, lemma, frequency, pos_tag, role, "
        " epa_e, epa_p, epa_a, stage, vector_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        word_rows,
    )
    conn.executemany(
        "INSERT OR REPLACE INTO id_registry (id, type, tier) VALUES (?,?,?)",
        registry_rows,
    )
    conn.commit()
    log.info("Wrote %d words to DB", len(records))


# ---------------------------------------------------------------------------
# Phase 8 — FAISS index
# ---------------------------------------------------------------------------

def build_faiss_index(embeddings: np.ndarray, faiss_path: str = FAISS_PATH) -> faiss.IndexFlatIP:
    """
    Build a flat inner-product FAISS index (cosine similarity on L2-normalised
    vectors). Saves index to disk and returns the index object.
    """
    Path(faiss_path).parent.mkdir(parents=True, exist_ok=True)
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    faiss.write_index(index, faiss_path)
    log.info("FAISS index built: %d vectors at %s", index.ntotal, faiss_path)
    return index


# ---------------------------------------------------------------------------
# Main build entry point
# ---------------------------------------------------------------------------

def build(
    transcript_dir: str = TRANSCRIPT_DIR,
    db_path: str = DB_PATH,
    faiss_path: str = FAISS_PATH,
    file_limit: int | None = None,
    top_vocab: int = 100_000,
) -> None:
    """
    Run all 8 phases to build the canonical word library.

    Args:
        file_limit:  cap number of transcript files scanned (None = full corpus)
        top_vocab:   max unique surface forms to lemmatize (default 100k)
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    conn = init_db_schema(db_path)

    # Phase 1
    log.info("=== Phase 1: Frequency counting ===")
    surface_counts = count_frequencies(transcript_dir, db_path, file_limit)

    # Phase 2
    log.info("=== Phase 2: Lemmatization ===")
    lemma_data = lemmatize_vocab(surface_counts, top_n=top_vocab)

    # Phase 3
    log.info("=== Phase 3: Tier assignment + ID generation ===")
    records = assign_ids(lemma_data, conn)

    # Phase 4
    log.info("=== Phase 4: Embedding ===")
    lemmas = [r['lemma'] for r in records]
    model = SentenceTransformer(EMBEDDING_MODEL)
    embeddings = embed_words(lemmas, batch_size=256)

    # Phase 5
    log.info("=== Phase 5: EPA projection ===")
    epa_scores = project_epa(embeddings, model)

    # Phase 6
    log.info("=== Phase 6: Stage classification ===")
    stages = classify_stages(epa_scores)

    # Merge EPA + stage into records
    for i, record in enumerate(records):
        record['epa_e']    = float(epa_scores[i, 0])
        record['epa_p']    = float(epa_scores[i, 1])
        record['epa_a']    = float(epa_scores[i, 2])
        record['stage']    = stages[i]
        record['vector_id'] = i

    # Phase 7
    log.info("=== Phase 7: Writing to DB ===")
    write_db(records, conn)

    # Phase 8
    log.info("=== Phase 8: Building FAISS index ===")
    build_faiss_index(embeddings, faiss_path)

    conn.close()
    log.info("=== Library build complete: %d words ===", len(records))
