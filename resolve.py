"""
resolve.py -- THE one function for "which dictionary, and is it the one you were
built against?"

RULING (integration lane, 2026-08-28): *package the RESOLVER, not the dictionary.*
The data stays a build artifact on disk. Resolution becomes one importable function,
one env var, one index.

WHAT THIS REPLACES. A survey found four private implementations of this function:

    Verbalizer            ELO_DICT_DB     -> semantic_compression/db/dictionary.lmdb
    ExtractionPipeline    ELO_DICT_LMDB   -> the same legacy file
    ExtractionPipeline/verify   --        -> Memory/data/dictionary.lmdb
    08-MCP                      --        -> base.dictionary.lmdb

Two spellings of one variable, and the ones that resolved at all landed on a
dictionary predating facets, vfacets, wordclass and the id renumbering -- measured as
fingerprint 9a77e623/v1.2.0 with no `wordclass` sub-db, against a current build of
b0164e50/v4.0.0 with one. Both hold 437,995 forward entries, which is precisely why
four lanes never noticed. Worse, 9a77e623 matches NO build package in the index: the
file every consumer defaulted to is an orphan wearing a real release label.

PRECEDENCE -- and nothing else is legal:

    1. ELO_DICT              explicit path OR build name. ONE spelling.
    2. dist/dictionary/STANDARD.json     the promoted standard. The normal path.
    3. DictionaryUnavailable             a NAMED REFUSAL.

THERE IS NO STEP 4. No default, no legacy file, no guess.

    A resolver that falls back to a default is how four lanes ended up on a
    two-generation-old dictionary without one error message between them.

That is the whole reason this module exists, so the refusal is the feature. It names
what it looked for, what it found, and the one command that fixes it -- the same
shape as MorphArtifactsUnavailable.

BINDING CONTRACT. `build` is a LABEL, never an identity. Ids are build-specific
(`drive`: gjR -> tz -> iMN -> BhN across builds). Bind by SURFACE, resolve ids per
session, never persist a raw dictionary id. Anything that DOES persist ids must
persist `fingerprint` beside them and verify tri-state on read -- matches: proceed;
differs: refuse, naming both; unknown: proceed with a warning. `verify_fingerprint()`
implements exactly that tri-state so no consumer has to re-derive it.

    from compression_dictionary import resolve_dictionary
    d = resolve_dictionary()
    d.path            # the .lmdb to open
    d.fingerprint     # what to bind by
    d.require("wordclass")   # raises if the channel is absent
"""
from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "resolve_dictionary", "ResolvedDictionary", "DictionaryUnavailable",
    "ChannelUnavailable", "FingerprintMismatch", "verify_fingerprint",
    "ENV_VAR", "DEPRECATED_ENV_VARS", "clear_cache",
]

ENV_VAR = "ELO_DICT"
# Kept resolvable for ONE release so nothing breaks silently, but every read warns.
# Two spellings for one variable is how the current mess stayed invisible; these are
# scheduled to raise, not to persist.
DEPRECATED_ENV_VARS = ("ELO_DICT_DB", "ELO_DICT_LMDB")

_STANDARD_REL = Path("dist") / "dictionary" / "STANDARD.json"
_BUILDS_REL = Path("semantic_compression") / "db" / "builds"
# STRONG markers identify the PROJECT root specifically. Weak ones (.git, CLAUDE.md)
# are not usable alone: `semantic_compression` is a git SUBMODULE, so it carries its own
# .git and its own CLAUDE.md, and a nearest-marker walk stops there -- resolving the
# standard to <submodule>/dist/dictionary/STANDARD.json, which does not exist. Measured
# on the first run of this module. Strong markers are checked across every parent before
# any weak marker is considered.
_ROOT_MARKERS_STRONG = ("dist/dictionary/STANDARD.json",
                        "semantic_compression/db/builds",
                        "semantic_compression/dictionary_standard.py")
