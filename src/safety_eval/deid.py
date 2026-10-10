"""De-identification: replace personal details in a conversation with placeholders (roadmap 2B).

    conv, mapping = deidentify_conversation(conv, clinic, known_names=("Jane Testperson",))

Every turn and every string in the patient record is scrubbed. Placeholders are [NAME_1],
[EMAIL_1], [PHONE_1], [MEDICARE_1], [IHI_1], [DATE_1], [ADDRESS_1] and [URL_1]. The same original
value always gets the same placeholder within one conversation (phones compare by digits, emails
ignoring case, "Jane" and "Jane Testperson" together), and `mapping` is placeholder -> the first
original seen, so a caller that is allowed to keep it can audit the change. It is not an exact
restore: "Hi Jane" and "Jane Testperson" share one placeholder and so one original. Ingestion is not allowed to (see ingest/__init__.py).

Clinic material is never scrubbed. The Accuracy criterion checks the phone numbers, hours and URLs
that staff quote from the reference sheet and protocol, so any phone number (compared by digits),
URL, email or address that also appears in that material is left alone, as are 000 and 112.

Detected: emails, Australian phone numbers (mobile, landline, +61, 13/1300/1800), Medicare numbers
(check digit validated, so random 10-digit numbers are not hit), IHIs (16 digits from 800360),
full dates, URLs, street addresses, and names in three ways: the `known_names` the caller supplies
(whole word, case-insensitive, first names and surnames too), self-introductions ("my name is
Jane", "I'm Jane."), and staff greetings and sign-offs ("Hi Sarah,", "Thanks, Priya.", "- Sam").
A name found by the second or third way is then scrubbed everywhere else in the conversation too.

Limitations, honestly. Regex de-identification is incomplete. A name that is not in known_names
and is not introduced in one of those ways survives. So do dates without a year, eight-digit local
phone numbers, lower-case addresses, suburbs on their own, and anything spelled out or broken up
("zero four one two ..."). A known name that is also an ordinary word ("Will", "Mark") is scrubbed
everywhere, because matching is case-insensitive. Stored data is therefore still treated as
sensitive (roadmap 2G); this module reduces the risk and does not remove it.

The output is deterministic: the same input gives the same text and numbering.
"""

import re
from functools import cache

PLACEHOLDERS = ("NAME", "EMAIL", "PHONE", "MEDICARE", "IHI", "DATE", "ADDRESS", "URL")
NEVER_SCRUB = frozenset({"000", "112"})

# When two detections overlap, the earlier category wins (a name inside a URL stays in the URL).
_PRIORITY = ("EMAIL", "URL", "IHI", "MEDICARE", "PHONE", "DATE", "ADDRESS", "NAME")

# ------------------------------------------------------------------------------ patterns

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"')\]]+")

_GAP = r"[ -]?"
PHONE = re.compile(
    r"(?<![\d/])(?:"
    r"(?:\+61[ -]?|0)4(?:[ -]?\d){8}"                                  # mobile
    r"|(?:\(0[2378]\)|0[2378]|\+61[ -]?[2378])" + _GAP + r"\d{4}" + _GAP + r"\d{4}"   # landline
    r"|1[38]00" + _GAP + r"\d{3}" + _GAP + r"\d{3}"                    # 1300 / 1800
    r"|13" + _GAP + r"\d{2}" + _GAP + r"\d{2}"                         # 13 xx xx
    r")(?!\d)")

MEDICARE = re.compile(r"(?<![\d/])[2-6]\d{3}[ -]?\d{5}[ -]?\d(?:[ -]?\d)?(?!\d)")
IHI = re.compile(r"(?<![\d/])8003[ -]?60(?:[ -]?\d){10}(?!\d)")

_MONTH = (r"(?i:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_ORD = r"(?:st|nd|rd|th)?"
DATES = (
    re.compile(r"(?<![\d/.-])(\d{1,2})([/.-])(\d{1,2})\2(\d{4}|\d{2})(?![\d/])"),
    re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)"),
    re.compile(rf"\b\d{{1,2}}{_ORD}\s+(?:of\s+)?{_MONTH}\.?,?\s+\d{{4}}\b"),
    re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}{_ORD},?\s+\d{{4}}\b"),
)

_STREET_TYPES = ("St|Street|Rd|Road|Ave|Avenue|Cres|Crescent|Dr|Drive|Ct|Court|Pl|Place|Pde|Parade|"
                 "Hwy|Highway|Ln|Lane|Tce|Terrace|Bvd|Boulevard|Way|Cl|Close")
ADDRESS = re.compile(
    r"(?<![\w/])(?:(?i:unit|apt|flat)\s+)?(?:\d{1,3}/)?\d{1,4}[A-Za-z]?\s+"
    r"(?:[A-Z][A-Za-z'’-]*\s+){1,3}"
    rf"(?i:{_STREET_TYPES})\b"
    r"(?:,?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?,?\s+(?:NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s+\d{4})?")

