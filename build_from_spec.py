"""
build_from_spec.py -- build a dictionary from a single declarative YAML build spec.

One spec file fully defines a build: metadata (name/date/author/model/purpose/
goals/use-cases), the CORPUS it is built from, the held-out EVAL/tests, and the
build parameters (size/strategy/reserve/facets). The resolver records the full
spec + a reproducible CORPUS FINGERPRINT (every source file hashed) into the
build package's manifest.json, so each dictionary permanently declares exactly
what produced it. Different dictionaries = different spec files.

    python -m semantic_compression.build_from_spec semantic_compression/builds/<name>.yaml
"""
from __future__ import annotations

import hashlib
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, ".")
sys.path.insert(0, "semantic_compression")
import semantic_compression.dictionary_builder_v03 as bld
from semantic_compression.tokenizer import tokenize
from semantic_compression import build_suite

SIZE_MAX_TIER = {"char-2": 1, "char-3": 2, "char-4": 3}
STRATEGIES = {"frequency": bld.score_by_frequency, "bytes_saved": bld.score_by_bytes_saved}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_corpus(spec: dict, repo_root: Path) -> tuple[Path, dict]:
    """Merge all corpus sources into one word_frequencies file; return (path, manifest)."""
    held_out = set(spec.get("eval", {}).get("held_out_books", []))
    sources_manifest = []
    base = Counter()

    for src in spec.get("corpus", []):
        stype = src["type"]
        weight = float(src.get("weight", 1.0))
        if "precomputed" in src:                       # e.g. transcripts counts
            pc = repo_root / src["precomputed"]
            counts = bld.load_word_frequencies(pc)
            for w, c in counts.items():
                base[w] += int(round(c * weight))
            sources_manifest.append({"type": stype, "file": src["precomputed"],
                                     "sha256": _sha256(pc), "weight": weight,
                                     "unique_tokens": len(counts)})
        else:                                          # raw text files (books, etc.)
            root = repo_root / src["path"]
            files = sorted(root.glob(src.get("include", "*.txt")))
            for f in files:
                if f.name in held_out:                 # eval-only -> never in corpus
                    continue
                t = f.read_text(encoding="utf-8", errors="replace")
                toks = tokenize(t)
                for w in toks:
                    base[w] += int(round(weight))
                sources_manifest.append({"type": stype, "file": str(f.relative_to(repo_root)),
                                         "sha256": _sha256(f), "weight": weight,
                                         "tokens": len(toks)})

    # reproducible corpus fingerprint over (file, sha, weight), sorted
    fp = hashlib.sha256()
    for m in sorted(sources_manifest, key=lambda x: x["file"]):
        fp.update(f"{m['file']}:{m['sha256']}:{m['weight']}\n".encode())
    corpus_fingerprint = fp.hexdigest()

    name = spec["meta"]["name"]
    out = Path("semantic_compression/data") / f"word_frequencies_{name}.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# word_frequencies for build '{name}'  corpus_fingerprint={corpus_fingerprint}\n")
        f.write("# count\\tescaped_token\n")
        for w, c in sorted(base.items(), key=lambda kv: -kv[1]):
            f.write(f"{c}\t{w.encode('unicode_escape').decode('ascii')}\n")

    manifest = {"corpus_fingerprint": corpus_fingerprint,
                "corpus_sources": sources_manifest,
                "corpus_unique_tokens": len(base),
                "corpus_total_tokens": sum(base.values()),
                "held_out_books": sorted(held_out)}
    return out, manifest


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: build_from_spec.py <spec.yaml>"); sys.exit(1)
    repo_root = Path(".")
    spec = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
    meta, build = spec["meta"], spec.get("build", {})
    name = meta["name"]

    # Resolve the declared asset suite (FAIL FAST on an inconsistent declaration).
    # build_from_spec builds the CORE (dictionary + optionally facets + meta); the
    # rest of the suite (epa/meta_layer2/vectors/browser) is derived by build_assets.
    enabled = build_suite.declared_set(build)
    preset = build.get("suite", "standard")
    core = [a for a in ("dictionary", "facets", "meta") if a in enabled]
    downstream = sorted(enabled - set(core))
    print(f"=== build '{name}'  ({meta.get('purpose','')[:60]}) ===")
    print(f"suite: preset={preset}  core={core}"
          + (f"  downstream(build_assets)={downstream}" if downstream else ""))
    wf, corpus_manifest = resolve_corpus(spec, repo_root)
    print(f"corpus: {corpus_manifest['corpus_unique_tokens']:,} unique / "
          f"{corpus_manifest['corpus_total_tokens']:,} tokens / "
          f"{len(corpus_manifest['corpus_sources'])} sources / "
          f"fp={corpus_manifest['corpus_fingerprint'][:16]}…")

    out_dir = Path("semantic_compression/db/builds") / name
    # Record the resolved suite so build_assets + the registry read one declaration.
    suite_record = {"preset": preset, "enabled": sorted(enabled)}
    extra = {"spec_meta": meta, "build_params": build, "suite": suite_record,
             "eval": spec.get("eval", {}),
             "instructions": spec.get("instructions", ""), **corpus_manifest,
             "built_by_spec": str(sys.argv[1]),
             "resolved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    m = bld.build_package(
        out_dir, overwrite=True, force=False,
        max_tier=SIZE_MAX_TIER[build.get("size", "char-4")],
        min_freq=int(build.get("min_freq", 1)),
        tier1_word_reserve=int(build.get("tier1_word_reserve", 1024)),
        select_strategy=STRATEGIES[build.get("select_strategy", "frequency")],
        # Structural characters are grammar, not vocabulary — guaranteed a slot in
        # every cut regardless of corpus frequency (see dictionary_builder_v03).
        force_include=build.get("force_include") or [],
        with_facets=("facets" in enabled),          # SUITE-gated (was raw with_facets)
        word_freq_file=wf,
        # PHRASES ARE PART OF THE CORPUS DECLARATION, not a fixed asset.
        #
        # This was hardcoded to data/phrase_candidates.txt — phrases mined from the
        # 327M-token TRANSCRIPT corpus, carrying transcript frequencies. So a build
        # declaring a different `corpus:` still inherited transcript phrases, and they
        # won on frequency because they were counted against a corpus 20x larger.
        #
        # Measured 2026-07-31 on books-fitted-v1 (22 public-domain books): the build
        # produced 81,434 phrases and 1,819 words, dropping 163,118 book words. The
        # top phrases were 'you know' (539,244) and 'going to' (275,046) — Victorian
        # novels do not say 'you know' half a million times. The `corpus:` block only
        # ever controlled the WORD half of the competition.
        phrase_file=(repo_root / build["phrase_file"]) if build.get("phrase_file")
                    else (repo_root / 'semantic_compression/data/phrase_candidates.txt'),
        extra_manifest=extra)
    # Meta layer (System-1 deterministic) -> meta.db  — only if the suite declares it
    if "meta" in enabled:
        try:
            sys.path.insert(0, "semantic_compression")
            import meta_builder
            ms = meta_builder.build_meta(out_dir / "dictionary.lmdb", out_dir / "meta.db")
            print(f"       meta: {ms['rows']:,} rows  fp={ms['meta_fingerprint'][:12]}")
        except Exception as e:
            print(f"       [meta] skipped: {e}")
    else:
        print("       meta: off (not in suite)")
    # Unified artifact identity registry (dictionary/facets/epa/meta/templates).
    from semantic_compression.artifact_identity import write_registry, validate_registry
    reg = write_registry(out_dir)
    problems = validate_registry(reg)
    print(f"\n[done] package -> {out_dir}/manifest.json")
    print(f"       size={m['size']} entries={m['entries']:,} "
          f"corpus_fp={corpus_manifest['corpus_fingerprint'][:16]}…")
    print(f"       artifacts: " + ", ".join(
        f"{k}{'' if a['present'] else '(reserved)'}" for k, a in reg.items()))
    if problems:
        print(f"       [WARN] registry problems: {problems}")


if __name__ == "__main__":
    main()
