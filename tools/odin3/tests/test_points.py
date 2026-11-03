# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# Smash points (smash_record.Points) in each mode, from the KOs and damage a match ends with.
import json
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import smash_record  # noqa: E402
import smash_state  # noqa: E402
from smash_record import Points, mode_of  # noqa: E402

AIS = ["Ultimus", "Optimus", "Nostradamus", "Magnus"]


@pytest.fixture
def points(tmp_path, monkeypatch):
    monkeypatch.setattr(smash_record, "log", lambda *args: None)
    return Points(str(tmp_path / "points.json"), AIS)


def match(points, ais, tags, last, dealt=None, had=None):
    """A match of ais and people's tags ({fighter: name}) ending with last ({fighter: (falls,
    {fighter: KOs on it}, team)}) and dealt ({(from, to): %}), between fighters of had points."""
    for mode, table in (had or {}).items():
        points.points[mode].update(table)
    points.start(ais, tags)
    points.last, points.dealt = last, dict(dealt or {})
    return points.finish()


def test_free_for_all_of_1_11_at_23_45(points):
    # Bare (DK) against Hamz (Little Mac) and two CPUs: Hamz KOs Bare 3 times, Bare KOs Hamz once,
    # each % dealt to the other is worth 100 between equal points; the CPUs count for nothing.
    last = {0: (3, {1: 1, 3: 1}, 0), 1: (3, {}, 1), 2: (3, {}, 2), 3: (1, {0: 3, 1: 2, 2: 3}, 3)}
    dealt = {(3, 0): 361.57, (0, 3): 167.05, (3, 1): 52.0, (0, 1): 86.6, (0, 2): 50.8, (3, 2): 92.3}
    assert match(points, {}, {0: "Bare", 3: "Hamz"}, last, dealt) == {
        "Bare": (1_026_705, 26_705), "Hamz": (1_066_157, 66_157)}


def test_free_for_all_without_a_ko(points):
    # Hamz falls 3 times (once to Bare, once to a CPU, once on his own) and KOs no one: whatever
    # damage he dealt, it costs him NO_KO_POINTS between equals.
    last = {0: (3, {}, 0), 1: (0, {0: 1}, 1), 2: (0, {0: 1}, 2)}
    assert match(points, {}, {0: "Hamz", 1: "Bare"}, last, {(0, 1): 200}) == {
        "Hamz": (900_000, -100_000), "Bare": (1_010_000, 10_000)}


def test_free_for_all_without_a_ko_by_level(points):
    # 400,000 points apart, the stronger was expected to score 10 KOs for 1.
    last = {0: (1, {}, 0), 1: (1, {}, 1), 2: (0, {0: 1, 1: 1}, 2)}
    got = match(points, {}, {0: "Hamz", 1: "Bare"}, last, had={"ffa": {"Hamz": 1_400_000, "Bare": 1_000_000}})
    assert got == {"Hamz": (1_218_182, -181_818), "Bare": (981_818, -18_182)}


def test_free_for_all_without_a_ko_among_several(points):
    # Against one 400,000 stronger and one 400,000 weaker, as against equals.
    last = {0: (1, {}, 0), 1: (0, {0: 1}, 1), 2: (0, {}, 2), 3: (0, {2: 1}, 3)}
    got = match(points, {2: "Ultimus"}, {0: "Hamz", 1: "Bare"}, last,
                had={"ffa": {"Bare": 1_400_000, "Ultimus": 600_000}})
    assert got["Hamz"] == (900_000, -100_000)


def test_free_for_all_a_ko_on_a_cpu_is_a_ko(points):
    last = {0: (1, {2: 1}, 0), 1: (0, {0: 1}, 1), 2: (1, {}, 2)}
    assert match(points, {}, {0: "Hamz", 1: "Bare"}, last, {(0, 1): 50})["Hamz"] == (1_005_000, 5_000)


def test_one_on_one_between_equals(points):
    # Hamz KOs Ultimus 3 times, Ultimus KOs him once and he falls once on his own.
    last = {0: (2, {1: 3}, 0), 1: (3, {0: 1}, 1)}
    assert match(points, {1: "Ultimus"}, {0: "Hamz"}, last, {(0, 1): 300}) == {
        "Hamz": (1_010_000, 10_000), "Ultimus": (980_000, -20_000)}


def test_one_on_one_against_a_stronger_one(points):
    # 400,000 points apart, the stronger is expected to score 10 KOs for 1: one each costs him.
    last = {0: (1, {1: 1}, 0), 1: (1, {0: 1}, 1)}
    got = match(points, {}, {0: "Hamz", 1: "Bare"}, last,
                had={"1v1": {"Hamz": 1_400_000, "Bare": 1_000_000}})
    assert got == {"Hamz": (1_383_636, -16_364), "Bare": (1_016_364, 16_364)}


def test_one_on_one_a_win_never_costs(points):
    # 2/11 22:16, Hamz (Lucina) beats Bare (Ganondorf): 2 KOs each and Bare's last fall to no one.
    # His KOs on the weaker Bare are worth less than Bare's on him, but a win earns a KO at least.
    last = {0: (3, {1: 2}, 0), 1: (2, {0: 2}, 1)}
    got = match(points, {}, {0: "Bare", 1: "Hamz"}, last, had={"1v1": {"Hamz": 1_032_801, "Bare": 985_474}})
    assert got == {"Bare": (966_828, -18_646), "Hamz": (1_041_447, 8_646)}


