#!/usr/bin/env python3
"""Check that home-guard default paths follow a non-default my.homeGuard.home."""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CUSTOM_HOME = "/srv/home-guard-audit"
NIX_EXPR = """
let
  repo = builtins.getEnv "DSH_REPO";
  flake = builtins.getFlake ("path:" + repo);
  base = flake.nixosConfigurations.lfour;
  custom = (base.extendModules {
    modules = [ ({ lib, ... }: { my.homeGuard.home = lib.mkForce "%s"; }) ];
  }).config;
  moved = (base.extendModules {
    modules = [ ({ lib, ... }: {
      users.users.${base.config.my.homeGuard.user}.home = lib.mkForce "/srv/account-home";
    }) ];
  }).config;
in {
  accountHome = base.config.users.users.${base.config.my.homeGuard.user}.home;
  defaultHome = base.config.my.homeGuard.home;
  defaultPaths = base.config.my.homeGuard.paths;
  customHome = custom.my.homeGuard.home;
  customPaths = custom.my.homeGuard.paths;
  movedHome = moved.my.homeGuard.home;
  movedPaths = moved.my.homeGuard.paths;
}
""" % CUSTOM_HOME


class TestFailure(Exception):
    pass


def check(condition, message):
    if not condition:
        raise TestFailure(message)


def eval_config():
    env = os.environ.copy()
    cache = REPO / ".nix-cache" / "xdg"
    cache.mkdir(parents=True, exist_ok=True)
    env["XDG_CACHE_HOME"] = str(cache)
    env["DSH_REPO"] = str(REPO)
    proc = subprocess.run(
        ["nix", "eval", "--json", "--impure", "--expr", NIX_EXPR],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise TestFailure(f"nix eval failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def main():
    data = eval_config()

    default_home = data["defaultHome"]
    custom_home = data["customHome"]
    default_paths = data["defaultPaths"]
    custom_paths = data["customPaths"]

    check(default_home == data["accountHome"], f"unexpected default home: {default_home!r}")
    check(custom_home == CUSTOM_HOME, f"custom home was not applied: {custom_home!r}")
    check(default_paths, "default path list is empty")
    check(custom_paths, "custom path list is empty")
    check(len(default_paths) == len(custom_paths), "path count changed with the home value")
    check(data["movedHome"] == "/srv/account-home", "home guard ignored the account home")
    check(all(path.startswith(data["movedHome"] + "/") for path in data["movedPaths"]),
          "protected paths ignored the moved account home")

    for path in default_paths:
        check(path.startswith(default_home + "/"), f"default path ignores default home: {path}")
    for path in custom_paths:
        check(path.startswith(custom_home + "/"), f"default path ignores custom home: {path}")
        check(not path.startswith(default_home + "/"), f"default path still uses the hardcoded home: {path}")

    check(any(path.endswith("/.ssh/id_*") for path in custom_paths), "ssh key glob is missing")
    check(any(path.endswith("/.ssh/authorized_keys") for path in custom_paths), "authorized_keys is missing")
    check(
        any(path.endswith("/.config/sops/age/keys.txt") for path in custom_paths),
        "sops age key path is missing",
    )
    print(f"PASS default paths start with {default_home}")
    print(f"PASS custom home {custom_home}: {len(custom_paths)} default paths follow it")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TestFailure as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