_ROOT_MARKERS_WEAK = ("CLAUDE.md", ".git")

# Resolution CACHE. Without it, every call reopened the LMDB -- and a consumer that
# already holds the same path open (the verbalizer's lmdb_cache does, by design) hits
# `lmdb.Error: The environment ... is already open in this process`. That broke 4 test
# collections on the first real suite run, 2026-08-29: a regression introduced by the
# repoint itself. Resolution is a lookup, not an I/O operation; it should happen once.
# Keyed on the inputs that can change it, so a promotion mid-process is still seen.
_CACHE: dict = {}


class DictionaryUnavailable(RuntimeError):
    """No dictionary could be resolved. Deliberately fatal -- see module docstring."""


class ChannelUnavailable(RuntimeError):
    """The resolved dictionary lacks a channel the caller requires."""


class FingerprintMismatch(RuntimeError):
    """Persisted ids were built against a different dictionary."""


def _find_root(start: Path | None = None) -> Path | None:
    here = (start or Path(__file__)).resolve()
    chain = [here, *here.parents]
    for m in _ROOT_MARKERS_STRONG:          # strong markers win across the WHOLE chain
        for p in chain:
            if (p / m).exists():
                return p
    for p in chain:                          # weak markers only if no strong one exists
        for m in _ROOT_MARKERS_WEAK:
            if (p / m).exists():
                return p
    return None


@dataclass(frozen=True)
class ResolvedDictionary:
    """A resolved dictionary. `build` is a LABEL; `fingerprint` is the identity."""
    path: Path
    fingerprint: str | None
    build: str | None
    release: str | None
    status: str | None
    channels: tuple = ()
    source: str = ""            # how it was resolved -- for error messages and logs
    standard_path: Path | None = None
    _info: dict = field(default_factory=dict, repr=False, compare=False)

    def has(self, channel: str) -> bool:
        return channel in self.channels

    def require(self, *channels: str) -> "ResolvedDictionary":
        """Assert channels are present. RAISES rather than degrading.

        A missing channel must not read as absent data. That is the exact failure
        this module exists to end: the Verbalizer's wordclass calls resolved against
        a build with no wordclass sub-db and returned 'absent' -- indistinguishable
        from a surface genuinely having no class."""
        missing = [c for c in channels if c not in self.channels]
        if missing:
            raise ChannelUnavailable(
                f"dictionary {self.build or self.path} (fp "
                f"{(self.fingerprint or '?')[:16]}) has no channel(s): "
                f"{', '.join(missing)}.\n"
                f"  present: {', '.join(self.channels) or '(none)'}\n"
                f"  resolved via: {self.source}\n"
                f"  A build lacking a required channel is a WRONG BUILD, not empty "
                f"data. Promote or select a build that carries it:\n"
                f"    python semantic_compression/dictionary_standard.py index")
        return self

    def verify_fingerprint(self, persisted: str | None) -> str:
        return verify_fingerprint(self, persisted)


def verify_fingerprint(resolved: ResolvedDictionary, persisted: str | None,
                       *, strict: bool = True) -> str:
    """Tri-state check for consumers that persisted ids. Returns the verdict.

    matches  -> 'match'    proceed
    differs  -> raises FingerprintMismatch naming BOTH (or 'mismatch' if not strict)
    unknown  -> 'unknown'  proceed, with a warning

    'unknown' is deliberately not an error: data written before fingerprints were
    carried cannot prove itself, and refusing it would strand it. But it warns, so
    it never looks like a verified match."""
    if not persisted:
        warnings.warn(
            "ids carry no dictionary fingerprint -- cannot verify they were built "
            f"against the resolved dictionary ({(resolved.fingerprint or '?')[:16]}). "
            "Proceeding UNVERIFIED.", RuntimeWarning, stacklevel=2)
        return "unknown"
    if resolved.fingerprint and persisted == resolved.fingerprint:
        return "match"
    if strict:
        raise FingerprintMismatch(
            f"ids were built against dictionary {persisted[:16]} but the resolved "
            f"dictionary is {(resolved.fingerprint or '?')[:16]} "
            f"({resolved.build or resolved.path}).\n"
            f"  Ids are NOT stable across builds -- the same surface takes different "
            f"ids in different builds, so reading these ids here yields wrong "
            f"surfaces, silently.\n"
            f"  Re-resolve ids from surfaces, or set {ENV_VAR} to the build these "
            f"ids came from.")
    return "mismatch"


