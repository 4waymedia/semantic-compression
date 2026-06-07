# v0.3 Test Reports

Captured-at-source artifacts from the v0.3 mining + dictionary + compressor work.
Preserved here so reviewers can verify the analysis docs against the raw output.

| File | Source | Description |
|---|---|---|
| `v0.3-ngram-mining-2to5.txt` | `ngram_counter --nmax 5` | Full corpus 2-5 gram mining run output |
| `v0.3-ngram-mining-6to9.txt` | `ngram_counter --nmin 6 --nmax 9` | Full corpus 6-9 gram mining run output |
| `v0.3-phrase-mining.txt` | `phrase_miner` | PMI scoring + maximal-phrase filter cascade |
| `v0.3-dict-stats.json` | `dictionary_builder_v03` | Build configuration + tier counts + profile cuts |
| `v0.3-test-binary-stream.txt` | `test_binary_stream.py` | Round-trip benchmark on 10 samples + 3 transcripts |

All reports captured at branch `v0.3-phrase-dictionary` commit `bf4fbd3`.
Held-out test transcripts: `KhyQCU6oqE8`, `KOhbGjmidgs`, `bRwFb8JmznE`.

For analysis and conclusions see:
- `../benchmark-v0.3.md`     — the formal benchmark
- `../v0.3-analysis.md`      — theory vs practice retrospective
- `../spec-v0.3.md`          — the design document
