import json
from pathlib import Path

import pytest

from corpusdiff.cli import main

HERE = Path(__file__).parent


@pytest.fixture
def corpus(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    for name, text in {
        "a.txt": "hello",
        "b.txt": "bad",
        "c.txt": "slow",
        "d.txt": "boom",
        "e.txt": "exit",
        "sub/f.txt": "fine",
    }.items():
        (d / name).parent.mkdir(exist_ok=True)
        (d / name).write_text(text)
    return d


def rows(p):
    return {json.loads(line)["f"]: json.loads(line) for line in p.read_text().splitlines()}


def run(target, corpus, out, *extra):
    return main(["run", f"{HERE / 'targets.py'}:{target}", str(corpus), "-o", str(out), "-q", "-t", "3", *extra])


def test_run_records_every_outcome(corpus, tmp_path):
    out = tmp_path / "r.jsonl"
    run("picky", corpus, out, "-j", "2")
    r = rows(out)
    assert set(r) == {"a.txt", "b.txt", "c.txt", "d.txt", "e.txt", "sub/f.txt"}
    assert r["a.txt"]["ok"] and r["sub/f.txt"]["ok"]
    assert r["b.txt"]["etype"] == "ValueError" and "picky" in r["b.txt"]["where"][-1]
    assert r["c.txt"]["etype"] == "TIMEOUT"
    assert r["d.txt"]["etype"] == "CRASH" and ("SIGSEGV" in r["d.txt"]["msg"] or "SIGBUS" in r["d.txt"]["msg"])
    assert r["e.txt"]["etype"] == "CRASH" and "exit 3" in r["e.txt"]["msg"]


def test_resume_skips_done_files(corpus, tmp_path):
    out = tmp_path / "r.jsonl"
    run("upper", corpus, out, "--limit", "2")
    assert len(rows(out)) == 2
    run("upper", corpus, out)
    assert len(out.read_text().splitlines()) == 6


def test_diff_flags_broken_and_empty(corpus, tmp_path, capsys):
    for f in ("c.txt", "d.txt", "e.txt"):
        (corpus / f).unlink()
    old, new, emp = tmp_path / "old.jsonl", tmp_path / "new.jsonl", tmp_path / "emp.jsonl"
    run("upper", corpus, old)
    run("picky", corpus, new)
    run("empty", corpus, emp)
    assert main(["diff", str(old), str(new)]) == 1  # b.txt broke
    assert "broke" in capsys.readouterr().out
    assert main(["diff", str(old), str(old)]) == 0
    assert main(["diff", str(old), str(emp)]) == 1  # all outputs became empty
    capsys.readouterr()
    assert main(["diff", str(new), str(old), "--json"]) == 0  # going back fixes b.txt: nothing bad
    d = json.loads(capsys.readouterr().out)
    assert d["fixed"] == ["b.txt"]


def test_bad_target_fails_clearly(corpus, tmp_path):
    with pytest.raises(SystemExit, match="did not start"):
        main(["run", "nosuchmodule:f", str(corpus), "-o", str(tmp_path / "x.jsonl"), "-q"])


def test_diff_ignores_moved_lines_and_sets_aside_unstable_files(tmp_path, capsys):
    def write(name, rows):
        p = tmp_path / name
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return str(p)

    err = {"f": "x", "ok": False, "etype": "KeyError", "where": ["/a/checkout1/pkg/mod.py:10 in parse"]}
    moved = {**err, "where": ["/b/checkout2/pkg/mod.py:17 in parse"]}
    flaky = {"f": "y", "ok": True, "out_hash": "1", "empty": False}
    old = write("old", [err, flaky])
    new = write("new", [moved, {**flaky, "out_hash": "2"}])
    rerun = write("rerun", [err, {**flaky, "out_hash": "3"}])
    main(["diff", old, new, "--json"])
    assert json.loads(capsys.readouterr().out)["output-changed"] == ["y"]
    main(["diff", old, new, "--json", "--rerun", rerun])
    d = json.loads(capsys.readouterr().out)
    assert d["unstable"] == ["y"] and d["same"] == 1 and "output-changed" not in d


def test_diff_shows_what_changed_in_saved_outputs(corpus, tmp_path, capsys):
    for f in ("b.txt", "c.txt", "d.txt", "e.txt"):
        (corpus / f).unlink()
    saved = tmp_path / "outs"
    old, new = tmp_path / "old.jsonl", tmp_path / "new.jsonl"
    run("lines", corpus, old, "--save-outputs", str(saved))
    (corpus / "a.txt").write_text("hello\nworld")
    run("lines", corpus, new, "--save-outputs", str(saved))
    capsys.readouterr()
    main(["diff", str(old), str(new), "--outputs", str(saved)])
    out = capsys.readouterr().out
    assert "output-changed  a.txt" in out and "|   text:" not in out
    assert "| +  WORLD" in out


def test_corpus_paths():
    from corpusdiff.corpora import rel_path

    marker = {"marker": "/src/test/resources/test-documents/"}
    p = "tika-parsers/std/tika-parser-pdf-module/src/test/resources/test-documents/a b.pdf"
    assert rel_path(p, marker) == "tika-parser-pdf-module/a b.pdf"
    assert rel_path("tika-core/src/main/x.java", marker) is None
    assert rel_path("test/pdfs/x.pdf", ["test/pdfs/"]) == "x.pdf"
    assert rel_path("src/x.pdf", ["test/pdfs/"]) is None
