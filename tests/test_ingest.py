"""Ingestion (roadmap 2B): each format end to end, rejections, normalisation flags, the CLI."""

import json
from pathlib import Path

import pytest
import yaml

from safety_eval import ingest as I
from safety_eval.clinic import Clinic
from safety_eval.deid import find_pii
from safety_eval.ingest import mapped

FIX = Path(__file__).parent / "fixtures" / "ingest"
SECRETS = ["Testa", "Fakename", "Jane", "Testperson", "example.com", "0400 000", "5550 0123", "45670", "6012 3456",
           "14/02/1990", "3 March 1985", "Placeholder", "Sam"]


@pytest.fixture(scope="module")
def hl():
    return Clinic.load("harbourline")


def dump(result):
    return json.dumps([c.model_dump() for c in result.accepted])


def reasons(result):
    return dict(result.rejected)


def test_canonical_json_end_to_end(hl):
    r = I.ingest(FIX / "harbourline_pii.json", "json", hl)
    assert [c.conversation_id for c in r.accepted] == ["pii-001"]
    assert reasons(r) == {"pii-002": ["too short: 3 turns, need 4 to 10"]}
    text = dump(r)
    assert not [s for s in SECRETS if s in text]
    assert "1800 555 0199" in text and "13 11 14" in text            # clinic numbers kept
    for c in r.accepted:
        for t in c.turns:
            assert find_pii(t.text, hl) == []


def test_canonical_jsonl(hl, tmp_path):
    data = json.loads((FIX / "harbourline_pii.json").read_text())
    path = tmp_path / "in.jsonl"
    path.write_text("\n".join(json.dumps(d) for d in data) + "\n")
    r = I.ingest(path, "json", hl)
    assert len(r.accepted) == 1 and len(r.rejected) == 1


def test_canonical_bad_files_fail_loudly(hl, tmp_path):
    (tmp_path / "a.json").write_text('{"not": "a list"}')
    (tmp_path / "b.json").write_text("{oops")
    for name in ("a.json", "b.json"):
        with pytest.raises(ValueError):
            I.ingest(tmp_path / name, "json", hl)


def test_csv_end_to_end(hl):
    r = I.ingest(FIX / "harbourline_turns.csv", "csv", hl)
    assert [c.conversation_id for c in r.accepted] == ["csv-001", "csv-002"]
    first = r.accepted[0]
    assert first.patient_record["sessions_completed"] == 4                       # parsed to int
    assert first.patient_record["current_supports"] == ["weekly psychologist session", "gp visits"]
    assert "notes" in first.patient_record and "[ADDRESS_1]" in first.patient_record["notes"]
    assert "[EMAIL_1]" in first.turns[2].text
    assert "Testa" not in dump(r) and "Fakename" not in dump(r)                  # known_names column
    second = r.accepted[1]
    assert [t.text for t in second.turns][0] == "Can you help with my plan?"     # sorted by turn
    assert "notes" not in second.patient_record                                  # empty cells omitted
    assert reasons(r)["csv-003"] == ["unknown speaker value(s) ['robot']"]
    assert reasons(r)["csv-004"] == ["record field 'sessions_completed' should be int"]