# Names. A candidate is one to three Capitalised words after an optional title.
_NAME_WORD = r"[A-Z][a-z][A-Za-z'’-]*"
_NAME = rf"(?:(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof)\.?\s+)?({_NAME_WORD}(?:\s+{_NAME_WORD}){{0,2}})"
_END = r"(?=\s*(?:[.,!?;:)\-–—]|$))"
NAME_INTROS = (
    # "my name is Jane" is specific enough that no punctuation is required after it.
    re.compile(rf"(?i:\bmy name(?:'s|’s| is)|\bname's)\s+{_NAME}"),
    re.compile(rf"(?i:\bi'm|\bi’m|\bi am|\bcall me)\s+{_NAME}{_END}"),
)
NAME_GREETING = re.compile(
    rf"\A\s*(?i:hi|hiya|hello|hey|dear|g'day|gday|howdy|good (?:morning|afternoon|evening))"
    rf",?\s+{_NAME}(?=\s*[,!.:;])")
NAME_SIGNOFF = re.compile(
    rf"\A\s*(?i:thanks|thank you|cheers|bye|goodbye|take care|kind regards|regards)\s*[,!]?\s+"
    rf"{_NAME}{_END}")
NAME_SIGNATURE = re.compile(rf"(?m)^[ \t]*[-–—~][ \t]*{_NAME}[ \t]*$")

# Capitalised words that follow "I'm" or "Hi" without being a name. One of them anywhere in the
# candidate cancels it, so "I'm Not Sure." and "Thanks, Very Much." are left alone.
NOT_NAMES = frozenset("""
a about afraid again all alone also always an and angry anxious are asking at away back bad
better bit bleeding but busy by calling calm concerned confused dear dizzy doctor done down dr
embarrassed everyone exhausted feeling female fine finished folks for friend from getting glad going
good goodbye grateful great guys happy having he hello her here hi high hopeless hungry ill in is it
just later lost low madam male mate me much new no not now nurse of off ok okay on one only or out
over pregnant ready really regards sad scared she sick sir so sorry still struggling stuck stressed
sure taking team thank thanks that the their there they this thinking tired to too trying
unsure unwell up very we well what when where who why with wondering worried worse writing yes
you your
""".split())


# ------------------------------------------------------------------------------ clinic material

class _Material:
    """What the clinic itself publishes. Anything found here is quoted on purpose and kept."""

    def __init__(self, clinic):
        text = "\n".join([*clinic.facts().values(), clinic.protocol_text(),
                          clinic.paths["reference"].read_text()])
        self.phones = {_phone_key(m.group()) for m in PHONE.finditer(text)} | NEVER_SCRUB
        self.urls = {_url_key(m.group()) for m in _urls(text)}
        self.emails = {m.group().lower() for m in EMAIL.finditer(text)}
        self.addresses = {_squash(m.group()) for m in ADDRESS.finditer(text)}

    def covers(self, category, value):
        if category == "PHONE":
            return _phone_key(value) in self.phones
        if category == "URL":
            return _url_key(value) in self.urls
        if category == "EMAIL":
            return value.lower() in self.emails
        if category == "ADDRESS":
            return _squash(value) in self.addresses
        return False


@cache
def _material(clinic):
    return _Material(clinic)


# ------------------------------------------------------------------------------ normal forms

def _squash(s):
    return " ".join(s.lower().split())


def _digits(s):
    return re.sub(r"\D", "", s)


def _phone_key(s):
    d = _digits(s)
    return "0" + d[2:] if d.startswith("61") and len(d) == 11 else d


def _url_key(s):
    s = re.sub(r"^(?:https?://)?(?:www\.)?", "", s.lower())
    return s.rstrip("/")


def _urls(text):
    """URL matches without trailing sentence punctuation."""
    for m in URL.finditer(text):
        end = m.end()
        while text[end - 1] in ".,;:!?":
            end -= 1
        yield _Span(m.start(), end, text[m.start():end])


class _Span:
    def __init__(self, start, end, value):
        self.start, self.end, self.value = start, end, value

    def group(self):
        return self.value


def medicare_valid(number):
    """Medicare check digit: weights 1,3,7,9,1,3,7,9 over the first eight digits, mod 10."""
    d = _digits(number)
    if len(d) not in (10, 11) or d[0] not in "23456":
        return False
    total = sum(int(c) * w for c, w in zip(d[:8], (1, 3, 7, 9, 1, 3, 7, 9)))
    return total % 10 == int(d[8])


def _date_valid(m):
    a, b = int(m.group(1)), int(m.group(3))
    return (1 <= a <= 31 and 1 <= b <= 12) or (1 <= a <= 12 and 1 <= b <= 31)


# ------------------------------------------------------------------------------ detection

