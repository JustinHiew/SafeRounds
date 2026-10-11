# The reviewer queue (product phase 2D)

A small web app, served by the same FastAPI service, where a clinic's reviewers work through the
results the judge was not sure about: read the de-identified conversation, see the judge's verdicts
and quoted evidence side by side, and confirm or overturn each verdict. Everything is scoped to the
reviewer's own clinic. Code: `src/safety_eval/service/ui.py`, `ui_db.py`, `static/`.

## Turn it on

The UI is served only when `SESSION_SECRET` is set. The production app (`get_app()`) refuses to
start without it.

| Variable | Default | Meaning |
|---|---|---|
| `SESSION_SECRET` | none, required | at least 32 random characters; signs session cookies. `python -c 'import secrets; print(secrets.token_urlsafe(48))'` |
| `UI_COOKIE_SECURE` | `1` | set `0` only for local `http://` testing; in production cookies must be `Secure`, so serve over HTTPS |

Changing `SESSION_SECRET` signs everyone out. For Postgres, run `sql/003_reviewers.sql` once (SQLite
creates the table itself).

## Create reviewers

Like API keys, accounts are made on the server with the admin CLI; there is no sign-up page.

```bash
python -m safety_eval.service.admin create-reviewer --clinic harbourline --email ana@clinic.example --role reviewer
python -m safety_eval.service.admin create-reviewer --clinic harbourline --email lead@clinic.example --role admin --generate
python -m safety_eval.service.admin disable-reviewer --clinic harbourline --email ana@clinic.example
```

Without `--generate` it prompts for a password (10 or more characters, typed twice). With
`--generate` it makes a random one and prints it once; only a scrypt hash is stored. The clinic must
already exist (`create-clinic`). Emails are unique per clinic and compared case-insensitively.
Disabling a reviewer ends their session immediately.

Roles: `reviewer` can read the queue and give feedback. `admin` can also export (below).

## Sign in and use the queue

Open `/ui` (it redirects to `/ui/login`). Enter the clinic id, email and password. A session lasts
8 hours; sign out with the button in the header.

- **Left:** the list. Tabs switch between *Open* (no confirm or overturn yet) and *Reviewed*; the
  outcome filter narrows to needs review, unsure, or flagged-but-ok. Newest first, 25 per page. The
  list shows ids, outcome, severity and which criteria failed or were unsure, never conversation text.
- **Right:** the conversation as chat turns, the patient record, and one card per criterion with the
  judge's verdict, analysis and quoted evidence. The quotes are highlighted in the transcript
  (matched ignoring case, spacing and curly quotes, as the judge does); the focused criterion's
  quotes are highlighted more strongly.
- **Confirm** says the verdict is right. **Overturn** says it is wrong (a pass becomes fail, a fail
  becomes pass). An *unsure* verdict can be confirmed or commented on but not overturned, because
  there is no single value to flip it to. A result the judge produced nothing for (an internal error)
  can be confirmed or commented on.
- **Confirm all** records one whole-result confirmation and moves to the next item. The note box
  is saved with whichever button you press. Do not type patient details in notes.
- On a phone the list and the detail take turns; use *Back to list*.

A result counts as **reviewed** once any confirm or overturn exists for it (any criterion, or the
whole result). A comment alone leaves it open. Open results are therefore the ones nobody has made a
call on.

### Keyboard shortcuts

| Key | Action |
|---|---|
| `j` / `k` | next / previous result in the list |
| `n` / `p` | next / previous criterion (the focused one has a green outline) |
| `c` | confirm all |
| `o` | overturn the focused criterion |

Shortcuts are off while you type in the note box. The focused criterion starts on the first one that
is not a pass; clicking a card also focuses it.

## Export (admin only)

`GET /ui/api/export/candidates.jsonl` (the *Export overturned* link in the header) returns one JSON
line per result with at least one overturned criterion, as a candidate for a new eval case:

```json
{"source": "reviewer_feedback", "clinic_id": "...", "result_id": 12, "conversation_id": "...",
 "conversation": {"...de-identified...": ""}, "judge": {"verdicts": {"safety": "fail", "...": ""}, "severity": "high", "...": ""},
 "verdicts": {"safety": "pass", "...": ""}, "severity": "none", "unsure": false,
 "overturned": ["safety"], "reviewers": ["ana@clinic.example"], "notes": ["..."], "labelled_at": "..."}
```

`verdicts` are the corrected ones: the judge's verdict, flipped on criteria whose latest
confirm/overturn is an overturn. `severity` is the judge's when something still fails, `none` when
nothing does, and `null` when a reviewer flipped a pass to fail and the judge gave no severity (a
person must decide). Conversations are the stored de-identified text, so the file holds
placeholders such as `[NAME_1]`, never raw identifiers; notes are free text the reviewers typed.

The export is a download only. It is **never written into `data/labels/`**: those are human
labels from blind labelling, and reviewer feedback was given while looking at the judge's answer.
A person can review the file and decide which lines become labelled cases through the usual
labelling and reconcile steps. Exports are audited.

## Security notes

- Passwords: scrypt (`n=16384, r=8, p=1`, random 16-byte salt) via `hashlib`; verified in constant
  time. An unknown email, wrong password, unknown clinic and disabled reviewer all give the same
  401, and unknown emails still run one scrypt so timing does not tell them apart.
- Login is limited to 8 attempts a minute per IP and email (and 32 per IP), 429 beyond that.
  In memory, per process, and the IP is the connecting address: behind a proxy that is the proxy
  unless uvicorn is told to trust forwarded headers (`--proxy-headers --forwarded-allow-ips`).
- Session: cookie `se_session` = `reviewer_id.expiry.session_id.HMAC-SHA256`, `HttpOnly`,
  `SameSite=Strict`, `Secure`, path `/ui`. The reviewer row is re-read on every request, so a
  disabled reviewer is out at once. There is no server-side session list, so signing out clears the
  cookie but cannot revoke a copy of it before it expires; rotate `SESSION_SECRET` for that.
- CSRF: writes under `/ui/api` and `/ui/logout` need `X-CSRF-Token` equal to
  `HMAC(secret, session id)`; the page reads it from the `se_csrf` cookie (readable by script by
  design, useless without the session cookie). Login itself takes JSON only.
- Tenancy: every query filters on the reviewer's `clinic_id`; another clinic's result is a 404, the
  same as an id that does not exist. Reading a result and exporting are written to `audit_log` with
  the actor `reviewer:<id>`. The queue list shows no conversation text and is not audited.
- Pages: a Content-Security-Policy allows only the page's own script and style (by hash) and
  same-origin requests, `no-store`, no framing. The pages load nothing external and write data into
  the page only with `textContent`; a test greps for `innerHTML` and external URLs.
- Nothing logs conversation text, notes or passwords.
