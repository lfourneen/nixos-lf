#!/usr/bin/env python3
"""List local Markdown resources reached by literal Nix file/directory paths."""
import argparse
from pathlib import Path
import re

PATH = re.compile(r"(?<![\w/])(?:\.\./|\./)[\w./+-]+")
TREES = ("home", "system", "overlays")


def resources(root):
    found = set()
    for tree in TREES:
        for source in sorted((root / tree).rglob("*.nix")):
            text = "\n".join(line.split("#", 1)[0] for line in source.read_text().splitlines())
            for match in PATH.finditer(text):
                path = (source.parent / match.group()).resolve()
                if not path.is_relative_to(root):
                    continue
                if path == root or path.name in TREES:
                    continue
                candidates = path.rglob("*.md") if path.is_dir() else [path]
                for candidate in candidates:
                    if candidate.suffix == ".md" and candidate.is_file():
                        rel = candidate.relative_to(root)
                        if rel.parts[0] in (*TREES, "local-data"):
                            found.add(rel.as_posix())
    return sorted(found)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print("\n".join(resources(args.root.resolve())))
