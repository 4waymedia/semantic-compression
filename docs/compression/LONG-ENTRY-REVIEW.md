# Long-Entry Review — `elo-browser-v01b`

> Generated 2026-07-30 from `db/builds/elo-browser-v01b/token-ids.csv.gz`.
> The 100 longest **single-word** entries, for a cull decision on the next build.

## Why this matters

The longest reachable single-word entry defines the **opaque-data threshold**: any OOV
token longer than it cannot be a word, so it is compressed losslessly but excluded from
EPA, facets and seed formation. That threshold is therefore only as trustworthy as the
longest entry we admit.

- longest entry in the dictionary: **88 chars**
- longest entry reachable in the `full` cut: **29 chars** (the live threshold)

If junk like a repeated-syllable ASR artifact is ever promoted into `full`, the threshold
widens silently and real blobs stop being flagged. Culling these is a correctness matter
for the semantic layer, not just tidiness.

## Suggested actions

| verdict | meaning |
|---|---|
| `CUT` | no linguistic content; remove from the corpus/miner |
| `REVIEW` | may carry signal (URLs especially — see `URL-AS-SIGNAL.md`) |
| `KEEP` | plausible real word |

**The verdicts below oversimplify, because this dictionary does two jobs.** A cull
decision that is right for the codec can be wrong for the semantic layer, and the
long entries split three ways rather than two:

- **ASR repeat artifacts** (`modernwisdom.dom.dom.dom.dom`, `switchero…`) — freq 1–3,
  all `full = N`, worthless to both jobs. **Cut at the miner.** This is the only
  category that is unambiguously junk.
- **CSS utility classes** (`focus-visible:justify-between`, freq 3,012, Tier 2, live) —
  **1,844 of them are reachable, covering 16,229,956 corpus tokens, and 33 hold Tier 1
  slots out of the 1,280 that exist.** They *earn* those ids on web content. Cutting
  them would degrade compression to fix a semantic problem. **Keep, but mark
  non-lexical**, and exclude them from EPA and seed formation.
- **URLs** — the largest category here, and the one with real semantic content.
  **Keep and exploit**; see `URL-AS-SIGNAL.md`.

**Consequence for the threshold.** The longest live entry is
`focus-visible:justify-between` at 29 chars — so the opaque-data gate is currently
calibrated by a Tailwind class, not by a word. The longest live *purely alphabetic*
entry is `congressionallymandated` at 23. Deriving the threshold from lexical entries
only tightens the gate from 29 to 23 characters while keeping the compression.

## The 100 longest single-word entries

