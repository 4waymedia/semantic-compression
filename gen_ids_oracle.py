"""DEPRECATED 2026-09-25 -- this oracle tests an artifact that no longer exists.

Paul, 2026-09-25: "we have NO LLM contract. The LLM training is on hold. We are not using
v1 anymore. That was months ago, and 9 dictionary builds in the past."

`ids.json` pinned integer token ids over the v1 LLM vocabulary contract
(`token-ids-v1.csv.gz`, 373,917 rows, frozen 2026-06-11). That contract is retired. There
is no current LLM tokenizer to generate an oracle FOR, so this file generates nothing:
`conformance_ids` in the browser should be RETIRED, not parked. Integration's instruction to
"regenerate under elo-v5r3" and this lane's day spent on it were a category error built on a
dead document that nobody had marked dead. It is marked now.

Kept in the tree, refusing to run, so the next person who finds `ids.json` referenced
somewhere reads this instead of rebuilding it. Delete when the LLM track resumes on a
current build, and write a new generator against whatever the tokenizer is then.

---- original header follows, for the record ----

Emit `ids.json` -- the LLM-TOKENIZER oracle. The one oracle that had no generator.

    python semantic_compression/gen_ids_oracle.py --build elo-v5 --profile full

WHAT IT PINS, and why it is not the codec oracle
------------------------------------------------
`vectors.json` pins the CODEC: Base64 ids, `.elo`/`.eloB` bytes, `compressor.py` vs
`elo.rs`. This file pins the LLM TOKENIZER: integer token ids over a profile cut, BOS/EOS,
`elo_tokenizer.py` vs the Rust port. Different artifact, different contract, different
failure. A build can be byte-perfect in one and wrong in the other.

The property under test, per integration 2026-09-24: *the Rust port and `elo_tokenizer.py`
produce identical LLM ids.* **Port fidelity, not model compatibility** -- so it is worth
pinning under whatever build is current, and the `(dictionary, model)` lock has no bearing
on it while no model is deployed.

WHY THIS FILE EXISTS (2026-09-24)
---------------------------------
ELO-Browser's `conformance_ids()` (`elo.rs:~1516`) panicked reading
`poc/conformance/elo-v5/ids.json`. That file does not exist -- and neither did a generator
for it. `grep ids_special` across the repo matches only READERS (`elo.rs`, `bindings.toml`)
and the v01 artifact itself. **The v01 oracle was produced out of band**, which is the same
thing `export_browser_vocab.py`'s header records happening to the v01 `.browser.json`:
"produced out-of-band and drifted out of the pipeline".

Integration's instruction was "run the generator against elo_tokenizer.py under elo-v5r3".
There was no generator to run. This is it.

An oracle is GENERATED, never authored, and never by reimplementing the thing it tests --
so this imports the real `EloTokenizer` and records what it returns. A generator that
re-derived the ids itself would produce a test that agrees with itself.

TWO DEPENDENCIES WORTH KNOWING BEFORE YOU RUN IT
------------------------------------------------
1. `llm-training/` is **gitignored**. `elo_tokenizer.py` lives there and is on disk but not
   in git, so this committed script imports an untracked module. That is a real fragility
   and it is deliberate: the alternative is reimplementing the tokenizer here, which is
   worse. If the import fails, this refuses loudly rather than guessing.
2. The tokenizer directory must have been BUILT for the target profile by
   `llm-training/build_tokenizer.py`. As of 2026-09-24 only `compact` and `tiny` exist --
   there is no `full`, which is the cut the browser's chat path ships and the cut the v01
   oracle declares. Building it is the prerequisite, not an optional step.

The text set is the v01 oracle's, VERBATIM and APPEND-ONLY. An oracle whose inputs change
between builds cannot distinguish "the tokenizer changed" from "the test changed".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "elo-dictionary" / "src"))

# APPEND-ONLY. Copied verbatim from ELO-Browser/poc/conformance/elo-browser-v01.ids.json
# so v5's oracle is comparable to v01's line for line. Each one buys a codec branch:
# empty input · plain words · punctuation+caps · proper nouns with internal caps ·
# version strings · runs of whitespace and a newline · a plain sentence · HTML ·
# out-of-vocabulary words · mixed alphanumeric signs · non-ASCII and an emoji.
TEXTS = [
    "",
    "the cat sat on the mat",
    "Hello there, friend.",
    "NASA and the iPhone changed McDonald's forever",
    "i don't think v1.2.3-rc1 is ready",
    "rabbits   and    spaces\nand newlines",
    "meaning is the unit not bytes",
    '<div class="container"><p>hello world</p></div>',
    "synergize blockchain quantum supremacy",
    "EPA is E+0.3 P+0.8 A+0.6 for rules of engagement",
    "café résumé naïve — unicode 😀 test",
]


def _load_tokenizer(tokenizer_dir: Path):
    tdir = ROOT / "llm-training"
    if not (tdir / "elo_tokenizer.py").is_file():
        raise SystemExit(
            f"REFUSING: {tdir / 'elo_tokenizer.py'} not found.\n"
            f"  `llm-training/` is gitignored, so a fresh clone does not have it. This\n"
            f"  generator will not reimplement the tokenizer to work around that -- an\n"
            f"  oracle built from a second implementation tests the second implementation.")
    sys.path.insert(0, str(tdir))
    try:
        from elo_tokenizer import EloTokenizer          # noqa: PLC0415
    except Exception as e:                              # noqa: BLE001
        raise SystemExit(f"REFUSING: cannot import EloTokenizer: {type(e).__name__}: {e}")

    if not tokenizer_dir.is_dir():
        have = sorted(p.name for p in (tdir / "tokenizer").glob("*") if p.is_dir()) \
            if (tdir / "tokenizer").is_dir() else []
        raise SystemExit(
            f"REFUSING: no tokenizer at {tokenizer_dir}.\n"
            f"  built profiles present: {have or '(none)'}\n\n"
            f"  A tokenizer must be BUILT for this profile before an oracle can record it:\n"
            f"      python llm-training/build_tokenizer.py --build <build> "
            f"--profile {tokenizer_dir.name}\n\n"
            f"  This is the missing link the browser hit on 2026-09-24: the oracle had no\n"
            f"  generator AND the `full` profile had never been built. Neither is a path\n"
            f"  you can repoint your way out of.")
    return EloTokenizer.from_pretrained(tokenizer_dir)


def build_oracle(build: str, profile: str, tokenizer_dir: Path) -> dict:
    tok = _load_tokenizer(tokenizer_dir)

    vectors = []
    for text in TEXTS:
        ids = tok.encode(text, add_special_tokens=False)
        ids_special = tok.encode(text, add_special_tokens=True)
        vectors.append({"text": text, "ids": list(ids),
                        "ids_special": list(ids_special), "n": len(ids)})

    # Shape FIRST, identity SECOND: the five v01 keys are kept exactly so `elo.rs` reads
    # this file with no Rust change, and the identity block is additive.
    out = {
        "dictionary": build,
        "profile": profile,
        "total_vocab": tok.vocab_size,
        "bos": tok.bos_token_id,
        "eos": tok.eos_token_id,
    }

    # WHICH dictionary produced these ids (2026-09-22 helper). The v01 oracle named a
    # build in `dictionary` and nothing else -- no fingerprint, no revision, no codec
    # policy -- so a regenerated oracle was indistinguishable from a relabelled one.
    try:
        from compression_dictionary import dictionary_identity   # noqa: PLC0415
        out.update(dictionary_identity(prefix="oracle_"))
    except Exception as e:                                       # noqa: BLE001
        out["oracle_identity_error"] = (
            f"{type(e).__name__}: {e} -- this oracle does not say which dictionary "
            f"produced it. Do not treat a pass as meaningful.")

    out["generated_by"] = "semantic_compression/gen_ids_oracle.py"
    out["vectors"] = vectors
    return out


def main(argv=None) -> int:
    raise SystemExit(
        "DEPRECATED: ids.json tested the v1 LLM vocabulary contract, which is retired "
        "(Paul, 2026-09-25 -- no LLM contract, training on hold, v1 unused for 9 builds). "
        "There is nothing to generate. Retire `conformance_ids` in the browser; do not "
        "park it. See this file's docstring.")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", required=True, help="dictionary build name, e.g. elo-v5")
    ap.add_argument("--profile", default="full",
                    help="profile cut the tokenizer was built for (default: full)")
    ap.add_argument("--tokenizer-dir", type=Path, default=None,
                    help="default: llm-training/tokenizer/<profile>")
    ap.add_argument("--out", type=Path, default=None,
                    help="default: ELO-Browser/poc/conformance/<build>/ids.json")
    ap.add_argument("--check", action="store_true",
                    help="compare against the file on disk; exit 1 on drift, write nothing")
    a = ap.parse_args(argv)

    tdir = a.tokenizer_dir or (ROOT / "llm-training" / "tokenizer" / a.profile)
    oracle = build_oracle(a.build, a.profile, tdir)
    dst = a.out or (ROOT / "ELO-Browser" / "poc" / "conformance" / a.build / "ids.json")

    print(f"  build        {oracle['dictionary']}   profile {oracle['profile']}")
    print(f"  total_vocab  {oracle['total_vocab']:,}   bos {oracle['bos']}  "
          f"eos {oracle['eos']}")
    print(f"  identity     bundle_id={oracle.get('oracle_bundle_id')}  "
          f"codec_policy={oracle.get('oracle_codec_policy_version')}")
    print(f"  vectors      {len(oracle['vectors'])}  "
          f"(ids: {sum(v['n'] for v in oracle['vectors'])} total)")

    if a.check:
        if not dst.is_file():
            print(f"  MISSING      {dst}")
            return 1
        cur = json.loads(dst.read_text(encoding="utf-8"))
        same = (cur.get("vectors") == oracle["vectors"]
                and cur.get("total_vocab") == oracle["total_vocab"]
                and cur.get("bos") == oracle["bos"]
                and cur.get("eos") == oracle["eos"])
        cur_pol = cur.get("oracle_codec_policy_version")
        if cur_pol is None:
            print("  UNVERIFIABLE -- oracle on disk carries no identity block. Regenerate.")
        elif cur_pol != oracle.get("oracle_codec_policy_version"):
            print(f"  POLICY DRIFT -- oracle was generated under codec policy {cur_pol}, "
                  f"this build is {oracle.get('oracle_codec_policy_version')}.")
        else:
            print(f"  {'no drift' if same else 'DRIFT -- regenerate'}")
        return 0 if same and cur_pol == oracle.get("oracle_codec_policy_version") else 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(oracle, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  wrote        {dst.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
