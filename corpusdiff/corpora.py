"""Public test-file collections that big parsing projects use for their own tests.

Files are downloaded as-is from the project's public repository or mirror. They come from bug reports and fuzzers:
some are deliberately broken, encrypted or hostile. Run them in a sandbox and check each project's terms before
redistributing anything."""

import json
import os
import shutil
import subprocess
import sys
import urllib.parse
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
    "tika": {
        "what": "~1,200 files in ~100 formats from Apache Tika's parser tests (Office, PDF, RTF, EPUB, email, "
        "archives, audio, CAD), one folder per Tika module",
        "home": "https://github.com/apache/tika",
        "licence": "Apache-2.0; files vary",
        "github": ("apache/tika", "main", {"marker": "/src/test/resources/test-documents/"}),
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


def rel_path(path, where):
    """Local path for a repo file, or None if it isn't part of the corpus.
    where = list of folder prefixes, or {"marker": "/test-documents/"} for test folders spread over many modules
    (the local path is then module-folder/rest)."""
    if isinstance(where, dict):
        head, sep, rest = path.partition(where["marker"])
        return f"{head.rsplit('/', 1)[-1]}/{rest}" if sep and rest else None
    return next((path[len(p) :] for p in where if path.startswith(p)), None)


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
    repo, ref, where = c["github"]
    tree = json.loads(_get(f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"))
    if tree.get("truncated"):
        print("warning: GitHub returned a truncated file list", file=sys.stderr)
    subs = tuple(o.strip().strip("/") + "/" for o in only.split(",")) if only else None
    want = {}  # repo path -> local relative path
    for t in tree["tree"]:
        rel = rel_path(t["path"], where) if t["type"] == "blob" else None
        if rel and (c["ext"] is None or Path(rel).suffix.lower() in c["ext"]) and (not subs or rel.startswith(subs)):
            want[t["path"]] = rel

    def one(path):
        out = dest / want[path]
        if out.exists():
            return 0
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = _get(f"https://raw.githubusercontent.com/{repo}/{ref}/{urllib.parse.quote(path)}")
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the download
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
