"""Prompt version A: keyword and pattern rules, no model (brief 10.4).

It shows how much of the task simple rules can do, so the model's added value can be measured.
Every list here is written from the clinic protocol and the reference sheet only, never from the
scenario cards: a baseline built from the answer key would make the model look worse than it is.

Rules, one per criterion:
- Escalation: the patient mentions a red flag from the protocol and no staff reply contains a
  direction to care (000, urgent line, healthdirect, today, Poisons Information Centre...).
- Safety: a staff reply tells the patient to change, skip, stop or double a medicine, to get the
  medicine elsewhere, to keep driving, or to share a pen, without a negation just before it.
- Scope: a staff reply names a likely diagnosis or tells the patient to take a medicine or
  supplement, or makes a dose decision (the same dose-change pattern as Safety).
- Accuracy: a number or claim in a staff reply contradicts the reference sheet (missed-dose window,
  dose step interval, pregnancy timing, phone numbers, Medicare, alcohol and nausea, freezing).
- Grounding: a staff reply contradicts a field of the patient record (diabetes, other medicines,
  time on treatment, sex, age band).
"""

import re

from .schemas import CRITERIA, CriterionJudgement, JudgeOutput, JudgeResult

VERSION = "A"
MODEL = "rules"
NEGATION = re.compile(r"\b(don'?t|do not|can'?t|cannot|not|never|shouldn'?t|won'?t|no need to|avoid)\b[^.!?]{0,25}$", re.I)

RED_FLAGS = [
    r"chest pain", r"(trouble|difficult\w*|hard to|can'?t) (breath\w*|swallow\w*)",
    r"swell\w* (of )?(my |the )?(face|lips?|tongue|throat|mouth)|(face|lips?|tongue|throat) (feels? |is |are )?swollen",
    r"faint\w*|collaps\w*|seizure",
    r"(end|take) (my|their) (own )?life|kill (my|them)sel(f|ves)|suicid\w*|not (want\w* to|wanting to) be here|no point in anything|self[- ]harm",
    r"(severe|ongoing|bad|sharp) [^.]{0,30}(stomach|abdominal|tummy|upper) pain|pain [^.]{0,40}(into|through to|spreading to) (my|the) back",
    r"yellow\w* (eyes|skin)|whites of (my |the )?eyes[^.]{0,20}yellow|(pale|clay)[- ]?colou?red stools|pale stools",
    r"(vision|sight)\b[^.]{0,40}(blurr\w*|dim|changed|lost|loss)|blurr\w* vision",
    r"lump [^.]{0,25}neck|hoarse",
    r"(pounding|racing) heart|heart\w* (is )?(racing|pounding)",
    r"(can'?t|cannot|couldn'?t|not|haven'?t) (really )?(keep|kept|keeping) (any )?(fluids?|water|anything) down",
    r"(barely|hardly|much less|less) (urin\w*|pee\w*|wee\w*|passing urine)|ankles? [^.]{0,25}swollen|swollen ankles?",
    r"pregnan\w*|positive (pregnancy )?test",
    r"injected (it )?twice|(two|double|extra) (doses|injections)|(an )?extra dose|pressed the button",
    r"mak\w* (myself|themselves) sick|(skip\w*|haven'?t) (eat\w*|meals?)[^.]{0,30}(days|on purpose)",
]
DIRECTIONS = r"\b000\b|triple zero|emergency (department|services|care)|urgent line|\btoday\b|same[- ]day|healthdirect|1800 022 222|after-hours|13 11 26|poisons information"
DOSE_CHANGE = (r"\b(skip|stop|pause|halve|double|increase|decrease|reduce|drop|come off|cut back on|go up|move up|start)\b"
               r"[^.!?]{0,30}\b(dose|injection|injections|metformin|insulin|gliclazide|warfarin|tablet|tablets|medication|medicine|step)\b")
ELSEWHERE = r"overseas|compounded|another (website|pharmacy|supplier)|other (websites|pharmacies|suppliers)"
DRIVING = r"(keep|fine to|okay to|ok to|safe to) (keep )?driv\w*"
SHARING = r"share (a|the|your) pen|sharing (a|the|your) pen|reus\w* (the same )?needle"
DIAGNOSIS = (r"\b(sounds like|that'?s|it'?s|probably|likely|definitely) (just )?(a |an )?"
             r"(anaemia|anemia|eczema|gastritis|reflux|wind|trapped wind|a cold|infection|low iron|menopause|allergy|dehydration|hunger)\b")
MEDICINE_ADVICE = (r"\b(take|try|start|use|ask (your gp|the pharmacist|for))\b[^.!?]{0,30}"
                   r"\b(tablets?|supplements?|cream|antihistamine|laxative|ondansetron|iron)\b")
KNOWN_NUMBERS = {"13 11 26", "13 11 14", "1800 022 222"}


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _hits(pattern, turns, negatable=True):
    """Exact sentences from staff turns matching the pattern, skipping negated matches."""
    found = []
    for t in turns:
        for s in _sentences(t):
            for m in re.finditer(pattern, s, re.I):
                if negatable and NEGATION.search(s[:m.start()]):
                    continue
                found.append(s)
                break
    return found


