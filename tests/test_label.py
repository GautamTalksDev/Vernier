"""Protocol tests for blind KT-2 labelling (scripts/label.py)."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LABEL_PATH = ROOT / "scripts" / "label.py"


def _load_label_module():
    spec = importlib.util.spec_from_file_location("vernier_label", LABEL_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vernier_label"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def label_mod(tmp_path, monkeypatch):
    mod = _load_label_module()
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    monkeypatch.setattr(mod, "LABELS_DIR", labels_dir)
    monkeypatch.setattr(mod, "QUEUE_PATH", labels_dir / "queue.json")
    monkeypatch.setattr(mod, "LABELS_PATH", labels_dir / "labels.jsonl")
    monkeypatch.setattr(mod, "SEALED_DIR", labels_dir / "sealed")
    monkeypatch.setattr(mod, "BLIND_DIR", labels_dir / "blind")
    monkeypatch.setattr(mod, "STATE_PATH", labels_dir / "session.json")
    return mod


def test_prepare_queue_omits_verdict_fields(label_mod, tmp_path):
    csv_path = tmp_path / "kt1.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "run_id",
                "baseline_model",
                "candidate_model",
                "severity",
                "rule_ids",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "run_id": "run-aaa",
                "baseline_model": "base",
                "candidate_model": "cand-a",
                "severity": "HIGH",
                "rule_ids": "D05",
            }
        )
        writer.writerow(
            {
                "run_id": "run-bbb",
                "baseline_model": "base",
                "candidate_model": "cand-a",
                "severity": "NONE",
                "rule_ids": "",
            }
        )
    assert label_mod.cmd_prepare(csv_path) == 0
    queue = json.loads(label_mod.QUEUE_PATH.read_text(encoding="utf-8"))
    assert len(queue) == 1
    assert "severity" not in queue[0]
    assert "rule_ids" not in queue[0]
    sealed_files = list(label_mod.SEALED_DIR.glob("*.json"))
    assert len(sealed_files) == 1
    sealed = json.loads(sealed_files[0].read_text(encoding="utf-8"))
    assert sealed["severity"] == "HIGH"
    assert sealed["rule_ids"] == ["D05"]


def test_assert_blind_text_rejects_verdict_leaks(label_mod):
    with pytest.raises(RuntimeError, match="leak"):
        label_mod._assert_blind_text("severity=HIGH\nrule_ids=['D05']\n")
    with pytest.raises(RuntimeError, match="rule id"):
        label_mod._assert_blind_text("Fired rule D05 on this pair\n")
    # Clean blind text is accepted.
    label_mod._assert_blind_text(
        "=== BLIND CASE (classifier verdict HIDDEN) ===\n"
        "ToolCall:search\nInserted ToolCall:search\n"
    )


def test_commit_refuses_without_pending(label_mod):
    label_mod.QUEUE_PATH.write_text("[]\n", encoding="utf-8")
    label_mod._save_state({"pending_case_id": None, "revealed": False})
    assert label_mod.cmd_commit("COSMETIC") == 2


def test_commit_appends_before_reveal_and_blocks_relabel(label_mod, monkeypatch, capsys):
    case_id = "abc123"
    label_mod.SEALED_DIR.mkdir(parents=True)
    label_mod.BLIND_DIR.mkdir(parents=True)
    (label_mod.SEALED_DIR / f"{case_id}.json").write_text(
        json.dumps(
            {
                "case_id": case_id,
                "severity": "HIGH",
                "rule_ids": ["D05"],
            }
        ),
        encoding="utf-8",
    )
    label_mod.QUEUE_PATH.write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "run_id": "run-1",
                    "baseline_model": "base",
                    "candidate_model": "cand",
                }
            ]
        ),
        encoding="utf-8",
    )
    label_mod._save_state({"pending_case_id": case_id, "revealed": False})

    order: list[str] = []
    real_load = label_mod.load_sealed_after_commit

    def tracked_load(cid: str):
        # labels.jsonl must already contain the case when sealed is opened.
        assert cid in label_mod._labelled_ids()
        order.append("sealed")
        return real_load(cid)

    monkeypatch.setattr(label_mod, "load_sealed_after_commit", tracked_load)

    assert label_mod.cmd_commit("BEHAVIOURAL") == 0
    out = capsys.readouterr().out
    assert "LABEL COMMITTED" in out
    assert "severity=HIGH" in out
    assert order == ["sealed"]

    lines = label_mod.LABELS_PATH.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["label"] == "BEHAVIOURAL"

    # Re-label refused even if session is reset.
    label_mod._save_state({"pending_case_id": case_id, "revealed": False})
    assert label_mod.cmd_commit("COSMETIC") == 2
    lines2 = label_mod.LABELS_PATH.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines2) == 1


def test_next_never_reads_sealed(label_mod, monkeypatch, capsys):
    case_id = "blindonly"
    label_mod.SEALED_DIR.mkdir(parents=True)
    label_mod.BLIND_DIR.mkdir(parents=True)
    sealed = label_mod.SEALED_DIR / f"{case_id}.json"
    sealed.write_text(
        json.dumps({"severity": "CRITICAL", "rule_ids": ["D01"]}),
        encoding="utf-8",
    )
    label_mod.QUEUE_PATH.write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "run_id": "run-x",
                    "baseline_model": "base",
                    "candidate_model": "cand",
                }
            ]
        ),
        encoding="utf-8",
    )
    label_mod._save_state({"pending_case_id": None, "revealed": False})

    def boom(*_a, **_k):
        raise AssertionError("sealed must not be opened during next/present")

    monkeypatch.setattr(label_mod, "load_sealed_after_commit", boom)

    # Pre-write blind cache so next does not redrive.
    blind = (
        "=== BLIND CASE (classifier verdict HIDDEN) ===\n"
        "case run_id=run-x\n"
        "candidate_model=cand\n"
        "ToolCall:lookup\n"
    )
    (label_mod.BLIND_DIR / f"{case_id}.txt").write_text(blind, encoding="utf-8")

    # Also monkeypatch Path.read_text on sealed path via wrapping open of sealed dir.
    original_read = Path.read_text

    def guarded_read(self, *args, **kwargs):
        if self.resolve() == sealed.resolve():
            raise AssertionError("sealed file read during next")
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)

    assert label_mod.cmd_next() == 0
    out = capsys.readouterr().out
    assert "CRITICAL" not in out
    assert "D01" not in out
    assert "severity" not in out.lower()
    assert "BLIND CASE" in out
