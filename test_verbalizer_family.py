"""
test_verbalizer_family.py -- smoke test: the verbalizer on the elo-browser-v01a
dictionary family with BOTH channels live:
  * affect     -> faiss_query (EPA/affect index; rebuilt via faiss_builder.py)
  * denotation -> denotative_neighbours (768-d meaning index in the family)

For each probe word it prints the raw denotation neighbours and the full expanded
field (nodes scored by affect + facets), so you can see MEANING words (automobile,
vehicle) sitting alongside AFFECT words in one field.

Run (from semantic_compression/):
    python test_verbalizer_family.py
"""
from pathlib import Path

from verbalizer import VerbalizerSubstrate, Verbalizer, VerbalizerInput

BUILD = Path("db/builds/elo-browser-v01a")
PROBES = ("car", "doctor", "memory", "compression")


def main() -> None:
    sub = VerbalizerSubstrate(dict_db=BUILD / "dictionary.lmdb", denotative_dir=BUILD)
    print(f"faiss verified : {sub.index_verified}")
    print(f"denotation     : {'loaded' if sub.denotative is not None else 'MISSING'}")
    v = Verbalizer(sub)

    for word in PROBES:
        denot = [s for s, _ in sub.denotative_neighbours(word, k=6)]
        epa = sub.get_epa(word) or (1.0, 0.5, 0.5)
        field = v.expand(VerbalizerInput.from_vector(*epa, anchor_ids=[word]))
        epa_r = tuple(round(x, 2) for x in epa)
        print(f"\n=== {word}   EPA={epa_r}   field nodes={len(field.nodes)} ===")
        print(f"  denotation : {denot}")
        # mark which field nodes came from the meaning channel
        denot_set = {d.lower() for d in denot}
        for n in field.nodes[:10]:
            src = "MEANING" if n.phrase_atom.lower() in denot_set else "affect "
            pol = n.facet_profile.get("polarity")
            print(f"    [{src}] {n.phrase_atom:18} score={n.score:.3f}  pol={pol}")


if __name__ == "__main__":
    main()
