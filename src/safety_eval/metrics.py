"""Agreement and classification metrics, written out by hand so each step can be explained."""

from collections import Counter
from itertools import combinations


def cohen_kappa(a, b):
    """Agreement between two raters beyond what chance would give (see docs/concepts.md).

    kappa = (observed agreement - chance agreement) / (1 - chance agreement), where chance
    agreement assumes each rater labels independently at their own base rates.
    """
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty label lists")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    chance = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if chance == 1 else (observed - chance) / (1 - chance)


def pooled_pairwise(label_sets):
    """Agreement when each item was rated by a different, varying group of raters.

    Every unordered pair of ratings on the same item counts once, in both orders, so neither
    rater in a pair is privileged. Returns (pairwise percent agreement, kappa on those pairs, n pairs).
    """
    first, second = [], []
    for labels in label_sets:
        for x, y in combinations(labels, 2):
            first += [x, y]
            second += [y, x]
    if not first:
        raise ValueError("no item has two or more ratings")
    agree = sum(x == y for x, y in zip(first, second)) / len(first)
    return agree, cohen_kappa(first, second), len(first) // 2


def confusion(truth, predicted):
    """Counts for a yes/no flag: true/false positives and negatives."""
    tp = sum(t and p for t, p in zip(truth, predicted))
    fp = sum(p and not t for t, p in zip(truth, predicted))
    fn = sum(t and not p for t, p in zip(truth, predicted))
    tn = sum(not t and not p for t, p in zip(truth, predicted))
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def rate(numerator, denominator):
    """A share with its counts kept alongside, because the counts are small (analysis plan 1)."""
    return {"count": f"{numerator}/{denominator}",
            "rate": round(numerator / denominator, 3) if denominator else None}


def wilson(successes, n, z=1.96):
    """Wilson score interval for a share (default 95%). Returns (low, high), or (None, None) when n is 0.

    Unlike the plain normal interval it behaves at 0/n and n/n and with the small counts used here."""
    if n <= 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def recall_precision(truth, predicted):
    c = confusion(truth, predicted)
    return {"recall": rate(c["tp"], c["tp"] + c["fn"]),
            "precision": rate(c["tp"], c["tp"] + c["fp"]),
            "confusion": c}
