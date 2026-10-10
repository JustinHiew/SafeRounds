"""Build the web labelling page (the same blind view as `safety_eval.labelling`, in a browser).

  python -m safety_eval.label_page            writes labelling/label_desk.html

The page shows exactly what the terminal tool shows: responder type, patient record and turns,
in the same fixed random order (pass1 seed, explore seed). It never contains the scenario
cards, planted mistakes, splits or source models. Answers are saved to the page's own private
store; `python -m safety_eval.labelling import-web FILE` turns an export of that store into
data/labels/labels_pass1.jsonl and data/labels/exploration_notes.md.

The page is built from labelling/template.html; the built file is not committed (rebuild it).
"""

import html
import json
import random
import re
from pathlib import Path

from .labelling import RESPONDER, RUBRIC, SEEDS, load_conversations, queue, rubric_version
from .schemas import CRITERIA

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "labelling" / "template.html"
OUT = ROOT / "labelling" / "label_desk.html"
FACTS = ROOT / "data" / "reference" / "medication_facts.md"
PROTOCOL = ROOT / "data" / "reference" / "clinic_protocol.md"
EXPLORE_N = 30


def _inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)


def md_to_html(md):
    """Just enough Markdown for the rubric and reference files: headings, paragraphs, lists, tables."""
    out, para, items = [], [], []

    def flush():
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush()
        elif m := re.match(r"(#{1,4})\s+(.*)", line):
            flush()
            level = len(m.group(1)) + 1          # the page has its own h1
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
        elif line.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.fullmatch(r"\|[\s:|-]+\|", lines[i].strip()):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], rows[1:]
            out.append('<div class="table-wrap"><table><thead><tr>'
                       + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table></div>")
            continue
        elif line.startswith("- "):
            if para:
                flush()
            items.append(line[2:].strip())
        elif items and line.startswith("  "):
            items[-1] += " " + line.strip()
        else:
            para.append(line.strip())
        i += 1
    flush()
    return "\n".join(out)


def criteria_definitions(rubric_md):
    """{criterion: definition sentence} from the rubric's '### Name' / '**Definition.**' lines."""
    defs = {}
    for name, body in re.findall(r"^### (\w+)\s*\n+\*\*Definition\.\*\*\s*(.+?)(?:\n\n|\Z)", rubric_md, re.M | re.S):
        defs[name.lower()] = " ".join(body.split())
    return defs


def page_data():
    convs = load_conversations()
    order = [c.conversation_id for c in queue(convs, "pass1", {})]
    pool = list(convs)
    random.Random(SEEDS["explore"]).shuffle(pool)       # same order as `labelling explore`
    explore = [c.conversation_id for c in pool[:EXPLORE_N]]
    rubric_md = RUBRIC.read_text()
    defs = criteria_definitions(rubric_md)
    rules = rubric_md[rubric_md.index("## How to label"):]
    return {
        "order": order,
        "explore": explore,
        "convs": {c.conversation_id: {"responder": RESPONDER[c.responder],
                                      "record": c.patient_record.model_dump(),
                                      "turns": [[t.speaker, t.text] for t in c.turns]} for c in convs},
        "rubricVersion": rubric_version(),
        "criteria": [{"key": k, "name": k.capitalize(), "definition": defs.get(k, "")} for k in CRITERIA],
        "ref": {"rubric": md_to_html(rules), "facts": md_to_html(FACTS.read_text()),
                "protocol": md_to_html(PROTOCOL.read_text())},
    }


def build():
    data = json.dumps(page_data(), ensure_ascii=False).replace("</", "<\\/")
    OUT.write_text(TEMPLATE.read_text().replace("/*DATA*/null", data))
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    build()
