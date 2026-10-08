"""Measure how real people write health questions, to calibrate the synthetic personas.

Source: MeQSum (Ben Abacha & Demner-Fushman, ACL 2019, CC BY 4.0), 1,000 consumer health
questions sent to the US National Library of Medicine. The questions are not about
semaglutide; they are used only for *how* people write (length, buried questions, several
questions at once), not *what* they ask. Topics come from the studies cited in
docs/patient_themes.md.

The raw questions are real people's messages, so they are downloaded to data/external/
(git-ignored) and never committed. Only aggregate shares are written to reports/.

Run: python -m safety_eval.patient_style
"""

import hashlib
import json
import re
import statistics
import urllib.request
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "external" / "meqsum.xlsx"
REPORT = ROOT / "reports" / "patient_style_stats.json"

# Pinned to a commit and checksum so the numbers can be reproduced exactly.
URL = (
    "https://raw.githubusercontent.com/abachaa/MeQSum/"
    "51e36cf65c12602333b6fdc3ae8ecf10430529f7/MeQSum_ACL2019_BenAbacha_Demner-Fushman.xlsx"
)
SHA256 = "abedd939d5014306ca576522416bf69103e85dc8fcf1668a4099e8b84a39eeea"

FAMILY = r"\bmy (daughter|son|husband|wife|mother|father|mom|mum|dad|child|baby|sister|brother|partner|grand\w+)\b"
AGE = r"\b\d{1,3} ?(yr|yrs|year|years)[- ]?old\b|\bi am (a )?\d{1,3}\b|\bi'?m (a )?\d{1,3}\b"
EMOTION = r"!!|\bplease help\b|\bworried\b|\bscared\b|\bafraid\b|\bdesperate\b|\bfrustrat\w*|\bconcerned\b"
COST = r"\bcost\w*\b|\bprice\w*\b|\bafford\w*\b|\binsurance\b|\bexpensive\b"


def download():
    """Fetch the spreadsheet once; refuse it if the bytes differ from the pinned version."""
    if not RAW.exists():
        RAW.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL, RAW)
    digest = hashlib.sha256(RAW.read_bytes()).hexdigest()
    if digest != SHA256:
        raise ValueError(f"{RAW} checksum {digest} does not match the pinned version")


def load_questions():
    sheet = openpyxl.load_workbook(RAW, read_only=True).active
    rows = list(sheet.iter_rows(values_only=True))
    header = rows[0]
    col = header.index("CHQ")
    return [message_body(r[col]) for r in rows[1:] if r[col]]


def message_body(text):
    """Drop the subject line: the style we care about is the message the person typed."""
    if "MESSAGE:" in text:
        return text.split("MESSAGE:", 1)[1].strip()
    head, sep, rest = text.partition("\n")
    return rest.strip() if sep and rest.strip() else text.strip()


def sentences_before_first_question(text):
    first_q = text.find("?")
    if first_q == -1:
        return None
    return len(re.findall(r"[.!](\s|$)", text[:first_q]))


def share(flags):
    return round(sum(flags) / len(flags), 3)


def measure(questions):
    lower = [q.lower() for q in questions]
    words = [len(q.split()) for q in questions]
    deciles = statistics.quantiles(words, n=10)
    before_q = [sentences_before_first_question(q) for q in questions]
    return {
        "n_messages": len(questions),
        "words_median": statistics.median(words),
        "words_p10": deciles[0],
        "words_p90": deciles[-1],
        "share_no_question_mark": share([b is None for b in before_q]),
        "share_two_or_more_question_marks": share([q.count("?") >= 2 for q in questions]),
        # "Buried": at least two sentences of background before the first question.
        "share_question_after_2plus_sentences": share([b is not None and b >= 2 for b in before_q]),
        "share_lowercase_i_pronoun": share([bool(re.search(r"(^|\s)i(\s|')", q)) for q in questions]),
        "share_asking_for_family_member": share([bool(re.search(FAMILY, q)) for q in lower]),
        "share_states_own_age": share([bool(re.search(AGE, q)) for q in lower]),
        "share_emotional_language": share([bool(re.search(EMOTION, q)) for q in lower]),
        "share_mentions_cost": share([bool(re.search(COST, q)) for q in lower]),
    }


def main():
    download()
    stats = measure(load_questions())
    report = {
        "source": "MeQSum consumer health questions (Ben Abacha & Demner-Fushman, ACL 2019), CC BY 4.0",
        "source_url": URL,
        "source_sha256": SHA256,
        "method": "Regular-expression counts over message bodies; see src/safety_eval/patient_style.py",
        "stats": stats,
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
