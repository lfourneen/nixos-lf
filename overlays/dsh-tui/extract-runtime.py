"""Extract the published pkg asset without executing or rebuilding the binary."""

import gzip
import json
import shutil
import sys
from pathlib import Path


binary, destination = map(Path, sys.argv[1:])
with binary.open("rb") as stream:
    # pkg appends a small, uncompressed JS prelude after its compressed assets.
    tail_start = max(0, binary.stat().st_size - 1024 * 1024)
    stream.seek(tail_start)
    tail = stream.read()
    signature = b"(function(process, require, console, EXECPATH_FD, PAYLOAD_POSITION, PAYLOAD_SIZE)"
    prelude_offset = tail.rindex(signature)
    prelude = tail[prelude_offset:].decode()
    table_start = prelude.index('{"0/')
    decoder = json.JSONDecoder()
    table, table_end = decoder.raw_decode(prelude[table_start:])
    # Arguments following the VFS are entrypoint, symlinks, path dictionary,
    # and compression (1 = gzip). Parse JSON only; never evaluate the prelude.
    rest = prelude[table_start + table_end:]
    args = []
    for _ in range(4):
        rest = rest.lstrip().removeprefix(",").lstrip()
        value, end = decoder.raw_decode(rest)
        args.append(value)
        rest = rest[end:]
    _, _, dictionary, compression = args
    assert compression == 1, "upstream pkg compression changed"
    asset_id = dictionary["runtime.tar.gz"]
    assets = [v["1"] for k, v in table.items() if k.endswith("/" + asset_id)]
    assert len(assets) == 1, "expected exactly one runtime.tar.gz asset"
    offset, length = assets[0]
    payload_size = max(offset + size for entry in table.values() for offset, size in entry.values())
    payload_start = tail_start + prelude_offset - payload_size
    assert payload_start > 0
    stream.seek(payload_start + offset)
    # Copy just this member: adjacent gzip members belong to different assets.
    with Path("runtime.asset.gz").open("wb") as asset:
        remaining = length
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            assert chunk, "truncated pkg asset"
            asset.write(chunk)
            remaining -= len(chunk)
with gzip.open("runtime.asset.gz", "rb") as asset, destination.open("wb") as output:
    shutil.copyfileobj(asset, output)