| # | len | surface | freq | tier | full | class | verdict |
|---:|---:|---|---:|---:|:---:|---|---|
| 1 | 88 | `switcherooooooooooooooooooooooooooooooooooo...` | 3 | 3 | N | ASR-REPEAT | **CUT** |
| 2 | 48 | `scalarfysics.comresourcesfan_marininovmagvi...` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 3 | 42 | `birthcap.ap.ap.ap.ap.ap.ap.ap.ap.ap.ap.org` | 3 | 3 | N | ASR-REPEAT | **CUT** |
| 4 | 42 | `echelonfront.ront.ront.ront.ront.ront.ront` | 1 | 3 | N | ASR-REPEAT | **CUT** |
| 5 | 39 | `modernwisdomd.dom.dom.dom.dom.dom.dom.d` | 1 | 3 | N | ASR-REPEAT | **CUT** |
| 6 | 38 | `modernwisdom.dom.dom.dom.dom.dom.dom.d` | 1 | 3 | N | ASR-REPEAT | **CUT** |
| 7 | 37 | `modernwisdism.dom.dom.dom.dom.dom.dom` | 2 | 3 | N | ASR-REPEAT | **CUT** |
| 8 | 36 | `modernwisdom.dom.dom.dom.dom.dom.dom` | 2 | 3 | N | ASR-REPEAT | **CUT** |
| 9 | 31 | `members.americancontingency.com` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 10 | 31 | `integrativemedicine.arizona.edu` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 11 | 31 | `jockonunderground.comground.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 12 | 31 | `primalbeef.comoladcraftbeef.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 13 | 30 | `americasmightywarriors.com.org` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 14 | 30 | `bfriends.comfriendcapscapscaps` | 1 | 3 | N | ASR-REPEAT | **CUT** |
| 15 | 30 | `dakotamyers.flipsidecanvas.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 16 | 30 | `peterson.locals.com.locals.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 17 | 30 | `wilsonhealthandperformance.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 18 | 30 | `wisdom.dom.dom.dom.dom.dom.dom` | 1 | 3 | N | ASR-REPEAT | **CUT** |
| 19 | 29 | `focus-visible:justify-between` | 3,012 | 2 | Y | OTHER | **REVIEW** |
| 20 | 29 | `globalcompassioncoalition.org` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 21 | 29 | `healthpromoting.promoting.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 22 | 29 | `servicesparamounttactical.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 23 | 28 | `focus-visible:justify-center` | 3,014 | 2 | Y | OTHER | **REVIEW** |
| 24 | 28 | `christianwarriortraining.com` | 6 | 3 | Y | URL/DOMAIN | **REVIEW** |
| 25 | 28 | `thesuccessprinciplesbook.com` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 26 | 28 | `elizabethsmartfoundation.org` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 27 | 27 | `group-hover:justify-between` | 3,134 | 2 | Y | OTHER | **REVIEW** |
| 28 | 27 | `focus-visible:font-semibold` | 2,940 | 2 | Y | OTHER | **REVIEW** |
| 29 | 27 | `focus-visible:text-blue-600` | 2,914 | 2 | Y | OTHER | **REVIEW** |
| 30 | 27 | `americaasmightywarriors.org` | 31 | 3 | Y | URL/DOMAIN | **REVIEW** |
| 31 | 27 | `children'shealthdefense.org` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 32 | 27 | `findyoullightfoundation.org` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 33 | 27 | `maydayexecutiveservices.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 34 | 27 | `wilsonhealthperformance.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 35 | 26 | `group-hover:justify-center` | 3,136 | 2 | Y | OTHER | **REVIEW** |
| 36 | 26 | `focus-visible:inline-block` | 3,022 | 2 | Y | OTHER | **REVIEW** |
| 37 | 26 | `focus-visible:items-center` | 3,016 | 2 | Y | OTHER | **REVIEW** |
| 38 | 26 | `americasmightywarriors.org` | 21 | 3 | Y | URL/DOMAIN | **REVIEW** |
| 39 | 26 | `kyle.guntaskandpurpose.com` | 10 | 3 | Y | URL/DOMAIN | **REVIEW** |
| 40 | 26 | `stephvenbartlet.stan.store` | 6 | 3 | Y | URL/DOMAIN | **REVIEW** |
| 41 | 26 | `americananfinancing.netens` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 42 | 26 | `firstformm.comdrinkingbros` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 43 | 26 | `assistance.underground.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 44 | 26 | `everly.everlasting.com.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 45 | 26 | `fruitionflipsidecampus.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 46 | 26 | `peakfinancialinvesting.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 47 | 26 | `psychopharmarmacologically` | 1 | 3 | N | WORD? | **KEEP** |
| 48 | 26 | `socialjusticeparenting.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 49 | 25 | `group-hover:font-semibold` | 3,062 | 2 | Y | OTHER | **REVIEW** |
| 50 | 25 | `group-hover:text-blue-600` | 3,036 | 2 | Y | OTHER | **REVIEW** |
| 51 | 25 | `focus-visible:text-center` | 3,010 | 2 | Y | OTHER | **REVIEW** |
| 52 | 25 | `focus-visible:grid-cols-1` | 3,004 | 2 | Y | OTHER | **REVIEW** |
| 53 | 25 | `focus-visible:grid-cols-2` | 3,002 | 2 | Y | OTHER | **REVIEW** |
| 54 | 25 | `focus-visible:grid-cols-3` | 3,000 | 2 | Y | OTHER | **REVIEW** |
| 55 | 25 | `focus-visible:grid-cols-4` | 2,998 | 2 | Y | OTHER | **REVIEW** |
| 56 | 25 | `focus-visible:opacity-100` | 2,922 | 2 | Y | OTHER | **REVIEW** |
| 57 | 25 | `focus-visible:bg-blue-700` | 2,918 | 2 | Y | OTHER | **REVIEW** |
| 58 | 25 | `focus-visible:bg-gray-100` | 2,916 | 2 | Y | OTHER | **REVIEW** |
| 59 | 25 | `document.querySelectorAll` | 2,200 | 2 | Y | URL/DOMAIN | **REVIEW** |
| 60 | 25 | `sportsbook.draftkings.com` | 7 | 3 | Y | URL/DOMAIN | **REVIEW** |
| 61 | 25 | `benny.y.y.y.y.y.y.y.y.y.y` | 3 | 3 | N | ASR-REPEAT | **CUT** |
| 62 | 25 | `thereresponsibleparty.com` | 3 | 3 | N | URL/DOMAIN | **REVIEW** |
| 63 | 25 | `www.theighhandmanbook.com` | 3 | 3 | N | URL/DOMAIN | **REVIEW** |
| 64 | 25 | `counterculturethreads.com` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 65 | 25 | `ponchooutdoors.comdeloney` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 66 | 25 | `arttoofaccomplishment.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 67 | 25 | `charles.a.morgganyale.edu` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 68 | 25 | `everythingisbullshit.blog` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 69 | 25 | `facebook.comrobbyworldcup` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 70 | 25 | `fitnessfuelslongevity.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 71 | 25 | `masterclass.comdannyjones` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 72 | 25 | `mccllcleinttockwilson.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 73 | 25 | `petermaguire.substack.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 74 | 25 | `rebuilderdemand.comstores` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 75 | 25 | `sportsbusinessgeneral.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 76 | 25 | `stonehillandfrenchies.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 77 | 25 | `workconnectivityindex.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 78 | 24 | `group-hover:inline-block` | 3,144 | 2 | Y | OTHER | **REVIEW** |
| 79 | 24 | `group-hover:items-center` | 3,138 | 2 | Y | OTHER | **REVIEW** |
| 80 | 24 | `focus-visible:col-span-2` | 2,996 | 2 | Y | OTHER | **REVIEW** |
| 81 | 24 | `focus-visible:rounded-lg` | 2,934 | 2 | Y | OTHER | **REVIEW** |
| 82 | 24 | `focus-visible:shadow-2xl` | 2,924 | 2 | Y | OTHER | **REVIEW** |
| 83 | 24 | `focus-visible:text-white` | 2,912 | 2 | Y | OTHER | **REVIEW** |
| 84 | 24 | `disabled:justify-between` | 2,890 | 2 | Y | OTHER | **REVIEW** |
| 85 | 24 | `beyondthebrotherhood.org` | 3 | 3 | N | URL/DOMAIN | **REVIEW** |
| 86 | 24 | `fundamentallyformational` | 3 | 3 | N | WORD? | **KEEP** |
| 87 | 24 | `laurenveld.substacks.com` | 3 | 3 | N | URL/DOMAIN | **REVIEW** |
| 88 | 24 | `stevenbartlet.stan.store` | 3 | 3 | N | URL/DOMAIN | **REVIEW** |
| 89 | 24 | `store.taskandpurpose.com` | 3 | 3 | N | URL/DOMAIN | **REVIEW** |
| 90 | 24 | `americanfinancing.netsrs` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 91 | 24 | `healthrustfinanicial.com` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 92 | 24 | `matthewhusseyretreat.com` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 93 | 24 | `receivingmyblessings.com` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 94 | 24 | `www.ethancrosswithak.com` | 2 | 3 | N | URL/DOMAIN | **REVIEW** |
| 95 | 24 | `americanfinancing.netens` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 96 | 24 | `blackbearsportsgroup.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 97 | 24 | `bpw.bedavidconulting.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 98 | 24 | `constitutionallymandated` | 1 | 3 | N | WORD? | **KEEP** |
| 99 | 24 | `firstlbertyinstitute.org` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |
| 100 | 24 | `intelligenceofnature.com` | 1 | 3 | N | URL/DOMAIN | **REVIEW** |

## Tally

- **REVIEW**: 87
- **CUT**: 10
- **KEEP**: 3

Entries marked `full = N` are already unreachable by the browser; cutting them
reclaims dictionary slots but does not change encoder behaviour. Entries with
`full = Y` are live and a cut changes what the encoder emits.