def _read_identity(lmdb_path: Path) -> dict:
    """Identity from the artifact's own meta sub-db. Never from a filename."""
    out = {"fingerprint": None, "release": None, "status": None, "channels": ()}
    try:
        import lmdb  # local import: resolution must work without the build extra
    except Exception:
        return out
    if not lmdb_path.exists():
        return out
    try:
        env = lmdb.open(str(lmdb_path), readonly=True, lock=False, max_dbs=20)
    except lmdb.Error as e:
        # "already open in this process" is NOT an error condition -- it means a
        # consumer got there first, which is the normal case for a cached reader.
        # Signal 'could not read' so the caller falls back to the pointer's recorded
        # values rather than failing resolution outright.
        out["_unreadable"] = str(e)
        return out
    try:
        main = env.open_db()
        with env.begin() as t:
            names = tuple(sorted(bytes(k).decode("utf-8", "replace")
                                 for k, _ in t.cursor(db=main)))
        out["channels"] = names
        if "meta" in names:
            mdb = env.open_db(b"meta", create=False)   # OUTSIDE the txn
            with env.begin() as t:
                for key, f in ((b"dictionary_fingerprint", "fingerprint"),
                               (b"dictionary_release", "release"),
                               (b"dictionary_status", "status")):
                    v = t.get(key, db=mdb)
                    if v:
                        out[f] = v.decode("utf-8", "replace")
    except Exception:
        pass
    finally:
        env.close()
    return out


def _from_path(p: Path, source: str, standard: Path | None = None) -> ResolvedDictionary:
    ident = _read_identity(p)
    return ResolvedDictionary(
        path=p, fingerprint=ident["fingerprint"], build=None,
        release=ident["release"], status=ident["status"],
        channels=ident["channels"], source=source, standard_path=standard)


