#!/usr/bin/env python3
"""Emit the file botcabinet.com should publish at /catalog.json, from this plugin's catalog snapshot.

    python3 tools/make_site_catalog.py                       # writes ./site-catalog/catalog.json
    python3 tools/make_site_catalog.py --out public/catalog.json
    python3 tools/make_site_catalog.py --check path/to/catalog.json   # check any file, change nothing

The output is data/catalog.json (the snapshot ``tools/make_catalog.py`` takes from the site itself) run
through the same strict checker Cabinet applies to what it fetches (``catalog_schema.validate_catalog``).
If this script writes it, Cabinet will accept it. The format is described in docs/site-catalog-contract.md.

To publish it: copy the file into the website repository's ``public/`` folder so it is served at
https://botcabinet.com/catalog.json, with the content type ``application/json``. This script never
touches the website repository.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from catalog_schema import SchemaError, validate_catalog  # noqa: E402

SNAPSHOT = ROOT / "data" / "catalog.json"
DEFAULT_OUT = ROOT / "site-catalog" / "catalog.json"


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def check(path: Path) -> int:
    try:
        doc = validate_catalog(json.loads(path.read_text(encoding="utf-8")))
    except SchemaError as exc:  # before ValueError: SchemaError is one
        print(f"{path}: Cabinet would refuse this file:")
        for problem in exc.problems:
            print(f"  - {problem}")
        return 1
    except (OSError, ValueError) as exc:
        print(f"{path}: not a readable catalog: {exc}")
        return 1
    print(f"{path}: OK. {len(doc['bots'])} Bots, {len(doc['crews'])} Crew Kits, {len(doc['jobs'])} job guides, generated {doc['generatedAt']}")
    return 0


def main(argv: list[str]) -> int:
    if "--check" in argv:
        return check(Path(argv[argv.index("--check") + 1]))
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else DEFAULT_OUT
    doc = validate_catalog(json.loads(SNAPSHOT.read_text(encoding="utf-8")))
    text = render(doc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    print(f"{out}: {len(text.encode('utf-8')) // 1024} KB, {len(doc['bots'])} Bots, {len(doc['crews'])} Crew Kits, "
          f"{len(doc['jobs'])} job guides, generated {doc['generatedAt']}, sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
