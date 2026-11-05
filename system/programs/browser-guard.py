"""Launch a managed browser profile while respecting explicit network intent.

Mode A must work before public DNS or a proxy is available, for portal login.
Mode B requires current verification. The mode-aware relay and kernel guards
enforce the data path after launch; this process is not a network monitor.
"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def verify_sandbox(settings, browser, runner=None):
    """Check production mounts/overrides, without injecting fonts or policies."""
    app, policy, fontconfig = {
        "zen": ("app.zen_browser.zen", "/app/etc/zen/policies/policies.json", "/app/etc/zen/fonts.conf"),
        "chrome": ("com.google.Chrome", "/run/host/etc/opt/chrome/policies/managed/browser.json",
                   "/run/host/etc/netsec-browser/fonts.conf"),
    }[browser]
    script = '''
test -f "$1"
printf 'policy:'; sha256sum "$1"
if [ "$2" = strict ]; then
    test "${FONTCONFIG_FILE-}" = "$3"
    printf 'fontconfig:'; sha256sum "$FONTCONFIG_FILE"
    fc-list --format 'font:%{file}\\n'
fi
'''
    result = (subprocess.run if runner is None else runner)(
        [settings["flatpak"], "run", "--command=sh", app, "-eu", "-c", script,
         "browser-policy-check", policy, "strict" if settings["strictPrivacy"] else "normal", fontconfig],
        capture_output=True, text=True, timeout=30, check=False)
    if result.returncode or "Fontconfig error:" in result.stderr:
        raise ValueError("browser sandbox policy/fonts unavailable; check Flatpak overrides and policy installer")
    lines = result.stdout.splitlines()
    def digest(prefix, expected):
        records = [line[len(prefix):].split() for line in lines if line.startswith(prefix)]
        if len(records) != 1 or not records[0] or records[0][0] != hashlib.sha256(Path(expected).read_bytes()).hexdigest():
            raise ValueError("browser sandbox " + prefix.rstrip(":") + " differs from current configuration")
    digest("policy:", settings[browser + "Policy"])
    if settings["strictPrivacy"]:
        digest("fontconfig:", settings["fonts"])
        fonts = [line[5:] for line in lines if line.startswith("font:")]
        if not fonts or any(not any(path.startswith(directory + "/") for directory in settings["fontDirectories"])
                            for path in fonts):
            raise ValueError("browser sandbox font inventory is empty or includes host/user fonts")


def verify_core(settings):
    # Reuse the passive listener identity checks and the live controller's
    # managed-policy digest. Fresh state from a previous Nix generation alone
    # cannot authorize launch before the new core policy has loaded.
    result = subprocess.run([sys.executable, "-I", settings["healthModule"],
                             settings["healthSettings"], "--fast"],
                            capture_output=True, text=True, timeout=9, check=False)
    try:
        verdict = json.loads(result.stdout)
    except ValueError:
        raise ValueError("current core verification is unavailable") from None
    if result.returncode != 0 or not verdict.get("ok"):
        raise ValueError("current core policy is not verified: " + verdict.get("detail", "configuration mismatch"))


def prepare(settings, browser, home=None, check=None, check_core=None, check_only=False):
    home = Path.home() if home is None else Path(home)
    if browser not in {"zen", "chrome"}:
        raise ValueError("choose zen or chrome")
    if check is None:
        spec = importlib.util.spec_from_file_location("netsec_state", settings["stateModule"])
        state = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(state)
        check = state.readiness
    verdict = check(target="proxy", maximum=settings["maxAge"],
                    verification_maximum=settings["verificationMaxAge"])
    mode = verdict.get("mode", "unknown")
    if mode not in {"direct", "proxy"}:
        raise ValueError("network policy is " + mode + "; use the explicit mode controls")
    if mode == "proxy" and not verdict["ready"]:
        raise ValueError("proxy is not ready: " + "; ".join(verdict["reasons"]))
    expected = Path(settings[browser + "Policy"]).read_bytes()
    installed = Path(settings["installed" + browser.title() + "Policy"]).read_bytes()
    if expected != installed:
        raise ValueError("browser policy is stale; restart browser-policy-install.service or deploy the current generation")
    if settings["strictPrivacy"]:
        if Path(settings["fonts"]).read_bytes() != Path(settings["installed" + browser.title() + "Fonts"]).read_bytes():
            raise ValueError("browser font configuration is stale; restore the managed font configuration")
    # No DNS/core readiness requirement in A: that would deadlock an HTTP
    # gateway login on an uplink that has not authorized Internet access yet.
    if mode == "proxy":
        (verify_core if check_core is None else check_core)(settings)
    verify_sandbox(settings, browser)
    current = check(target="proxy", maximum=settings["maxAge"],
                    verification_maximum=settings["verificationMaxAge"])
    if (current.get("mode") != mode
            or current.get("policy_version") != verdict.get("policy_version")
            or (mode == "proxy" and not current["ready"])):
        raise ValueError("proxy state changed during browser launch; retry after it is ready")
    if check_only:
        return []
    if browser == "zen":
        # Flatpak's persistent=.zen maps this directory to ~/.zen in its sandbox.
        profile = home / ".var/app/app.zen_browser.zen/.zen/netsec-private"
        profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        profile.chmod(0o700)
        # User-owned dedicated profile, never an existing personal user.js.
        with tempfile.NamedTemporaryFile(mode="w", dir=profile, prefix="user.js.", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(Path(settings["userJs"]).read_text())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(profile / "user.js")
        finally:
            temporary.unlink(missing_ok=True)
        # Gecko's remote destination includes the profile path. Allow a second
        # checked invocation to send links to this profile's existing window.
        return [settings["flatpak"], "run", "app.zen_browser.zen",
                "--profile", str(home / ".zen/netsec-private")]
    profile = home / ".var/app/com.google.Chrome/config/netsec-private"
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    profile.chmod(0o700)
    return [settings["flatpak"], "run", "com.google.Chrome", "--user-data-dir=" + str(profile)]


def main():
    try:
        settings = json.loads(Path(sys.argv[1]).read_text())
        if os.geteuid() == 0:
            raise ValueError("run the browser as the desktop user")
        check_only = sys.argv[3:] == ["--check"]
        command = prepare(settings, sys.argv[2], check_only=check_only)
        if check_only:
            print("Browser policy, sandbox and network mode verified")
            return 0
        os.execv(command[0], command + sys.argv[3:])
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as error:
        print("Browser launch refused: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
