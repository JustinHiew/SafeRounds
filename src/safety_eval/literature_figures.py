"""Copy cited published figures into reports/, so every number in the docs traces to reports/.

The figures are typed in by hand from the papers (with the exact sentence they come from) in
data/reference/literature_figures.yaml. This script only validates and copies them; it makes
no network calls.

Run: python -m safety_eval.literature_figures
"""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "reference" / "literature_figures.yaml"
REPORT = ROOT / "reports" / "literature_figures.json"
REQUIRED = {"citation", "url", "licence", "quote", "denominator", "figures"}


def load():
    entries = yaml.safe_load(SOURCE.read_text())
    for key, entry in entries.items():
        missing = REQUIRED - entry.keys()
        if missing:
            raise ValueError(f"{key} is missing {sorted(missing)}")
        for name, value in entry["figures"].items():
            # Shares are stored as fractions; a value like 29.4 would mean a typo.
            if name.startswith("share_") and not 0 <= value <= 1:
                raise ValueError(f"{key}.{name}={value} is not a fraction")
    return entries


def main():
    entries = load()
    REPORT.write_text(json.dumps(entries, indent=2) + "\n")
    print(f"wrote {len(entries)} sources to {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
