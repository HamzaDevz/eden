# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# The true level (smash_rl.performance, fit_levels): wins, spacing and speed.
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
pytest.importorskip("torch")
import smash_rl  # noqa: E402


def match(won, dealt, taken, seconds=180):
    return smash_rl.Match("", won, 0.0, 9, None, 0, True, "AI", False, [dealt, taken, seconds])


def test_spacing_counts_in_a_win():
    clean, scraped = match(True, 300, 0), match(True, 300, 280)
    assert smash_rl.performance(clean) > smash_rl.performance(scraped) > 0.5


def test_faster_is_better():
    assert smash_rl.performance(match(True, 300, 100, 90)) > smash_rl.performance(match(True, 300, 100, 300))


def test_kept_without_stats_is_the_win():
    assert smash_rl.performance(match(True, 0, 0)._replace(stats=None)) == 1.0


def test_level_follows_the_performance():
    even = smash_rl.fit_levels([("AI", 9, 0.5)] * 50)["AI"]
    better = smash_rl.fit_levels([("AI", 9, 0.8)] * 50)["AI"]
    assert even == pytest.approx(smash_rl.CPU_LEVEL[9], abs=0.1)
    assert better > even + 3
