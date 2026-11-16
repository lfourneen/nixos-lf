#!/usr/bin/env python3
"""Offline checks for the DSH Desktop launcher profiles.

The launcher is built from the overlay passthru, then its --print-command
output is parsed. No sandbox is entered, no GUI is started and no secret is
read.
"""

import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALLABLE = ".#nixosConfigurations.lfour.pkgs.dsh-desktop.passthru.launcher"

ALLOWED_ENV = {
    "USER",
    "LOGNAME",
    "SHELL",
    "PATH",
    "TERM",
    "LANG",
    "TZ",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
    "DBUS_SESSION_BUS_ADDRESS",
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "TMPDIR",
    "http_proxy",
    "https_proxy",
    "ftp_proxy",
    "all_proxy",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "FTP_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "GTK_IM_MODULE",
    "QT_IM_MODULE",
    "XMODIFIERS",
    "SDL_IM_MODULE",
    "GLFW_IM_MODULE",
    "INPUT_METHOD",
    "IBUS_ADDRESS",
    "PIPEWIRE_REMOTE",
    "PULSE_SERVER",
}

FORBIDDEN_ENV = {
    "SSH_AUTH_SOCK",
    "DOCKER_HOST",
    "SOPS_AGE_KEY_FILE",
    "KUBECONFIG",
    "AWS_PROFILE",
    "AWS_SECRET_ACCESS_KEY",
    "GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "MY_SECRET",
}

SECRET_VALUES = {
    "secret-ssh-agent-value",
    "secret-docker-value",
    "secret-sops-value",
    "secret-kube-value",
    "secret-aws-profile-value",
    "secret-aws-key-value",
    "secret-github-value",
    "secret-openai-value",
    "secret-generic-value",
}

VALUE_OPTIONS = {
    "--ro",
    "--rox",
    "--rw",
    "--rwx",
    "--env",
    "--bind-tcp",
    "--connect-tcp",
}


class TestFailure(Exception):
    pass


def check(condition, message):
    if not condition:
        raise TestFailure(message)


def within(path, root):
    try:
        return Path(path).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False


def nix_env():
    env = os.environ.copy()
    cache = REPO / ".nix-cache" / "xdg"
    cache.mkdir(parents=True, exist_ok=True)
    env["XDG_CACHE_HOME"] = str(cache)
    return env


def build_launcher():
    proc = subprocess.run(
        ["nix", "build", "--no-link", "--print-out-paths", INSTALLABLE],
        cwd=REPO,
        env=nix_env(),
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise TestFailure(f"nix build failed:\n{proc.stderr}")
    paths = [line for line in proc.stdout.splitlines() if line.startswith("/nix/store/")]
    check(len(paths) == 1, f"expected one launcher store path, got {paths!r}")
    launcher = Path(paths[0])
    check(launcher.is_file(), f"launcher is not a file: {launcher}")
    return launcher


def parse(argv):
    options = []
    command = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            command = argv[index + 1 :]
            break
        if token in VALUE_OPTIONS:
            check(index + 1 < len(argv), f"missing value for {token}")
            options.append((token, argv[index + 1]))
            index += 2
            continue
        options.append((token, None))
        index += 1
    return options, command


class Harness:
    def __init__(self, launcher, root):
        self.launcher = launcher
        self.root = root
        self.home = root / "home"
        self.workspace = root / "workspace"
        self.state = root / "state"
        self.runtime = root / "runtime"
        self.wayland = "wayland-audit"
        for path in (
            self.home / ".config",
            self.home / ".cache",
            self.home / ".local" / "state",
            self.home / ".ssh",
            self.home / "Projects",
            self.home / "Downloads",
            self.workspace,
            self.runtime,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.env = {
            "PATH": f"{self.home}/.nix-profile/bin:/run/current-system/sw/bin:/usr/bin:/bin",
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.state),
            "XDG_RUNTIME_DIR": str(self.runtime),
            "USER": "audit-user",
            "LOGNAME": "audit-user",
            "SHELL": "/bin/bash",
            "TERM": "xterm-256color",
            "TZ": "UTC",
            "LANG": "C.UTF-8",
            "LC_MESSAGES": "C.UTF-8",
            "DISPLAY": ":7",
            "WAYLAND_DISPLAY": self.wayland,
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "niri",
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.runtime}/bus",
            "http_proxy": "http://proxy.test:3128",
            "HTTPS_PROXY": "http://proxy.test:3128",
            "GTK_IM_MODULE": "fcitx",
            "XMODIFIERS": "@im=fcitx",
            "SSH_AUTH_SOCK": str(self.root / "agent.sock"),
            "DOCKER_HOST": "secret-docker-value",
            "SOPS_AGE_KEY_FILE": "secret-sops-value",
            "KUBECONFIG": "secret-kube-value",
            "GITHUB_TOKEN": "secret-github-value",
            "OPENAI_API_KEY": "secret-openai-value",
            "MY_SECRET": "secret-generic-value",
            "AWS_PROFILE": "secret-aws-profile-value",
            "AWS_SECRET_ACCESS_KEY": "secret-aws-key-value",
        }

    def run(self, args, cwd=None, extra_env=None):
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(self.launcher), *args],
            cwd=cwd or self.workspace,
            env=env,
            text=True,
            capture_output=True,
        )

    def dry_run(self, profile_args, cwd=None, extra_env=None):
        proc = self.run([*profile_args, "--print-command"], cwd=cwd, extra_env=extra_env)
        check(proc.returncode == 0, f"dry run failed: {proc.stderr.strip()}")
        check(proc.stdout.strip(), "dry run printed no argv")
        options, command = parse(shlex.split(proc.stdout.strip()))
        return proc.stdout, options, command


