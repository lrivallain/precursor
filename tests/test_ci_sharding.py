"""The CI shard split (``PRECURSOR_TEST_SHARD``) covers every test exactly once."""

from __future__ import annotations

import conftest


def test_every_key_lands_in_exactly_one_shard() -> None:
    keys = [f"tests/test_x.py::test_{i}" for i in range(500)]
    picks = [conftest.shard_selection(keys, shard, 4) for shard in range(1, 5)]
    for i in range(len(keys)):
        assert sum(pick[i] for pick in picks) == 1
    # Roughly even, so no shard becomes the new long pole.
    assert all(80 < sum(pick) < 170 for pick in picks)


def test_the_split_is_stable_across_calls() -> None:
    keys = ["tests/test_a.py::test_one", "xdist_group:loopback-port"]
    assert conftest.shard_selection(keys, 2, 3) == conftest.shard_selection(keys, 2, 3)


def test_a_single_shard_keeps_everything() -> None:
    assert all(conftest.shard_selection(["a", "b", "c"], 1, 1))
