"""corpusdiff: run one function over thousands of real test files, then diff two runs.

corpusdiff fetch pdfjs ./corpora/pdfjs
corpusdiff run mylib.convert:to_text ./corpora/pdfjs -o before.jsonl
# ...change your code...
corpusdiff run mylib.convert:to_text ./corpora/pdfjs -o after.jsonl
corpusdiff diff before.jsonl after.jsonl
"""

import argparse
import difflib
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from . import corpora

WORKER = Path(__file__).with_name("worker.py")


# ---------------------------------------------------------------- run
class Worker:
    def __init__(self, a):
        self.a = a
        self.p = None

    def start(self):
        env = dict(os.environ)
        if self.a.path:
            env["CORPUSDIFF_PATH"] = os.pathsep.join(os.path.abspath(p) for p in self.a.path)

        def limits():
            os.setsid()
            if self.a.mem_mb:
                import resource

                b = self.a.mem_mb * 2**20
                resource.setrlimit(resource.RLIMIT_AS, (b, b))

        self.p = subprocess.Popen(
            [self.a.python, "-I", str(WORKER), self.a.target, self.a.save_outputs or ""],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            stderr=None if self.a.show_stderr else subprocess.DEVNULL,
            preexec_fn=limits,
        )
        first = self.read(self.a.start_timeout)
        if not (first and first.get("ready")):
            code = self.p.wait() if first == {} else self.kill()  # {} = worker exited by itself
            raise SystemExit(f"worker did not start (exit code {code}). Run again with --show-stderr to see why.")

    def read(self, timeout):
        r, _, _ = select.select([self.p.stdout], [], [], timeout)
        if not r:
            return None
        line = self.p.stdout.readline()
        return json.loads(line) if line else {}

    def kill(self):
        if self.p and self.p.poll() is None:
            try:
                os.killpg(self.p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        code = self.p.wait() if self.p else None
        self.p = None
        return code

    def handle(self, path):
        if self.p is None:
            self.start()
        t = time.perf_counter()
        try:
            self.p.stdin.write(path + "\n")
            self.p.stdin.flush()
            row = self.read(self.a.timeout)
        except BrokenPipeError:
            row = {}
        if row:
            if self.a.fresh:
                self.kill()
            return row
        dt = round(time.perf_counter() - t, 3)
        if row is None:  # still running at the deadline
            self.kill()
            return {"ok": False, "etype": "TIMEOUT", "msg": f"no result after {self.a.timeout} s", "s": dt}
        code = self.kill()  # the worker died while on this file
        name = signal.Signals(-code).name if code and code < 0 else f"exit {code}"
        hint = " (memory limit?)" if self.a.mem_mb else ""
        return {"ok": False, "etype": "CRASH", "msg": f"worker died: {name}{hint}", "s": dt}


def files_in(root, exts):
    root = Path(root)
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.name.startswith(".") and (not exts or p.suffix.lower().lstrip(".") in exts):
            yield p


def cmd_run(a):
    exts = {e.lower().lstrip(".") for e in a.ext.split(",")} if a.ext else set()
    out = Path(a.output)
    done = set()
    if out.exists() and not a.restart:
        for line in out.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["f"])
    elif out.exists():
        out.unlink()
    if a.save_outputs:
        Path(a.save_outputs).mkdir(parents=True, exist_ok=True)
    todo = [p for p in files_in(a.corpus, exts) if str(p.relative_to(a.corpus)) not in done]
    if a.limit:
        todo = todo[: a.limit]
    total, lock, n = len(todo), threading.Lock(), [0]
    print(f"{total} file(s) to run ({len(done)} already in {out.name}), {a.jobs} worker(s)", file=sys.stderr)
    fh = out.open("a")
    queue = iter(todo)
    t0 = time.perf_counter()

    errors = []

    def loop():
        w = Worker(a)
        try:
            while True:
                with lock:
                    p = None if errors else next(queue, None)
                if p is None:
                    return
                row = {"f": str(p.relative_to(a.corpus)), "bytes": p.stat().st_size, **w.handle(str(p.resolve()))}
                with lock:  # one line per file, written at once: a killed run loses nothing
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    n[0] += 1
                    if not a.quiet and (n[0] * 10 // total > (n[0] - 1) * 10 // total or n[0] == total):
                        print(f"  {n[0]}/{total} ({time.perf_counter() - t0:.0f} s)", file=sys.stderr)
        except BaseException as e:  # noqa: BLE001 - re-raised in the main thread
            errors.append(e)  # the other threads stop before their next file
        finally:
            w.kill()

    threads = [threading.Thread(target=loop, daemon=True) for _ in range(max(1, a.jobs))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    fh.close()
    if errors:
        raise errors[0]
    print_summary(load(out))


# ---------------------------------------------------------------- summary / diff
def load(path):
    rows = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["f"]] = r
    return rows


def fail_key(r):
    return f"{r.get('etype')}  @ {r['where'][-1]}" if r.get("where") else r.get("etype", "?")


def print_summary(rows):
    ok = [r for r in rows.values() if r["ok"]]
    bad = [r for r in rows.values() if not r["ok"]]
    empty = [r for r in ok if r.get("empty")]
    secs = sum(r.get("s", 0) for r in rows.values())
    print(f"{len(rows)} file(s): {len(ok)} ok ({len(empty)} with empty output), {len(bad)} failed, {secs:.0f} s total")
    for k, c in Counter(fail_key(r) for r in bad).most_common(12):
        ex = next(r["f"] for r in bad if fail_key(r) == k)
        print(f"  {c:5}  {k}   e.g. {ex}")
    slow = sorted(rows.values(), key=lambda r: -r.get("s", 0))[:5]
    print("  slowest:", ", ".join(f"{r['f']} {r.get('s', 0):.1f}s" for r in slow))


def cmd_summary(a):
    print_summary(load(a.run))


def same_error(o, n):
    """Same exception type raised in the same function of the same file; line numbers may move between versions."""

    def k(r):
        last = (r.get("where") or [""])[-1]
        file, _, fn = last.partition(" in ")
        return r.get("etype"), file.rsplit(":", 1)[0].rsplit("/", 1)[-1], fn

    return k(o) == k(n)


def classify(o, n, slow_x, slow_min):
    if o["ok"] and not n["ok"]:
        return "broke"
    if n["ok"] and not o["ok"]:
        return "fixed"
    if not o["ok"]:
        return "same" if same_error(o, n) else "error-changed"
    if o.get("empty") is False and n.get("empty"):
        return "now-empty"
    if o.get("empty") and n.get("empty") is False:
        return "now-has-output"
    if o.get("out_hash") != n.get("out_hash"):
        return "output-changed"
    if n.get("s", 0) > max(slow_min, slow_x * o.get("s", 0)):
        return "slower"
    if o.get("s", 0) > max(slow_min, slow_x * n.get("s", 0)):
        return "faster"
    return "same"


ORDER = [
    "broke",
    "now-empty",
    "error-changed",
    "output-changed",
    "slower",
    "fixed",
    "now-has-output",
    "faster",
    "unstable",
]
BAD = {"broke", "now-empty"}


def read_output(dirs, h):
    for d in dirs:
        p = Path(d) / h
        if p.exists():
            return p.read_bytes()
    return None


def as_lines(data):
    """Text as lines; JSON outputs flattened to 'key: value' lines, with multi-line strings spread out."""
    text = data.decode("utf-8", "replace")
    try:
        obj = json.loads(text)
    except ValueError:
        return text.splitlines()
    out = []

    def walk(prefix, v):
        if isinstance(v, dict):
            for k in v:
                walk(f"{prefix}.{k}" if prefix else str(k), v[k])
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(f"{prefix}[{i}]", x)
        elif isinstance(v, str) and "\n" in v:
            out.append(f"{prefix}:")
            out.extend("  " + ln for ln in v.splitlines())
        else:
            out.append(f"{prefix}: {json.dumps(v, ensure_ascii=False)}")

    walk("", obj)
    return out


def show_change(dirs, h_old, h_new, max_lines):
    """Print the first changed lines between two saved outputs (saved with run --save-outputs)."""
    a, b = read_output(dirs, h_old), read_output(dirs, h_new)
    if a is None or b is None:
        print(f"  {'':15}   (output not saved; use run --save-outputs DIR)")
        return
    if b"\0" in a[:8000] or b"\0" in b[:8000]:
        print(f"  {'':15}   (binary output)")
        return
    lines = difflib.unified_diff(as_lines(a), as_lines(b), "old", "new", n=1, lineterm="")
    lines = [ln for ln in lines if not ln.startswith(("---", "+++"))]
    for ln in lines[:max_lines]:
        print(f"  {'':15}   | {ln[:150]}")
    if len(lines) > max_lines:
        print(f"  {'':15}   | ... {len(lines) - max_lines} more diff lines (--lines N)")


def cmd_diff(a):
    old, new = load(a.old), load(a.new)
    both = sorted(set(old) & set(new))
    # Files whose result differs between two runs of the SAME code are noise, not changes.
    unstable = set()
    for rerun in map(load, a.rerun):
        unstable |= {
            f
            for f in both
            if f in rerun and classify(old[f], rerun[f], a.slower, a.min_seconds) not in ("same", "slower", "faster")
        }
    groups = {}
    for f in both:
        groups.setdefault(
            "unstable" if f in unstable else classify(old[f], new[f], a.slower, a.min_seconds), []
        ).append(f)
    if a.json:
        json.dump(
            {k: v for k, v in groups.items() if k != "same"}
            | {
                "same": len(groups.get("same", [])),
                "only_old": sorted(set(old) - set(new)),
                "only_new": sorted(set(new) - set(old)),
            },
            sys.stdout,
            indent=1,
        )
        print()
    else:
        t_old, t_new = (sum(r[f].get("s", 0) for f in both) for r in (old, new))
        print(
            f"{len(both)} files in both runs: "
            + ", ".join(f"{k} {len(groups[k])}" for k in ORDER + ["same"] if k in groups)
            + f"   time {t_old:.0f}s -> {t_new:.0f}s"
        )
        if set(old) ^ set(new):
            print(f"  (only in old: {len(set(old) - set(new))}, only in new: {len(set(new) - set(old))})")
        for k in ORDER:
            for f in groups.get(k, [])[: a.show]:
                o, n = old[f], new[f]

                def desc(r):
                    if not r["ok"]:
                        return f"{r.get('etype')}: {r.get('msg', '')[:70]}"
                    return f"{r.get('out_len')} bytes, {r.get('s', 0):.2f}s"

                print(f"  {k:15} {f}\n  {'':15}   {desc(o)}  ->  {desc(n)}")
                if a.outputs and o.get("out_hash") and n.get("out_hash") and o["out_hash"] != n["out_hash"]:
                    show_change(a.outputs, o["out_hash"], n["out_hash"], a.lines)
            if len(groups.get(k, [])) > a.show:
                print(f"  {k:15} ... and {len(groups[k]) - a.show} more (--show N)")
    return 1 if any(groups.get(k) for k in BAD) else 0


def cmd_fetch(a):
    if a.name == "list" or not a.name:
        for name, c in corpora.CORPORA.items():
            print(f"{name:8} {c['what']}\n{'':8} source: {c['home']}   licence: {c['licence']}")
        return 0
    corpora.fetch(a.name, a.dest or f"corpora/{a.name}", only=a.only)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="corpusdiff", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run TARGET on every file in CORPUS; one JSON line per file (resumable)")
    r.add_argument("target", help="'package.module:function' or 'path/to/file.py:function'; called with one file path")
    r.add_argument("corpus", help="folder with test files (searched recursively)")
    r.add_argument("-o", "--output", required=True, help="results file (.jsonl); existing results are kept and skipped")
    r.add_argument("-j", "--jobs", type=int, default=2, help="parallel worker processes (default 2)")
    r.add_argument(
        "-t", "--timeout", type=float, default=120, help="seconds per file before the worker is killed (120)"
    )
    r.add_argument("--mem-mb", type=int, default=0, help="address-space limit per worker in MB (0 = none)")
    r.add_argument("--ext", help="only these extensions, e.g. pdf,docx")
    r.add_argument("--limit", type=int, default=0, help="only the first N files (quick try)")
    r.add_argument("--python", default=sys.executable, help="interpreter for the workers (e.g. your venv's python)")
    r.add_argument("--path", action="append", default=[], help="extra import path for the target (repeatable)")
    r.add_argument("--fresh", action="store_true", help="new process for every file (exact memory per file, slower)")
    r.add_argument("--save-outputs", metavar="DIR", help="also store every output, named by its hash")
    r.add_argument("--start-timeout", type=float, default=300, help="seconds allowed for importing the target")
    r.add_argument("--restart", action="store_true", help="discard existing results in --output")
    r.add_argument("--show-stderr", action="store_true", help="let the target's prints and warnings through")
    r.add_argument("-q", "--quiet", action="store_true")
    s = sub.add_parser("summary", help="counts, top failure kinds and slowest files of one run")
    s.add_argument("run")
    d = sub.add_parser("diff", help="compare two runs; exit code 1 if anything broke or became empty")
    d.add_argument("old")
    d.add_argument("new")
    d.add_argument("--slower", type=float, default=2.0, help="flag files at least this many times slower (2.0)")
    d.add_argument("--min-seconds", type=float, default=1.0, help="...and slower than this many seconds (1.0)")
    d.add_argument("--show", type=int, default=10, help="files listed per group (10)")
    d.add_argument(
        "--rerun",
        action="append",
        default=[],
        metavar="RUN",
        help="a second run of the OLD code: files that differ from it are set aside as 'unstable'",
    )
    d.add_argument(
        "--outputs",
        action="append",
        default=[],
        metavar="DIR",
        help="folder(s) given to run --save-outputs: show what changed in each changed output",
    )
    d.add_argument("--lines", type=int, default=12, help="diff lines shown per changed file (12)")
    d.add_argument("--json", action="store_true")
    f = sub.add_parser("fetch", help="download a public test corpus ('corpusdiff fetch list' shows them)")
    f.add_argument("name", nargs="?", default="list")
    f.add_argument("dest", nargs="?")
    f.add_argument("--only", help="comma-separated sub-folders (for fate: e.g. aac,mp3-conformance)")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "summary": cmd_summary, "diff": cmd_diff, "fetch": cmd_fetch}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
