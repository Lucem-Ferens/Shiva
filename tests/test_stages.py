"""Stage recording: what each run passes through, and proof that recording changes nothing."""
import ast
import hashlib
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

from shiva import agecli, clean_pipeline, folder, heldfile, output, pipeline, stages, steps, storage
from shiva.metadata import CleanCancelled
from shiva.stages import CLEAN, ENCRYPT, SHRED, VERIFY, planned
from shiva.steps import StepOptions, run_steps

SRC = Path(__file__).resolve().parents[1] / "src" / "shiva"
PW = "correct horse battery staple x"
needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")


def _jpeg(path):
    img = Image.new("RGB", (16, 16), (1, 2, 3))
    exif = Image.Exif(); exif[0x010F] = "TestCam"
    img.save(path, "JPEG", exif=exif.tobytes())


def _plain(d, name="plain.bin"):
    f = d / name; f.write_bytes(b"P" * 3000)
    return f


def _fold(d):
    fo = d / "fold"; fo.mkdir()
    (fo / "a.txt").write_bytes(b"A" * 2000); (fo / "b.txt").write_bytes(b"B" * 2000)
    return fo


def _run(p, **kw):
    cancel = kw.pop("cancel", None)
    r = run_steps([p], PW, StepOptions(**kw), cancel=cancel)[0]
    assert [n for n, _ in r.stages] == list(planned(p, StepOptions(**kw))), r.stages
    return r


def _st(r):
    return dict(r.stages)


# ---------------------------------------------------------------- success paths

@needs_age
def test_success_paths_record_planned_stages(tmp_path):
    f = _plain(tmp_path)
    assert _run(f, encrypt=True).stages == ((ENCRYPT, "done"), (VERIFY, "done"))
    assert f.exists()
    g = _plain(tmp_path, "g.bin")
    assert _run(g, encrypt=True, shred=True).stages == (
        (ENCRYPT, "done"), (VERIFY, "done"), (SHRED, "attempted")) and not g.exists()
    j = tmp_path / "a.jpg"; _jpeg(j)
    assert _run(j, clean=True, encrypt=True).stages == (
        (CLEAN, "done"), (ENCRYPT, "done"), (VERIFY, "done"))
    j2 = tmp_path / "b.jpg"; _jpeg(j2)
    assert _run(j2, clean=True, encrypt=True, shred=True, confirmed_changes={str(j2): list(clean_pipeline.JPEG_CHANGES)}).stages == (
        (CLEAN, "done"), (ENCRYPT, "done"), (VERIFY, "done"), (SHRED, "attempted"))
    fo = _fold(tmp_path)
    assert _run(fo, encrypt=True).stages == ((ENCRYPT, "done"), (VERIFY, "done"))


@needs_age
def test_folder_shred_stages(tmp_path):
    fo = _fold(tmp_path)
    assert _run(fo, encrypt=True, shred=True).stages == (
        (ENCRYPT, "done"), (VERIFY, "done"), (SHRED, "attempted"))


def test_clean_only_and_shred_only_and_decrypt_have_no_verify(tmp_path):
    j = tmp_path / "a.jpg"; _jpeg(j)
    r = _run(j, clean=True, encrypt=False)
    assert r.stages == ((CLEAN, "done"),)
    f = _plain(tmp_path)
    r = _run(f, clean=False, encrypt=False, shred=True)
    assert r.stages == ((SHRED, "attempted"),) and not f.exists()
    assert all(n != VERIFY for n, _ in r.stages)


@needs_age
def test_age_decrypt_records_nothing(tmp_path):
    f = _plain(tmp_path)
    run_steps([f], PW, StepOptions(encrypt=True))
    f.unlink()
    r = run_steps([tmp_path / "plain.bin.age"], PW, StepOptions())[0]
    assert r.ok and r.stages == () and planned(tmp_path / "x.age", StepOptions()) == ()


# ---------------------------------------------------------------- early exits keep planned names

