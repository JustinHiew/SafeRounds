"""The LLM judge: build the prompt, call the model (cached), and check the answer (brief 10).

A prompt version is a file in prompts/, e.g. prompts/C.md:

    ---
    version: C
    mode: whole              # or per_criterion: one call per criterion (version E)
    ---
    <system>
    ...system prompt...
    </system>
    <user>
    ...template with {{RUBRIC}} {{REFERENCE_SHEET}} {{CLINIC_PROTOCOL}} {{PATIENT_RECORD}}
       {{CONVERSATION}} {{EXAMPLES}} {{CRITERIA}}...
    </user>

Checks around every answer (brief 10.3), all failing closed, never to "pass":
- the reply must be JSON matching JudgeOutput; one retry on malformed output, then needs_review;
- a failed or timed-out call is needs_review;
- every evidence quote must appear word for word in the conversation (whitespace and quote
  marks normalised); a quote that does not is needs_review, because the judge may be reasoning
  about something that is not there;
- a fail or unsure verdict needs at least one quote;
- any unsure verdict routes the conversation to a person (outcome "unsure").

Everything clinic-specific (rubric, reference sheet, protocol, staff role names) comes from a
Clinic (clinic.py). Without one, the judge uses Fernhill, exactly as before clinics existed.
"""

import hashlib
import json
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from .cache import ResponseCache, cache_key
from .clinic import DEFAULT_CLINIC, Clinic
from .leakage import find_leaks
from .llm import ModelCallError
from .schemas import CRITERIA, CriterionJudgement, JudgeOutput, JudgeResult

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "prompts"
EXAMPLES = ROOT / "prompts" / "examples.md"   # Fernhill-only worked examples (prompt D)


class PromptVersion:
    def __init__(self, path):
        text = Path(path).read_text()
        front, body = re.match(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL).groups()
        meta = yaml.safe_load(front)
        self.version = str(meta["version"])
        self.mode = meta.get("mode", "whole")
        self.system = re.search(r"<system>\n?(.*?)\n?</system>", body, re.DOTALL).group(1).strip()
        self.template = re.search(r"<user>\n?(.*?)\n?</user>", body, re.DOTALL).group(1).strip()
        # Any change to the file changes the cache key, so an edited prompt is never served old answers.
        self.sha = hashlib.sha256(text.encode()).hexdigest()[:16]
        self.file_text = text

    @classmethod
    def load(cls, version, folder=PROMPTS, check_leaks=True):
        prompt = cls(Path(folder) / f"{version}.md")
        if check_leaks:
            leaks = find_leaks(prompt.file_text)     # the whole file, not just the sections used
            if leaks:
                raise AssertionError(f"prompt {version} contains dev/test conversations: {leaks[:3]}")
        return prompt


# ------------------------------------------------------------------------- rendering

def _clinic(clinic):
    return clinic if clinic is not None else Clinic.load(DEFAULT_CLINIC)


def rubric_text(criteria=CRITERIA, clinic=None):
    """Each criterion's definition and boundary, copied word for word from the clinic's rubric."""
    text = _clinic(clinic).rubric_markdown()
    parts = []
    for crit in criteria:
        section = re.search(rf"### {crit.title()}\n(.*?)(?=\n### |\Z)", text, re.DOTALL).group(1)
        definition = re.search(r"\*\*Definition\.\*\* (.*?)\n\n", section, re.DOTALL).group(1)
        boundary = re.search(r"\*\*Boundary\.\*\* (.*?)\n\n", section, re.DOTALL).group(1)
        parts.append(f"{crit.upper()}\nDefinition: {' '.join(definition.split())}\n"
                     f"Boundary: {' '.join(boundary.split())}")
    severity = re.search(r"## Severity.*?\n\n(.*?)\n\nWhen unsure", text, re.DOTALL).group(1)
    return "\n\n".join(parts) + "\n\nSEVERITY\n" + severity.strip()


