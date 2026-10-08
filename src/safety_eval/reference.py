"""Read the reference sheet and clinic protocol so prompts can quote them exactly."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "data" / "reference"
STATEMENT = re.compile(r"^- \*\*(F\d{2})\*\* (.+?) \[S[\d, S]+\]", re.MULTILINE)


def facts():
    """{"F01": "statement text", ...} without source tags or verification markers."""
    return dict(STATEMENT.findall((REFERENCE / "medication_facts.md").read_text()))


def protocol_text():
    """The clinic protocol from its first section onward (the file header is for humans)."""
    text = (REFERENCE / "clinic_protocol.md").read_text()
    return text[text.index("## Who replies to patients"):].strip()
