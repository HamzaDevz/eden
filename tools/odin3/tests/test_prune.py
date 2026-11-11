# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# The AIs' matches on disk (smash_rl.prune): each AI's last ones kept, the others deleted once read,
# their results still there.
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
pytest.importorskip("torch")
import smash_rl  # noqa: E402

OLD = time.time() - 7200


def folder(tmp_path, names, read=None):
    for n in names:
        for ext in (".txt.gz", ".npz"):
            p = tmp_path / (n + ext)
            p.write_bytes(b"x" * 1000)
            os.utime(p, (OLD, OLD))
    read = names if read is None else read
    entry = lambda n: [True, 1.0, 9, None, 0, True, n.split("-")[1], False, None]
    (tmp_path / "results.json").write_text(json.dumps({"win": smash_rl.WIN, **{n + ".txt.gz": entry(n) for n in read}}))
    (tmp_path / "choices.json").write_text(json.dumps({"horizon": smash_rl.HORIZON, **{n + ".txt.gz": {} for n in read}}))
    return [smash_rl.Match(str(tmp_path / (n + ".txt.gz")), *entry(n)) for n in read]


def left(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".txt.gz"))


def test_keeps_each_ais_last(tmp_path, monkeypatch):
    names = [f"2026111{i}-A" for i in range(5)] + [f"2026110{i}-B" for i in range(3)]
    res = folder(tmp_path, names)
    smash_rl.prune(tmp_path, res, 2)
    assert left(tmp_path) == ["20261101-B.txt.gz", "20261102-B.txt.gz", "20261113-A.txt.gz", "20261114-A.txt.gz"]
    assert not (tmp_path / "20261110-A.npz").exists()


def test_cap_keeps_fewer(tmp_path, monkeypatch):
    monkeypatch.setattr(smash_rl, "PLAY_CAP", 4500)  # two matches and their caches
    res = folder(tmp_path, [f"2026111{i}-A" for i in range(5)])
    smash_rl.prune(tmp_path, res, 4)
    assert left(tmp_path) == ["20261113-A.txt.gz", "20261114-A.txt.gz"]


def test_unread_and_fresh_stay(tmp_path):
    names = [f"2026111{i}-A" for i in range(4)]
    res = folder(tmp_path, names, read=names[1:])
    os.utime(tmp_path / "20261113-A.txt.gz")  # just arrived
    smash_rl.prune(tmp_path, res, 1)
    assert left(tmp_path) == ["20261110-A.txt.gz", "20261113-A.txt.gz"]


def test_results_outlive_their_files(tmp_path):
    res = folder(tmp_path, [f"2026111{i}-A" for i in range(3)])
    smash_rl.prune(tmp_path, res, 1)
    assert [os.path.basename(m.path) for m in smash_rl.results(tmp_path)] == [
        "20261110-A.txt.gz", "20261111-A.txt.gz", "20261112-A.txt.gz"]
