"""Guard against dev or test conversations leaking into a judge prompt (brief section 9).

A prompt file may contain worked examples, and those must come from the train split only. The
check looks for two things: any dev/test conversation ID, and any long enough stretch of a
dev/test conversation's text (an example could be pasted in without its ID).
"""

import json
from pathlib import Path

from .splits import load_splits

ROOT = Path(__file__).resolve().parents[2]
CONVERSATIONS = ROOT / "data" / "conversations" / "conversations.jsonl"
PROMPTS = ROOT / "prompts"
MIN_SNIPPET = 60   # characters: long enough that a match cannot be a coincidence


def protected_texts():
    """{conversation_id: [snippets]} for every dev and test conversation imported so far."""
    splits = load_splits()
    protected = splits["dev"] | splits["test"]
    out = {cid: [] for cid in protected}
    if CONVERSATIONS.exists():
        for line in CONVERSATIONS.read_text().splitlines():
            conv = json.loads(line)
            if conv["conversation_id"] in protected:
                out[conv["conversation_id"]] = [t["text"][:MIN_SNIPPET] for t in conv["turns"]
                                                if len(t["text"]) >= MIN_SNIPPET]
    return out


def find_leaks(text, protected=None):
    """List of (conversation_id, what leaked) found in a prompt's text."""
    protected = protected_texts() if protected is None else protected
    leaks = []
    for cid, snippets in protected.items():
        if cid in text:
            leaks.append((cid, "id"))
        elif any(s in text for s in snippets):
            leaks.append((cid, "text"))
    return leaks


def check_prompt_files(folder=PROMPTS):
    """Raise if any file in prompts/ contains a dev or test conversation."""
    protected = protected_texts()
    for path in sorted(Path(folder).rglob("*")):
        if path.is_file():
            leaks = find_leaks(path.read_text(errors="ignore"), protected)
            if leaks:
                raise AssertionError(f"{path.name} contains dev/test conversations: {leaks[:5]}")