def option_values(options, name):
    return [value for key, value in options if key == name]


def option_keys(options):
    return {key for key, _ in options}


def env_map(options):
    result = {}
    for value in option_values(options, "--env"):
        name, _, setting = value.partition("=")
        result[name] = setting if _ else None
    return result


def assert_strict(harness):
    before = harness.state / "dsh-desktop"
    output, options, command = harness.dry_run(["--profile", "strict"])
    check(not before.exists(), "--print-command created the sandbox state directory")
    dry_env = harness.run(["--profile", "strict"], extra_env={"DSH_DRY_RUN": "1"})
    check(dry_env.returncode == 0, f"DSH_DRY_RUN failed: {dry_env.stderr.strip()}")
    check(dry_env.stdout == output, "DSH_DRY_RUN and --print-command print different argv")
    check("--print-command" not in dry_env.stdout, "--print-command leaked into the app argv")

    ro = option_values(options, "--ro")
    rox = option_values(options, "--rox")
    rw = option_values(options, "--rw")
    keys = option_keys(options)

    check("/" not in ro + rox + rw, "strict grants the filesystem root")
    check("/" not in rox, "strict uses --rox /")
    check("--unrestricted-filesystem" not in keys, "strict uses --unrestricted-filesystem")
    check("--best-effort" not in keys, "strict uses --best-effort")
    check("--no-sandbox" not in command, "strict passes --no-sandbox to the app")

    envs = env_map(options)
    check(envs.get("HOME") is not None, "strict does not set HOME")
    state_dir = Path(envs["HOME"]).parent
    check(state_dir.name == "strict", f"unexpected synthetic state directory {state_dir}")
    check(Path(envs["HOME"]) == state_dir / "home", "HOME is not under the state directory")
    check(envs.get("XDG_RUNTIME_DIR") == str(state_dir / "run"), "XDG_RUNTIME_DIR is not private")
    check(envs.get("TMPDIR") == str(state_dir / "tmp"), "TMPDIR is not private")
    check(envs.get("PATH") == f"{state_dir}/home/.local/bin:/run/current-system/sw/bin",
          f"strict PATH is not restricted: {envs.get('PATH')!r}")
    check(envs.get("WAYLAND_DISPLAY") == str(harness.runtime / harness.wayland),
          "Wayland display was not made absolute")
    check(envs.get("DISPLAY") == ":7", "DISPLAY was not forwarded")
    check("DBUS_SESSION_BUS_ADDRESS" not in envs, "strict forwards the session bus")
    check(envs.get("http_proxy") == "http://proxy.test:3128", "proxy settings were not forwarded")

    for name in FORBIDDEN_ENV:
        check(name not in envs, f"strict passes {name}")
    for name in envs:
        check(name in ALLOWED_ENV or name.startswith("LC_"), f"strict passes non-allowlisted {name}")
        check(not name.startswith("AWS_"), f"strict passes {name}")
        check(not re.search(r"_(TOKEN|API_KEY|SECRET)$", name), f"strict passes {name}")
    for secret in SECRET_VALUES:
        check(secret not in output, f"strict argv contains {secret}")

    check(str(harness.workspace) in rw, "workspace is not writable")
    check(str(state_dir) in rw, "sandbox state directory is not writable")
    for path in rw:
        if path == "/proc" or path.startswith("/dev/"):
            continue
        check(
            within(path, harness.workspace) or within(path, state_dir),
            f"strict grants writable access outside the workspace and state: {path}",
        )
    check("/tmp" not in rw, "strict grants all of /tmp")
    check("/dev" not in rw, "strict grants all of /dev")
    for path in rw:
        check(not path.startswith("/run/user"), f"strict writes under /run/user: {path}")
        check(not within(path, harness.home), f"strict writes the real home: {path}")

    for path in ro + rox:
        check(path != "/", "strict reads the filesystem root")
        check(not path.startswith("/home"), f"strict reads the real home: {path}")
        check(not path.startswith("/run/user"), f"strict reads the user runtime dir: {path}")
        check(
            path.startswith(
                ("/nix/store", "/run/current-system", "/etc", "/sys", "/dev",
                 "/.host-etc", "/run/opengl-driver", "/usr/share")
            ),
            f"unexpected strict read root: {path}",
        )
    check("/nix/store" in rox, "strict does not allow executing from the nix store")
    check("/run/current-system" in rox, "strict does not allow the current system profile")
    return ro, rox, rw, envs


