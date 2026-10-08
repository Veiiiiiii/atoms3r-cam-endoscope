#!/usr/bin/env python3
"""Tuning-drawer preset/export helpers (UV-PORT-PLAN.md §0 D11/D12, §3.7):
uv_presets_load / uv_presets_save / uv_preset_name / uv_preset_id /
uv_find_export_dir / uv_export. Pure Python, no Tk, no display needed.

Run: py -3 test_uv_presets.py
Everything is written under a throwaway temp folder; the real ~/.config,
home folder and /media are never touched (the helpers take explicit paths,
and the module's own constants are redirected as well).

The UVScope compatibility check loads an exported file with UVScope 1.1's
own core.py (UVParams.from_json, exactly what its "Load config" button
calls) and runs its processor with the result; it is skipped when the
UVScope source tree is not on this machine (e.g. on the Pi).
"""
import datetime
import importlib.util
import json
import math
import os
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

def patch_fsync(mod):
    """Track per-file fsync calls separately from the directory fsync
    (_uv_fsync_dir, S7), so fsync-count assertions are platform independent:
    on Linux _uv_write_json's fsync=True path ALSO fsyncs the directory
    entry, which would inflate a plain os.fsync count there but not on
    Windows (no os.O_DIRECTORY, so _uv_fsync_dir is already a no-op) --
    the tests here run on Windows but must hold on the Pi too. Stubbing
    out _uv_fsync_dir itself (rather than letting it run and go through
    the same os.fsync mock) keeps file_fsyncs counting only the per-file
    fsync regardless of platform, while dir_calls records whether the
    directory fsync was invoked at all. Returns (file_fsyncs, dir_calls,
    restore)."""
    real_fsync = mod.os.fsync
    real_dir_fsync = mod._uv_fsync_dir
    file_fsyncs = []
    dir_calls = []

    def fake_fsync(fd):
        file_fsyncs.append(1)
        return real_fsync(fd)

    def fake_dir_fsync(folder):
        dir_calls.append(folder)

    mod.os.fsync = fake_fsync
    mod._uv_fsync_dir = fake_dir_fsync

    def restore():
        mod.os.fsync = real_fsync
        mod._uv_fsync_dir = real_dir_fsync

    return file_fsyncs, dir_calls, restore


HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="test_uv_presets_"))
UVSCOPE_CORE = Path(r"C:\Users\weic\OneDrive - Innova Electronics Corp\Desktop"
                    r"\UVScope\UVScope1.1\uvscope\core.py")


