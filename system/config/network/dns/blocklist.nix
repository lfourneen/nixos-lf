{ pkgs, ... }:

# Validate the shared suffix-blocking snapshot and emit dnsmasq/core inputs.
# The manifest binds source counts and compressed/decompressed digests.

# BEGIN GENERATED
#   stevenblack    76104  https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts
#   peterlowe       3564  https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=0&mimetype=plaintext
#   adaway          6540  https://adaway.org/hosts.txt
#   oisd_big      228765  https://big.oisd.nl/dnsmasq
#   1hosts_lite   202953  https://raw.githubusercontent.com/badmojr/1Hosts/master/Lite/hosts.txt
#   notracking    250170  https://raw.githubusercontent.com/notracking/hosts-blocklists/master/hostnames.txt
#   total         673861  merged and de-duplicated
# Snapshot: 2026-11-16
# Manifest: sha256 44c6e0c0e8645e61e7dc81c92d19863115ccc715545e3ab62956c33a86098106
# END GENERATED
#
# Additions that are specific to this host belong in `my.proxy.blockedDomains`
# (see system/config/network/proxy/options.nix); they render as their own address=
# lines and leave the merged file alone.
let
  data = ../../../../local-data/dns-blocklist.txt.xz;
  manifest = ../../../../local-data/dns-blocklist.manifest.json;

  # The validator is embedded so the module stays self-contained when the flake
  # is evaluated from /etc/nixos.
  validator = pkgs.writeText "dns-blocklist-validate.py" ''
    import hashlib
    import json
    import lzma
    import os
    import sys

    MAX_DOMAIN_BYTES = 253
    MAX_LABEL_BYTES = 63
    MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024
    MAX_COMPRESSED_BYTES = 64 * 1024 * 1024
    MAX_MANIFEST_BYTES = 4 * 1024 * 1024
    MAX_HEADER_BYTES = 1024 * 1024
    XZ_MEMORY_LIMIT = 128 * 1024 * 1024


    def fail(message):
        print("invalid: " + message, file=sys.stderr)
        raise SystemExit(2)


    def read_file(path, limit):
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            fail("file exceeds its size limit: " + path)
        return data


    def sha256(data):
        return hashlib.sha256(data).hexdigest()


    def is_int(value):
        return isinstance(value, int) and not isinstance(value, bool)


    def is_sha(value):
        return (isinstance(value, str) and len(value) == 64
                and all(character in "0123456789abcdef" for character in value))


    def is_date(value):
        return (isinstance(value, str) and len(value) == 10
                and value[4] == "-" and value[7] == "-"
                and all(character.isdigit() for character in value[:4] + value[5:7] + value[8:]))


    def main():
        if len(sys.argv) != 6:
            fail("expected data, manifest, header and two output paths")
        data_path, manifest_path, header_path, names_path, rules_path = sys.argv[1:6]
        manifest_raw = read_file(manifest_path, MAX_MANIFEST_BYTES)
        try:
            manifest = json.loads(manifest_raw)
        except json.JSONDecodeError as exc:
            fail("manifest is not valid JSON: " + str(exc))
        if not isinstance(manifest, dict):
            fail("manifest is not a JSON object")
        if manifest.get("schema") != "lf-dns-blocklist-manifest/1":
            fail("unexpected manifest schema")
        generated = manifest.get("generated_at")
        if not is_date(generated):
            fail("manifest generated_at is not YYYY-MM-DD")
        sources = manifest.get("sources")
        if not isinstance(sources, list) or not sources:
            fail("manifest has no sources")
        for source in sources:
            if not isinstance(source, dict):
                fail("manifest source is not an object")
            name = source.get("name")
            url = source.get("url")
            final_url = source.get("final_url")
            if not isinstance(name, str) or not name:
                fail("manifest source has no name")
            if not isinstance(url, str) or not url.startswith("https://"):
                fail("manifest source URL is not HTTPS: " + str(name))
            if not isinstance(final_url, str) or not final_url.startswith("https://"):
                fail("manifest final_url is not HTTPS: " + str(name))
            if source.get("format") not in ("hosts", "domain", "dnsmasq"):
                fail("manifest source format is invalid: " + str(name))
            count = source.get("count")
            if not is_int(count) or count < 0:
                fail("manifest source count is invalid: " + str(name))
            if "bytes" not in source or "sha256" not in source:
                fail("manifest source lacks raw evidence fields: " + str(name))
            if source["bytes"] is not None and (not is_int(source["bytes"]) or source["bytes"] < 0):
                fail("manifest source bytes is invalid: " + str(name))
            if source["sha256"] is not None and not is_sha(source["sha256"]):
                fail("manifest source sha256 is invalid: " + str(name))
        merged = manifest.get("merged")
        if not isinstance(merged, dict):
            fail("manifest merged field is not an object")
        for field in ("count", "uncompressed_bytes", "compressed_bytes"):
            if not is_int(merged.get(field)) or merged.get(field) <= 0:
                fail("manifest merged." + field + " is invalid")
        for field in ("sha256", "compressed_sha256"):
            if not is_sha(merged.get(field)):
                fail("manifest merged." + field + " is invalid")

        compressed = read_file(data_path, MAX_COMPRESSED_BYTES)
        if len(compressed) != merged["compressed_bytes"]:
            fail("compressed snapshot size does not match the manifest")
        if sha256(compressed) != merged["compressed_sha256"]:
            fail("compressed snapshot digest does not match the manifest")
        decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=XZ_MEMORY_LIMIT)
        try:
            payload = decompressor.decompress(compressed, max_length=MAX_DECOMPRESSED_BYTES + 1)
        except lzma.LZMAError as exc:
            fail("xz memory limit exceeded or corrupt stream: " + str(exc))
        if len(payload) > MAX_DECOMPRESSED_BYTES:
            fail("decompressed snapshot exceeds the size limit")
        if not decompressor.eof:
            fail("xz stream is truncated or exceeds the decompressed size limit")
        if decompressor.unused_data:
            fail("xz stream has trailing data")
        if len(payload) != merged["uncompressed_bytes"]:
            fail("decompressed snapshot size does not match the manifest")
        if sha256(payload) != merged["sha256"]:
            fail("decompressed snapshot digest does not match the manifest")
        try:
            text = payload.decode("ascii")
        except UnicodeDecodeError as exc:
            fail("snapshot is not ASCII: " + str(exc))
        if b"\x00" in payload or b"\r" in payload:
            fail("snapshot contains NUL or CR")
        if not text.endswith("\n"):
            fail("snapshot does not end with a newline")
        lines = text[:-1].split("\n")
        if not lines or any(not line for line in lines):
            fail("snapshot contains an empty line")
        previous = None
        protected = ${builtins.toJSON (builtins.fromJSON (builtins.readFile ./protected-domains.json))}
        for number, line in enumerate(lines, 1):
            if len(line) > MAX_DOMAIN_BYTES:
                fail("snapshot line " + str(number) + " is too long")
            labels = line.split(".")
            if any(name == line or name.endswith("." + line) for name in protected):
                fail("protected domain would be blocked")
            if len(labels) < 2:
                fail("snapshot line " + str(number) + " has fewer than two labels")
            for label in labels:
                if not 1 <= len(label) <= MAX_LABEL_BYTES:
                    fail("snapshot line " + str(number) + " has a bad label length")
                if label[0] == "-" or label[-1] == "-":
                    fail("snapshot line " + str(number) + " has a leading or trailing hyphen")
                for character in label:
                    if not ("a" <= character <= "z" or "0" <= character <= "9"
                            or character in "-_"):
                        fail("snapshot line " + str(number) + " has an invalid character")
            if previous is not None:
                if line == previous:
                    fail("snapshot line " + str(number) + " is a duplicate")
                if line < previous:
                    fail("snapshot line " + str(number) + " is out of order")
            previous = line
        if len(lines) != merged["count"]:
            fail("snapshot domain count does not match the manifest")

        try:
            header_text = read_file(header_path, MAX_HEADER_BYTES).decode("utf-8")
        except UnicodeDecodeError as exc:
            fail("header is not UTF-8: " + str(exc))
        begin = "# " + "BEGIN GENERATED"
        end = "# " + "END GENERATED"
        start = header_text.find(begin)
        stop = header_text.find(end, start + 1)
        if start < 0 or stop < 0:
            fail("header markers are missing")
        if header_text.find(begin, start + 1) >= 0 or header_text.find(end, stop + 1) >= 0:
            fail("header markers are duplicated")
        block = header_text[start:stop + len(end)]
        canonical = [begin]
        for source in sources:
            canonical.append("#   " + source["name"].ljust(12) + " "
                             + str(source["count"]).rjust(7) + "  " + source["url"])
        canonical.append("#   " + "total".ljust(12) + " "
                         + str(merged["count"]).rjust(7) + "  merged and de-duplicated")
        canonical.append("# Snapshot: " + generated)
        canonical.append("# Manifest: sha256 " + sha256(manifest_raw))
        canonical.append(end)
        if block != "\n".join(canonical):
            fail("generated header does not match the manifest")

        directory = os.path.dirname(rules_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(names_path, "wb") as handle:
            handle.write(payload)
        rule_text = "".join("address=/" + line + "/\n" for line in lines)
        with open(rules_path, "wb") as handle:
            handle.write(rule_text.encode("ascii"))
        report = {
            "schema": "lf-dns-blocklist-verification/1",
            "mode": "check",
            "status": "consistent",
            "ok": True,
            "data": {"count": len(lines), "sha256": sha256(payload)},
        }
        print(json.dumps(report, indent=2, sort_keys=True))


    main()
  '';

  dataExists = builtins.pathExists data;
  manifestExists = builtins.pathExists manifest;

  dataSrc =
    if dataExists then data
    else throw ''
      local-data/dns-blocklist.txt.xz is missing.
      Run ./scripts/refresh-dns-blocklist.py from the repository root, or copy
      local-data/ next to this flake (scripts/push-to-dir.sh does that for
      /etc/nixos). See local-data/README.md.
    '';

  manifestSrc =
    if manifestExists then manifest
    else throw ''
      local-data/dns-blocklist.manifest.json is missing.
      Run ./scripts/refresh-dns-blocklist.py --offline from the repository root
      to derive it from the committed snapshot.
    '';

  verified = pkgs.runCommand "dns-blocklist-verified" { } ''
    mkdir -p $out/rules
    PYTHONDONTWRITEBYTECODE=1 ${pkgs.python3}/bin/python3 ${validator} \
      ${dataSrc} \
      ${manifestSrc} \
      ${./blocklist.nix} \
      $out/names.txt \
      $out/rules/blocklist.conf \
      > $out/verification.json
  '';

  names = pkgs.runCommand "dns-blocklist.txt" { } ''
    cp ${verified}/names.txt $out
  '';

  rules = pkgs.runCommand "dnsmasq-dns-blocklist.d" { } ''
    mkdir -p $out
    cp ${verified}/rules/blocklist.conf $out/blocklist.conf
  '';

in
{
  config.my.proxy.dnsBlocklistFile = names;

  # conf-dir is read as configuration, so the generated file is just a batch of
  # address= directives, the same form the hand-written list uses.
  config.services.dnsmasq.settings.conf-dir = "${rules}";
}
