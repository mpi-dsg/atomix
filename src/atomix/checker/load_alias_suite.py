"""Load and canonicalize the adversarial alias suite (A6).

Suite YAML lives at `atomix/checker/alias_suite.yaml`. Each case has:
- name: stable identifier
- substrate: filesystem | taubench | dom | rw_dep
- scope_a, scope_b: the two scope IDs to compare
- label: should-conflict | should-not-conflict
- notes: free-form
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Optional


SuiteLabel = Literal["should-conflict", "should-not-conflict"]
Substrate = Literal["filesystem", "taubench", "dom", "rw_dep"]


@dataclass(frozen=True)
class AliasCase:
    name: str
    substrate: Substrate
    scope_a: str
    scope_b: str
    label: SuiteLabel
    notes: str = ""


_SUITE_PATH = Path(__file__).parent / "alias_suite.yaml"


def load_alias_suite(path: Optional[Path] = None) -> List[AliasCase]:
    """Load the alias suite from YAML (or another path)."""
    p = Path(path) if path else _SUITE_PATH
    try:
        import yaml  # type: ignore
    except ImportError as e:
        raise ImportError(
            "PyYAML required to load alias suite. `uv pip install pyyaml`."
        ) from e
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = []
    for entry in data["cases"]:
        cases.append(
            AliasCase(
                name=entry["name"],
                substrate=entry["substrate"],
                scope_a=entry["scope_a"],
                scope_b=entry["scope_b"],
                label=entry["label"],
                notes=entry.get("notes", ""),
            )
        )
    return cases


def canonicalize(scope: str, substrate: Substrate) -> str:
    """Canonicalize a scope ID to a stable form for conflict matching.

    The canonical form must be identical for two scopes that name the same
    underlying resource (the alias suite's `should-conflict` cases).

    Returns the canonical string. With Tx-NaiveStringScopes the harness
    skips this call and uses the raw scope, which the suite proves misses
    real conflicts.
    """
    if substrate == "filesystem":
        return _canon_fs(scope)
    if substrate == "taubench":
        return _canon_taubench(scope)
    if substrate == "dom":
        return _canon_dom(scope)
    if substrate == "rw_dep":
        return _canon_rw_dep(scope)
    return scope


def _canon_rw_dep(scope: str) -> str:
    """Canonicalize a read-write dependency scope.

    Patterns recognized:
      - `order:N(consumes=inv:item:K)` -> `inv:item:K` (alias of consumed inventory)
      - `refund:N(of=order:K)` -> `order:K`            (alias of refunded order)
    Other scopes pass through unchanged so should-not-conflict cases
    canonicalize to themselves.
    """
    m = re.match(r"^order:\d+\(consumes=(?P<inv>[^)]+)\)$", scope)
    if m:
        return m.group("inv")
    m = re.match(r"^refund:\d+\(of=(?P<ord>[^)]+)\)$", scope)
    if m:
        return m.group("ord")
    return scope


# --- per-substrate canonicalizers ---


def _canon_fs(path: str) -> str:
    """Canonicalize a filesystem path: resolve symlinks/`..`, lower-case on
    case-insensitive volumes (heuristic), strip trailing slash. Wildcards are
    preserved literally — they are different scopes from concrete paths but
    treated as overlapping via separate matching logic upstream.
    """
    # Strip a fs: prefix used by some scope conventions.
    raw = path[3:] if path.startswith("fs:") else path
    if "*" in raw or "?" in raw:
        # Glob: just normalize separators / collapse `..`.
        return "fs:" + os.path.normpath(raw)
    # Concrete path: try to resolve. If the path doesn't exist, fall back to
    # normpath so the canonicalizer is still deterministic.
    p = Path(raw)
    try:
        resolved = str(p.resolve(strict=False))
    except OSError:
        resolved = os.path.normpath(raw)
    # Heuristic case-folding: macOS HFS/APFS default is case-insensitive.
    # We do not lowercase Linux paths to avoid false aliases.
    if _is_case_insensitive_volume(resolved):
        resolved = resolved.lower()
    if resolved.endswith("/") and len(resolved) > 1:
        resolved = resolved[:-1]
    return "fs:" + resolved


_CI_VOLUMES_CACHE: dict[str, bool] = {}


def _is_case_insensitive_volume(path: str) -> bool:
    """Heuristic: true on macOS for paths under typical CI volumes."""
    # The plan's note: "case-folding on case-insensitive volumes". We only
    # report True for macOS-style paths. The flag is conservative so we
    # don't over-canonicalize on Linux.
    if not path.startswith("/"):
        return False
    if os.uname().sysname != "Darwin":  # type: ignore[attr-defined]
        return False
    # Cache by top-level dir to keep this cheap.
    head = "/" + path.lstrip("/").split("/", 1)[0] if "/" in path[1:] else path
    if head in _CI_VOLUMES_CACHE:
        return _CI_VOLUMES_CACHE[head]
    _CI_VOLUMES_CACHE[head] = True  # macOS default
    return True


def _canon_taubench(scope: str) -> str:
    """Canonicalize a tau-bench entity. We collapse alternate keys to a
    primary key form `taubench:<entity>:<pk>`.

    Examples:
      `taubench:customer:id=42` -> `taubench:customer:42`
      `taubench:customer:email=x@y.com` -> `taubench:customer:email:x@y.com`
        (cannot resolve to id without DB; treated as distinct key family)
      `taubench:order:id=7` -> `taubench:order:7`
      `taubench:order:customer=42,t=2026-01-01` -> `taubench:order:by:42:2026-01-01`
    """
    raw = scope
    if not raw.startswith("taubench:"):
        return raw
    parts = raw.split(":", 2)
    if len(parts) < 3:
        return raw
    _, entity, rest = parts
    if rest.startswith("id="):
        return f"taubench:{entity}:{rest[3:]}"
    if rest.startswith("email="):
        return f"taubench:{entity}:email:{rest[6:]}"
    if rest.startswith("customer=") and ",t=" in rest:
        cust, t = rest[len("customer="):].split(",t=", 1)
        return f"taubench:{entity}:by:{cust}:{t}"
    return f"taubench:{entity}:{rest}"


def _canon_dom(scope: str) -> str:
    """Canonicalize a DOM scope. Multiple selectors that resolve to the same
    element should map to the same canonical id. We collapse to a tuple of
    (page, element_id_or_xpath_signature).

    Selectors recognized: `dom://<page>/xpath=<x>`, `dom://<page>/css=<c>`,
    `dom://<page>/aria=<a>`, `dom://<page>/id=<i>`. Without a live DOM we
    canonicalize by best-effort: prefer id when given, else hash the selector
    type+value pair so identical selectors collide and different ones don't.
    """
    if not scope.startswith("dom://"):
        return scope
    body = scope[len("dom://"):]
    if "/" not in body:
        return scope
    page, sel = body.split("/", 1)
    if sel.startswith("id="):
        return f"dom://{page}/id={sel[3:]}"
    if sel.startswith("xpath="):
        # Strip whitespace differences in the xpath.
        x = re.sub(r"\s+", "", sel[len("xpath="):])
        return f"dom://{page}/xpath={x}"
    return f"dom://{page}/{sel}"


def expected_match(case: AliasCase) -> bool:
    """True iff the canonical scopes for both sides should be equal.

    By construction, every `should-conflict` case must canonicalize to the
    same string, and every `should-not-conflict` case must canonicalize to
    different strings.
    """
    a = canonicalize(case.scope_a, case.substrate)
    b = canonicalize(case.scope_b, case.substrate)
    return a == b