def _pattern_spans(text, clinic):
    """(start, end, category, value) for everything except names."""
    found = [(m.start(), m.end(), "EMAIL", m.group()) for m in EMAIL.finditer(text)]
    found += [(m.start, m.end, "URL", m.value) for m in _urls(text)]
    found += [(m.start(), m.end(), "IHI", m.group()) for m in IHI.finditer(text)]
    found += [(m.start(), m.end(), "MEDICARE", m.group()) for m in MEDICARE.finditer(text)
              if medicare_valid(m.group())]
    found += [(m.start(), m.end(), "PHONE", m.group()) for m in PHONE.finditer(text)]
    for i, pattern in enumerate(DATES):
        found += [(m.start(), m.end(), "DATE", m.group()) for m in pattern.finditer(text)
                  if i != 0 or _date_valid(m)]
    found += [(m.start(), m.end(), "ADDRESS", m.group()) for m in ADDRESS.finditer(text)]
    material = _material(clinic)
    return [s for s in found if not material.covers(s[2], s[3])]


def _introduced_names(text):
    """Names the text introduces (self-introductions, greetings, sign-offs, signatures)."""
    names = set()
    patterns = (*NAME_INTROS, NAME_GREETING, NAME_SIGNOFF, NAME_SIGNATURE)
    for pattern in patterns:
        for m in pattern.finditer(text):
            candidate = m.group(1)
            if not {w.lower() for w in candidate.split()} & NOT_NAMES:
                names.add(candidate)
    return names


def _name_index(names):
    """(regex, group) for whole-word, case-insensitive matching of each name and each part of it.
    `group` maps every surface form to its full name, so "Jane" and "Jane Testperson" share a
    placeholder. A part shared by two names goes to whichever sorts first."""
    group = {}
    for name in sorted({" ".join(n.split()) for n in names if n.split()}, key=str.lower):
        full = _squash(name)
        group.setdefault(full, full)
        for word in name.split():
            if len(word) >= 2:
                group.setdefault(word.lower(), full)
    if not group:
        return None, {}
    ordered = sorted(group, key=lambda g: (-len(g), g))
    surfaces = [r"\s+".join(re.escape(w) for w in g.split()) for g in ordered]
    return re.compile(r"(?<![A-Za-z])(?:" + "|".join(surfaces) + r")(?![A-Za-z])", re.IGNORECASE), group


def _resolve(spans):
    """Drop overlapping detections, keeping the highest-priority (then longest, then first)."""
    ranked = sorted(spans, key=lambda s: (_PRIORITY.index(s[2]), s[0] - s[1], s[0]))
    kept = []
    for s in ranked:
        if all(s[1] <= k[0] or s[0] >= k[1] for k in kept):
            kept.append(s)
    return sorted(kept)


def find_pii(text, clinic, known_names=()):
    """[(category, value)] still detectable in text. Used by tests and ingestion to re-check."""
    names = {n.strip() for n in known_names if n.strip()} | _introduced_names(text)
    return [(cat, value) for _, _, cat, value in _all_spans(text, clinic, _name_index(names)[0])]


def _all_spans(text, clinic, name_re):
    spans = _pattern_spans(text, clinic)
    if name_re:
        spans += [(m.start(), m.end(), "NAME", m.group()) for m in name_re.finditer(text)]
    return _resolve(spans)


# ------------------------------------------------------------------------------ replacement

class _Scrubber:
    def __init__(self, clinic, names):
        self.clinic = clinic
        self.name_re, self.name_group = _name_index(names)
        self.placeholders = {}     # (category, normal form) -> placeholder
        self.mapping = {}          # placeholder -> first original seen

    def placeholder(self, category, value):
        key = (category, self._normal(category, value))
        if key not in self.placeholders:
            n = sum(1 for c, _ in self.placeholders if c == category) + 1
            self.placeholders[key] = f"[{category}_{n}]"
            self.mapping[self.placeholders[key]] = value
        return self.placeholders[key]

    def _normal(self, category, value):
        if category == "NAME":
            return self.name_group.get(_squash(value), _squash(value))
        if category == "PHONE":
            return _phone_key(value)
        if category in ("MEDICARE", "IHI"):
            return _digits(value)
        if category == "URL":
            return _url_key(value)
        return _squash(value)

    def text(self, text):
        out, last = [], 0
        for start, end, category, value in _all_spans(text, self.clinic, self.name_re):
            out += [text[last:start], self.placeholder(category, value)]
            last = end
        return "".join(out) + text[last:]

    def value(self, value):
        """A record value: strings are scrubbed, lists of strings too, anything else is kept."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(v) for v in value]
        return value


def _record_strings(record):
    flat = []
    for v in record.values():
        flat += [v] if isinstance(v, str) else [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
    return flat


def deidentify_conversation(conv, clinic, known_names=()):
    """Return a copy of conv with personal details replaced by placeholders, and the mapping
    placeholder -> original. The input is not modified."""
    data = conv.model_dump()
    names = {n.strip() for n in known_names if n and n.strip()}
    for text in [t["text"] for t in data["turns"]] + _record_strings(data["patient_record"]):
        names |= _introduced_names(text)
    scrubber = _Scrubber(clinic, names)
    for turn in data["turns"]:
        turn["text"] = scrubber.text(turn["text"])
    data["patient_record"] = {k: scrubber.value(v) for k, v in data["patient_record"].items()}
    return type(conv).model_validate(data), scrubber.mapping


__all__ = ["deidentify_conversation", "find_pii", "medicare_valid", "PLACEHOLDERS"]
