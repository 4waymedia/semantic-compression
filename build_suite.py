"""
build_suite.py -- the declarative "which supporting assets" model for a build.

A dictionary is a shipped product, and different products need different suites: the
browser ships facets + epa + neighbours; a keyboard ships the bare dictionary; a
coding-language pack ships dictionary + meta. This module is the SINGLE SOURCE OF
TRUTH for that choice — which assets exist, their dependency order, each asset's
prerequisites, and how a preset expands. The build spec declares the suite; the
`artifact_identity` registry records what actually got built; the two agree.

Declared in a build spec's `build:` block:

    build:
      size: char-4
      suite: full            # preset: minimal | standard | full
      assets:                # OPTIONAL explicit overrides on top of the preset
        vectors: false       #   turn the heavy 768-d index off for this build
        epa: true

Presets expand to:
    minimal   dictionary                       fast; edge / keyboard
    standard  + facets + meta                   normal build (DEFAULT)
    full      + epa + meta_layer2 + vectors + browser   whole stack

`templates` (System 2) is reserved and never auto-enabled.

Two prerequisite classes, per the design:
  * DECLARATION deps -- asset A needs asset B also declared. Missing -> FAIL FAST
    (resolve_suite raises), so a build never half-derives.
  * ENVIRONMENT deps -- a substrate file or heavy library must be present. Missing ->
    the asset is marked BLOCKED with a reason (not built, not fatal), mirroring the
    registry's `present:false` + note. The driver reports it; the build continues.

No heavy imports at module load — `resolve_suite` stays importable anywhere.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

# Dependency order (also the build order). Index in this list = stage order.
ORDER = ["dictionary", "facets", "meta", "epa", "meta_layer2",
         "vectors", "browser", "templates"]

# Declaration deps: each asset requires these OTHER assets to also be enabled.
DEPS: dict[str, list[str]] = {
    "dictionary":  [],
    "facets":      ["dictionary"],
    "meta":        ["dictionary"],
    "epa":         ["dictionary"],
    "meta_layer2": ["meta", "epa"],
    "vectors":     ["meta"],
    "browser":     ["facets", "epa"],   # neighbours sub-channel also wants `vectors`
    "templates":   ["dictionary"],
}

# Which `artifact_identity` KIND records the asset (None = no identity kind yet).
IDENTITY_KIND: dict[str, str | None] = {
    "dictionary": "dictionary", "facets": "facets", "meta": "meta",
    "epa": "epa", "meta_layer2": "meta", "vectors": None,
    "browser": None, "templates": "templates",
}

PRESETS: dict[str, set[str]] = {
    "minimal":  {"dictionary"},
    "standard": {"dictionary", "facets", "meta"},
    "full":     {"dictionary", "facets", "meta", "epa", "meta_layer2",
                 "vectors", "browser"},
}

RESERVED = {"templates"}          # System 2; never auto-enabled


# ---------------------------------------------------------------------------
# Environment prerequisites (soft — missing => BLOCKED, not fatal)
# ---------------------------------------------------------------------------

def _have(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def _env_status(asset: str, repo_root: Path) -> tuple[bool, str]:
    """(ok, reason). ok=True => environment can build this asset now."""
    if asset == "epa":
        sub = repo_root / "Memory" / "data" / "epa_substrate.lmdb"
        return (sub.exists(), "" if sub.exists() else f"missing EPA substrate {sub}")
    if asset in ("vectors", "meta_layer2"):
        miss = [m for m in ("sentence_transformers", "faiss") if not _have(m)]
        if asset == "meta_layer2":                     # meta_layer2 needs epa sub-DB, not models
            return (True, "")
        return (not miss, "" if not miss else f"missing {', '.join(miss)}")
    if asset == "browser":
        tool = repo_root / "ELO-Browser" / "tools" / "export_browser_assets.py"
        return (tool.exists(), "" if tool.exists() else f"missing {tool.name}")
    if asset == "templates":
        return (False, "System 2 not built (reserved)")
    return (True, "")


@dataclass
class ResolvedAsset:
    name: str
    order: int
    enabled: bool
    identity_kind: str | None
    env_ok: bool
    reason: str = ""            # why blocked / reserved
    deps: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        if not self.enabled:
            return "off"
        if self.name in RESERVED:
            return "reserved"
        return "build" if self.env_ok else "blocked"


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------

def declared_set(build: dict) -> set[str]:
    """Expand `suite:` preset + apply `assets:`/`with_facets` overrides. Validates
    names and declaration deps; raises on either. `dictionary` is always present."""
    preset = build.get("suite", "standard")
    if preset not in PRESETS:
        raise ValueError(f"unknown suite preset {preset!r}; choose {sorted(PRESETS)}")
    enabled = set(PRESETS[preset])

    # back-compat: the old single boolean still works
    if "with_facets" in build:
        (enabled.add if build["with_facets"] else enabled.discard)("facets")

    for name, on in (build.get("assets") or {}).items():
        if name not in ORDER:
            raise ValueError(f"unknown asset {name!r}; known: {ORDER}")
        (enabled.add if on else enabled.discard)(name)

    enabled.add("dictionary")                          # never optional

    # FAIL FAST on missing declaration deps
    missing = []
    for a in enabled:
        for d in DEPS[a]:
            if d not in enabled:
                missing.append(f"{a} requires {d}")
    if missing:
        raise ValueError(
            "suite declaration is inconsistent:\n  - " + "\n  - ".join(sorted(missing))
            + "\nEnable the missing asset(s) or pick a higher preset.")
    return enabled


def resolve_suite(spec: dict, *, repo_root: Path) -> list[ResolvedAsset]:
    """Full plan: every asset in build order with enabled/env state + reason."""
    build = spec.get("build", {})
    enabled = declared_set(build)
    out = []
    for i, name in enumerate(ORDER):
        on = name in enabled
        env_ok, reason = _env_status(name, repo_root) if on else (True, "")
        out.append(ResolvedAsset(name, i, on, IDENTITY_KIND[name], env_ok,
                                 reason, DEPS[name]))
    return out


def format_plan(plan: list[ResolvedAsset]) -> str:
    rows = ["  # asset         state     identity     note",
            "  " + "-" * 58]
    icon = {"build": "✓ build", "blocked": "✗ blocked", "reserved": "· reserved",
            "off": "  off"}
    for a in plan:
        rows.append(f"  {a.order} {a.name:<13}{icon[a.state]:<10}"
                    f"{(a.identity_kind or '-'):<12} {a.reason}")
    return "\n".join(rows)


if __name__ == "__main__":
    import sys
    import yaml                                          # spec files are YAML
    if len(sys.argv) < 2:
        sys.exit("usage: python build_suite.py builds/<name>.yaml")
    spec_path = Path(sys.argv[1]).resolve()
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    # repo_root = two up from semantic_compression/build_suite.py
    repo_root = Path(__file__).resolve().parent.parent
    plan = resolve_suite(spec, repo_root=repo_root)
    build = spec.get("build", {})
    print(f"suite for {spec.get('meta', {}).get('name', spec_path.stem)}  "
          f"preset={build.get('suite', 'standard')}")
    print(format_plan(plan))
    will = [a.name for a in plan if a.state == "build"]
    blocked = [f"{a.name} ({a.reason})" for a in plan if a.state == "blocked"]
    print(f"\n  will build: {', '.join(will)}")
    if blocked:
        print(f"  blocked:    {'; '.join(blocked)}")
