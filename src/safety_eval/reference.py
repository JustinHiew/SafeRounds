"""Read Fernhill's reference sheet and clinic protocol so prompts can quote them exactly.

The generation and import tools build Fernhill conversations and call these directly; the judge
reads the same files through the Fernhill clinic config (clinic.py), so both always agree.
"""

from pathlib import Path

from .clinic import Clinic

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "data" / "reference"


def facts():
    """{"F01": "statement text", ...} without source tags or verification markers."""
    return Clinic.load("fernhill").facts()


def protocol_text():
    """The clinic protocol from its first section onward (the file header is for humans)."""
    return Clinic.load("fernhill").protocol_text()
