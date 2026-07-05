# semantic_compression · Handbooks

> Plain-language guides ("what it means, how to read it, how to adjust it") for the
> concepts in this subsystem. Each concept gets its own folder with a `HANDBOOK.md`
> and any assets; the **deep specs stay in `docs/compression/`** and are linked, not
> moved. (Colocated convention — handbooks live next to the code they describe.)

| Concept | Folder | Status | Deep specs (in `docs/compression/`) |
|---|---|---|---|
| Facets · Meta · EPA | [`facets-epa/`](facets-epa/HANDBOOK.md) | **Active** | `spec-facets-db`, `spec-meta-db`, `spec-facet-accuracy` |
| Dictionary & Compression | [`dictionary-compression/`](dictionary-compression/HANDBOOK.md) | **Active** | `spec-v0.3`, `spec-v0.4`, `spec-vocab-strategy`, `spec-dict-testgroups`, `spec-corpus-sourcing`, `GUIDE-create-dictionary`, `SYSTEM1.md` |
| Artifact Identity & Versioning | _planned_ | — | `spec-artifact-identity` |

**Convention:** `handbook/<concept>/HANDBOOK.md` is the readable front door;
`docs/compression/spec-*.md` is the depth behind it. A handbook teaches; a spec
defines. Status: Stub → Draft → Active.