def assert_compat(harness):
    output, options, command = harness.dry_run([])
    keys = option_keys(options)
    rox = option_values(options, "--rox")
    rw = option_values(options, "--rw")
    envs = env_map(options)

    check("/" in rox, "compat no longer uses --rox /")
    check("--unrestricted-network" in keys, "compat no longer allows the network")
    check("/dev" in rw, "compat no longer writes /dev")
    check("/proc" in rw, "compat no longer writes /proc")
    check(str(harness.home / ".config") in rw, "compat no longer writes the home config")
    check(any(path.startswith("/run/user/") for path in rw), "compat no longer writes the user runtime dir")
    for name in ("SSH_AUTH_SOCK", "DOCKER_HOST", "KUBECONFIG", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY"):
        check(name in envs, f"compat no longer forwards {name}")
    check(envs["HOME"] is None, "compat unexpectedly overrides HOME")
    check(command and command[0].endswith("dsh-desktop-next"), "compat command is malformed")


    _, default_options, _ = harness.dry_run([], cwd=harness.workspace)
    check(
        option_values(default_options, "--rox") == rox,
        "the default profile differs from compat",
    )


def assert_fail_closed(harness):
    cases = [
        (["--profile", "strict"], {"DSH_NO_LANDLOCK": "1"}, "DSH_NO_LANDLOCK"),
        (["--profile", "strict"], {"DSH_EXTRA_RW": "/tmp"}, "DSH_EXTRA_RW"),
        (["--profile", "strict", "--print-command", "--no-sandbox"], {}, "--no-sandbox"),
        (["--profile", "strict", "--print-command"], {}, "project directory"),
    ]
    for index, (args, extra_env, needle) in enumerate(cases):
        cwd = harness.home if index == 3 else harness.workspace
        proc = harness.run(args, cwd=cwd, extra_env=extra_env)
        check(proc.returncode != 0, f"strict accepted {args} with {extra_env}")
        check(needle in proc.stderr, f"strict failed without mentioning {needle}: {proc.stderr!r}")
    check(not (harness.state / "dsh-desktop").exists(),
          "strict fail-closed paths created the sandbox state directory")


def main():
    launcher = build_launcher()
    with tempfile.TemporaryDirectory(prefix="dsh-sandbox-test-") as tmp:
        harness = Harness(launcher, Path(tmp))
        assert_strict(harness)
        print("PASS strict argv: workspace/state writes only, allowlisted env, no root or runtime rules")
        assert_compat(harness)
        print("PASS compat argv: --rox /, /dev and runtime writes, full environment forwarding")
        assert_fail_closed(harness)
        print("PASS strict fail-closed: DSH_NO_LANDLOCK, DSH_EXTRA_RW, --no-sandbox, home cwd")
    print("PASS all DSH sandbox launcher checks")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TestFailure as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