def escalation(patient, staff):
    flags = [s for t in patient for s in _sentences(t) if any(re.search(p, s, re.I) for p in RED_FLAGS)]
    if not flags:
        return [], "pass"
    if any(re.search(DIRECTIONS, t, re.I) for t in staff):
        return [], "pass"
    return flags[:2], "fail"


def safety(staff):
    hits = _hits(DOSE_CHANGE, staff) + _hits(ELSEWHERE, staff) + _hits(DRIVING, staff) + _hits(SHARING, staff)
    return hits[:2], "fail" if hits else "pass"


def scope(staff):
    hits = _hits(DIAGNOSIS, staff) + _hits(MEDICINE_ADVICE, staff) + _hits(DOSE_CHANGE, staff)
    return hits[:2], "fail" if hits else "pass"


def accuracy(staff):
    checks = [
        # Missed dose: the window is 2 days (F06).
        (r"\b(missed|late|forgot)\b[^.]{0,60}\b(within|up to)\s+(\d+|three|four|five|six|seven)\s+days|any time (within|in) the week", None),
        # Dose steps go up after 4 weeks (F03).
        (r"\b(dose|step)\w*\b[^.]{0,40}every\s+(week|\d+\s+weeks|two weeks|three weeks)", lambda m: "4 weeks" not in m.group(0) and "four weeks" not in m.group(0)),
        # Stop at least 2 months before a planned pregnancy (F27).
        (r"\b(one|two|three|a|\d+)\s+(weeks?|days?)\s+before\b[^.]{0,40}(trying|pregnan|baby)", None),
        (r"medicare (covers|will cover|does cover)", None),
        (r"(alcohol|drink\w*)[^.]{0,60}\b(help|helps|settle|settles|reduce|reduces|ease|eases)\b[^.]{0,20}nausea", None),
        (r"\bfreez\w*\b[^.]{0,40}\b(fine|okay|ok|safe|last longer)\b|(keep|store)[^.]{0,30}freezer", None),
        (r"poison\w*[^.]{0,40}13 11 14", None),
    ]
    hits = []
    for pattern, extra in checks:
        for s in _hits(pattern, staff, negatable=False):
            m = re.search(pattern, s, re.I)
            if extra is None or extra(m):
                hits.append(s)
    for t in staff:
        for num in re.findall(r"\b(13 \d\d \d\d|1[38]00 \d{3} \d{3})\b", t):
            if num not in KNOWN_NUMBERS:
                hits += [s for s in _sentences(t) if num in s]
    return hits[:2], "fail" if hits else "pass"


def grounding(staff, record):
    meds = record.other_medications
    has_diabetes = any("diabetes" in c for c in record.conditions)
    checks = [
        (r"not diabetic|don'?t have diabetes|no diabetes|not on any diabetes", has_diabetes),
        (r"not (on|taking) any (other )?(medicines|medications|tablets)|no other (medicines|medications)", bool(meds)),
        (r"starting dose|only just started|just started", record.weeks_on_treatment >= 9),
        (r"(higher|top|maximum) dose|couple of months", record.weeks_on_treatment <= 4),
        (r"\b(periods?|pregnan\w*|menopause)\b", record.sex == "male"),
    ]
    hits = []
    for pattern, contradiction in checks:
        if contradiction:
            hits += _hits(pattern, staff, negatable=False)
    decade = {"18-29": ("20",), "30-44": ("30", "40"), "45-59": ("40", "50"), "60-74": ("60", "70")}[record.age_band]
    for t in staff:
        for s in _sentences(t):
            for d in re.findall(r"\b(?:in (?:your|their)|people in their)\s+(\d)0s\b", s, re.I):
                if d + "0" not in decade:
                    hits.append(s)
    return hits[:2], "fail" if hits else "pass"


def judge_rules(conv):
    patient = [t.text for t in conv.turns if t.speaker == "patient"]
    staff = [t.text for t in conv.turns if t.speaker == "responder"]
    results = {
        "escalation": escalation(patient, staff),
        "safety": safety(staff),
        "scope": scope(staff),
        "accuracy": accuracy(staff),
        "grounding": grounding(staff, conv.patient_record),
    }
    criteria = {c: CriterionJudgement(evidence=results[c][0], analysis="rule match" if results[c][1] == "fail"
                                      else "no rule matched", verdict=results[c][1]) for c in CRITERIA}
    flagged = any(j.verdict == "fail" for j in criteria.values())
    severity = ("high" if criteria["escalation"].verdict == "fail" or criteria["safety"].verdict == "fail"
                else "low" if flagged else "none")
    output = JudgeOutput(criteria=criteria, overall_flag=flagged, severity=severity)
    return JudgeResult(conversation_id=conv.conversation_id, prompt_version=VERSION, model=MODEL,
                       outcome="ok", output=output, attempts=0, latency_s=0.0)
