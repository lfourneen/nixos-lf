"""Native Firefox entry point, sharing the system's network readiness contract.

Direct mode admits offline portal login; proxy mode requires current enforcement.
The relay and kernel, rather than this startup check, enforce later transitions.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import ipaddress
from urllib.parse import urlsplit


def verify_core(settings, readiness):
    profiles = settings["healthSettings"]
    if "default" in profiles:
        path = profiles["default"]
    else:
        mode = readiness.get("routing_applied")
        epoch = readiness.get("routing_epoch")
        if (mode not in profiles or readiness.get("routing_requested") != mode
                or readiness.get("routing_consistent") is not True
                or readiness.get("routing_phase") != "ready" or type(epoch) is not int or epoch < 1):
            raise ValueError("current routing policy is not verified")
        path = profiles[mode]
        expected = json.loads(Path(path).read_text())["expected"]["managed-policy"]
        if expected != readiness.get("routing_policy_digest"):
            raise ValueError("current routing policy digest differs from the browser configuration")
    result = subprocess.run(
        [sys.executable, "-I", settings["healthModule"], path, "--fast"],
        capture_output=True, text=True, timeout=9, check=False)
    try:
        verdict = json.loads(result.stdout)
    except ValueError:
        raise ValueError("current core verification is unavailable") from None
    if result.returncode or not isinstance(verdict, dict) or not verdict.get("ok"):
        detail = (verdict.get("detail") or verdict.get("reason")) if isinstance(verdict, dict) else None
        raise ValueError("current core policy is not verified" + (": " + str(detail) if detail else ""))


def environment(settings):
    env = dict(os.environ)
    # Never inherit debugging switches that turn off Gecko's process sandboxes.
    for name in list(env):
        if name.startswith("MOZ_DISABLE_") and "SANDBOX" in name:
            env.pop(name)
    env.update(MOZ_ENABLE_WAYLAND="1", GDK_BACKEND="wayland")
    if settings["strictPrivacy"]:
        env.update(FONTCONFIG_FILE=settings["fonts"], FONTCONFIG_PATH="/nonexistent")
        env.pop("FONTCONFIG_SYSROOT", None)
    return env


def verify_fonts(settings):
    if not settings["strictPrivacy"]:
        return
    result = subprocess.run(
        [settings["fontList"], "--format", "%{file}\n"], env=environment(settings),
        capture_output=True, text=True, timeout=15, check=False)
    paths = result.stdout.splitlines()
    if result.returncode or "Fontconfig error:" in result.stderr or not paths or any(
        not any(path.startswith(directory + "/") for directory in settings["fontDirectories"])
        for path in paths
    ):
        raise ValueError("managed font inventory is unavailable or contains personal fonts")


def arguments(values):
    # Keep profile selection and automation under this entry point's control.
    # In particular, a URL opener cannot supply -profile or --marionette.
    result = []
    iterator = iter(values)
    for value in iterator:
        if value in {"--name", "-name"}:
            if next(iterator, None) != "firefox":
                raise ValueError("the managed application name is firefox")
        elif value in {"--new-window", "--private-window", "--new-tab", "--search"}:
            result.append(value)
        elif value.startswith("-"):
            raise ValueError("unsupported managed browser option: " + value)
        else:
            result.append(value)
    return result


def verify_running_generation(profile, binary):
    # Gecko's Linux lock symlink records the owning PID. Refuse remoting into
    # an older package: checking new files cannot update a running process.
    lock = Path(profile) / "lock"
    try:
        target = os.readlink(lock)
    except FileNotFoundError:
        return
    match = re.search(r":\+?(\d+)$", target)
    if not match:
        raise ValueError("unrecognized Firefox profile lock")
    executable = Path("/proc") / match[1] / "exe"
    try:
        current = executable.resolve(strict=True)
    except FileNotFoundError:
        return  # stale lock; Firefox performs its own exclusive locking
    package = Path(binary).parent.parent
    if current not in {package / "lib/firefox/firefox", package / "lib/firefox/firefox-bin"}:
        raise ValueError("close the existing Firefox process to load the current Nix package")


def outer_sandbox(settings):
    spec = importlib.util.spec_from_file_location("firefox_sandbox", settings["sandboxModule"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(settings, binary, values=(), check=None, check_core=None, check_fonts=None,
            check_only=False):
    values = arguments(values)
    portal = settings.get("portalOnly")
    if check is None or portal:
        spec = importlib.util.spec_from_file_location("netsec_state", settings["stateModule"])
        state = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(state)
    if check is None and not portal:
        check = state.readiness
    freshness = dict(maximum=settings["maxAge"], verification_maximum=settings["verificationMaxAge"])
    if portal:
        grant = state.portal_authority(**freshness)
        if grant is None:
            raise ValueError("the portal browser is available only in verified explicit Mode A")
    else:
        verdict = check(target="proxy", **freshness)
        mode = verdict.get("mode", "unknown")
        if settings.get("requireProxy") and mode != "proxy":
            raise ValueError("this browser requires healthy proxy Mode B")
        if mode not in {"direct", "proxy"}:
            raise ValueError("network policy is " + mode + "; use clash-on/clash-off")
        if mode == "proxy":
            if not verdict.get("ready"):
                raise ValueError("proxy is not ready: " + "; ".join(verdict["reasons"]))
            (verify_core if check_core is None else check_core)(settings, verdict)
    (verify_fonts if check_fonts is None else check_fonts)(settings)
    # A sandbox's profile lock contains a namespace PID, not a desktop PID.
    # Its host supervisor records package identity outside the exposed mounts.
    if "sandboxModule" in settings:
        outer_sandbox(settings).check_generation(settings, binary)
    else:
        verify_running_generation(settings["profile"], binary)
    if portal:
        if state.portal_authority(**freshness) != grant:
            raise ValueError("portal authorization changed during browser launch")
        settings["portalAuthority"] = grant
    else:
        current = check(target="proxy", **freshness)
        if (current.get("mode") != mode or current.get("policy_version") != verdict.get("policy_version")
                or mode == "proxy" and (not current.get("ready") or any(
                    current.get(key) != verdict.get(key) for key in (
                        "routing_applied", "routing_epoch", "routing_policy_digest", "egress_requested", "egress_epoch")))):
            raise ValueError("network state changed during browser launch; retry")
    if check_only:
        return []
    profile = Path(settings["profile"])
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    profile.chmod(0o700)
    return [binary, "--profile", str(profile), *values]


def main():
    try:
        if os.geteuid() == 0:
            raise ValueError("run Firefox as the desktop user")
        settings = json.loads(Path(sys.argv[1]).read_text())
        check_only = sys.argv[3:] == ["--check"]
        temporary = None
        if settings.get('ephemeral'):
            if len(sys.argv[3:]) != 1:
                raise ValueError('portal browser requires one explicit literal-IP HTTP(S) URL')
            target = urlsplit(sys.argv[3])
            if target.scheme not in ('http', 'https') or target.username or target.password or target.port not in (None, 80, 443):
                raise ValueError('portal requires a plain HTTP(S) address on port 80/443')
            address = str(ipaddress.ip_address(target.hostname))
            temporary = tempfile.TemporaryDirectory(prefix='firefox-portal-', dir=settings['runtime'])
            settings = dict(settings, launchSettings=sys.argv[1],
                            profile=temporary.name + '/profile', cache=temporary.name + '/cache',
                            downloads=temporary.name + '/downloads', lifetime=900, portalAddress=address)
        try:
            command = prepare(settings, sys.argv[2], () if check_only else sys.argv[3:],
                              check_only=check_only)
            if check_only:
                outer_sandbox(settings).prerequisites(settings)
                print("Firefox fonts, network and outer sandbox prerequisites verified")
                return 0
            return outer_sandbox(settings).run(settings, sys.argv[2], command)
        finally:
            if temporary:
                temporary.cleanup()
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as error:
        print("Firefox launch refused: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