def load_endoscope():
    if "fcntl" not in sys.modules:
        try:
            import fcntl  # noqa: F401
        except ImportError:
            fake = types.ModuleType("fcntl")
            fake.LOCK_EX, fake.LOCK_NB, fake.LOCK_UN = 2, 4, 8
            fake.flock = lambda *a, **k: None
            sys.modules["fcntl"] = fake
    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = object
    sys.modules.setdefault("serial", fake_serial)
    spec = importlib.util.spec_from_file_location("endoscope_uv_presets_test",
                                                  HERE / "endoscope.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CONFIG = str(TMP / "endoscope.json")
    mod.UV_PRESETS_FILE = str(TMP / "never_used_presets.json")
    mod.UV_MEDIA_ROOT = str(TMP / "never_used_media")
    return mod


def fresh(name):
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def entry(mod, pid, name, created, **params):
    return {"id": pid, "name": name, "created": created,
            "params": dict(mod.UV_FACTORY, box_bgr=[255, 0, 255], **params)}


# --------------------------------------------------------------- load/save
def test_load_missing_and_corrupt(mod):
    d = fresh("load")
    empty = {"version": 1, "active": "factory", "presets": []}
    assert mod.uv_presets_load(str(d / "missing.json")) == empty
    for i, text in enumerate(["", "{ not json", "[1, 2, 3]", "null", "42",
                              '{"presets": "nope", "active": 5}',
                              '{"presets": {"id": "x"}}', "\x00\xff"]):
        p = d / "bad{}.json".format(i)
        p.write_bytes(text.encode("latin-1"))
        assert mod.uv_presets_load(str(p)) == empty, text
    assert mod.uv_presets_load(str(d)) == empty            # a directory, not a file
    print("PASS: LOAD -- missing, empty, garbage, wrong types, a directory -> FACTORY only")


def test_load_partial_and_bad_entries(mod):
    d = fresh("partial")
    p = d / "p.json"
    good_old = entry(mod, "pA", "10:00 01-10", "2026-10-01T10:00:00", exposure=1.5)
    good_new = entry(mod, "pB", "11:00 02-10", "2026-10-02T11:00:00", gamma=2.0)
    raw = {"version": 1, "active": "pA", "presets": [
        good_old,
        {"id": "pC", "name": "partial", "created": "2026-09-01T00:00:00",
         "params": {"hue_min": 30}},                        # partial params: fine
        good_new,
        "not a dict", None, 7,
        {"id": "", "name": "no id", "params": {}},
        {"id": "factory", "name": "fake factory", "params": {}},
        {"id": "pA", "name": "duplicate id", "params": {}},
        {"id": "pD", "name": "   ", "params": {}},
        {"id": "pE", "name": "no params"},
        {"id": "pF", "name": "params list", "params": [1, 2]},
        {"id": 12, "name": "numeric id", "params": {}},
        {"id": "pG", "name": "odd created", "created": 12345, "params": {}},
    ]}
    p.write_text(json.dumps(raw), encoding="utf-8")
    data = mod.uv_presets_load(str(p))
    ids = [e["id"] for e in data["presets"]]
    # R4: file order kept, not re-sorted by "created" -- good_old (pA) comes
    # before good_new (pB) in the raw list even though pB's timestamp is
    # later, and the load must not swap them.
    assert ids == ["pA", "pC", "pB", "pG"], ids             # file order, junk dropped
    assert data["active"] == "pA"
    by = {e["id"]: e for e in data["presets"]}
    assert by["pA"]["params"]["exposure"] == 1.5
    assert by["pC"]["params"]["hue_min"] == 30
    assert by["pC"]["params"]["sat_min"] == mod.UV_FACTORY["sat_min"]   # filled from factory
    assert set(by["pC"]["params"]) == set(mod.UV_FACTORY)
    assert by["pG"]["created"] == ""
    assert isinstance(by["pA"]["params"]["box_bgr"], list)
    # Unknown active id -> FACTORY.
    raw["active"] = "gone"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert mod.uv_presets_load(str(p))["active"] == "factory"
    print("PASS: PARTIAL -- bad entries dropped one by one, partial params filled from "
          "factory, file order kept, unknown active -> FACTORY")


def test_load_keeps_order_with_backwards_clock(mod):
    """R4: an offline Pi's clock can jump backwards between SAVEs, so a
    preset saved later can carry an earlier "created" timestamp than one
    saved before it. uv_presets_load must not use "created" to reorder --
    SAVE already writes newest-first, so the file order IS the right order,
    clock or no clock."""
    d = fresh("clock")
    p = d / "presets.json"
    # Saved in this order (newest-first, as SAVE writes): "newer" first, then
    # "older" -- but the clock jumped back, so "newer"'s own timestamp is
    # EARLIER than "older"'s. A sort-by-created would swap them back.
    raw = {"version": 1, "active": "newer", "presets": [
        entry(mod, "newer", "09:00 07-10", "2026-10-07T09:00:00", exposure=1.9),
        entry(mod, "older", "10:00 07-10", "2026-10-07T10:00:00", exposure=1.1)]}
    p.write_text(json.dumps(raw), encoding="utf-8")
    data = mod.uv_presets_load(str(p))
    assert [e["id"] for e in data["presets"]] == ["newer", "older"]
    assert data["presets"][0]["params"]["exposure"] == 1.9
    # A round trip through uv_presets_save (which does not sort either)
    # keeps that same order on disk and on the next load.
    assert mod.uv_presets_save(str(p), data)
    assert [e["id"] for e in mod.uv_presets_load(str(p))["presets"]] == ["newer", "older"]
    print("PASS: BACKWARDS CLOCK -- file order survives a load/save round trip "
          "even when 'created' timestamps are out of order")


def test_load_nan_and_ranges(mod):
    d = fresh("nan")
    p = d / "nan.json"
    # json.dump writes NaN / Infinity literals, which json.load accepts back.
    raw = {"version": 1, "active": "pN", "presets": [
        {"id": "pN", "name": "nan", "created": "2026-10-07T00:00:00",
         "params": {"sensitivity": float("nan"), "exposure": float("inf"),
                    "gamma": -5, "hue_max": 999, "uv_reject": "off",
                    "box_bgr": [300, -4, "x"], "merge_px": "12"}}]}
    p.write_text(json.dumps(raw), encoding="utf-8")
    params = mod.uv_presets_load(str(p))["presets"][0]["params"]
    assert params["sensitivity"] == mod.UV_FACTORY["sensitivity"]
    assert params["exposure"] == mod.UV_FACTORY["exposure"]
    assert params["gamma"] == mod.UV_UI_RANGES["gamma"][0]
    assert params["hue_max"] == 179 and params["uv_reject"] is False
    assert params["box_bgr"] == [255, 0, 255]
    assert params["merge_px"] == 12
    assert all(not isinstance(v, float) or math.isfinite(v) for v in params.values())
    # And it saves back as strict JSON (no NaN literal).
    out = d / "out.json"
    assert mod.uv_presets_save(str(out), mod.uv_presets_load(str(p)))
    json.loads(out.read_text(encoding="utf-8"),
               parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    print("PASS: NAN -- NaN/inf/out-of-range/bad types in a preset come back factory or clamped, "
          "and are saved back as strict JSON")


def test_save_roundtrip_and_atomic(mod):
    d = fresh("save")
    p = d / "sub" / "dir" / "presets.json"                    # makedirs
    data = {"version": 1, "active": "pB", "presets": [
        entry(mod, "pB", "11:00 02-10", "2026-10-02T11:00:00", warmth=0.3),
        entry(mod, "pA", "10:00 01-10", "2026-10-01T10:00:00"),
        entry(mod, "factory", "FACTORY", "", exposure=2.0)]}  # never stored
    # uv_presets_save (unlike uv_export, R1) still fsyncs: _uv_write_json's
    # fsync=True default is untouched for every caller except uv_export.
    # (The per-file fsync is counted separately from the directory fsync --
    # S7 -- so this holds on Linux, where a durable write also fsyncs the
    # directory entry, not just on Windows where that part is a no-op.)
    fsyncs, dir_calls, restore_fsync = patch_fsync(mod)
    try:
        assert mod.uv_presets_save(str(p), data) is True
    finally:
        restore_fsync()
    assert fsyncs == [1], fsyncs
    assert len(dir_calls) == 1, dir_calls
    stored = json.loads(p.read_text(encoding="utf-8"))
    assert stored["version"] == 1 and stored["active"] == "pB"
    assert [e["id"] for e in stored["presets"]] == ["pB", "pA"]
    back = mod.uv_presets_load(str(p))
    assert back["active"] == "pB" and back["presets"][0]["params"]["warmth"] == 0.3
    assert mod.uv_presets_load(str(p)) == back
    assert sorted(os.listdir(p.parent)) == ["presets.json"], "temp file left behind"
    # Active id that is not saved -> FACTORY on disk.
    assert mod.uv_presets_save(str(p), {"active": "nope", "presets": data["presets"][:1]})
    assert json.loads(p.read_text(encoding="utf-8"))["active"] == "factory"
    # A failure mid-write (the rename) leaves the old file intact, no temp file,
    # and returns False instead of raising.
    before = p.read_bytes()
    real_replace = mod.os.replace

    def broken_replace(*a, **k):
        raise OSError("power cut")
    mod.os.replace = broken_replace
    try:
        assert mod.uv_presets_save(str(p), data) is False
    finally:
        mod.os.replace = real_replace
    assert p.read_bytes() == before
    assert sorted(os.listdir(p.parent)) == ["presets.json"]
    # Unwritable target (a directory in the way) -> False, no exception.
    blocker = d / "blocked.json"
    blocker.mkdir(exist_ok=True)
    assert mod.uv_presets_save(str(blocker), data) is False
    # Garbage data -> False, no exception.
    assert mod.uv_presets_save(str(d / "x.json"), {"presets": [{"no": "id"}]}) is False
    assert mod.uv_presets_save(str(d / "y.json"), None) is False
    print("PASS: SAVE -- round trip, makedirs, FACTORY never stored, invalid active -> factory, "
          "atomic (failed rename keeps the old file, no temp left), failures return False")


# ------------------------------------------- second-round fixes (item 7d)
def test_load_unhashable_active(mod):
    """S1: "active" is checked for its type before the `in seen` membership
    test. A set membership test on an unhashable value (a list or a dict --
    valid JSON, just not a valid preset id) used to raise TypeError there,
    which crashed App.__init__ before this fix even though every preset
    entry itself was perfectly fine and every OTHER guard here already
    passed."""
    d = fresh("active_type")
    good = entry(mod, "pA", "good", "2026-10-07T00:00:00")
    for bad_active in ([], {}, {"x": 1}, 5, 5.5, True, None):
        p = d / "p.json"
        p.write_text(json.dumps({"version": 1, "active": bad_active,
                                 "presets": [good]}), encoding="utf-8")
        data = mod.uv_presets_load(str(p))                  # must not raise
        assert data["active"] == "factory", (bad_active, data)
        assert [e["id"] for e in data["presets"]] == ["pA"], (
            "an unrelated bad 'active' must not drop the good presets")
    print("PASS: LOAD UNHASHABLE ACTIVE -- a list/dict/other non-string 'active' "
          "never raises and falls back to FACTORY without losing the saved presets")


def test_activate_skips_fsync(mod):
    """S2: uv_presets_save(..., fsync=False) (what _uv_activate calls, since
    activating a preset only changes which id is "active" over the same
    already-saved presets, and happens on every tap of the list) really
    does skip the per-file fsync, while the default (what SAVE/DELETE use)
    still fsyncs -- unchanged from R1/test_save_roundtrip_and_atomic."""
    d = fresh("activate_fsync")
    p = d / "presets.json"
    data = {"version": 1, "active": "pA", "presets": [
        entry(mod, "pA", "a", "2026-10-07T00:00:00")]}
    fsyncs, dir_calls, restore_fsync = patch_fsync(mod)
    try:
        assert mod.uv_presets_save(str(p), data, fsync=False) is True
        assert fsyncs == [], "preset ACTIVATE must not fsync (S2)"
        assert dir_calls == [], "preset ACTIVATE must not fsync the directory either (S2)"
        assert mod.uv_presets_save(str(p), data) is True        # default
        assert fsyncs == [1], "SAVE/DELETE must still fsync"
        assert len(dir_calls) == 1, "SAVE/DELETE must still fsync the directory (S7)"
    finally:
        restore_fsync()
    assert json.loads(p.read_text(encoding="utf-8"))["active"] == "pA"
    print("PASS: ACTIVATE SKIPS FSYNC -- fsync=False really does skip the per-file fsync, "
          "the fsync=True default (SAVE/DELETE) is unchanged")


def test_atomic_write_fallback(mod):
    """S6: when the temp file itself cannot be created because of a
    PERMISSION problem (a sticky/read-only directory that still allows
    overwriting an existing file -- exactly what 6.1.0's own direct,
    non-atomic write always tolerated), the write must not be lost:
    _uv_write_json falls back to writing the target in place. A failure
    anywhere else (the write itself, the rename, or a temp-file-create
    failure that is NOT a permission problem -- e.g. a full disk) is a
    separate matter and must NOT take this fallback -- it would turn a
    real failure (or, on a full disk, truncate the existing good file)
    into a silent, non-atomic overwrite."""
    import builtins
    import errno as errno_mod
    d = fresh("fallback")
    target = d / "cfg.json"
    target.write_text(json.dumps({"old": True}), encoding="utf-8")
    real_open = builtins.open

    def make_flaky_open(exc):
        def flaky_open(file, mode="r", *a, **kw):
            if "w" in mode and ".tmp" in os.path.basename(str(file)):
                raise exc
            return real_open(file, mode, *a, **kw)
        return flaky_open

    # EACCES -> permission problem -> falls back to an in-place write.
    builtins.open = make_flaky_open(PermissionError(errno_mod.EACCES, "permission denied"))
    try:
        mod._uv_write_json(str(target), {"new": True})      # must not raise
    finally:
        builtins.open = real_open
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert sorted(p.name for p in d.iterdir()) == ["cfg.json"], (
        "no temp file left behind by the fallback")

    # EPERM (a plain OSError, not necessarily a PermissionError subclass on
    # every platform) -> also a permission problem -> also falls back.
    builtins.open = make_flaky_open(OSError(errno_mod.EPERM, "operation not permitted"))
    try:
        mod._uv_write_json(str(target), {"newer": True})
    finally:
        builtins.open = real_open
    assert json.loads(target.read_text(encoding="utf-8")) == {"newer": True}

    # ENOSPC (full disk) -> NOT a permission problem -> must propagate,
    # and the original good file must be left completely untouched.
    before = target.read_bytes()
    builtins.open = make_flaky_open(OSError(errno_mod.ENOSPC, "no space left on device"))
    try:
        try:
            mod._uv_write_json(str(target), {"lost": True})
            raised = False
        except OSError:
            raised = True
    finally:
        builtins.open = real_open
    assert raised, "a full disk must propagate, not silently fall back"
    assert target.read_bytes() == before, "original file must be untouched on ENOSPC"
    # And the caller (uv_presets_save) reports this as a failure, not a
    # crash and not a false success.
    assert mod.uv_presets_save(str(target), {"active": "x", "presets": []}) is True
    builtins.open = make_flaky_open(OSError(errno_mod.ENOSPC, "no space left on device"))
    try:
        ok = mod.uv_presets_save(str(target), {"active": "x", "presets": []})
    finally:
        builtins.open = real_open
    assert ok is False, "ENOSPC creating the temp file must report failure"
    print("PASS: ATOMIC WRITE FALLBACK -- a PERMISSION problem creating the temp file "
          "(EACCES/EPERM) falls back to a direct write with no stray temp file, while a "
          "non-permission failure (ENOSPC) propagates/reports failure and leaves the "
          "existing good file untouched")


def test_dir_fsync_after_replace(mod):
    """S7: a successful fsync=True write also fsyncs the directory entry
    after the rename (POSIX only -- Windows, where this test runs, has no
    os.O_DIRECTORY, so the real call is a no-op there; os.O_DIRECTORY is
    faked in just for this test to exercise the POSIX branch the way a
    Linux box runs it). An error meaning "fsyncing a directory just isn't
    a thing here" (EINVAL and friends) must never turn an already-
    successful write into a reported one -- but a real I/O error (EIO)
    is not that, and must propagate so EXPORT/SAVE report failure."""
    d = fresh("dirfsync")
    p = d / "x.json"
    had_flag = hasattr(mod.os, "O_DIRECTORY")
    if not had_flag:
        mod.os.O_DIRECTORY = 0x10000            # Linux's actual value; unused as a number here
    real_open, real_fsync, real_close = mod.os.open, mod.os.fsync, mod.os.close
    dir_opens, dir_fsyncs = [], []

    def fake_open(path, flags):
        if flags == mod.os.O_DIRECTORY:
            dir_opens.append(path)
            return -99
        return real_open(path, flags)

    def fake_fsync(fd):
        if fd == -99:
            dir_fsyncs.append(fd)
            return
        real_fsync(fd)

    def fake_close(fd):
        if fd != -99:
            real_close(fd)
    mod.os.open, mod.os.fsync, mod.os.close = fake_open, fake_fsync, fake_close
    try:
        mod._uv_write_json(str(p), {"a": 1})
        assert dir_opens == [str(d)], dir_opens
        assert dir_fsyncs == [-99]
        # fsync=False must not touch the directory at all.
        dir_opens.clear()
        mod._uv_write_json(str(p), {"b": 2}, fsync=False)
        assert dir_opens == []
        # An "unsupported here" failure fsyncing the directory is swallowed
        # -- the file itself already landed, so this must not be reported
        # as a failed write.
        import errno as errno_mod

        def make_failing_open(exc):
            def failing_open(path, flags):
                if flags == mod.os.O_DIRECTORY:
                    raise exc
                return real_open(path, flags)
            return failing_open
        mod.os.open = make_failing_open(OSError(errno_mod.EINVAL, "fsync not supported here"))
        mod._uv_write_json(str(p), {"c": 3})              # must not raise
        assert json.loads(p.read_text(encoding="utf-8")) == {"c": 3}
        # But a REAL I/O error fsyncing the directory is not "unsupported"
        # and must propagate.
        mod.os.open = make_failing_open(OSError(errno_mod.EIO, "I/O error"))
        try:
            mod._uv_write_json(str(p), {"d": 4})
            raised = False
        except OSError:
            raised = True
        assert raised, "a real I/O error fsyncing the directory must propagate"
    finally:
        mod.os.open, mod.os.fsync, mod.os.close = real_open, real_fsync, real_close
        if not had_flag:
            del mod.os.O_DIRECTORY
    print("PASS: DIR FSYNC -- the directory entry is fsynced after a successful fsync=True "
          "rename (skipped for fsync=False), and a failure doing so never turns an "
          "already-written file into a reported failure")


# --------------------------------------------------------------- naming
def test_naming(mod):
    t = datetime.datetime(2026, 10, 7, 14, 32, 59)
    assert mod.uv_preset_name(t, []) == "14:32 07-10"
    assert mod.uv_preset_name(t, ["FACTORY", "14:31 07-10"]) == "14:32 07-10"
    assert mod.uv_preset_name(t, ["14:32 07-10"]) == "14:32 07-10 (2)"
    assert mod.uv_preset_name(t, ["14:32 07-10", "14:32 07-10 (2)"]) == "14:32 07-10 (3)"
    assert mod.uv_preset_name(t, ["14:32 07-10", "14:32 07-10 (3)"]) == "14:32 07-10 (2)"
    assert mod.uv_preset_name(datetime.datetime(2026, 1, 5, 9, 5), []) == "09:05 05-01"
    assert mod.uv_preset_name(datetime.datetime(2026, 12, 31, 0, 0), []) == "00:00 31-12"
    assert mod.uv_preset_name(datetime.datetime(2026, 3, 9, 23, 7), []) == "23:07 09-03"
    # Epoch numbers work too (local time).
    epoch = datetime.datetime(2026, 10, 7, 8, 1).timestamp()
    assert mod.uv_preset_name(epoch, []) == "08:01 07-10"
    ids = []
    for _ in range(4):
        ids.append(mod.uv_preset_id(t, ids))
    assert len(set(ids)) == 4 and "factory" not in ids
    print("PASS: NAMING -- 'HH:MM DD-MM' 24 h zero padded, duplicates (2)/(3) first free, "
          "unique ids")


# --------------------------------------------------------------- export dir
def test_export_dir(mod):
    """R2: three search tiers, in priority order -- /media/<user>/* (the
    normal case), then /media/*/* (the app can run as a different effective
    user than whoever's folder the stick actually mounted under -- root via
    systemd, or a desktop test run as someone else), then a bare
    /media/<label> some distros use instead. Unmounted, read-only and plain
    file candidates are skipped at every tier; nothing anywhere -> home."""
    d = fresh("media")
    home = d / "home" / "pi"
    home.mkdir(parents=True, exist_ok=True)
    media = d / "media"
    pi_dir, bob_dir = media / "pi", media / "bob"
    for sub in ("AAA_not_mounted", "BBB_readonly", "CCC_stick"):
        (pi_dir / sub).mkdir(parents=True, exist_ok=True)
    (bob_dir / "DDD_stick").mkdir(parents=True, exist_ok=True)
    (media / "ZZZ_bare").mkdir(parents=True, exist_ok=True)
    (pi_dir / "a_file").write_text("x")
    readonly = {str(pi_dir / "BBB_readonly")}
    real_ismount, real_access = mod.os.path.ismount, mod.os.access
    mounts = set()

    def fake_ismount(p):
        return str(p) in mounts

    def fake_access(p, mode):
        if str(p) in readonly and mode & os.W_OK:
            return False
        return real_access(p, mode)
    mod.os.path.ismount, mod.os.access = fake_ismount, fake_access
    try:
        # Tier 1 (/media/<user>/*) wins even with valid tier-2/3 candidates
        # mounted at the same time.
        mounts |= {str(pi_dir / "BBB_readonly"), str(pi_dir / "CCC_stick"),
                  str(bob_dir / "DDD_stick"), str(media / "ZZZ_bare")}
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media), user="pi")
        assert got == (str(pi_dir / "CCC_stick" / "Endoscope_UV_presets"), True), got
        # Tier 2 (/media/*/*): nothing under /media/pi now, but the stick
        # under another account's folder (bob) is still found, ahead of the
        # bare tier-3 candidate that is ALSO mounted right now.
        mounts.clear()
        mounts |= {str(bob_dir / "DDD_stick"), str(media / "ZZZ_bare")}
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media), user="pi")
        assert got == (str(bob_dir / "DDD_stick" / "Endoscope_UV_presets"), True), got
        # Same again when the requesting user's own /media folder does not
        # exist at all (e.g. the app running as root, or as a user who has
        # never had a stick mounted).
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media), user="nouser")
        assert got == (str(bob_dir / "DDD_stick" / "Endoscope_UV_presets"), True), got
        # Tier 3 (bare /media/<label>): only the flat mount is left.
        mounts.clear()
        mounts.add(str(media / "ZZZ_bare"))
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media), user="pi")
        assert got == (str(media / "ZZZ_bare" / "Endoscope_UV_presets"), True), got
        # Unmounted, read-only and a plain file are skipped at every tier.
        mounts.clear()
        mounts |= {str(pi_dir / "BBB_readonly"), str(pi_dir / "a_file")}
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media), user="pi")
        assert got == (str(home / "Endoscope_UV_presets"), False), got
        # Nothing mounted anywhere, and no /media at all.
        mounts.clear()
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media), user="pi")
        assert got == (str(home / "Endoscope_UV_presets"), False), got
        got = mod.uv_find_export_dir(home=str(home), media_root=str(d / "nope"), user="pi")
        assert got == (str(home / "Endoscope_UV_presets"), False)
        # The user name defaults to $USER / the home folder's name, and (via
        # tier 2) a stick is still found even if that default is not "pi".
        mounts.add(str(pi_dir / "CCC_stick"))
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media))
        assert got[1] is True, got
    finally:
        mod.os.path.ismount, mod.os.access = real_ismount, real_access
    print("PASS: EXPORT DIR -- /media/<user>/* first, then /media/*/* (another "
          "account's folder), then a bare /media/<label>; unmounted/read-only/"
          "plain files skipped at every tier; else ~/Endoscope_UV_presets")


