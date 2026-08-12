"""Corpus structure checks (raw runs are gitignored; rebuild locally)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "testdata" / "corpus"
MANIFEST = CORPUS / "MANIFEST.json"
IDEAS = REPO / "IDEAS.md"


def _eval_corpus_baseline_model() -> str:
    """Load EVAL_CORPUS_BASELINE_MODEL from scripts/build_corpus.py (single source)."""
    path = REPO / "scripts" / "build_corpus.py"
    spec = importlib.util.spec_from_file_location("vernier_build_corpus", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    value = getattr(mod, "EVAL_CORPUS_BASELINE_MODEL", None)
    assert isinstance(value, str) and value, "EVAL_CORPUS_BASELINE_MODEL missing"
    return value


def test_ideas_has_at_least_ten_entries() -> None:
    text = IDEAS.read_text(encoding="utf-8")
    entries = [
        line
        for line in text.splitlines()
        if line and line[0].isdigit() and ". **" in line
    ]
    assert len(entries) >= 10, f"IDEAS.md needs >=10 entries, found {len(entries)}"


def test_corpus_readme_and_scripts_exist() -> None:
    assert (CORPUS / "README.md").is_file()
    assert (REPO / "scripts" / "build_corpus.py").is_file()
    assert (REPO / "scripts" / "verify_corpus.py").is_file()
    assert (REPO / "scripts" / "run_kt1.py").is_file()


def test_six_agent_examples_exist() -> None:
    for name in (
        "research_agent.py",
        "data_agent.py",
        "support_agent.py",
        "coding_agent.py",
        "booking_agent.py",
        "router_agent.py",
    ):
        assert (REPO / "examples" / name).is_file()


def test_manifest_when_present_has_30_runs() -> None:
    if not MANIFEST.is_file():
        return
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["run_count"] >= 30
    assert len(data["runs"]) >= 30
    assert len(data["shapes"]) >= 6
    expected = _eval_corpus_baseline_model()
    assert data["baseline_model"] == expected, (
        f"MANIFEST baseline_model={data['baseline_model']!r} != "
        f"EVAL_CORPUS_BASELINE_MODEL={expected!r} in scripts/build_corpus.py"
    )
