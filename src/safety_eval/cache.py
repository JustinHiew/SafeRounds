"""On-disk cache of model responses, so a rerun never repeats a call it already has.

Each response is one JSON file under cache/<namespace>/, named by a hash of the key fields
(for the judge: prompt version, model and conversation ID, as the brief requires). The cache is
committed, so reports can be rebuilt on a clean clone without any model calls.
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "cache"


def cache_key(**fields):
    """Stable hash of the fields that define a call. Field order does not matter."""
    blob = json.dumps(fields, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


class ResponseCache:
    def __init__(self, namespace, root=CACHE_DIR):
        self.dir = Path(root) / namespace

    def _path(self, key):
        return self.dir / f"{key}.json"

    def get(self, key):
        path = self._path(key)
        return json.loads(path.read_text()) if path.exists() else None

    def put(self, key, entry):
        self.dir.mkdir(parents=True, exist_ok=True)
        # Write then rename, so an interrupted run never leaves a half-written entry behind.
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(self._path(key))

    def __contains__(self, key):
        return self._path(key).exists()


def confirm_batch(n_new, threshold=100, assume_yes=False, ask=input):
    """Print how many new calls a batch will make, and ask before going above the threshold."""
    print(f"{n_new} new model call(s) needed (the rest are cached).")
    if n_new <= threshold or assume_yes:
        return True
    answer = ask(f"That is more than {threshold}. Continue? [y/N] ")
    return answer.strip().lower() in ("y", "yes")
