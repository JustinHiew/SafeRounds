"""The reference sheet is what Accuracy is judged against, so its shape is checked like code."""

import re
from pathlib import Path

REFERENCE = Path(__file__).resolve().parents[1] / "data" / "reference"
STATEMENT = re.compile(r"^- \*\*(F\d{2})\*\* (.+)$", re.MULTILINE)


def _statements():
    return STATEMENT.findall((REFERENCE / "medication_facts.md").read_text())


def _source_ids():
    return set(re.findall(r"^\| (S\d+) \|", (REFERENCE / "sources.md").read_text(), re.MULTILINE))


def test_between_25_and_35_statements():
    assert 25 <= len(_statements()) <= 35


def test_statement_ids_unique():
    ids = [sid for sid, _ in _statements()]
    assert len(ids) == len(set(ids))


def test_every_statement_cites_a_registered_source():
    known = _source_ids()
    for sid, text in _statements():
        cited = set(re.findall(r"S\d+", text))
        assert cited, f"{sid} cites no source"
        assert cited <= known, f"{sid} cites unknown source {cited - known}"


def test_every_statement_awaits_verification_or_is_verified():
    # Until Justin checks a statement against its source it must carry the TODO marker.
    for sid, text in _statements():
        assert "TODO-JUSTIN: verify" in text or "verified" in text, sid


def test_no_brand_names():
    # D2: generic names only. Lowercased brand names of weight-loss injections.
    banned = ["wegovy", "ozempic", "rybelsus", "mounjaro", "zepbound", "saxenda"]
    for path in REFERENCE.glob("*.md"):
        text = path.read_text().lower()
        assert not any(b in text for b in banned), path.name


def test_literature_figures_load_and_match_report():
    import json

    from safety_eval.literature_figures import REPORT, load

    # The committed report must be an exact copy of the hand-entered, quoted figures.
    assert json.loads(REPORT.read_text()) == load()