def resolve_dictionary(root: Path | None = None, *,
                       require: tuple = ()) -> ResolvedDictionary:
    """Resolve the dictionary. See module docstring for precedence.

    Raises DictionaryUnavailable if nothing resolves -- there is no default."""
    r = _find_root(root)
    tried: list[str] = []
    _sp = (r / _STANDARD_REL) if r else None
    try:
        _stamp = (_sp.stat().st_mtime_ns, _sp.stat().st_size) if _sp and _sp.exists() else None
    except OSError:
        _stamp = None
    cache_key = (str(root) if root else None,
                 os.environ.get(ENV_VAR),
                 tuple(os.environ.get(v) for v in DEPRECATED_ENV_VARS),
                 _stamp)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached.require(*require) if require else cached

    # ---- 1. ELO_DICT (path or build name) --------------------------------
    for var in (ENV_VAR, *DEPRECATED_ENV_VARS):
        val = os.environ.get(var)
        if not val:
            continue
        if var != ENV_VAR:
            warnings.warn(
                f"{var} is DEPRECATED and will raise in the next release -- use "
                f"{ENV_VAR}. Two spellings of one variable is how four lanes "
                f"silently resolved to different dictionaries.",
                DeprecationWarning, stacklevel=2)
        cand = Path(val)
        if not cand.exists() and r is not None:
            # a bare build name is legal: ELO_DICT=elo-browser-v04
            byname = r / _BUILDS_REL / val / "dictionary.lmdb"
            if byname.exists():
                cand = byname
        if cand.exists():
            res = _from_path(cand, f"${var}={val}")
            res = ResolvedDictionary(**{**res.__dict__,
                                        "build": (val if not Path(val).exists() else cand.parent.name)})
            _CACHE[cache_key] = res
            return res.require(*require) if require else res
        tried.append(f"${var}={val} (does not exist)")

    # ---- 2. the promoted standard ---------------------------------------
    if r is not None:
        sp = r / _STANDARD_REL
        if sp.exists():
            try:
                doc = json.loads(sp.read_text(encoding="utf-8"))
            except Exception as e:
                raise DictionaryUnavailable(
                    f"{sp} is unreadable ({e}). The standard pointer is the project's "
                    f"single source of truth; a corrupt one must not fall back to a "
                    f"guess.\n  Regenerate: python semantic_compression/"
                    f"dictionary_standard.py promote <build> --allow-staged")
            p = (r / doc["path"]) if doc.get("path") else None
            if p and p.exists():
                ident = _read_identity(p)
                # The pointer is a claim; the ARTIFACT is the authority. If they
                # disagree the pointer is stale -- refuse rather than trust either.
                if (doc.get("dictionary_fingerprint") and ident["fingerprint"]
                        and doc["dictionary_fingerprint"] != ident["fingerprint"]):
                    raise DictionaryUnavailable(
                        f"STANDARD.json is STALE: it names fingerprint "
                        f"{doc['dictionary_fingerprint'][:16]} for build "
                        f"{doc.get('build')}, but that build's LMDB self-reports "
                        f"{(ident['fingerprint'] or '?')[:16]}. The build was rebuilt "
                        f"after promotion.\n  Re-promote: python semantic_compression/"
                        f"dictionary_standard.py promote {doc.get('build')} --allow-staged")
                if ident.get("_unreadable"):
                    # Artifact held open by another reader. Fall back to the values the
                    # pointer recorded -- promote() copied them FROM this artifact, so
                    # they are its own, not a restatement. The staleness check above is
                    # skipped only because there is nothing to compare against; the
                    # source string records that this happened.
                    res = ResolvedDictionary(
                        path=p, fingerprint=doc.get("dictionary_fingerprint"),
                        build=doc.get("build"), release=doc.get("release"),
                        status=doc.get("status"),
                        channels=tuple(doc.get("channels") or ()),
                        source=f"STANDARD.json ({sp}) [artifact open elsewhere]",
                        standard_path=sp, _info=doc)
                else:
                    res = ResolvedDictionary(
                        path=p, fingerprint=ident["fingerprint"], build=doc.get("build"),
                        release=ident["release"], status=ident["status"],
                        channels=ident["channels"], source=f"STANDARD.json ({sp})",
                        standard_path=sp, _info=doc)
                _CACHE[cache_key] = res
                return res.require(*require) if require else res
            tried.append(f"{sp} -> {doc.get('path')} (missing)")
        else:
            tried.append(f"{sp} (no standard promoted)")
    else:
        tried.append("repo root not found from " + str(Path(__file__).parent))

    # ---- 3. NAMED REFUSAL. There is no step 4. ---------------------------
    raise DictionaryUnavailable(
        "no dictionary could be resolved.\n"
        + "".join(f"  tried: {t}\n" for t in tried)
        + f"  There is deliberately NO fallback: a resolver that guesses is how four "
        f"lanes ran for weeks on a two-generation-old dictionary with no error.\n"
        f"  Fix, in order of preference:\n"
        f"    1. promote a standard:  python semantic_compression/"
        f"dictionary_standard.py promote <build> --allow-staged\n"
        f"    2. see what exists:     python semantic_compression/"
        f"dictionary_standard.py index\n"
        f"    3. override for one run: {ENV_VAR}=<build-name-or-path>")


def clear_cache() -> None:
    """Drop memoized resolutions. For tests and for code that promotes in-process."""
    _CACHE.clear()