def render_conversation(conv, clinic=None):
    staff = _clinic(clinic).staff_label(conv.responder)
    lines = [f"Turn {i}, {'Patient' if t.speaker == 'patient' else 'Staff (' + staff + ')'}: {t.text}"
             for i, t in enumerate(conv.turns, 1)]
    return "\n\n".join(lines)


def render_record(record):
    data = record if isinstance(record, dict) else record.model_dump()
    return yaml.safe_dump(data, sort_keys=False).strip()


def render(prompt, conv, criteria=CRITERIA, examples="", clinic=None):
    clinic = _clinic(clinic)
    fact_lines = "\n".join(f"{fid}: {text}" for fid, text in clinic.facts().items())
    fills = {
        "RUBRIC": rubric_text(criteria, clinic),
        "REFERENCE_SHEET": fact_lines,
        "CLINIC_PROTOCOL": clinic.protocol_text(),
        "PATIENT_RECORD": render_record(conv.patient_record),
        "CONVERSATION": render_conversation(conv, clinic),
        "EXAMPLES": examples,
        "CRITERIA": ", ".join(criteria),
        "RESPONDER": clinic.staff_label(conv.responder),
    }
    text = prompt.template
    for key, value in fills.items():
        text = text.replace("{{" + key + "}}", value)
    left = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if left:
        raise ValueError(f"unfilled placeholders: {left}")
    return text


# ------------------------------------------------------------------------- checking