def test_csv_header_problems_fail_loudly(hl, tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("conversation_id,turn,speaker,txt,responder\nc,1,patient,hi,peer\n")
    with pytest.raises(ValueError, match="missing columns"):
        I.ingest(path, "csv", hl)


def test_csv_duplicate_turns_and_changing_responder_rejected(hl, tmp_path):
    head = "conversation_id,turn,speaker,text,responder,record_age_band\n"
    rows = ("a,1,patient,hi,peer,35-44\na,1,responder,hello,peer,\na,3,patient,x,peer,\na,4,responder,y,peer,\n"
            "b,1,patient,hi,peer,35-44\nb,2,responder,hello,assistant,\nb,3,patient,x,peer,\nb,4,responder,y,peer,\n")
    path = tmp_path / "x.csv"
    path.write_text(head + rows)
    r = I.ingest(path, "csv", hl)
    assert reasons(r) == {"a": ["duplicate turn numbers"], "b": ["responder differs between rows"]}


def test_mapped_end_to_end(hl):
    r = I.ingest(FIX / "helpdesk_export.json", "mapped", hl, FIX / "helpdesk_mapping.yaml")
    assert [c.conversation_id for c in r.accepted] == ["T-1001", "T-1005"]
    t1 = r.accepted[0]
    assert t1.responder == "assistant"                                  # responder_map: ai -> assistant
    assert [t.speaker for t in t1.turns] == ["patient", "responder", "patient", "responder"]
    assert t1.turns[0].text.count("\n") == 1                            # consecutive patient messages merged
    assert t1.patient_record["sessions_completed"] == 3
    text = dump(r)
    assert not [s for s in SECRETS if s in text] and "Botty" not in text
    assert "1800 555 0199" in text
    assert reasons(r) == {
        "T-1002": ["unknown speaker value 'system'"],
        "T-1003": ["too short: 2 turns, need 4 to 10"],
        "T-1004": ["too long: 12 turns, the limit is 10 (conversations are never truncated)"],
    }


def mapping_with(**changes):
    m = yaml.safe_load((FIX / "helpdesk_mapping.yaml").read_text())
    m.update(changes)
    return m


def test_mapped_flags_off(hl):
    # Without the drop flags: T-1001 starts with the bot greeting, T-1005 ends on a patient message.
    m = mapping_with(drop_leading_responder=False, drop_trailing_patient=False)
    r = I.ingest(FIX / "helpdesk_export.json", "mapped", hl, m)
    assert r.accepted == []
    for cid in ("T-1001", "T-1005"):
        assert "starts with the patient and ends with a reply" in " ".join(reasons(r)[cid])


def test_mapped_merge_off_rejects_non_alternating(hl):
    r = I.ingest(FIX / "helpdesk_export.json", "mapped", hl, mapping_with(merge_consecutive=False))
    assert "T-1001" in reasons(r)
    assert "alternate" in " ".join(reasons(r)["T-1001"])


def test_mapped_fixed_responder_and_bad_mapping(hl):
    m = mapping_with(responder="peer")
    del m["responder_field"], m["responder_map"]
    r = I.ingest(FIX / "helpdesk_export.json", "mapped", hl, m)
    assert {c.responder for c in r.accepted} == {"peer"}
    for bad in ({"colour": "red"}, {"responder": "peer"}, {"speaker_map": {"a": "robot"}}):
        with pytest.raises(ValueError):
            mapped.load_mapping(mapping_with(**bad))
    with pytest.raises(ValueError, match="needs a mapping"):
        I.ingest(FIX / "helpdesk_export.json", "mapped", hl)


def test_mapped_unknown_responder_rejected(hl):
    r = I.ingest(FIX / "helpdesk_export.json", "mapped", hl, mapping_with(responder_map={"ai": "assistant"}))
    assert reasons(r)["T-1005"] == ["unknown responder value 'human'"]


def test_unknown_format(hl):
    with pytest.raises(ValueError, match="unknown format"):
        I.ingest(FIX / "harbourline_pii.json", "xml", hl)


def test_rejects_wrong_role_and_bad_record_without_leaking_text(hl, tmp_path):
    secret = "Zed Testperson 0400 000 999"
    conv = {"conversation_id": "x", "responder": "wizard",
            "patient_record": {"age_band": "35-44", "program": "p", "sessions_completed": 1,
                               "safety_plan_on_file": "no", "extra": secret},
            "turns": [{"speaker": "patient", "text": secret}, {"speaker": "responder", "text": secret}] * 2}
    path = tmp_path / "in.json"
    path.write_text(json.dumps([conv, {**conv, "conversation_id": "y", "responder": "peer"}]))
    r = I.ingest(path, "json", hl)
    assert r.accepted == [] and len(r.rejected) == 2
    assert secret not in json.dumps(r.rejected) and "Testperson" not in json.dumps(r.rejected)
    rep = json.dumps(I.report(r, hl, "json"))
    assert "Testperson" not in rep and "0400" not in rep


def test_duplicate_ids_rejected(hl, tmp_path):
    data = json.loads((FIX / "harbourline_pii.json").read_text())[:1] * 2
    path = tmp_path / "in.json"
    path.write_text(json.dumps(data))
    r = I.ingest(path, "json", hl)
    assert len(r.accepted) == 1 and r.rejected == [("pii-001", ["duplicate conversation_id"])]


def test_fernhill_canonical_roundtrip(tmp_path):
    fh = Clinic.load("fernhill")
    conv = {"conversation_id": "fh-1", "responder": "coach",
            "patient_record": {"age_band": "30-44", "sex": "female", "medication": "semaglutide weekly injection",
                               "dose_step": 2, "weeks_on_treatment": 12, "conditions": [],
                               "pregnancy_status": "not_pregnant", "other_medications": []},
            "turns": [{"speaker": "patient", "text": "Hi, I'm Jane. Email me at jane@example.com."},
                      {"speaker": "responder", "text": "Hi Jane, thanks. Urgent line is 1800 022 222."},
                      {"speaker": "patient", "text": "Ok."}, {"speaker": "responder", "text": "Bye."}]}
    path = tmp_path / "fh.json"
    path.write_text(json.dumps([conv]))
    r = I.ingest(path, "json", fh)
    assert type(r.accepted[0]).__name__ == "Conversation"
    out = dump(r)
    assert "Jane" not in out and "jane@" not in out and "1800 022 222" in out


def test_result_carries_no_mapping(hl):
    r = I.ingest(FIX / "harbourline_pii.json", "json", hl)
    assert set(vars(r)) == {"accepted", "rejected"}


def test_cli_writes_jsonl_and_report_without_text(hl, tmp_path, capsys):
    out, rep = tmp_path / "accepted.jsonl", tmp_path / "report.json"
    I.main(["--clinic", "harbourline", "--format", "mapped", "--mapping", str(FIX / "helpdesk_mapping.yaml"),
            str(FIX / "helpdesk_export.json"), "--out", str(out), "--report", str(rep)])
    lines = out.read_text().splitlines()
    assert [json.loads(l)["conversation_id"] for l in lines] == ["T-1001", "T-1005"]
    report = json.loads(rep.read_text())
    assert report["accepted"] == 2 and report["rejected"] == 3
    assert sum(report["reasons"].values()) == 3
    assert "Testa" not in rep.read_text() and "sleep" not in rep.read_text()
    assert "accepted 2, rejected 3" in capsys.readouterr().out
