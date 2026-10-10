"""Per-clinic configuration: everything the judge reads that belongs to one clinic (roadmap 2A).

A clinic is a folder clinics/<id>/ with a clinic.yaml:

    id: harbourline
    name: Harbourline Support
    staff:                      # responder role -> how the judge prompt names the staff member
      assistant: AI assistant
      peer: peer support worker
    files:                      # relative to the clinic folder
      rubric: rubric.md         # same structure as docs/rubric.md
      reference: reference.md   # facts as "- **H01** statement [S1]", one per line
      protocol: protocol.md
    protocol_start: "## Who replies to clients"   # optional: the protocol is read from here on
    record_fields:              # the patient record schema; type is str, int or list
      age_band: {type: str, required: true}
      notes: {type: str, required: false}

Fernhill, the research clinic, sets `record_schema: fernhill` instead of record_fields, so its
conversations keep the typed PatientRecord and Responder checks. Its rendered prompts are
byte-identical to those before clinics existed (tests/fixtures/fernhill_render_hashes.json), so
every cached judge answer stays valid.
"""

import argparse
import hashlib
import json
import re
from functools import cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from .schemas import BaseConversation, Conversation

ROOT = Path(__file__).resolve().parents[2]
CLINICS = ROOT / "clinics"
DEFAULT_CLINIC = "fernhill"
# Fernhill's facts carry a source tag and a verification marker after it; other clinics may omit both.
FACT = re.compile(r"^- \*\*([A-Z]+\d+)\*\* (.+?)(?: \[S[\d, S]+\][^\n]*)?$", re.MULTILINE)
FIELD_TYPES = {"str": str, "int": int, "list": list}


class ClinicConfigError(ValueError):
    """A clinic folder or clinic.yaml that cannot be used. Raised at load time, never mid-run."""


class Clinic:
    def __init__(self, folder):
        self.folder = Path(folder)
        path = self.folder / "clinic.yaml"
        if not path.exists():
            raise ClinicConfigError(f"no clinic.yaml in {self.folder}")
        self.config = yaml.safe_load(path.read_text())
        known = {"id", "name", "description", "staff", "files", "protocol_start",
                 "record_schema", "record_fields"}
        unknown = set(self.config) - known
        if unknown:
            raise ClinicConfigError(f"{path}: unknown keys {sorted(unknown)}")
        self.id = self.config["id"]
        if self.id != self.folder.name:
            raise ClinicConfigError(f"{path}: id {self.id!r} does not match folder name")
        self.name = self.config["name"]
        self.staff = dict(self.config["staff"])
        self.record_schema = self.config.get("record_schema")
        self.record_fields = self.config.get("record_fields")
        if (self.record_schema is None) == (self.record_fields is None):
            raise ClinicConfigError(f"{path}: set exactly one of record_schema and record_fields")
        if self.record_schema not in (None, "fernhill"):
            raise ClinicConfigError(f"{path}: unknown record_schema {self.record_schema!r}")
        for name, spec in (self.record_fields or {}).items():
            if spec.get("type") not in FIELD_TYPES:
                raise ClinicConfigError(f"{path}: field {name!r} needs type str, int or list")
        self.paths = {}
        for kind in ("rubric", "reference", "protocol"):
            p = (self.folder / self.config["files"][kind]).resolve()
            if not p.exists():
                raise ClinicConfigError(f"{path}: {kind} file {p} does not exist")
            self.paths[kind] = p
        if not self.facts():
            raise ClinicConfigError(f"{self.paths['reference']}: no facts found")
        self.protocol_text()      # fails now if protocol_start is missing from the file

    @classmethod
    def load(cls, clinic_id=DEFAULT_CLINIC, root=CLINICS):
        return _load(str(clinic_id), str(root))

    # ------------------------------------------------------------------ what the judge reads

    def rubric_markdown(self):
        return self.paths["rubric"].read_text()

    def facts(self):
        """{"F01": "statement text", ...} without source tags or verification markers. A
        "## Sources" section, if the file has one, lists sources rather than facts and is skipped."""
        text = self.paths["reference"].read_text()
        return dict(FACT.findall(re.split(r"^## Sources\b", text, flags=re.MULTILINE)[0]))

    def protocol_text(self):
        text = self.paths["protocol"].read_text()
        start = self.config.get("protocol_start")
        if start:
            if start not in text:
                raise ClinicConfigError(f"{self.paths['protocol']}: heading {start!r} not found")
            text = text[text.index(start):]
        return text.strip()

    def staff_label(self, responder):
        try:
            return self.staff[responder]
        except KeyError:
            raise ClinicConfigError(f"clinic {self.id} has no responder role {responder!r}") from None

    @property
    def sha(self):
        """Hash of the config and every file the judge reads, so an edit changes the cache key."""
        h = hashlib.sha256((self.folder / "clinic.yaml").read_bytes())
        for kind in ("rubric", "reference", "protocol"):
            h.update(self.paths[kind].read_bytes())
        return h.hexdigest()[:16]

    # ------------------------------------------------------------------ conversations

    def parse_conversation(self, data):
        """A validated conversation for this clinic. Raises ValueError with every problem found."""
        if self.record_schema == "fernhill":
            return Conversation.model_validate(data)
        conv = BaseConversation.model_validate(data)
        self.staff_label(conv.responder)
        problems = self.record_problems(conv.patient_record)
        if problems:
            raise ValueError(f"{conv.conversation_id}: " + "; ".join(problems))
        return conv

    def record_problems(self, record):
        problems = [f"unknown record field {k!r}" for k in record if k not in self.record_fields]
        for name, spec in self.record_fields.items():
            if name not in record:
                if spec.get("required"):
                    problems.append(f"missing record field {name!r}")
                continue
            value, want = record[name], FIELD_TYPES[spec["type"]]
            # bool is a subclass of int in Python; a yes/no belongs in a str field.
            if not isinstance(value, want) or isinstance(value, bool):
                problems.append(f"record field {name!r} should be {spec['type']}")
            elif want is list and not all(isinstance(v, str) for v in value):
                problems.append(f"record field {name!r} should be a list of text")
        return problems


@cache
def _load(clinic_id, root):
    return Clinic(Path(root) / clinic_id)


def available(root=CLINICS):
    return sorted(p.name for p in Path(root).iterdir() if (p / "clinic.yaml").exists())


def main():
    ap = argparse.ArgumentParser(description="Check clinic configs and preview a judge prompt.")
    ap.add_argument("clinic", nargs="?", help="clinic id (default: check every clinic)")
    ap.add_argument("--render", metavar="CONVERSATIONS_JSON",
                    help="print the judge prompt for the first conversation in this JSON list")
    ap.add_argument("--version", default="C", help="prompt version to render (default C)")
    args = ap.parse_args()
    from .judge import PromptVersion, render, rubric_text   # judge imports this module
    for cid in [args.clinic] if args.clinic else available():
        clinic = Clinic.load(cid)
        rubric_text(clinic=clinic)    # fails if the rubric does not have the expected structure
        print(f"{cid}: ok ({len(clinic.facts())} facts, roles: {', '.join(clinic.staff)})")
    if args.render:
        if not args.clinic:
            ap.error("--render needs a clinic id")
        conv = clinic.parse_conversation(json.loads(Path(args.render).read_text())[0])
        print(render(PromptVersion.load(args.version), conv, clinic=clinic))


if __name__ == "__main__":
    main()