# --------------------------------------------------------------- export files
def test_export_files(mod):
    d = fresh("export") / "Endoscope_UV_presets"
    t = datetime.datetime(2026, 10, 7, 14, 32, 5)
    presets = [{"name": "FACTORY", "params": mod.UVParams()},
               {"name": "14:32 07-10", "params": dict(mod.UV_FACTORY, exposure=1.5,
                                                      box_bgr=[0, 255, 0])},
               {"name": "14:32 07-10 (2)", "params": {"hue_min": 30}},
               {"name": "a b", "params": {}}, {"name": "a_b", "params": {}},
               {"name": "../../etc/passwd", "params": {}}, {"name": "", "params": {}}]
    toggles = {"boost": False, "boxes": True, "filter": True}
    calls = []
    had_sync = hasattr(mod.os, "sync")
    real_sync = getattr(mod.os, "sync", None)
    mod.os.sync = lambda: calls.append(1)
    # S8: EXPORT runs off the Tk thread (R1 moved it to a worker thread, not
    # by skipping fsyncs), so each file IS fsynced -- a write error on a
    # failing/half-pulled stick must surface as EXPORT FAILED, not a false
    # success papered over until the trailing os.sync() (checked above, USB
    # only), which is now just an optional final flush on top of that.
    fsyncs, dir_calls, restore_fsync = patch_fsync(mod)
    try:
        paths = mod.uv_export(str(d), presets, toggles, t, sync=True)
        assert calls == [1]
        assert fsyncs == [1] * len(paths), (
            "uv_export must fsync every file (S8)", fsyncs, len(paths))
        assert len(dir_calls) == len(paths), (
            "uv_export must fsync the directory for every file too (S7)", dir_calls)
        fsyncs.clear()
        dir_calls.clear()
        paths2 = mod.uv_export(str(d / "nosync"), presets[:1], toggles, t)
        assert calls == [1]
        assert fsyncs == [1] * len(paths2)
        assert len(dir_calls) == len(paths2)
    finally:
        if had_sync:
            mod.os.sync = real_sync
        else:
            del mod.os.sync
        restore_fsync()
    names = [Path(p).name for p in paths]
    assert names == ["uv_params_20261007-1432_FACTORY.json",
                     "uv_params_20261007-1432_14_32_07-10.json",
                     "uv_params_20261007-1432_14_32_07-10_2.json",
                     "uv_params_20261007-1432_a_b.json",
                     "uv_params_20261007-1432_a_b_2.json",
                     "uv_params_20261007-1432_etc_passwd.json",
                     "uv_params_20261007-1432_preset.json",
                     "uv_presets_all_20261007-143205.json"], names
    assert all(Path(p).parent == d for p in paths)          # nothing escapes dest_dir
    fields = set(mod.UVParams().to_dict())
    for p, preset in zip(paths, presets):
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        assert set(data) == fields | {"preset_name", "ref_short_side", "exported_by"}
        assert data["preset_name"] == preset["name"]
        assert data["ref_short_side"] == 360 and data["exported_by"] == "Endoscope 6.2.0"
        assert (data["filter_enabled"], data["boost_enabled"], data["draw_boxes"],
                data["detect_enabled"]) == (True, False, True, True)
        assert isinstance(data["box_bgr"], list) and len(data["box_bgr"]) == 3
    second = json.loads(Path(paths[1]).read_text(encoding="utf-8"))
    assert second["exposure"] == 1.5 and second["box_bgr"] == [0, 255, 0]
    third = json.loads(Path(paths[2]).read_text(encoding="utf-8"))
    assert third["hue_min"] == 30 and third["sat_min"] == mod.UV_FACTORY["sat_min"]
    bundle = json.loads(Path(paths[-1]).read_text(encoding="utf-8"))
    assert [b["preset_name"] for b in bundle["presets"]] == [p["name"] for p in presets]
    assert bundle["toggles"] == toggles and bundle["exported_by"] == "Endoscope 6.2.0"
    assert bundle["exported_at"] == "2026-10-07T14:32:05"
    # Detection runs whenever boost OR boxes needs it.
    p2 = mod.uv_export(str(d / "off"), presets[:1], {"boost": False, "boxes": False,
                                                       "filter": False}, t)
    assert json.loads(Path(p2[0]).read_text(encoding="utf-8"))["detect_enabled"] is False
    # An unwritable destination raises (the App turns it into a toast).
    blocker = fresh("export_blocked") / "file"
    blocker.write_text("x")
    try:
        mod.uv_export(str(blocker), presets, toggles, t)
    except OSError:
        pass
    else:
        raise AssertionError("export into a file path should fail")
    print("PASS: EXPORT FILES -- one uv_params_<YYYYMMDD-HHMM>_<safe name>.json per preset "
          "(safe, de-duplicated names), full field set + toggles + preset_name/ref_short_side/"
          "exported_by, bundle, os.sync only for USB, failure raises")
    return paths


