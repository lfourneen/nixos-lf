#!/usr/bin/env python3
"""Copy the installed USBGuard rules into private persistent storage."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        help="daemon configuration; defaults to usbguard.service's -c path")
    parser.add_argument("--destination", type=Path, default=Path("/persist/usbguard/rules.conf"))
    parser.add_argument("--systemctl", default="systemctl", help="systemctl executable")
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parents[1] / "system/hardware/usbguard/policy.py"
    spec = importlib.util.spec_from_file_location("usbguard_policy", source)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    try:
        policy.adopt(args.config, args.destination, systemctl=args.systemctl)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"usbguard-policy-migrate: {error}", file=sys.stderr)
        return 1
    print(f"USBGuard private policy is ready: {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
