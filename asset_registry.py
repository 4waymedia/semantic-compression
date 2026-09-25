"""THE asset registry — one declaration per ELO Dictionary asset.

The ELO Dictionary is an **id↔asset binding table**, not a word list. One Base64 id
space covers words, phrases, private file ids, CSS, JavaScript, HTML and reserved system
ids; the value is that an id resolves into every asset the build carries. So "what is an
asset, and what is true of it" is the most load-bearing fact in the system, and until
2026-09-10 it was written down **seven times**, in seven modules, maintained by hand:

    build_assets._stages()              the builder stage        knew wordclass
    publish_dictionary.BUNDLE_CHANNELS  what gets published      did NOT
    publish_dictionary.MAGIC            the wire headers         did NOT
    export_browser_assets.py            the exporter body        did NOT
    artifact_identity.KINDS             the registry/manifest    did NOT
    coverage_census.CHANNELS            the census               knew wordclass
    config.KNOWN_SUBDBS                 the package inventory    knew wordclass

Measured consequence on elo-browser-v04: **`wordclass` — 437,995 records, format v3,
locked, adopted by the Verbalizer — is absent from the published bundle, from
`manifest.json`, and from every gate.** It passes publish by not being mentioned. The
mirror image also holds: `templates` is declared in `KINDS` and carried in `manifest.json`
with `present: false`, and no stage anywhere builds it.

Built-and-undeclared and declared-and-unbuilt are the same defect from opposite ends, and
neither is a clerical slip: with seven lists and no cross-check, an asset added to one of
them IS silently absent from the other six.

**This module is the one list.** Everything else derives from it.

DESIGN NOTE — why the build side is not here. The dictionary lane does the rebuilding
(Paul, 2026-09-10), so `elo-dictionary` ships as a READER. This module carries only what
a reader needs: sub-db, record geometry, wire magic, contract file, and how the asset
expresses ABSENCE. `build_assets.py` keys its stage table by `Asset.name` and adds the
builder wiring on the repo side, where the heavy deps live. One table, two halves, and
the half consumers need travels with the package.

    python semantic_compression/asset_registry.py        # print the registry
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Asset:
    """One asset, declared once.

    `absent` is a required field with no default on purpose. Every recurring bug in this
    repo for a month has been an absent-vs-zero confusion -- `polarity` NEUTRAL is 0b00,
    `utility` CONTENT is 0b00, `wordclass` UNKNOWN is 0, `epa`'s documented NaN marker is
    dead code because absence is a missing row, and an empty `neighbours` list means NOT
    INDEXED rather than nothing-similar-enough. An asset that cannot say how it expresses
    absence cannot be read correctly, so declaring it is not optional."""

    name: str
    #: LMDB sub-db, or None for an asset that exists only as a bundle file.
    subdb: bytes | None
    #: Fixed record width in bytes; None = variable-length (CSR).
    record_width: int | None
    #: How this asset says "no measurement here". Free text, but MANDATORY.
    absent: str
    #: Bundle file + its 8-byte wire magic. None/None = not exported (yet).
    bin_file: str | None = None
    wire_magic: bytes | None = None
    #: The sidecar declaring shifts/masks/value names. A channel without one is not
    #: decodable from the bundle alone.
    contract_file: str | None = None
    #: meta key carrying this asset's format version, when it stamps one.
    format_version_key: str | None = None
    #: Further files this asset ships beyond `bin_file`. `morph_map` ships a verdict
    #: map AND a veto set; both are required to reproduce its predicate, so a bundle
    #: with one and not the other is incomplete rather than smaller.
    extra_files: tuple = ()
    #: True once the asset is expected in a published bundle.
    ships: bool = False
    #: Dense or CSR over the vocab index n, so `count == vocab_entries` (gate G1) and an
    #: 80-byte framed header applies. FALSE for a sparse asset: `morph_map` is keyed on
    #: PAIRS of surfaces, so it has no n and no per-n count, and asserting one would be
    #: comparing a pair count to a vocabulary size.
    n_parallel: bool = True
    #: Carries its own identity inside the file (a `meta` block) rather than in a framed
    #: binary header. Such an asset needs no separate contract file to be decodable.
    self_describing: bool = False
    #: Every build must carry it; a build without it is incomplete, not minimal.
    required: bool = False
    #: Declared-but-deliberately-unbuilt assets name their reason here. A registry
    #: entry with no builder and no reason is the `templates` defect.
    unbuilt_reason: str = ""

    # WHAT A CONSUMER WOULD USE THIS FOR, in one line (2026-09-24).
    #
    # Added because the published bundle listed eleven files and said nothing about what
    # any of them was FOR. ELO-Browser had a working integration and still could not tell
    # which channel answered which question -- so the answer lived in four handoffs, a
    # spec, and this lane's head, and a consumer had to ask.
    #
    # Declared here rather than in the manifest generator for the usual reason: a
    # description written next to the generator drifts from the asset it describes. This
    # is the tenth field on this dataclass and the tenth thing that used to be
    # hand-maintained somewhere else.
    #
    # Defaulted rather than required, unlike `absent`: a missing purpose produces a
    # manifest entry that says so, which is recoverable. A missing absence rule produces
    # a consumer that reads zero as a measurement, which is not.
    purpose: str = ""

    # What it is NOT good for. The manifest ships this beside `purpose` because every
    # measured misuse in this project was a consumer reaching for a field that looked
    # applicable: EPA for synonymy, `bucket` for ranking, `utility` for salience.
    # Naming the misuse next to the use is cheaper than the handoff that follows it.
    not_for: str = ""
    note: str = ""

    @property
    def in_lmdb(self) -> bool:
        return self.subdb is not None

    @property
    def framed(self) -> bool:
        """Carries the 80-byte magic+count+fingerprint header the .bin channels share.
        A JSON asset does not, and asking it for one is how a reader invents an offset."""
        return self.wire_magic is not None

    @property
    def decodable_from_bundle(self) -> bool:
        """Ships WITH the geometry needed to read it. `facets` did; `vfacets` shipped
        without its names file until 2026-09-10 and could not be read from `dist/`.

        Three ways to qualify: a contract file, a self-describing container, or the
        trivially-shaped `<fff` epa record. Everything else ships data a consumer cannot
        decode without reaching outside the bundle."""
        return bool(self.ships and self.bin_file
                    and (self.contract_file or self.self_describing
                         or self.record_width == 12))


# --------------------------------------------------------------------------------
# THE REGISTRY. Add an asset HERE and nowhere else.
# --------------------------------------------------------------------------------
ASSETS: tuple[Asset, ...] = (
    Asset(
        purpose="surface -> Base64 id. The encode direction; the id space itself.",
        not_for="Reading channel values -- it carries ids, not assets.",
        name="forward", subdb=b"forward", record_width=None,
        absent="a surface with no id is not in the dictionary; there is no null row",
        required=True,
        note="surface -> id. Half of the binding table; the ids every asset is keyed by.",
    ),
    Asset(
        purpose="Base64 id -> surface. The decode direction.",
        not_for="Reconstructing authored text by space-joining -- use the codec (text()).",
        name="reverse", subdb=b"reverse", record_width=None,
        absent="an id with no surface is a build error, not absence",
        required=True,
        note="id -> surface.",
    ),
    Asset(
        purpose="Coarse SEMANTIC TYPE per surface: bucket (topic/concept/relation/...), "
                "a composable logic-cue mask, and flags carrying utility "
                "(content/structural/function/filler). Use it to FILTER -- exclude "
                "structural surfaces, find connectives.",
        not_for="RANKING OR SCORING. bucket's effective cardinality is 2.0 and utility's "
                "is 1.17 -- two of six values hold 99.9%. Measured: an adapter ranked "
                "salience by bucket and produced the 90%-bold defect. 61.7% of records "
                "carry FLAG['HEURISTIC'] -- read it; the channel says when it guessed.",
        name="facets", subdb=b"facets", record_width=4,
        absent="bucket byte 0xFF; UNKNOWN=0x00 is a MEASURED value, not absence",
        bin_file="facets.bin", wire_magic=b"ELOFCT\x01\x00",
        contract_file="facets.names.json",
        format_version_key="facets_format_version",
        ships=True, required=True,
        note="bucket + logic-cue mask + flags. UTILITY lives in the top 2 bits of "
             "byte 3 and is a separate field that shares the byte.",
    ),
    Asset(
        purpose="AFFECT per surface: evaluation, potency, activity (Osgood/Heise scale). "
                "Use it for affective dynamics over Actor-Behavior-Object events, and "
                "for deflection in Affect Control Theory terms.",
        not_for="SYNONYMY OR SEMANTIC SEARCH. EPA is 3 dimensions and encodes no "
                "denotation: `car`'s nearest neighbours are `credentials`, `attention`, "
                "`decoration`. The retrieval is exact; the space cannot tell them apart. "
                "Use `neighbours` for denotative similarity. Potency is also the least "
                "reliable axis (cross-source agreement P=0.328).",
        name="epa", subdb=b"epa", record_width=12,
        # Corrected 2026-09-24: this said "on v04, never used", which was true of the LMDB
        # and false of the .bin, and the old text named only one of the two spellings. A
        # published absence rule that describes one representation of two is how a reader
        # gets the right answer for the wrong reason until the day it does not.
        absent="TWO SPELLINGS, unreconciled. In the LMDB: NO ROW (236,645 of 437,995 "
               "entries have one). In epa.bin: NaN,NaN,NaN -- that array is DENSE, so "
               "201,350 slots carry NaN. Both read as 'no value' through the API, so a "
               "reader that checks for a missing key against the .bin, or for NaN against "
               "the LMDB, is wrong and silent. On the bin: test is_nan, never == 0.0 -- "
               "zero is a measured neutral affect.",
        bin_file="epa.bin", wire_magic=b"ELOEPA\x01\x00",
        # DECLARED 2026-09-24. Was None -- one of two shipping channels with no contract
        # file, so a native reader hand-decoded its geometry from prose and BUNDLE.json
        # carried `format_version: null`. Declaring it here is what makes publish REFUSE
        # a bundle without it (publish_dictionary.py:611); export_browser_assets emits it.
        contract_file="epa.names.json",
        ships=True,
        note="<fff evaluation/potency/activity. AFFECT, never denotative similarity.",
    ),
    Asset(
        purpose="Verb/event shape per surface: agency, direction, temporal aspect, "
                "domain, polarity (+ a polarity_known bit). Use `direction` and "
                "`polarity` -- both are genuine measurements that emit absence.",
        not_for="Reading `temporal` as a measurement. Proven 2026-09-22: a VERB with no "
                "suffix evidence is assigned PROCESS, and temporal NEVER emits UNKNOWN "
                "over its qualifying class -- unlike facets, nothing marks the default. "
                "Also: polarity's absence is the polarity_known BIT, not polarity==0 "
                "(NEUTRAL is 0b00 and measured, 76% of it).",
        name="vfacets", subdb=b"vfacets", record_width=2,
        absent="PER FIELD, six independent absences. polarity's absence is the "
               "`polarity_known` bit, NOT polarity==0 (NEUTRAL is 0b00). There is no "
               "channel-level absent marker and asking for one is the bug.",
        bin_file="vfacets.bin", wire_magic=b"ELOVFT\x01\x00",
        contract_file="vfacets.names.json",
        format_version_key="vfacets_format_version",
        ships=True,
        note="agency/direction/temporal/domain/polarity/polarity_known.",
    ),
    Asset(
        purpose="DENOTATIVE nearest neighbours from the 768-d index, as CSR by vocab "
                "index. This is the channel for 'what means something similar' -- the "
                "question EPA cannot answer.",
        not_for="Reading an empty list as 'nothing similar enough'. It means NOT IN THE "
                "INDEX: 258,251 of 258,254 covered entries sit exactly at the k=16 cap, "
                "so the similarity floor never binds. Also ships no contract file, so "
                "its geometry cannot be gated -- see PACKAGE-CONTENTS.md §5.5.",
        name="neighbours", subdb=None, record_width=None,
        absent="an EMPTY list means NOT IN THE INDEX. It essentially never means "
               "'indexed, nothing similar enough': 258,251 of 258,254 covered "
               "entries sit exactly at the k=16 cap, so min_sim never binds.",
        bin_file="neighbours.bin", wire_magic=b"ELONBR\x01\x00",
        # DECLARED 2026-09-24 -- see epa. `neighbours.meta.json` is a build record in
        # NOT_PAYLOAD and was never the contract; this is. export_neighbours emits it.
        contract_file="neighbours.names.json",
        ships=True,
        note="CSR: 80B header (16B struct + 64B fingerprint -- `fp_len` at byte 12 is "
             "NOT the header length; this lane has misread it twice) + (count+1) u32 "
             "offsets + 5B records. Denotative (mpnet), not EPA.",
    ),
    Asset(
        purpose="Grammatical class per surface: dominant class + confidence, a composable "
                "class mask, ambivalence, countability, inherent number, proper, "
                "requires-determiner. The ONLY field in this dictionary that can rank -- "
                "effective cardinality 4.03 over the word-level classed set.",
        not_for="Truthiness tests. Tri-state fields are 0=UNKNOWN 1=NO 2=YES, so `1` is "
                "TRUTHY and means NOT: `if wc['proper']` fires on 'definitely not a "
                "name' and has already inverted a consumer's gate. Compare to 2. Also: "
                "`dominant == OTHER` IS the phrase segment (174,628 both), so exclude "
                "OTHER and UNKNOWN before ranking -- 4.03, not the inflated 3.78.",
        name="wordclass", subdb=b"wordclass", record_width=3,
        absent="dominant class UNKNOWN=0, and the tri-state feature fields use "
               "0=UNKNOWN / 1=NO / 2=YES -- so 1 is TRUTHY and means NOT. Use the "
               "predicates, never raw truthiness.",
        bin_file="wordclass.bin", wire_magic=b"ELOWCL\x01\x00",
        contract_file="wordclass.names.json",
        format_version_key="wordclass_format_version",
        ships=True,
        note="format v3: class + confidence + ambivalent, class mask, tri-state "
             "features. LOCKED 2026-08-29 and adopted by the Verbalizer -- and it has "
             "never shipped in a bundle. This entry is what changes that.",
    ),
    Asset(
        # `morph` -- the ASSET. `morph_map.json` is one of its files, and naming the
        # asset after a file is how a second file (morph_vetoes.bin) ends up looking
        # like a different thing. The manifest publishes `morph`.
        purpose="Same-lemma adjudications for surface PAIRS that suffix rules cannot "
                "settle, decided by the embedding. Use it to know two surfaces are "
                "inflections of one lemma when the spelling does not show it.",
        not_for="Treating a missing pair as 'not the same lemma'. A pair with no entry is "
                "UNDECIDED -- unsettled or never scored. This is the absent-vs-zero error "
                "for this asset and the one consumers reach for first. Also: "
                "meta.bundle_fingerprint carries the DICTIONARY fingerprint, so verifying "
                "against it FAILS OPEN -- pin meta.dictionary_fingerprint.",
        name="morph", subdb=None, record_width=None,
        absent="a PAIR with no entry is UNDECIDED -- not 'these are unrelated'. The map "
               "holds the residue suffix rules cannot settle, adjudicated by the "
               "embedding; a missing pair means unsettled or never scored. Treating "
               "absence as 'not the same lemma' is the absent-vs-zero error for this "
               "asset, and it is the one a consumer will reach for first.",
        bin_file="morph_map.json",
        extra_files=("morph_vetoes.bin",),
        contract_file=None,      # self-describing: `meta` rides inside the json
        ships=True,
        n_parallel=False,        # keyed on PAIRS of surfaces; there is no n
        self_describing=True,
        note="PAIRWISE same-lemma verdicts, keyed on SURFACES not ids (Paul, "
             "2026-08-03) so a rebuild does not invalidate it: {'meta': {...}, "
             "'decided': {'a|b': bool}}. Generator + tiered predicate live in "
             "elo_reasoning.morphology.lemma; the sweep bakes this. Carries its own "
             "build+bundle pin, so staleness is detectable (lemma.verify_pin).\n"
             "THIS IS THE VALIDATED REPLACEMENT for ad-hoc singularisation. "
             "wordclass_builder PASS 2 still guesses a singular by string surgery and "
             "accepts the first candidate that exists -- the exact bug this asset was "
             "built to end (lens/len, physics/physic, roses/ros). PASS 2 must consume "
             "this map.",
    ),
    Asset(
        purpose="(unbuilt) Reserved for surface -> template bindings.",
        not_for="Anything. No build produces it; see unbuilt_reason. elo-v5 does not "
                "create the sub-db at all, so assert against this registry entry rather "
                "than probing for the channel.",
        name="templates", subdb=b"templates", record_width=None,
        absent="0 entries is not low coverage; nobody has built this",
        unbuilt_reason="DECLARED, NEVER BUILT. No stage produces it and no lane owns "
                       "it. It is kept in the registry so it stays visible as owed "
                       "rather than quietly disappearing -- but a build carrying an "
                       "empty `templates` sub-db is not carrying an asset.",
        note="Reserved for phrase/sentence templates. Needs an owner.",
    ),
)

BY_NAME: dict[str, Asset] = {a.name: a for a in ASSETS}


# --- derived views: what the seven old lists used to say, computed ---------------
# Files that live IN a published bundle directory but are not payload -- they describe
# the payload rather than being it. Declared here, with the assets, because three callers
# need the same answer: publish_dictionary's G9 (every payload declared, two-way),
# dictionary_standard's published read-back, and anything auditing a bundle directory.
#
# It was defined in publish_dictionary alone until 2026-09-21, which meant the read-back
# either imported a publish-time script or restated the list -- and a restated list is how
# nine asset lists happened. A manifest is not payload of itself.
NOT_PAYLOAD = ("BUNDLE.json", "assets.meta.json", "neighbours.meta.json",
               "PACKAGE.md")


def known_subdbs() -> dict:
    """`config.KNOWN_SUBDBS` — every LMDB sub-db, with its key scheme."""
    out = {}
    for a in ASSETS:
        if a.subdb is None:
            continue
        out[a.subdb] = ("surface->id" if a.name == "forward"
                        else "id->surface" if a.name == "reverse" else "base64_id")
    out[b"meta"] = "identity keys"
    return out


def bundle_channels(required_only: bool = False) -> tuple:
    """`publish_dictionary.BUNDLE_CHANNELS` — assets expected in a published bundle."""
    return tuple(a.name for a in ASSETS
                 if a.ships and a.bin_file and (a.required or not required_only))


def wire_magic() -> dict:
    """`publish_dictionary.MAGIC` — role -> 8-byte header magic."""
    return {a.name: a.wire_magic for a in ASSETS if a.wire_magic}


def bin_files() -> dict:
    return {a.name: a.bin_file for a in ASSETS if a.bin_file}


def contract_files() -> dict:
    return {a.name: a.contract_file for a in ASSETS if a.contract_file}


def identity_kinds() -> tuple:
    """`artifact_identity.KINDS` — what the manifest must carry an entry for."""
    return ("dictionary",) + tuple(a.name for a in ASSETS
                                   if a.name not in ("forward", "reverse")) + ("meta",)


def missing_from(present: set) -> list:
    """Assets this build does NOT carry, given the sub-db names it has.

    Paul's requirement, as a function: *older dictionaries can be rebuilt with newer
    assets* needs, first, a way to ask which ones are missing. Unbuilt-by-declaration
    assets are excluded -- `templates` is owed by nobody, so reporting every older build
    as "missing templates" would be noise that trains people to ignore the answer."""
    return [a.name for a in ASSETS
            if a.in_lmdb and not a.unbuilt_reason
            and a.subdb.decode() not in present]


def undecodable() -> list:
    """Shipping channels whose geometry does NOT ship with them."""
    return [a.name for a in ASSETS if a.ships and not a.decodable_from_bundle]


def _selftest() -> int:
    fails = 0

    def check(label, got, want):
        nonlocal fails
        ok = got == want
        fails += (not ok)
        print(f"  {'ok ' if ok else 'FAIL'} {label:52}{got!r}"
              f"{'' if ok else f'   want {want!r}'}")

    print("registry integrity:")
    check("names unique", len(BY_NAME), len(ASSETS))
    check("every asset declares absence", all(a.absent for a in ASSETS), True)
    check("wire magics unique",
          len({a.wire_magic for a in ASSETS if a.wire_magic}),
          len([a for a in ASSETS if a.wire_magic]))
    check("every magic is 8 bytes",
          all(len(a.wire_magic) == 8 for a in ASSETS if a.wire_magic), True)
    check("a shipping asset has a bin file",
          all(a.bin_file for a in ASSETS if a.ships), True)
    check("an unbuilt asset states why",
          all(a.unbuilt_reason for a in ASSETS if a.name == "templates"), True)

    print("\nthe defect this module exists to prevent:")
    check("wordclass is a bundle channel", "wordclass" in bundle_channels(), True)
    check("wordclass has a wire magic", wire_magic().get("wordclass"),
          b"ELOWCL\x01\x00")
    check("wordclass is in the manifest kinds", "wordclass" in identity_kinds(), True)
    check("templates is NOT a bundle channel", "templates" in bundle_channels(), False)

    print("\nthe morph asset, declared 2026-09-11:")
    check("morph is a bundle channel", "morph" in bundle_channels(), True)
    check("morph is in the manifest kinds", "morph" in identity_kinds(), True)
    check("morph ships its veto set too", BY_NAME["morph"].extra_files,
          ("morph_vetoes.bin",))
    check("morph is NOT n-parallel (keyed on surface PAIRS)",
          BY_NAME["morph"].n_parallel, False)
    check("morph is NOT framed (identity rides inside the json)",
          BY_NAME["morph"].framed, False)
    check("...and is still decodable from the bundle",
          BY_NAME["morph"].decodable_from_bundle, True)

    print("\nchannels that ship without their geometry:")
    for n in undecodable():
        print(f"       {n}: {BY_NAME[n].bin_file} has no contract file")

    print(f"\n{'FAILURES: %d' % fails if fails else 'registry self-test passes'}")
    return 1 if fails else 0


if __name__ == "__main__":
    for _a in ASSETS:
        _f = f"{_a.bin_file or '-':16}"
        print(f"{_a.name:12} subdb={(_a.subdb or b'-').decode():10} w={_a.record_width!s:5}"
              f" bin={_f} ships={_a.ships!s:5} contract={_a.contract_file or '-'}")
    print()
    raise SystemExit(_selftest())
