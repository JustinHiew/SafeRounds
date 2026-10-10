"""Web labelling page: blind content, same order as the terminal tool, and the import of saved answers."""

import json

import pytest

from safety_eval import label_page, labelling
from safety_eval.grid import load_cards
from safety_eval.schemas import CRITERIA


@pytest.fixture(scope="module")
def data():
    return label_page.page_data()


def test_page_holds_only_what_the_labeller_may_see(data):
    convs = json.dumps(data["convs"]).lower()
    cards = load_cards()
    for secret in ("planted", "blueprint", "scenario", "chatgpt", "gemini", "claude", "expected_severity"):
        assert secret not in convs
    for card in cards[:40]:
        assert card.situation.lower() not in convs
    assert set(data["convs"]) == {c.conversation_id for c in labelling.load_conversations()}
    for c in data["convs"].values():
        assert set(c) == {"responder", "record", "turns"}


def test_order_matches_terminal_tool(data):
    convs = labelling.load_conversations()
    assert data["order"] == [c.conversation_id for c in labelling.queue(convs, "pass1", {})]
    assert len(data["explore"]) == 30 and set(data["explore"]) <= set(data["order"])


def test_criteria_definitions_come_from_rubric(data):
    assert [c["key"] for c in data["criteria"]] == list(CRITERIA)
    assert all(c["definition"].startswith("A reply fails") for c in data["criteria"])


def test_markdown_tables_and_escaping():
    out = label_page.md_to_html("## H\n\n| a | b |\n|---|---|\n| <x> | **y** |\n\n- one\n  more\n")
    assert "<table>" in out and "&lt;x&gt;" in out and "<strong>y</strong>" in out
    assert "<li>one more</li>" in out


def test_import_web(tmp_path, monkeypatch):
    monkeypatch.setattr(labelling, "LABELS", tmp_path)
    monkeypatch.setattr(labelling, "NOTES", tmp_path / "exploration_notes.md")
    label = {"conversation_id": "cv-000001", "pass_name": "pass1", "rubric_version": "1",
             "verdicts": {c: "pass" for c in CRITERIA} | {"scope": "fail"}, "severity": "low",
             "unsure": False, "note": "", "labelled_at": "2026-10-10T01:00:00+00:00"}
    export = {"documents": [{"id": "cv-000001", "data": label},
                            {"id": "cv-000002", "data": {"conversation_id": "cv-000002", "note": "tells them to skip",
                                                         "saved_at": "2026-10-10T00:00:00Z"}},
                            {"id": "cv-000003", "data": {"conversation_id": "cv-000003", "note": " ",
                                                         "saved_at": "2026-10-10T00:01:00Z"}}]}
    path = tmp_path / "export.json"
    path.write_text(json.dumps(export))
    labelling.import_web(path)
    labels = labelling.load_labels("pass1")
    assert labels["cv-000001"].verdicts["scope"] == "fail" and labels["cv-000001"].labeller == "justin"
    assert (tmp_path / "exploration_notes.md").read_text() == "- cv-000002: tells them to skip\n"

    # A changed answer replaces the earlier one; nothing is duplicated.
    label["verdicts"]["scope"] = "pass"
    label["severity"] = "none"
    path.write_text(json.dumps([label]))
    labelling.import_web(path)
    assert labelling.load_labels("pass1")["cv-000001"].severity == "none"
    assert len(labelling.labels_path("pass1").read_text().splitlines()) == 1


def test_import_web_reads_an_export_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(labelling, "LABELS", tmp_path / "labels")
    monkeypatch.setattr(labelling, "NOTES", tmp_path / "notes.md")
    folder = tmp_path / "export" / "explore_notes"
    folder.mkdir(parents=True)
    (folder / "cv-000002.json").write_text(json.dumps({"conversation_id": "cv-000002", "note": "misses insulin",
                                                       "saved_at": "2026-10-10T00:00:00Z"}))
    labelling.import_web(tmp_path / "export")
    assert (tmp_path / "notes.md").read_text() == "- cv-000002: misses insulin\n"


def test_import_rejects_inconsistent_label(tmp_path, monkeypatch):
    monkeypatch.setattr(labelling, "LABELS", tmp_path)
    bad = {"conversation_id": "cv-000001", "pass_name": "pass1", "rubric_version": "1",
           "verdicts": {c: "pass" for c in CRITERIA}, "severity": "high",
           "unsure": False, "note": "", "labelled_at": "2026-10-10T01:00:00+00:00"}
    path = tmp_path / "export.json"
    path.write_text(json.dumps([bad]))
    with pytest.raises(ValueError):
        labelling.import_web(path)
