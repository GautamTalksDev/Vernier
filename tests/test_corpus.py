"""Corpus structure checks (raw runs are gitignored; rebuild locally)."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "testdata" / "corpus"
MANIFEST = CORPUS / "MANIFEST.json"
IDEAS = REPO / "IDEAS.md"


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
    assert data["baseline_model"] == "vernier-baseline-local-v1"
