"""
meta_layer2.py -- the System-2 layer-2 pass over a build's meta.db (L7 slice 1).

Fills the reserved S2 columns `epa_e/p/a` + `polarity` from the build package's
own id-keyed `epa` sub-DB (written by epa_match.py against the global EPA
substrate -- direct + lemma + composed; NO difflib, rejected per O-EPA5/O-EPA8).
Also fills `frequency` (words from the build's word-frequency file, phrases from
phrase_candidates.txt) so coverage can be token-weighted.

Flips meta_layer 1->2, bumps meta_format_version, records the substrate identity
(global_epa_version) and a separate S2 fingerprint; the S1 deterministic
fingerprint is untouched. `polarity` stays NULL for unrated surfaces -- never
fake-neutral (O-EPA9). agency/directionality/temporal_stage remain reserved
(separate S2 components, spec-meta-db.md §5).

Usage: python meta_layer2.py db/builds/general_v0.4_char4
Spec:  docs/compression/spec-meta-db.md §5 ; audit-meta-layer2-epa.md (addendum)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import lmdb

EPA_STRUCT = struct.Struct("<fff")

# Polarity binning (documented constants; spec §5 gives the rule, not the values)
POL_T        = 0.5   # neg if E < -POL_T ; pos if E > +POL_T ; else neutral
BIPOLAR_EMAX = 0.5   # bipolar if |E| <= BIPOLAR_EMAX and A >= BIPOLAR_AMIN
BIPOLAR_AMIN = 1.5

# Abstraction backfill. PRIMARY: Brysbaert et al. (2014) concreteness norms via
# concreteness_substrate.lmdb -- concreteness is its own lexical axis; the N4
# scale-up (n4-gold-scaleup-review.md) measured the affect proxy at 0.665/0.720
# vs the 0.80 target. FALLBACK (norms-OOV only): sign of Activity. All S2-derived
# values enter the S2 fingerprint, NOT the S1 deterministic one.
CONC_T    = 3.0   # Brysbaert scale midpoint: concrete if Conc.M >= CONC_T (1..5)
ABSTR_A_T = 0.0   # fallback: concrete if EPA-A < ABSTR_A_T

META_FORMAT_VERSION = "2"


def polarity_of(e: float, a: float) -> str:
    if abs(e) <= BIPOLAR_EMAX and a >= BIPOLAR_AMIN:
        return "bipolar"
    if e < -POL_T:
        return "negative"
    if e > POL_T:
        return "positive"
    return "neutral"


def load_epa_subdb(lmdb_path: Path) -> dict[str, tuple[float, float, float]]:
    env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=8, lock=False)
    db = env.open_db(b"epa", create=False)
    out = {}
    with env.begin() as txn:
        for k, v in txn.cursor(db=db):
            out[k.decode("utf-8", "replace")] = EPA_STRUCT.unpack(v)
    env.close()
    return out


CONC_STRUCT = struct.Struct("<ff")   # (conc_mean 1..5, percent_known)

def load_concreteness(lmdb_path: Path) -> tuple[dict[str, float], str]:
    """{surface: conc_mean} (en| prefix stripped) + substrate version string."""
    if not lmdb_path or not Path(lmdb_path).exists():
        return {}, ""
    env = lmdb.open(str(lmdb_path), readonly=True, max_dbs=4, lock=False)
    db = env.open_db(b"conc", create=False)
    mdb = env.open_db(b"meta", create=False)
    out, ver = {}, ""
    with env.begin() as txn:
        for k, v in txn.cursor(db=db):
            s = k.decode("utf-8", "replace")
            out[s[3:] if s.startswith("en|") else s] = CONC_STRUCT.unpack(v)[0]
        ver = (txn.get(b"concreteness_version", db=mdb) or b"").decode()
    env.close()
    return out, ver


def load_word_freq(path: Path) -> dict[str, int]:
    freq: dict[str, int] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or "\t" not in line:
                continue
            count_str, escaped = line.rstrip("\n").split("\t", 1)
            try:
                token = escaped.encode("ascii").decode("unicode_escape")
            except UnicodeDecodeError:
                token = escaped
            freq[token] = int(count_str)
    return freq


def load_phrase_freq(path: Path) -> dict[str, int]:
    freq: dict[str, int] = {}
    if not path.exists():
        return freq
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 8:
                try:
                    freq[parts[7]] = int(parts[2])
                except ValueError:
                    continue
    return freq


def run_layer2(build_dir: Path, word_freq_file: Path | None = None,
               phrase_freq_file: Path | None = None,
               concreteness_lmdb: Path | None = None) -> dict:
    build_dir = Path(build_dir)
    meta_db   = build_dir / "meta.db"
    lmdb_path = build_dir / "dictionary.lmdb"

    epa = load_epa_subdb(lmdb_path)
    conc, conc_ver = load_concreteness(concreteness_lmdb) if concreteness_lmdb else ({}, "")
    epa_stats = json.loads((build_dir / "epa_stats.json").read_text()) \
        if (build_dir / "epa_stats.json").exists() else {}
    dict_stats = json.loads((build_dir / "dict_stats.json").read_text()) \
        if (build_dir / "dict_stats.json").exists() else {}

    wfreq = load_word_freq(word_freq_file) if word_freq_file and word_freq_file.exists() else {}
    pfreq = load_phrase_freq(phrase_freq_file) if phrase_freq_file else {}

    # Work on a local copy (SQLite can't journal on the mounted FS).
    tmp = Path(tempfile.gettempdir()) / f"_meta_l2_{os.getpid()}.db"
    shutil.copyfile(meta_db, tmp)
    con = sqlite3.connect(tmp)

    cols = {r[1] for r in con.execute("PRAGMA table_info(meta)")}
    if "frequency" not in cols:
        # NOT in DET_COLS -- S1 deterministic fingerprint unaffected.
        con.execute("ALTER TABLE meta ADD COLUMN frequency INTEGER")

    rows = list(con.execute(
        "SELECT surface, id, kind, bucket, utility, abstraction FROM meta"))
    filled = unrated = abstr_filled = abstr_norm = abstr_fallback = 0
    freq_hits = 0
    tok_total = tok_covered = 0
    fp = hashlib.sha256()
    up_epa, up_freq, up_abstr = [], [], []
    for surface, sid, kind, bucket, utility, s1_abstr in rows:
        f = wfreq.get(surface) if kind == "word" else pfreq.get(surface)
        if f is not None:
            up_freq.append((f, surface))
            freq_hits += 1
            tok_total += f
        v = epa.get(sid)
        if v is not None:
            e, p, a = v
            pol = polarity_of(e, a)
            up_epa.append((round(e, 6), round(p, 6), round(a, 6), pol, surface))
            filled += 1
            if f is not None:
                tok_covered += f
            ab = ""
            if (s1_abstr in (None, "", "null") and utility == "CONTENT"
                    and bucket in ("TOPIC", "CONCEPT")):
                cm = conc.get(surface)
                if cm is not None:
                    ab = "abstract" if cm < CONC_T else "concrete"
                    abstr_norm += 1
                else:
                    ab = "abstract" if a >= ABSTR_A_T else "concrete"
                    abstr_fallback += 1
                up_abstr.append((ab, surface))
                abstr_filled += 1
            fp.update(f"{surface}\t{e:.6f},{p:.6f},{a:.6f},{pol},{ab}\n".encode())
        elif (s1_abstr in (None, "", "null") and utility == "CONTENT"
                and bucket in ("TOPIC", "CONCEPT") and surface in conc):
            # norms cover surfaces EPA doesn't -- abstraction from norms alone
            ab = "abstract" if conc[surface] < CONC_T else "concrete"
            up_abstr.append((ab, surface))
            abstr_filled += 1
            abstr_norm += 1
            fp.update(f"{surface}\t,,,,{ab}\n".encode())
            unrated += 1
        else:
            unrated += 1

    con.executemany("UPDATE meta SET epa_e=?, epa_p=?, epa_a=?, polarity=? WHERE surface=?", up_epa)
    con.executemany("UPDATE meta SET frequency=? WHERE surface=?", up_freq)
    con.executemany("UPDATE meta SET abstraction=? WHERE surface=?", up_abstr)

    info = {
        "meta_format_version": META_FORMAT_VERSION,
        "meta_layer": "2",
        "s2_fingerprint": fp.hexdigest(),
        "s2_source": "epa_subdb(epa_match)",
        "global_epa_version": str(epa_stats.get("global_epa_version", "")),
        "epa_subdb_entries": str(len(epa)),
        "polarity_bins": f"t={POL_T},bipolar:|E|<={BIPOLAR_EMAX}&A>={BIPOLAR_AMIN}",
        "abstraction_l2_backfill": (
            f"brysbaert(Conc.M>={CONC_T}=concrete, v={conc_ver or 'ABSENT'}) primary; "
            f"sign(A) at {ABSTR_A_T} fallback; CONTENT TOPIC/CONCEPT where S1 null"),
        "concreteness_version": conc_ver,
        "layer2_built_utc": datetime.now(timezone.utc).isoformat(),
    }
    con.executemany("INSERT OR REPLACE INTO meta_info VALUES (?,?)", list(info.items()))
    con.commit()

    n = con.execute("SELECT COUNT(*) FROM meta WHERE epa_e IS NOT NULL").fetchone()[0]
    assert n == filled, f"fill count mismatch {n} != {filled}"
    con.close()

    # Land atomically. NEVER fall back to copying over the live DB: a partial
    # write leaves a malformed SQLite image (observed on the mounted FS, where
    # os.replace/unlink raise EPERM). Verify the staged copy, then rename-over.
    tmp2 = meta_db.with_suffix(".db.tmp")
    shutil.copyfile(tmp, tmp2)
    with open(tmp2, "rb+") as fh:            # rb+ (writable) — Windows os.fsync needs a
        os.fsync(fh.fileno())                # write fd; "rb" raises EBADF (Errno 9)
    _v = sqlite3.connect(tmp2)
    _ok = _v.execute("PRAGMA integrity_check").fetchone()[0]
    _v.close()
    if _ok != "ok":
        raise RuntimeError(f"staged meta.db failed integrity_check: {_ok!r}")
    try:
        os.replace(tmp2, meta_db)
    except OSError as e:
        raise RuntimeError(
            f"could not atomically replace {meta_db} ({e}). A VALID rebuilt DB is "
            f"staged at {tmp2} -- move it into place manually. Refusing to "
            "overwrite in place: a non-atomic copy corrupts a live SQLite file."
        ) from e
    tmp.unlink()

    total = len(rows)
    stats = {
        "rows": total,
        "epa_filled": filled,
        "epa_unrated": unrated,
        "pct_filled": round(filled / total * 100, 1),
        "abstraction_backfilled": abstr_filled,
        "abstraction_from_norms": abstr_norm,
        "abstraction_from_fallback": abstr_fallback,
        "frequency_filled": freq_hits,
        "corpus_tokens_seen": tok_total,
        "corpus_tokens_epa_covered": tok_covered,
        "token_weighted_epa_coverage_pct":
            round(tok_covered / tok_total * 100, 1) if tok_total else None,
        "corpus_total_tokens_dict_stats": dict_stats.get("total_corpus_tokens"),
        **info,
    }
    (build_dir / "meta_layer2_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


if __name__ == "__main__":
    pkg = Path(sys.argv[1])
    data = pkg.parent.parent.parent / "data"
    # default: the global concreteness substrate (Memory/data); override via argv[2]
    conc_default = pkg.resolve().parents[3] / "Memory" / "data" / "concreteness_substrate.lmdb"
    conc_path = Path(sys.argv[2]) if len(sys.argv) > 2 else conc_default
    s = run_layer2(
        pkg,
        word_freq_file=data / f"word_frequencies_{pkg.name}.txt",
        phrase_freq_file=data / "phrase_candidates.txt",
        concreteness_lmdb=conc_path,
    )
    print(json.dumps(s, indent=2))
