"""Agreement metrics and the external-sample draw, checked on small hand-worked examples."""

import pytest

from safety_eval.external import draw_sample
from safety_eval.metrics import cohen_kappa, pooled_pairwise


def test_kappa_hand_worked_example():
    # 10 items: both say yes on 4, both no on 4, disagree on 2 -> observed 0.8.
    a = [1, 1, 1, 1, 0, 0, 0, 0, 1, 0]
    b = [1, 1, 1, 1, 0, 0, 0, 0, 0, 1]
    # Each rater says yes 5/10 times, so chance agreement is 0.5*0.5 + 0.5*0.5 = 0.5.
    assert cohen_kappa(a, b) == pytest.approx((0.8 - 0.5) / (1 - 0.5))


def test_kappa_edges():
    assert cohen_kappa([1, 0, 1], [1, 0, 1]) == 1.0
    assert cohen_kappa([1, 1], [1, 1]) == 1.0          # no variation: agreement is total
    with pytest.raises(ValueError):
        cohen_kappa([1], [1, 0])


def test_pooled_pairwise_counts_each_pair_once():
    agree, kappa, pairs = pooled_pairwise([[True, True], [True, False], [False, False, False]])
    assert pairs == 1 + 1 + 3
    assert agree == pytest.approx(4 / 5)


def fake_rows(cat_count=60):
    rows = {}
    for cat in ("emergent", "conditionally_emergent", "non_emergent"):
        rows[cat] = [{"completion_id": f"{cat}-{i}", "prompt_id": f"{cat}-p{i}",
                      "binary_labels": [i % 3 != 0] * 2 if i % 5 else [True, False]}
                     for i in range(cat_count)]
    return rows


def test_sample_is_balanced_unanimous_and_deterministic():
    rows = fake_rows()
    s = draw_sample(rows, seed=1, per_cell=5)
    assert len(s) == 20
    assert {x["category"] for x in s} == {"emergent", "conditionally_emergent"}
    assert len({x["prompt_id"] for x in s}) == 20
    by_id = {r["completion_id"]: r for rs in rows.values() for r in rs}
    assert all(len(set(by_id[x["completion_id"]]["binary_labels"])) == 1 for x in s)
    assert draw_sample(rows, seed=1, per_cell=5) == s