def _normalise(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(s.split()).lower()


def extract_object(text):
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    return json.loads(candidate[start:end + 1])


def parse(text, criteria=CRITERIA):
    """JudgeOutput, or raise ValueError. In per-criterion mode only the asked criterion is expected."""
    data = extract_object(text)
    if set(criteria) != set(CRITERIA):
        crits = {c: CriterionJudgement.model_validate(data["criteria"][c]) for c in criteria}
        return crits, data.get("severity", "none")
    try:
        return JudgeOutput.model_validate(data)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def quote_problems(output_criteria, conv):
    """Reasons to send to review: a quote not in the conversation, or a fail/unsure with no quote."""
    haystack = _normalise("\n".join(t.text for t in conv.turns))
    problems = []
    for crit, j in output_criteria.items():
        if j.verdict in ("fail", "unsure") and not [q for q in j.evidence if q.strip()]:
            problems.append(f"{crit}: {j.verdict} without evidence")
        for q in j.evidence:
            if q.strip() and _normalise(q) not in haystack:
                problems.append(f"{crit}: quote not found in conversation")
    return problems


# ------------------------------------------------------------------------- calling

def _key(prompt, backend, conv, criterion, attempt, repeat, clinic):
    # repeat > 0 is a deliberate re-run of the same call, for the consistency check (brief 11).
    fields = dict(prompt_version=prompt.version, prompt_sha=prompt.sha, model=backend.model,
                  conversation_id=conv.conversation_id, criterion=criterion, attempt=attempt,
                  repeat=repeat)
    # Fernhill keys stay as they were, so the committed cache still matches. Any other clinic
    # adds its id (conversation ids can repeat across clinics) and a hash of its files (an
    # edited protocol must never be served answers judged against the old one).
    if clinic.id != DEFAULT_CLINIC:
        fields.update(clinic_id=clinic.id, clinic_sha=clinic.sha)
    return cache_key(**fields)


def _call(backend, cache, prompt, conv, text, criterion, attempt, repeat=0, clinic=None):
    key = _key(prompt, backend, conv, criterion, attempt, repeat, _clinic(clinic))
    entry = cache.get(key)
    if entry is None:
        resp = backend.complete(prompt.system, text)
        entry = {"conversation_id": conv.conversation_id, "prompt_version": prompt.version,
                 "criterion": criterion, "attempt": attempt, "model": resp.model,
                 "latency_s": round(resp.latency_s, 3), "text": resp.text}
        cache.put(key, entry)
    return entry


def needed_calls(prompt, conv, backend, cache, repeat=0, clinic=None):
    """How many first-attempt calls are not cached yet (retries are not known in advance)."""
    crits = CRITERIA if prompt.mode == "per_criterion" else ["all"]
    clinic = _clinic(clinic)
    return sum(_key(prompt, backend, conv, c, 0, repeat, clinic) not in cache for c in crits)


def _judge_once(prompt, conv, backend, cache, criteria, examples, repeat=0, clinic=None):
    """(parsed output or None, model, attempts, latency, reasons) for one call with one retry."""
    text = render(prompt, conv, criteria, examples, clinic)
    label = "all" if len(criteria) == len(CRITERIA) else criteria[0]
    reasons, latency, model = [], 0.0, backend.model
    for attempt in (0, 1):
        try:
            entry = _call(backend, cache, prompt, conv, text, label, attempt, repeat, clinic)
        except ModelCallError as exc:
            return None, model, attempt + 1, latency, [f"call failed: {exc}"]
        latency += entry["latency_s"]
        model = entry["model"]
        try:
            return parse(entry["text"], criteria), model, attempt + 1, latency, []
        except (ValueError, KeyError, json.JSONDecodeError, ValidationError) as exc:
            reasons = [f"malformed output: {str(exc)[:200]}"]
    return None, model, 2, latency, reasons


def judge(conv, prompt, backend, cache=None, examples="", repeat=0, clinic=None):
    """Judge one conversation. Never raises for model problems: they become needs_review.
    A clinic config problem (for example an unknown responder role) does raise, before any call."""
    cache = cache or ResponseCache("judge")
    clinic = _clinic(clinic)
    clinic.staff_label(conv.responder)
    if prompt.mode == "per_criterion":
        return _judge_per_criterion(conv, prompt, backend, cache, examples, repeat, clinic)
    output, model, attempts, latency, reasons = _judge_once(prompt, conv, backend, cache, list(CRITERIA),
                                                            examples, repeat, clinic)
    if output is None:
        return JudgeResult(conversation_id=conv.conversation_id, prompt_version=prompt.version,
                           model=model, outcome="needs_review", reasons=reasons,
                           attempts=attempts, latency_s=latency)
    return _finish(conv, prompt, model, output, attempts, latency)


def _judge_per_criterion(conv, prompt, backend, cache, examples, repeat=0, clinic=None):
    merged, severities, reasons, attempts, latency, model = {}, [], [], 0, 0.0, backend.model
    for crit in CRITERIA:
        out, model, n, lat, why = _judge_once(prompt, conv, backend, cache, [crit], examples, repeat,
                                              clinic)
        attempts += n
        latency += lat
        if out is None:
            reasons += [f"{crit}: {r}" for r in why]
            continue
        crits, severity = out
        merged.update(crits)
        severities.append(severity)
    if reasons:
        return JudgeResult(conversation_id=conv.conversation_id, prompt_version=prompt.version,
                           model=model, outcome="needs_review", reasons=reasons,
                           attempts=attempts, latency_s=latency)
    flagged = any(j.verdict == "fail" for j in merged.values())
    worst = "high" if "high" in severities else "low" if flagged else "none"
    try:
        output = JudgeOutput(criteria=merged, overall_flag=flagged, severity=worst)
    except ValidationError as exc:
        return JudgeResult(conversation_id=conv.conversation_id, prompt_version=prompt.version,
                           model=model, outcome="needs_review", reasons=[f"inconsistent output: {exc}"],
                           attempts=attempts, latency_s=latency)
    return _finish(conv, prompt, model, output, attempts, latency)


def _finish(conv, prompt, model, output, attempts, latency):
    problems = quote_problems(output.criteria, conv)
    if problems:
        outcome = "needs_review"
    elif any(j.verdict == "unsure" for j in output.criteria.values()):
        outcome, problems = "unsure", [c for c, j in output.criteria.items() if j.verdict == "unsure"]
    else:
        outcome = "ok"
    return JudgeResult(conversation_id=conv.conversation_id, prompt_version=prompt.version, model=model,
                       outcome=outcome, reasons=problems, output=output, attempts=attempts, latency_s=latency)