def test_early_returns_and_cancel_before_start_list_every_planned_stage_not_started(tmp_path):
    f = _plain(tmp_path)
    ev = threading.Event(); ev.set()
    r = _run(f, encrypt=True, shred=True, cancel=ev)
    assert r.cancelled and set(_st(r).values()) == {"not_started"}
    t = tmp_path / "n.txt"; t.write_text("x")
    r = _run(t, clean=True, encrypt=False)                            # unsupported format
    assert not r.ok and _st(r) == {CLEAN: "not_started"}
    r = _run(f, clean=True, encrypt=False, shred=True)                # refused combination
    assert not r.ok and set(_st(r).values()) == {"not_started"}
    d = tmp_path / "d"; d.mkdir()
    r = _run(d, clean=True, encrypt=False)                            # folders cannot be cleaned
    assert not r.ok and _st(r) == {CLEAN: "not_started"}
    r = _run(f, clean=False, encrypt=False, shred=False)
    assert r.stages == ()
    a = tmp_path / "z.age"; a.write_bytes(b"x")
    r = _run(a, decrypt_age=False, encrypt=True)
    assert not r.ok and r.stages == ()


@needs_age
def test_failure_before_the_first_stage_leaves_all_not_started(tmp_path, monkeypatch):
    f = _plain(tmp_path)

    def refuse(*a, **k):
        raise agecli.AgeError("another program has this file open", "in_use")
    monkeypatch.setattr(pipeline, "hold_for_shredding", refuse)
    r = _run(f, encrypt=True, shred=True)
    assert not r.ok and set(_st(r).values()) == {"not_started"}


# ---------------------------------------------------------------- failures, cancel and changed files

@needs_age
def test_trial_decrypt_mismatch(tmp_path, monkeypatch):
    f = _plain(tmp_path); before = f.read_bytes()
    monkeypatch.setattr(agecli, "decrypt_hash_passphrase", lambda *a, **k: "0" * 64)
    r = _run(f, encrypt=True, shred=True)
    assert not r.ok and r.error_code == "verify_failed"
    assert _st(r) == {ENCRYPT: "done", VERIFY: "failed", SHRED: "not_started"}
    assert f.read_bytes() == before and [p.name for p in tmp_path.iterdir()] == ["plain.bin"]


@needs_age
def test_internal_hash_mismatch_fails_encrypt(tmp_path, monkeypatch):
    j = tmp_path / "a.jpg"; _jpeg(j)
    monkeypatch.setattr(agecli, "encrypt_stream", lambda *a, **k: "0" * 64)
    r = _run(j, clean=True, encrypt=True)
    assert not r.ok and _st(r) == {CLEAN: "done", ENCRYPT: "failed", VERIFY: "not_started"}


