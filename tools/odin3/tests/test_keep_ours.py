# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# Merging upstream into odin3 must never undo one of our changes, even through a conflict resolution.
import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "keep_ours.py")

SPIN = "    while (!stop) { Spin(); }\n"
OURS_LINE = "    if (IsOdin3()) { SleepUntilVsync(); }\n"


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def commit(repo, files, message):
    for name, body in files.items():
        path = os.path.join(repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def history(tmp_path):
    repo = str(tmp_path)
    git(repo, "init", "-q", "-b", "master")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    base = commit(repo, {"turbo.cpp": "void Run() {\n" + SPIN + "}\n",
                         "other.cpp": "int a = 1;\n"}, "base")
    git(repo, "checkout", "-qb", "odin3")
    ours = commit(repo, {"turbo.cpp": "void Run() {\n" + OURS_LINE + "}\n"}, "ours")
    git(repo, "checkout", "-q", "master")
    upstream = commit(repo, {"other.cpp": "int a = 2;\n"}, "upstream")
    return repo, base, ours, upstream


def candidate(repo, ours, files):
    git(repo, "checkout", "-q", "-B", "candidate", ours)
    merge = subprocess.run(["git", "-C", repo, "merge", "-q", "--no-edit", "master"],
                           capture_output=True)
    if not files:
        assert merge.returncode == 0
        return git(repo, "rev-parse", "HEAD")
    return commit(repo, files, "resolution")


def keep_ours(repo, base, ours, upstream, cand):
    return subprocess.run([sys.executable, SCRIPT, base, ours, upstream, cand], cwd=repo,
                          capture_output=True, text=True)


def test_clean_upstream_merge_keeps_our_changes(tmp_path):
    repo, base, ours, upstream = history(tmp_path)
    result = keep_ours(repo, base, ours, upstream, candidate(repo, ours, None))
    assert result.returncode == 0, result.stdout


def test_resolution_that_drops_our_line_is_refused(tmp_path):
    repo, base, ours, upstream = history(tmp_path)
    cand = candidate(repo, ours, {"turbo.cpp": "void Run() {\n}\n"})
    result = keep_ours(repo, base, ours, upstream, cand)
    assert result.returncode == 1
    assert "turbo.cpp" in result.stdout and "SleepUntilVsync" in result.stdout


def test_resolution_that_brings_back_a_line_we_removed_is_refused(tmp_path):
    repo, base, ours, upstream = history(tmp_path)
    cand = candidate(repo, ours, {"turbo.cpp": "void Run() {\n" + OURS_LINE + SPIN + "}\n"})
    result = keep_ours(repo, base, ours, upstream, cand)
    assert result.returncode == 1
    assert "Spin()" in result.stdout


def test_upstream_adding_its_own_copy_of_a_line_we_removed_is_allowed(tmp_path):
    repo, base, ours, _ = history(tmp_path)
    upstream = commit(repo, {"turbo.cpp": "void Run() {\n" + SPIN + "}\nvoid Wait() {\n" + SPIN
                             + "}\n"}, "upstream adds Wait")
    cand = candidate(repo, ours, {"turbo.cpp": "void Run() {\n" + OURS_LINE + "}\nvoid Wait() {\n"
                                  + SPIN + "}\n"})
    result = keep_ours(repo, base, ours, upstream, cand)
    assert result.returncode == 0, result.stdout


def test_upstream_deleting_a_file_we_changed_is_refused(tmp_path):
    repo, base, ours, _ = history(tmp_path)
    git(repo, "rm", "-q", "turbo.cpp")
    git(repo, "commit", "-qm", "upstream drops turbo")
    upstream = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-B", "candidate", ours)
    subprocess.run(["git", "-C", repo, "merge", "-q", "--no-edit", "master"], capture_output=True)
    git(repo, "rm", "-q", "turbo.cpp")
    git(repo, "commit", "-qm", "take theirs")
    result = keep_ours(repo, base, ours, upstream, git(repo, "rev-parse", "HEAD"))
    assert result.returncode == 1
    assert "turbo.cpp" in result.stdout


def test_upstream_removing_its_own_copy_of_a_line_we_added_is_allowed(tmp_path):
    repo = str(tmp_path)
    git(repo, "init", "-q", "-b", "master")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    base = commit(repo, {"a.cpp": "Init();\nint x;\n"}, "base")
    git(repo, "checkout", "-qb", "odin3")
    ours = commit(repo, {"a.cpp": "Init();\nint x;\nInit();\n"}, "ours: init again")
    git(repo, "checkout", "-q", "master")
    upstream = commit(repo, {"a.cpp": "int x;\n"}, "upstream drops Init")
    cand = candidate(repo, ours, {"a.cpp": "int x;\nInit();\n"})
    result = keep_ours(repo, base, ours, upstream, cand)
    assert result.returncode == 0, result.stdout


def test_dropping_one_of_our_two_copies_of_a_line_is_refused(tmp_path):
    repo, base, ours, upstream = history(tmp_path)
    ours = commit(repo, {"turbo.cpp": "void Run() {\n" + OURS_LINE + OURS_LINE + "}\n"}, "twice")
    git(repo, "checkout", "-q", "master")
    cand = candidate(repo, ours, {"turbo.cpp": "void Run() {\n" + OURS_LINE + "}\n"})
    result = keep_ours(repo, base, ours, upstream, cand)
    assert result.returncode == 1
    assert "SleepUntilVsync" in result.stdout


def test_reindenting_our_line_or_dropping_our_blank_line_is_allowed(tmp_path):
    repo, base, _, upstream = history(tmp_path)
    git(repo, "checkout", "-q", "odin3")
    ours = commit(repo, {"turbo.cpp": "void Run() {\n" + OURS_LINE + "\n}\n"}, "blank")
    git(repo, "checkout", "-q", "master")
    cand = candidate(repo, ours, {"turbo.cpp": "void Run() {\n\t" + OURS_LINE.strip() + "\n}\n"})
    result = keep_ours(repo, base, ours, upstream, cand)
    assert result.returncode == 0, result.stdout


def test_unknown_revision_never_passes(tmp_path):
    repo, base, ours, upstream = history(tmp_path)
    result = keep_ours(repo, base, ours, upstream, "no-such-branch")
    assert result.returncode != 0


def test_upstream_making_the_same_change_as_ours_is_allowed(tmp_path):
    repo, base, ours, _ = history(tmp_path)
    upstream = commit(repo, {"turbo.cpp": "void Run() {\n" + OURS_LINE + "}\n"}, "upstream fix")
    result = keep_ours(repo, base, ours, upstream, candidate(repo, ours, None))
    assert result.returncode == 0, result.stdout
