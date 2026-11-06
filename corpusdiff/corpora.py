"""Public test-file collections that big parsing projects use for their own tests.

Files are downloaded as-is from the project's public repository or mirror. They come from bug reports and fuzzers:
some are deliberately broken, encrypted or hostile. Run them in a sandbox and check each project's terms before
redistributing anything."""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CORPORA = {
    "pdfjs": {
        "what": "~1,000 PDFs from Mozilla pdf.js bug reports (fonts, encodings, broken xref, encryption)",
        "home": "https://github.com/mozilla/pdf.js/tree/master/test/pdfs",
        "licence": "repo Apache-2.0; files vary",
        "github": ("mozilla/pdf.js", "master", ["test/pdfs/"]),
        "ext": {".pdf"},
    },
    "poi": {
        "what": "~650 Word/Excel/PowerPoint files from Apache POI (real bug files, encrypted, malformed, XXE)",
        "home": "https://github.com/apache/poi/tree/trunk/test-data",
        "licence": "Apache-2.0; files vary",
        "github": ("apache/poi", "trunk", ["test-data/document/", "test-data/spreadsheet/", "test-data/slideshow/"]),
        "ext": {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".docm", ".xlsm", ".pptm", ".xlsb", ".rtf"},
    },
    "pillow": {
        "what": "~1,000 images from Pillow's tests (odd modes, truncated, huge, JPEG 2000, TIFF variants)",
        "home": "https://github.com/python-pillow/Pillow/tree/main/Tests/images",
        "licence": "MIT-CMU; files vary",
        "github": ("python-pillow/Pillow", "main", ["Tests/images/"]),
        "ext": None,
    },
    "fate": {
        "what": "FFmpeg's FATE suite: audio/video samples per codec and container (pick folders with --only)",
        "home": "https://fate-suite.ffmpeg.org/",
        "licence": "mixed; see FFmpeg",
        "rsync": "rsync://fate-suite.ffmpeg.org/fate-suite/",
        "default_only": "aac,mp3-conformance,ogg-vorbis,ogg-opus,wav,lossless-audio,mkv,mov",
    },
}


def _get(url, timeout=60):
    headers = {"User-Agent": "corpusdiff"}
    if "api.github.com" in url and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read()


def fetch(name, dest, only=None):
    if name not in CORPORA:
        raise SystemExit(f"unknown corpus {name!r}; known: {', '.join(CORPORA)}")
    c, dest = CORPORA[name], Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if "rsync" in c:
        if not shutil.which("rsync"):
            raise SystemExit("this corpus needs rsync")
        for d in (only or c["default_only"]).split(","):
            print(f"rsync {d} ...", file=sys.stderr)
            subprocess.run(
                ["rsync", "-a", "--timeout=120", c["rsync"] + d.strip() + "/", str(dest / d.strip())], check=True
            )
        return
    repo, ref, prefixes = c["github"]
    tree = json.loads(_get(f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"))
    if tree.get("truncated"):
        print("warning: GitHub returned a truncated file list", file=sys.stderr)
    want = [
        t["path"]
        for t in tree["tree"]
        if t["type"] == "blob"
        and any(t["path"].startswith(p) for p in prefixes)
        and (c["ext"] is None or Path(t["path"]).suffix.lower() in c["ext"])
    ]
    if only:
        subs = tuple(o.strip().strip("/") + "/" for o in only.split(","))
        want = [p for p in want if any(p[len(pre) :].startswith(subs) for pre in prefixes if p.startswith(pre))]

    def one(path):
        rel = next(path[len(p) :] for p in prefixes if path.startswith(p))
        out = dest / rel
        if out.exists():
            return 0
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = _get(f"https://raw.githubusercontent.com/{repo}/{ref}/{path}")
        except OSError as e:
            print(f"  failed: {path}: {e}", file=sys.stderr)
            return 0
        tmp = out.with_name("." + out.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(out)
        return 1

    print(f"{len(want)} files from {repo} -> {dest}", file=sys.stderr)
    with ThreadPoolExecutor(8) as ex:
        new = sum(ex.map(one, want))
    print(f"downloaded {new}, already had {len(want) - new}", file=sys.stderr)