@needs_age
def test_cancel_during_each_stage(tmp_path, monkeypatch):
    j = tmp_path / "a.jpg"; _jpeg(j); before = j.read_bytes()
    ev = threading.Event(); conf = {str(j): list(clean_pipeline.JPEG_CHANGES)}

    def clean_cancel(*a, **k):
        raise CleanCancelled("Cancelled")
    with monkeypatch.context() as m:
        m.setattr(clean_pipeline.runner, "clean_bytes", clean_cancel)
        r = _run(j, clean=True, encrypt=True, shred=True, cancel=ev, confirmed_changes=conf)
    assert r.cancelled and _st(r) == {CLEAN: "cancelled", ENCRYPT: "not_started", VERIFY: "not_started",
                                      SHRED: "not_started"}

    def stop(*a, **k):
        raise agecli.Cancelled("Cancelled")
    with monkeypatch.context() as m:
        m.setattr(agecli, "encrypt_stream", stop)
        r = _run(j, clean=True, encrypt=True, shred=True, cancel=ev, confirmed_changes=conf)
    assert r.cancelled and _st(r) == {CLEAN: "done", ENCRYPT: "cancelled", VERIFY: "not_started",
                                      SHRED: "not_started"}
    with monkeypatch.context() as m:
        m.setattr(agecli, "decrypt_hash_passphrase", stop)
        r = _run(j, clean=True, encrypt=True, shred=True, cancel=ev, confirmed_changes=conf)
    assert r.cancelled and _st(r) == {CLEAN: "done", ENCRYPT: "done", VERIFY: "cancelled", SHRED: "not_started"}
    f = _plain(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(agecli, "decrypt_hash_passphrase", stop)
        r = _run(f, encrypt=True, shred=True, cancel=ev)
    assert _st(r) == {ENCRYPT: "done", VERIFY: "cancelled", SHRED: "not_started"}
    assert j.read_bytes() == before and f.exists() and sorted(p.name for p in tmp_path.iterdir()) == ["a.jpg", "plain.bin"]


@needs_age
def test_changed_during_run_and_after_verify(tmp_path, monkeypatch):
    f = _plain(tmp_path)
    real = agecli.decrypt_hash_passphrase

    def touch_source(*a, **k):
        h = real(*a, **k)
        with open(f, "ab") as fh:
            fh.write(b"more")
        return h
    with monkeypatch.context() as m:
        m.setattr(agecli, "decrypt_hash_passphrase", touch_source)
        r = _run(f, encrypt=True)
    assert r.error_code == "changed_during_run" and _st(r) == {ENCRYPT: "done", VERIFY: "failed"}

    g = _plain(tmp_path, "g.bin")
    real_publish = output.publish_numbered

    def grow(*a):
        final, renamed = real_publish(*a)
        with open(final, "ab") as fh:
            fh.write(b"x")
        return final, renamed
    with monkeypatch.context() as m:
        m.setattr(output, "publish_numbered", grow)
        r = _run(g, encrypt=True, shred=True)
    assert r.error_code == "changed_after_verify" and g.exists()
    assert _st(r) == {ENCRYPT: "done", VERIFY: "failed", SHRED: "not_started"}

    # the same size re-check on the Clean + Encrypt path and on the folder path
    j = tmp_path / "a.jpg"; _jpeg(j)
    with monkeypatch.context() as m:
        m.setattr(output, "publish_numbered", grow)
        r = _run(j, clean=True, encrypt=True, shred=True, confirmed_changes={str(j): list(clean_pipeline.JPEG_CHANGES)})
    assert r.error_code == "changed_after_verify" and j.exists()
    assert _st(r) == {CLEAN: "done", ENCRYPT: "done", VERIFY: "failed", SHRED: "not_started"}

    fo = _fold(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(output, "publish_numbered", grow)
        r = _run(fo, encrypt=True, shred=True)
    assert r.error_code == "changed_after_verify" and (fo / "a.txt").exists()
    assert _st(r) == {ENCRYPT: "done", VERIFY: "failed", SHRED: "not_started"}


# ---------------------------------------------------------------- skipped stages and shred outcomes

@needs_age
def test_storage_assess_raising_after_publish(tmp_path, monkeypatch):
    f = _plain(tmp_path)

    def boom(p):
        raise RuntimeError("injected")
    monkeypatch.setattr(storage, "assess", boom)
    r = _run(f, encrypt=True, shred=True)
    assert not r.ok and r.error_kind == "error" and f.exists() and (tmp_path / "plain.bin.age").exists()
    assert _st(r) == {ENCRYPT: "done", VERIFY: "done", SHRED: "not_started"}


@needs_age
def test_unclean_encrypt_with_shred_skips_clean_and_shred(tmp_path):
    t = _plain(tmp_path, "n.txt")
    r = _run(t, clean=True, encrypt=True, shred=True, allow_unclean=True)
    assert r.ok and t.exists()
    assert _st(r) == {CLEAN: "skipped", ENCRYPT: "done", VERIFY: "done", SHRED: "skipped"}


@needs_age
def test_unconfirmed_content_change_skips_shred(tmp_path):
    j = tmp_path / "a.jpg"; _jpeg(j)
    real = clean_pipeline.runner.clean_bytes

    def changed(*a, **k):
        o = real(*a, **k)
        o.content_changed.append("something re-encoded")
        return o
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(clean_pipeline.runner, "clean_bytes", changed)
        r = _run(j, clean=True, encrypt=True, shred=True)
    finally:
        mp.undo()
    assert r.ok and j.exists()
    assert _st(r) == {CLEAN: "done", ENCRYPT: "done", VERIFY: "done", SHRED: "skipped"}


@needs_age
def test_shred_success_midway_failure_and_refusal_all_attempted(tmp_path, monkeypatch):
    f = _plain(tmp_path)
    r = _run(f, encrypt=True, shred=True)
    assert _st(r)[SHRED] == "attempted" and r.original_removed and not f.exists()

    g = _plain(tmp_path, "g.bin")

    def half_zero(self, size=None):
        self._f.seek(0); self._f.write(bytes(100)); self._f.flush()
        raise OSError("injected")
    with monkeypatch.context() as m:
        m.setattr(heldfile.HeldFile, "zero", half_zero)
        r = _run(g, encrypt=True, shred=True)
    assert _st(r)[SHRED] == "attempted" and r.shred_attempted and not r.original_untouched and not r.original_removed

    h = _plain(tmp_path, "h.bin")

    def refuse(held, report=None, allowed=None, expect=None):
        raise folder.shred.ShredRefused("injected")
    with monkeypatch.context() as m:
        m.setattr(folder.shred, "shred_held", refuse)
        r = _run(h, encrypt=True, shred=True)
    assert _st(r)[SHRED] == "attempted" and r.original_untouched and h.exists()


# ---------------------------------------------------------------- a raising recorder changes nothing

class _Boom:
    def append(self, *_):
        raise RuntimeError("recorder store down")

    def __iter__(self):
        raise RuntimeError("recorder store down")


def _enc(d, **kw):
    return run_steps([_plain(d)], PW, StepOptions(**kw))


def _enc_jpeg(d, **kw):
    j = d / "a.jpg"; _jpeg(j)
    return run_steps([j], PW, StepOptions(confirmed_changes={str(j): list(clean_pipeline.JPEG_CHANGES)}, **kw))


def _enc_folder(d, **kw):
    return run_steps([_fold(d)], PW, StepOptions(**kw))


def _clean_only(d):
    j = d / "a.jpg"; _jpeg(j)
    return run_steps([j], PW, StepOptions(clean=True, encrypt=False))


def _shred_only(d):
    return run_steps([_plain(d)], PW, StepOptions(encrypt=False, shred=True))


def _decrypt(d):
    run_steps([_plain(d)], PW, StepOptions(encrypt=True))
    (d / "plain.bin").unlink()
    return run_steps([d / "plain.bin.age"], PW, StepOptions())


def _verify_failed(d, mp):
    mp.setattr(agecli, "decrypt_hash_passphrase", lambda *a, **k: "0" * 64)
    return _enc(d, encrypt=True, shred=True)


def _cancel(d, mp):
    def stop(*a, **k):
        raise agecli.Cancelled("Cancelled")
    mp.setattr(agecli, "decrypt_hash_passphrase", stop)
    return run_steps([_plain(d)], PW, StepOptions(encrypt=True, shred=True), cancel=threading.Event())


def _midway(d, mp):
    def half_zero(self, size=None):
        self._f.seek(0); self._f.write(bytes(100)); self._f.flush()
        raise OSError("injected")
    mp.setattr(heldfile.HeldFile, "zero", half_zero)
    return _enc(d, encrypt=True, shred=True)


def _refused(d, mp):
    def refuse(held, report=None, allowed=None, expect=None):
        raise folder.shred.ShredRefused("injected")
    mp.setattr(folder.shred, "shred_held", refuse)
    return _enc(d, encrypt=True, shred=True)


SCENARIOS = {
    "encrypt": lambda d, mp: _enc(d, encrypt=True),
    "encrypt+shred": lambda d, mp: _enc(d, encrypt=True, shred=True),
    "clean+encrypt": lambda d, mp: _enc_jpeg(d, clean=True, encrypt=True),
    "clean+encrypt+shred": lambda d, mp: _enc_jpeg(d, clean=True, encrypt=True, shred=True),
    "folder": lambda d, mp: _enc_folder(d, encrypt=True),
    "folder+shred": lambda d, mp: _enc_folder(d, encrypt=True, shred=True),
    "clean only": lambda d, mp: _clean_only(d),
    "shred only": lambda d, mp: _shred_only(d),
    "decrypt": lambda d, mp: _decrypt(d),
    "verify failed": _verify_failed,
    "cancel": _cancel,
    "midway": _midway,
    "refused": _refused,
}


def _snapshot(d, results):
    fields = []
    for r in results:
        row = {k: v for k, v in vars(r).items() if k != "stages"}
        fields.append({k: (str(v).replace(str(d), "<d>") if v is not None and not isinstance(v, (bool, list)) else
                           ([str(x).replace(str(d), "<d>") for x in v] if isinstance(v, list) else v))
                       for k, v in row.items()})
    disk = {}
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d).as_posix()
        disk[rel] = None if p.is_dir() or p.suffix == ".age" else hashlib.sha256(p.read_bytes()).hexdigest()
    return fields, disk


@needs_age
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_raising_recorder_changes_nothing(tmp_path, name):
    out = {}
    for label in ("working", "raising"):
        d = tmp_path / label; d.mkdir()
        with pytest.MonkeyPatch.context() as mp:
            if label == "raising":
                mp.setattr(stages.Recorder, "_new_store", staticmethod(lambda: _Boom()))
            res = SCENARIOS[name](d, mp)
        out[label] = (res, _snapshot(d, res))
    assert out["working"][1] == out["raising"][1]
    assert all(r.stages == () for r in out["raising"][0])
    if name not in ("decrypt",):
        assert any(r.stages for r in out["working"][0])


# ---------------------------------------------------------------- static guard: recorder calls stay out of the shred code

def _tree(name):
    return ast.parse((SRC / name).read_text(encoding="utf-8"))


def _func(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _names(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out.update(a.name.split(".")[-1] for a in n.names)
            if isinstance(n, ast.ImportFrom) and n.module:
                out.add(n.module.split(".")[-1])
    return out


RECORDER_NAMES = {"rec", "stages", "NULL", "Recorder", "SHRED", "begin"}


def test_recorder_names_absent_from_the_shred_code():
    assert not _names(_func(_tree("pipeline.py"), "shred_held_original")) & RECORDER_NAMES
    assert not _names(_func(_tree("folder.py"), "_shred_manifest")) & RECORDER_NAMES
    for f in ("shred.py", "heldfile.py"):
        assert not _names(_tree(f)) & RECORDER_NAMES, f


def _is_begin_shred(stmt):
    c = stmt.value if isinstance(stmt, ast.Expr) else None
    return (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "begin"
            and isinstance(c.func.value, ast.Name) and c.func.value.id == "rec"
            and len(c.args) == 1 and isinstance(c.args[0], ast.Name) and c.args[0].id == "SHRED")


def _statement_before_call(fn, callee):
    parents = {}
    for n in ast.walk(fn):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and (getattr(n.func, "attr", None) == callee or getattr(n.func, "id", None) == callee)]
    assert len(calls) == 1, (fn.name, callee)
    node = calls[0]
    while True:
        while not isinstance(node, ast.stmt):
            node = parents[node]
        body = next(b for f in ("body", "orelse", "finalbody", "handlers")
                    for b in [getattr(parents[node], f, None)] if isinstance(b, list) and node in b)
        i = body.index(node)
        if i > 0:
            return body[i - 1]
        node = parents[node]


@pytest.mark.parametrize("module,func,callee", [
    ("pipeline.py", "encrypt_verify_shred", "shred_held_original"),
    ("clean_pipeline.py", "clean_encrypt_verify_shred", "shred_held_original"),
    ("folder.py", "encrypt_folder_verify_shred", "_shred_manifest"),
    ("steps.py", "_shred_only", "shred_tree"),
])
def test_begin_shred_is_the_statement_before_the_shred_call(module, func, callee):
    assert _is_begin_shred(_statement_before_call(_func(_tree(module), func), callee))


def test_shred_is_never_marked_done():
    for f in SRC.rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "done"
                    and any(isinstance(a, ast.Name) and a.id == "SHRED" for a in n.args)):
                pytest.fail(f"done(SHRED) in {f.name}")


# ---------------------------------------------------------------- shape and imports

def test_stages_is_an_immutable_tuple_and_the_module_is_standalone():
    from shiva.jobs import JobResult
    assert JobResult(Path("x"), True, "m").stages == ()
    rec = stages.Recorder((ENCRYPT,))
    rec.begin(ENCRYPT)
    frozen = rec.freeze(JobResult(Path("x"), False, "m"))
    assert isinstance(frozen, tuple) and all(isinstance(s, tuple) for s in frozen)
    imported = set()
    for n in ast.walk(_tree("stages.py")):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            assert n.level == 0, "no import from inside shiva"
            imported.add((n.module or "").split(".")[0])
    assert imported <= {"__future__", "pathlib"}
    code = "import sys; import shiva.stages; assert not [m for m in sys.modules if m.startswith('PySide6')]"
    env = dict(os.environ, PYTHONPATH=str(SRC.parent))
    assert subprocess.run([sys.executable, "-c", code], env=env).returncode == 0


def test_recorder_never_raises():
    rec = stages.Recorder((CLEAN, SHRED))
    rec._events = _Boom()
    rec.begin(CLEAN); rec.done(CLEAN); rec.skip(SHRED)
    assert rec.freeze(object()) == ()
    assert stages.NULL.freeze(object()) == ()
