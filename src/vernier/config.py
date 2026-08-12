"""Strict loader for project ``vernier.toml`` (untrusted when cloned)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

_ALLOWED_VERNIER_KEYS: Final = frozenset(
    {
        "corpus_path",
        "max_severity",
        "model",
        "baseline_model",
        "runs_dir",
    }
)
_ALLOWED_TOP_LEVEL: Final = frozenset({"vernier", "divergence"})
_ALLOWED_SEVERITIES: Final = frozenset({"NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"})


class VernierConfigError(ValueError):
    """Raised when ``vernier.toml`` is hostile or malformed."""


@dataclass(frozen=True, slots=True)
class VernierTomlConfig:
    corpus_path: Path
    max_severity: str
    model: str | None = None
    baseline_model: str | None = None
    runs_dir: Path | None = None
    divergence: dict[str, Any] | None = None


def _resolve_under(project_root: Path, raw: str, *, field: str) -> Path:
    project_root = project_root.resolve()
    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (project_root / candidate).resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise VernierConfigError(
            f"vernier.toml {field}={raw!r} escapes project root {project_root}"
        ) from exc
    return resolved


def load_vernier_toml(
    path: Path,
    *,
    project_root: Path | None = None,
) -> VernierTomlConfig:
    """Load ``vernier.toml`` with a strict allowlist (no env, no exec, no escape)."""
    path = Path(path)
    root = (project_root or path.parent).resolve()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise VernierConfigError(f"invalid vernier.toml: {exc}") from exc

    if not isinstance(data, dict):
        raise VernierConfigError("vernier.toml root must be a table")

    unknown_top = set(data) - _ALLOWED_TOP_LEVEL
    if unknown_top:
        raise VernierConfigError(
            f"vernier.toml unknown top-level tables: {sorted(unknown_top)}"
        )

    # Reject any attempt to smuggle environment or shell hooks.
    for banned in ("env", "exec", "environment", "hooks", "scripts"):
        if banned in data:
            raise VernierConfigError(f"vernier.toml must not contain [{banned}]")
        vern_probe = data.get("vernier")
        if isinstance(vern_probe, dict) and banned in vern_probe:
            raise VernierConfigError(
                f"vernier.toml must not contain [vernier.{banned}]"
            )

    vern = data.get("vernier", {})
    if not isinstance(vern, dict):
        raise VernierConfigError("[vernier] must be a table")
    unknown = set(vern) - _ALLOWED_VERNIER_KEYS
    if unknown:
        raise VernierConfigError(f"vernier.toml unknown keys: {sorted(unknown)}")

    corpus_raw = str(vern.get("corpus_path", "vernier-corpus"))
    corpus_path = _resolve_under(root, corpus_raw, field="corpus_path")

    runs_dir: Path | None = None
    if "runs_dir" in vern:
        runs_dir = _resolve_under(root, str(vern["runs_dir"]), field="runs_dir")

    severity = str(vern.get("max_severity", "MEDIUM")).upper()
    if severity not in _ALLOWED_SEVERITIES:
        raise VernierConfigError(f"invalid max_severity: {severity!r}")

    model = str(vern["model"]) if vern.get("model") else None
    baseline = str(vern["baseline_model"]) if vern.get("baseline_model") else None

    divergence = data.get("divergence")
    if divergence is not None and not isinstance(divergence, dict):
        raise VernierConfigError("[divergence] must be a table")

    return VernierTomlConfig(
        corpus_path=corpus_path,
        max_severity=severity,
        model=model,
        baseline_model=baseline,
        runs_dir=runs_dir,
        divergence=divergence,
    )
