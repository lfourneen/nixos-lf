#!/usr/bin/env python3
"""Run component checks against recorded or frozen inputs and archive evidence."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


INPUT_DIRS = ("home", "system", "tests", "overlays", "scripts", "maintenance", "local-data")
INPUT_FILES = ("flake.nix", "flake.lock", ".sops.yaml")
EPHEMERAL_DIRS = {"__pycache__", ".direnv", ".git"}
EPHEMERAL_NAMES = {"result", ".DS_Store"}
EPHEMERAL_SUFFIXES = (".pyc", ".pyo", ".swp", "~", ".bak")
CHECK_SYSTEM = "x86_64-linux"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture(args, cwd):
    try:
        result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def git_output(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def skip_ephemeral(name, is_dir):
    if is_dir:
        return name in EPHEMERAL_DIRS
    return (name in EPHEMERAL_NAMES or name.startswith("result-")
            or name.endswith(EPHEMERAL_SUFFIXES))


def file_manifest(root):
    """Return a deterministic content manifest of all flake build inputs."""
    entries = []
    for dirname in INPUT_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames
                                 if not skip_ephemeral(name, True))
            for name in dirnames[:]:
                path = Path(dirpath) / name
                if path.is_symlink():
                    entries.append({"path": path.relative_to(root).as_posix(),
                                    "type": "symlink", "target": os.readlink(path)})
                    dirnames.remove(name)
            for name in sorted(filenames):
                if skip_ephemeral(name, False):
                    continue
                path = Path(dirpath) / name
                rel = path.relative_to(root).as_posix()
                if path.is_symlink():
                    entries.append({"path": rel, "type": "symlink",
                                    "target": os.readlink(path)})
                elif path.is_file():
                    stat = path.stat()
                    entries.append({
                        "path": rel,
                        "type": "file",
                        "size": stat.st_size,
                        "sha256": sha256_file(path),
                        "executable": bool(stat.st_mode & 0o111),
                    })
                else:
                    entries.append({"path": rel, "type": "other"})
    for name in INPUT_FILES:
        path = root / name
        if path.is_symlink():
            entries.append({"path": name, "type": "symlink",
                            "target": os.readlink(path)})
        elif path.is_file():
            stat = path.stat()
            entries.append({"path": name, "type": "file", "size": stat.st_size,
                            "sha256": sha256_file(path),
                            "executable": bool(stat.st_mode & 0o111)})
        else:
            entries.append({"path": name, "type": "missing"})
    entries.sort(key=lambda entry: entry["path"])
    return entries


def manifest_sha256(entries):
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(payload)


def collect_inputs(root):
    commit = capture(["git", "rev-parse", "HEAD"], root)
    diff = git_output(root, "diff", "HEAD", "--binary")
    entries = file_manifest(root)
    manifest_hash = manifest_sha256(entries)
    tracked = {name for name in git_output(root, "ls-files", "-z")
               .decode("utf-8", "surrogateescape").split("\0") if name}
    untracked = sorted(entry["path"] for entry in entries
                       if entry["type"] != "missing" and entry["path"] not in tracked)
    lock = root / "flake.lock"
    lock_hash = sha256_file(lock) if lock.is_file() else None
    diff_hash = sha256_bytes(diff)
    input_hash = sha256_bytes("\0".join([
        commit or "", diff_hash, manifest_hash, lock_hash or "",
    ]).encode())
    return {
        "commit": commit,
        "diff_sha256": diff_hash,
        "manifest_sha256": manifest_hash,
        "lock_sha256": lock_hash,
        "input_sha256": input_hash,
        "untracked_build_inputs": untracked,
        "files": entries,
    }


def input_view(inputs):
    return {key: inputs[key] for key in
            ("commit", "diff_sha256", "manifest_sha256", "lock_sha256", "input_sha256")}


def live_identity(inputs):
    return {"kind": "live", **input_view(inputs)}


def frozen_identity(root):
    manifest_hash = manifest_sha256(file_manifest(root))
    return {"kind": "frozen", "manifest_sha256": manifest_hash,
            "input_sha256": manifest_hash}


def export_frozen(root, inputs, destination):
    """Materialize the recorded input as HEAD + diff + untracked build inputs."""
    archive = git_output(root, "archive", "--format=tar", "HEAD")
    subprocess.run(["tar", "-xf", "-", "-C", str(destination)],
                   input=archive, check=True)
    diff = git_output(root, "diff", "HEAD", "--binary")
    if diff:
        result = subprocess.run(
            ["git", "apply", "--binary", "--whitespace=nowarn", "-"],
            cwd=destination, input=diff, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError("git apply failed: "
                               + result.stderr.decode("utf-8", "replace").strip())
    tracked = {name for name in git_output(root, "ls-files", "-z")
               .decode("utf-8", "surrogateescape").split("\0") if name}
    for entry in inputs["files"]:
        rel = entry["path"]
        if entry["type"] == "missing" or rel in tracked:
            continue
        source = root / rel
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry["type"] == "symlink":
            os.symlink(entry["target"], target)
        elif entry["type"] == "file":
            shutil.copy2(source, target)
        else:
            raise RuntimeError("cannot export untracked input: " + rel)
    exported = file_manifest(destination)
    if exported != inputs["files"]:
        expected = {entry["path"]: entry for entry in inputs["files"]}
        actual = {entry["path"]: entry for entry in exported}
        differing = sorted(set(expected) ^ set(actual)
                           | {name for name in set(expected) & set(actual)
                              if expected[name] != actual[name]})
        raise RuntimeError("frozen input does not match the recorded manifest: "
                           + ", ".join(differing[:10]))


def write_private(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    os.chmod(path, 0o600)


def write_json(path, data):
    write_private(path, (json.dumps(data, indent=2) + "\n").encode())


def check_metadata(root, check, offline):
    expression = ".#checks.%s.%s" % (CHECK_SYSTEM, check)
    raw = capture(["nix", "eval", *offline, "--no-write-lock-file", "--json",
                   expression, "--apply",
                   "d: { drv = d.drvPath; out = d.outPath; }"], root)
    if not raw:
        return {}
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    output = info.get("out")
    valid = None
    if output:
        try:
            valid = subprocess.run(["nix", "path-info", output], cwd=root,
                                   capture_output=True, check=False).returncode == 0
        except OSError:
            valid = None
    return {
        "derivation": info.get("drv"),
        "output_path": output,
        "output_was_valid_before": valid,
    }


def runtime_verification(runtime=Path("/run"), proc=Path("/proc"), maximum=330):
    """Require a fresh successful verdict for the active boot and policy."""
    try:
        verdict = json.loads((runtime / "netsec/state.json").read_text())
        policy = json.loads((runtime / "proxy-mode/state.json").read_text())
        boot = (proc / "sys/kernel/random/boot_id").read_text().strip()
        now = float((proc / "uptime").read_text().split()[0])
        stamp = verdict.get("monotonic_seconds")
        version = policy.get("policy_version", policy.get("version"))
        reasons = []
        if verdict.get("result") != "ok":
            reasons.append("nftables result is not ok")
        if type(version) is not int or type(verdict.get("version")) is not int or verdict.get("version") != version:
            reasons.append("policy generation mismatch")
        if verdict.get("boot_id") != boot:
            reasons.append("boot mismatch")
        if not isinstance(stamp, (int, float)) or not 0 <= now - stamp <= maximum:
            reasons.append("stale verification")
        return {"ok": not reasons, "reasons": reasons, "verdict": verdict,
                "policy_version": version}
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return {"ok": False, "reasons": ["runtime evidence unavailable: " + str(error)]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--component", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--allow-untracked", action="store_true",
                        help="include untracked build inputs and mark them accepted")
    parser.add_argument("--frozen", action="store_true",
                        help="copy the exact input to a private temporary directory")
    parser.add_argument("--runtime-verification", action="store_true",
                        help="also require fresh nftables JSON evidence from this host")
    args = parser.parse_args()
    os.umask(0o077)

    root = Path(__file__).resolve().parents[1]
    inventory = json.loads(
        (root / "system/config/network/maintenance/sources.json").read_text()
    )["components"]
    selected = sorted(inventory) if args.all else args.component
    if not selected or any(name not in inventory for name in selected):
        parser.error("choose --all or --component from: " + ", ".join(sorted(inventory)))
    checks = sorted({check for name in selected for check in inventory[name]["checks"]})
    if args.plan:
        print(json.dumps({"components": selected, "checks": checks}, indent=2))
        return 0

    archive = args.archive or root / "audit/validation" / time.strftime("%Y%m%d-%H%M%S")
    archive.mkdir(parents=True, exist_ok=False)
    os.chmod(archive, 0o700)
    results_path = archive / "results.json"

    try:
        inputs = collect_inputs(root)
    except (OSError, RuntimeError) as error:
        write_json(results_path, {"schema": 2, "status": "input-error",
                                  "error": str(error), "components": selected,
                                  "checks": [{"name": name, "status": "not-run"}
                                             for name in checks]})
        print(str(results_path), flush=True)
        return 2

    manifest = {
        "schema": 2,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        **input_view(inputs),
        "untracked_build_inputs": inputs["untracked_build_inputs"],
        "untracked_build_inputs_accepted": bool(args.allow_untracked),
        "frozen": bool(args.frozen),
        "host_kernel": capture(["uname", "-r"], root),
        "nix": capture(["nix", "--version"], root),
        "components": selected,
        "checks": [{"name": name, "status": "not-run"} for name in checks],
        "input_changed": False,
    }
    lock = root / "flake.lock"
    if lock.is_file():
        write_private(archive / "flake.lock", lock.read_bytes())
    write_json(results_path, manifest)

    if inputs["untracked_build_inputs"] and not args.allow_untracked:
        manifest.update({
            "status": "rejected",
            "error": "untracked build inputs are part of the flake input; add them "
                     "to Git or pass --allow-untracked",
        })
        write_json(results_path, manifest)
        print("untracked build inputs: " + ", ".join(inputs["untracked_build_inputs"]),
              file=sys.stderr)
        print(str(results_path), flush=True)
        return 2

    frozen_dir = None
    run_root = root
    if args.frozen:
        frozen_root = Path(tempfile.mkdtemp(prefix="nixos-lf-frozen-"))
        frozen_root.chmod(0o700)
        try:
            export_frozen(root, inputs, frozen_root)
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            shutil.rmtree(frozen_root, ignore_errors=True)
            manifest.update({"status": "frozen-export-failed", "error": str(error)})
            write_json(results_path, manifest)
            print(str(results_path), flush=True)
            return 2
        frozen_dir = frozen_root
        run_root = frozen_root
        manifest["frozen_input"] = frozen_identity(frozen_root)
        baseline_identity = frozen_identity(frozen_root)
    else:
        baseline_identity = live_identity(inputs)

    offline = [] if args.online else ["--offline"]
    packages = capture(
        ["nix", "eval", *offline, "--no-write-lock-file", "--json",
         ".#nixosConfigurations.lfour.config",
         "--apply", "c: { kernel = c.boot.kernelPackages.kernel.version; "
                     "mihomo = c.programs.clash-verge.package.managedCore.version; "
                     "gost = c.my.proxy.gostPackage.version; "
                     "unbound = c.services.unbound.package.version; }"],
        run_root)
    try:
        manifest["packages"] = json.loads(packages) if packages else {"status": "unavailable"}
    except json.JSONDecodeError:
        manifest["packages"] = {"status": "unavailable"}
    write_json(results_path, manifest)

    failure = None
    try:
        for record in manifest["checks"]:
            check = record["name"]
            print("Checking " + check, flush=True)
            before = (frozen_identity(run_root) if frozen_dir
                      else live_identity(collect_inputs(root)))
            record["input_before_sha256"] = before["input_sha256"]
            if before != baseline_identity:
                record.update(status="input-changed",
                              error="input changed before the check")
                manifest["input_changed"] = True
                write_json(results_path, manifest)
                break

            metadata = check_metadata(run_root, check, offline)
            record.update(metadata)
            start = time.monotonic()
            with (archive / (check + ".log")).open("wb") as log:
                try:
                    result = subprocess.run(
                        ["nix", "build", "-L", *offline, "--no-write-lock-file",
                         "--no-link", "--json",
                         ".#checks.%s.%s" % (CHECK_SYSTEM, check)],
                        cwd=run_root, stdout=subprocess.PIPE, stderr=log,
                        timeout=args.timeout, check=False)
                    record["exit_code"] = result.returncode
                    record["status"] = "passed" if result.returncode == 0 else "failed"
                    if result.returncode == 0:
                        record["outputs"] = json.loads(result.stdout) if result.stdout else []
                except FileNotFoundError:
                    record.update(status="environment-unavailable", error="nix-missing")
                except subprocess.TimeoutExpired:
                    record.update(status="failed", error="timeout")
            record["seconds"] = round(time.monotonic() - start, 3)
            if metadata.get("output_was_valid_before") is True:
                record["build_kind"] = "already-valid"
            elif record.get("exit_code") == 0:
                record["build_kind"] = "built"
            else:
                record["build_kind"] = "unknown"

            after = (frozen_identity(run_root) if frozen_dir
                     else live_identity(collect_inputs(root)))
            record["input_after_sha256"] = after["input_sha256"]
            if after != baseline_identity or after != before:
                record.update(status="input-changed",
                              error="input changed during the check")
                manifest["input_changed"] = True
                write_json(results_path, manifest)
                break
            write_json(results_path, manifest)

        final = (frozen_identity(run_root) if frozen_dir
                 else live_identity(collect_inputs(root)))
        manifest["final_input_sha256"] = final["input_sha256"]
        if final != baseline_identity:
            manifest["input_changed"] = True
        if frozen_dir:
            try:
                source_after = collect_inputs(root)
                manifest["source_input_changed"] = (
                    source_after["input_sha256"] != inputs["input_sha256"])
            except (OSError, RuntimeError):
                manifest["source_input_changed"] = None
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        failure = error
        manifest["status"] = "error"
        manifest["error"] = str(error)
        write_json(results_path, manifest)
    finally:
        if frozen_dir is not None:
            shutil.rmtree(frozen_dir, ignore_errors=True)

    manifest["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if args.runtime_verification:
        manifest["runtime_verification"] = runtime_verification()
    write_json(results_path, manifest)
    print(str(results_path), flush=True)
    passed = (failure is None and not manifest["input_changed"]
              and manifest.get("runtime_verification", {"ok": True})["ok"]
              and all(record.get("status") == "passed" for record in manifest["checks"]))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