def test_uvscope_loads_export(mod, paths):
    if not UVSCOPE_CORE.exists():
        print("SKIPPED: UVSCOPE COMPAT ({} not found)".format(UVSCOPE_CORE))
        return
    spec = importlib.util.spec_from_file_location("uvscope_core_for_test", UVSCOPE_CORE)
    core = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = core          # dataclasses look their module up here
    spec.loader.exec_module(core)
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[100:200, 200:320] = (40, 230, 180)                 # a bright yellow-green patch
    for p in paths[:3]:
        exported = json.loads(Path(p).read_text(encoding="utf-8"))
        theirs = core.UVParams.from_json(p)                  # UVScope's "Load config"
        for name in mod.UVParams().to_dict():
            mine = exported[name]
            got = getattr(theirs, name)
            if name == "box_bgr":
                assert tuple(got) == tuple(mine), name
            else:
                assert got == mine and type(got) is type(mine), (name, got, mine)
        assert not hasattr(theirs, "preset_name")            # extra keys ignored
        out = core.UVProcessor(theirs).process(frame)[0]
        assert out.shape == frame.shape
    print("PASS: UVSCOPE COMPAT -- exported files load in UVScope 1.1's own UVParams.from_json "
          "with every field identical, and its processor runs on them")


def main():
    mod = load_endoscope()
    test_load_missing_and_corrupt(mod)
    test_load_partial_and_bad_entries(mod)
    test_load_keeps_order_with_backwards_clock(mod)
    test_load_nan_and_ranges(mod)
    test_save_roundtrip_and_atomic(mod)
    test_load_unhashable_active(mod)
    test_activate_skips_fsync(mod)
    test_atomic_write_fallback(mod)
    test_dir_fsync_after_replace(mod)
    test_naming(mod)
    test_export_dir(mod)
    paths = test_export_files(mod)
    test_uvscope_loads_export(mod, paths)
    print("DONE: test_uv_presets.py")


if __name__ == "__main__":
    main()
