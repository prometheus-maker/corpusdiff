"""Worker process: imports the target once, then handles one file path per stdin line, one JSON line per result.

Run by corpusdiff.run; not meant to be started by hand."""

import hashlib
import importlib
import importlib.util
import json
import os
import resource
import sys
import time
import traceback


def load(spec):
    mod, _, fn = spec.rpartition(":")
    if not mod or not fn:
        raise SystemExit(f"target must look like 'package.module:function' or 'path/file.py:function', got {spec!r}")
    if mod.endswith(".py"):
        s = importlib.util.spec_from_file_location("_corpusdiff_target", mod)
        m = importlib.util.module_from_spec(s)
        sys.path.insert(0, os.path.dirname(os.path.abspath(mod)))
        s.loader.exec_module(m)
    else:
        m = importlib.import_module(mod)
    return getattr(m, fn)


def normalize(out):
    """Turn whatever the target returned into bytes we can hash and save."""
    if out is None:
        return b""
    if isinstance(out, bytes):
        return out
    if isinstance(out, str):
        return out.encode("utf-8", "surrogatepass")
    try:
        return json.dumps(out, sort_keys=True, default=repr, ensure_ascii=False).encode()
    except (TypeError, ValueError):
        return repr(out).encode()


def short(filename):
    """Path relative to the import root it came from, so runs from two checkouts compare equal."""
    roots = sorted((os.path.join(os.path.abspath(p), "") for p in sys.path if p), key=len, reverse=True)
    return next((filename[len(r) :] for r in roots if filename.startswith(r)), filename)


def is_empty(out):
    """No text anywhere: None, blank str/bytes, or containers whose strings are all blank ({"pages": 3, "text": ""})."""
    if out is None:
        return True
    if isinstance(out, (str, bytes)):
        return not out.strip()
    if isinstance(out, dict):
        return all(is_empty(v) for v in out.values() if not isinstance(v, (int, float, bool)))
    if isinstance(out, (list, tuple)):
        return all(is_empty(v) for v in out if not isinstance(v, (int, float, bool)))
    return not repr(out).strip()


def where(exc):
    tb = traceback.extract_tb(exc.__traceback__)
    return [f"{short(fr.filename)}:{fr.lineno} in {fr.name}" for fr in tb][-3:]


def main():
    # Started as a script: drop our own folder from the path, look up the target like `python -c` would.
    sys.path[0] = os.getcwd()
    sys.path[1:1] = [p for p in os.environ.get("CORPUSDIFF_PATH", "").split(os.pathsep) if p]
    spec, save_dir = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None)
    # The protocol owns the real stdout; anything the target prints goes to stderr.
    proto = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    fn = load(spec)
    proto.write(json.dumps({"ready": True}) + "\n")
    for line in sys.stdin:
        path = line.rstrip("\n")
        t = time.perf_counter()
        row = {}
        try:
            out = fn(path)
            data = normalize(out)
            row.update(ok=True, out_len=len(data), out_hash=hashlib.sha1(data).hexdigest()[:16], empty=is_empty(out))
            if save_dir:
                with open(os.path.join(save_dir, row["out_hash"]), "wb") as f:
                    f.write(data)
        except BaseException as e:  # noqa: BLE001 - SystemExit/KeyboardInterrupt from a target are results too
            row.update(ok=False, etype=type(e).__name__, msg=str(e)[:300], where=where(e))
        row["s"] = round(time.perf_counter() - t, 3)
        rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        row["peak_mb"] = rss1 // 1024 if sys.platform != "darwin" else rss1 // 2**20
        proto.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
