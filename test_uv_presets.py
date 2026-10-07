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
    assert ids == ["pB", "pA", "pC", "pG"], ids             # newest first, junk dropped
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
          "factory, newest first, unknown active -> FACTORY")


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
    assert mod.uv_presets_save(str(p), data) is True
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
    d = fresh("media")
    home = d / "home" / "pi"
    home.mkdir(parents=True, exist_ok=True)
    media = d / "media"
    user = media / "pi"
    for sub in ("AAA_not_mounted", "BBB_readonly", "CCC_stick"):
        (user / sub).mkdir(parents=True, exist_ok=True)
    (media / "ZZZ_bare").mkdir(parents=True, exist_ok=True)
    (user / "a_file").write_text("x")
    mounts = {str(user / "BBB_readonly"), str(user / "CCC_stick"), str(media / "ZZZ_bare"),
              str(user / "a_file")}
    readonly = {str(user / "BBB_readonly")}
    real_ismount, real_access = mod.os.path.ismount, mod.os.access

    def fake_ismount(p):
        return str(p) in mounts

    def fake_access(p, mode):
        if str(p) in readonly and mode & os.W_OK:
            return False
        return real_access(p, mode)
    mod.os.path.ismount, mod.os.access = fake_ismount, fake_access
    try:
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media), user="pi")
        assert got == (str(user / "CCC_stick" / "Endoscope_UV_presets"), True), got
        mounts.discard(str(user / "CCC_stick"))
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media), user="pi")
        assert got == (str(media / "ZZZ_bare" / "Endoscope_UV_presets"), True), got
        mounts.discard(str(media / "ZZZ_bare"))
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media), user="pi")
        assert got == (str(home / "Endoscope_UV_presets"), False), got
        # No /media at all, and an unknown user.
        got = mod.uv_find_export_dir(home=str(home), media_root=str(d / "nope"), user="pi")
        assert got == (str(home / "Endoscope_UV_presets"), False)
        mounts.add(str(user / "CCC_stick"))
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media), user="bob")
        assert got == (str(home / "Endoscope_UV_presets"), False), got
        # The user name defaults to $USER / the home folder's name.
        got = mod.uv_find_export_dir(home=str(home), media_root=str(media))
        if os.environ.get("USER", os.environ.get("LOGNAME", "pi")) == "pi":
            assert got[1] is True
    finally:
        mod.os.path.ismount, mod.os.access = real_ismount, real_access
    print("PASS: EXPORT DIR -- first writable mounted stick under /media/<user>, then /media; "
          "unmounted, read-only and plain files skipped; else ~/Endoscope_UV_presets")


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
    try:
        paths = mod.uv_export(str(d), presets, toggles, t, sync=True)
        assert calls == [1]
        mod.uv_export(str(d / "nosync"), presets[:1], toggles, t)
        assert calls == [1]
    finally:
        if had_sync:
            mod.os.sync = real_sync
        else:
            del mod.os.sync
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
    test_load_nan_and_ranges(mod)
    test_save_roundtrip_and_atomic(mod)
    test_naming(mod)
    test_export_dir(mod)
    paths = test_export_files(mod)
    test_uvscope_loads_export(mod, paths)
    print("DONE: test_uv_presets.py")


if __name__ == "__main__":
    main()
