"""Lock user-facing product guarantees to tests (claim → control)."""

from __future__ import annotations

from pathlib import Path

from vernier.init_cmd import VERNIER_TOML

REPO = Path(__file__).resolve().parents[1]


def test_readme_states_egress_and_no_model_guarantees() -> None:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "Vernier does not phone home." in text
    assert (
        "The only network it uses is the model provider "
        "you already configured."
    ) in text
    assert "No LLM judge." in text
    assert "No similarity scores." in text
    assert "There is no model on this path." in text


def test_docs_ci_states_phone_home_guarantee() -> None:
    text = (REPO / "docs" / "CI.md").read_text(encoding="utf-8")
    assert "Vernier does not phone home." in text


def test_docs_architecture_states_no_llm_on_classify_path() -> None:
    text = (REPO / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "Why no LLM on that path" in text


def test_init_scaffold_states_zero_network_requests() -> None:
    assert "ZERO network requests" in VERNIER_TOML