def test_one_on_one_a_loss_never_earns(points):
    # Bare KOs the weaker Hamz twice, then falls 3 times on his own: Hamz wins a KO's worth still.
    last = {0: (3, {1: 2}, 0), 1: (2, {}, 1)}
    got = match(points, {}, {0: "Bare", 1: "Hamz"}, last, had={"1v1": {"Hamz": 600_000, "Bare": 1_000_000}})
    assert got == {"Bare": (951_818, -48_182), "Hamz": (618_182, 18_182)}


def test_teams(points):
    # Hamz and Bare against Ultimus and Magnus: Hamz KOs Ultimus twice, Magnus KOs Bare once,
    # Bare KOs Hamz once (his own teammate: a fall to no one).
    last = {0: (1, {2: 2}, 0), 1: (1, {0: 1}, 0), 2: (2, {}, 1), 3: (0, {1: 1}, 1)}
    assert match(points, {2: "Ultimus", 3: "Magnus"}, {0: "Hamz", 1: "Bare"}, last) == {
        "Hamz": (1_010_000, 10_000), "Bare": (990_000, -10_000),
        "Ultimus": (980_000, -20_000), "Magnus": (1_010_000, 10_000)}


def test_teams_against_cpus_only_lose(points):
    last = {0: (2, {2: 3}, 0), 1: (0, {3: 2}, 0), 2: (3, {0: 2}, 1), 3: (2, {}, 1)}
    assert match(points, {}, {0: "Hamz", 1: "Bare"}, last) == {
        "Hamz": (980_000, -20_000), "Bare": (1_000_000, 0)}


def test_never_under_zero(points):
    last = {0: (3, {}, 0), 1: (0, {}, 1)}
    got = match(points, {1: "Ultimus"}, {0: "Hamz"}, last, had={"1v1": {"Hamz": 5_000}})
    assert got["Hamz"] == (0, -5_000)


@pytest.mark.parametrize("ais, tags", [
    ({}, {0: "Hamz"}),                       # one person against CPUs
    ({0: "Ultimus", 1: "Magnus"}, {}),       # AIs alone
    ({}, {0: "Hamz", 1: ""}),                # someone without a name tag
    ({}, {0: "Hamz", 1: "Ultimus"}),         # an AI alone on a player card: training
])
def test_not_counted(points, ais, tags):
    assert match(points, ais, tags, {0: (1, {1: 1}, 0), 1: (1, {0: 1}, 1)}) == {}
    assert not os.path.exists(points.path)


def test_a_match_quit_before_its_results_is_not_counted(points):
    points.start({1: "Ultimus"}, {0: "Hamz"})
    points.last = {0: (2, {}, 0), 1: (0, {0: 2}, 1)}
    assert points.finish(results=False) == {}
    assert not os.path.exists(points.path)


def test_a_match_left_alone_is_not_counted(points):
    assert match(points, {1: "Ultimus"}, {0: "Hamz"}, {0: (0, {}, 0)}) == {}


def test_names(points):
    points.points["ffa"]["Nostradamus"] = 1_000_000
    assert points.key("Hamz -3") == "Hamz"
    assert points.key("Bare 6") == "Bare"
    assert points.key("Nostr") == "Nostradamus"  # an amiibo nickname cut short
    assert points.key("Ham") == "Ham"


def test_modes():
    assert mode_of(2, teams=True) == "1v1"
    assert mode_of(4, teams=True) == "teams"
    assert mode_of(3) == "ffa"


def test_saved_by_mode(points):
    match(points, {1: "Ultimus"}, {0: "Hamz"}, {0: (0, {1: 1}, 0), 1: (1, {}, 1)})
    with open(points.path) as fh:
        assert json.load(fh) == {"1v1": {"Hamz": 1_010_000, "Ultimus": 990_000}, "teams": {}, "ffa": {}}


class Frame:
    def __init__(self, watches):
        self.watches = watches


def hit_frame(victim_x, attacker_x, through_object=False):
    """Fighter 1 hit by fighter 0 (or by its projectile) at those x."""
    ids = {0: 100, 1: 101}
    watches = {f"{i}id": struct.pack("<I", ids[i]) for i in ids}
    watches["0pos"], watches["1pos"] = struct.pack("<ff", attacker_x, 0), struct.pack("<ff", victim_x, 0)
    watches["0dmg"], watches["1dmg"] = struct.pack("<f", 0), struct.pack("<f", 12)
    watches["0hitby"], watches["1hitby"] = struct.pack("<2I", 0, 0), struct.pack("<2I", 999 if through_object else 100, 100)
    return Frame(watches)


def test_damage_goes_to_who_hit():
    assert smash_state.damage(hit_frame(0, 30), 2)[1] == (12, 0)
    assert smash_state.damage(hit_frame(0, 90), 2)[1] == (12, 0)  # a long sword


def test_damage_with_no_one_near_is_no_ones():
    assert smash_state.damage(hit_frame(0, 150), 2)[1] == (12, None)


def test_damage_from_afar_through_a_projectile():
    assert smash_state.damage(hit_frame(0, 150, through_object=True), 2)[1] == (12, 0)


def test_global_level_from_every_mode(points):
    points.points["1v1"].update({"Hamz": 1_100_000})
    points.points["ffa"].update({"Hamz": 1_060_000, "Bare": 1_240_000})
    points.save_levels()
    with open(os.path.join(os.path.dirname(points.path), "levels.json")) as fh:
        levels = json.load(fh)
    assert levels["Hamz"] == 54.0 and levels["Bare"] == 62.0
    assert all(levels[ai] == 50.0 for ai in AIS)  # no match against people yet
