# Ingestion and de-identification

Conversations come in from a customer's own system, are checked against the clinic's config, have
personal details replaced by placeholders, and only then go anywhere else. All data in the repo and
its tests is synthetic.

```
python -m safety_eval.ingest --clinic harbourline --format csv|json|mapped [--mapping m.yaml] INPUT \
    --out accepted.jsonl --report report.json
```

`accepted.jsonl` holds the de-identified conversations. `report.json` holds counts, rejection
reasons and conversation ids, never conversation text. In code: `ingest(path, fmt, clinic, mapping=None)`
returns an `ImportResult` with `accepted` and `rejected` (`[(conversation_id, [reasons])]`).

## Order of work

1. The format's importer normalises the input into the canonical shape.
2. The clinic checks it: responder role, record fields and types, alternating turns.
3. Length: 4 to 10 turns. Outside that the conversation is rejected ("too short" or "too long"),
   never truncated, because cutting turns changes what the judge sees.
4. De-identification.
5. The clinic check again on the cleaned conversation.

A conversation that fails any step is rejected with reasons and the rest carry on. A repeated
`conversation_id` is rejected. A broken file (not JSON, missing CSV columns, a bad mapping) stops the
run with an error.

## Formats

**json**: a JSON list, or JSONL when the file ends in `.jsonl`, of conversations in the shape the
rest of the project uses: `conversation_id`, `responder`, `patient_record`, `turns`.

**csv**: one row per turn. Required columns: `conversation_id`, `turn` (a whole number giving the
order), `speaker` (`patient` or `responder`), `text`, `responder`. Record fields come from columns
named `record_<field>`, read from the conversation's first row in the file. Empty cells are left
out, `int` fields are parsed, `list` fields are split on `;`. An optional `known_names` column
(names split on `;`) feeds de-identification. Any other column is an error.

**mapped**: a helpdesk-style JSON export plus a YAML mapping, so a new export is configuration.
Example: `tests/fixtures/ingest/helpdesk_mapping.yaml` with `helpdesk_export.json`.

| Key | Meaning |
| --- | --- |
| `conversations` | dotted path to the list of conversations (empty: the file is the list) |
| `id` | field holding the conversation id |
| `messages` | field holding the message list |
| `speaker_field`, `text_field` | fields inside each message |
| `speaker_map` | source speaker value to `patient` or `responder`; an unmapped value rejects the conversation |
| `responder` | a fixed clinic role, or |
| `responder_field` + `responder_map` | read the role from the conversation and map it |
| `record` | record field to source path; values are converted to the clinic's field types |
| `known_name_fields` | source paths holding names (customer, agent), passed to de-identification |
| `merge_consecutive` | default true: same-speaker messages in a row are joined with a newline |
| `drop_leading_responder` | default false: drop a bot greeting before the first patient message |
| `drop_trailing_patient` | default false: drop patient messages after the last reply |

Paths are dotted and a number picks a list item. Unknown keys are an error. Messages without text
are skipped.

## What de-identification replaces

Placeholders: `[NAME_n]`, `[EMAIL_n]`, `[PHONE_n]`, `[MEDICARE_n]`, `[IHI_n]`, `[DATE_n]`,
`[ADDRESS_n]`, `[URL_n]`. The same value gets the same placeholder throughout one conversation
(phones by digits, emails ignoring case, a first name and the full name together). Every turn and
every string in the patient record (including strings in lists) is scrubbed; numbers, field names,
the conversation id and the responder are not.

- Emails; Australian phone numbers (04xx, +61, landlines with an area code, 13/1300/1800).
- Medicare numbers (10 or 11 digits, with the check digit validated so random numbers are left alone) and IHIs (16 digits from 800360).
- Full dates (`14/02/1990`, `4/2/90`, `3 March 1985`, `March 3, 1985`, ISO). A date without a year is kept.
- URLs and street addresses (number, name, street type, optional unit, optional suburb, state and postcode).
- Names: every `known_names` entry (whole word, any case, first names and surnames too); self-introductions ("my name is Jane", "I'm Jane."); staff greetings and sign-offs at the start of a turn ("Hi Sarah,", "Thanks, Priya.") and "- Sam" signature lines. A name found this way is scrubbed everywhere else in the conversation too. A short stoplist stops "I'm Sorry." and "Hi there," being read as names.

## The clinic-number allowlist

The Accuracy criterion checks the phone numbers, hours and URLs staff quote from the clinic's
reference sheet and protocol, for example Lifeline 13 11 14 or 1800 160 401. Scrubbing them would
make correct replies look wrong. So any phone number (compared by digits), URL, email or address
that appears in that clinic's facts, reference file or protocol is left alone, and 000 and 112 are
never touched. Another number in the same sentence is still scrubbed. The list is per clinic: a
number is only safe in the clinic whose material contains it.

## Evidence quotes

The judge must quote the conversation it was shown. Ingestion returns the de-identified text, and
that is what the judge sees and what `judge.quote_problems` checks against, so a quote such as
"My mobile is [PHONE_1]" passes and a quote containing the original number fails (tests cover both).

## The mapping is discarded

`deidentify_conversation` returns a placeholder-to-original mapping, but `ingest` throws it away.
A returned mapping would end up in a log, cache or queue and defeat the point. A deployment that
needs to reverse the change calls `deidentify_conversation` itself and keeps the mapping in the
customer's own store (roadmap 2G). The mapping is for audit rather than an exact restore: "Hi Jane"
and "Jane Testperson" share a placeholder and so a single original.

## Limitations

Regex de-identification is incomplete. Names that are not in `known_names` and not introduced in a
recognised way survive, as do dates without a year, eight-digit local phone numbers, lower-case
addresses, suburbs on their own, and details spelled out in words. A known name that is also an
ordinary word ("Will") is scrubbed everywhere. Stored data is therefore still treated as sensitive
(roadmap 2G): encrypted, access-controlled and deleted on request.
