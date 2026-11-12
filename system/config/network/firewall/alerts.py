"""Serialize per-owner alerts while retaining the legacy aggregate flag."""
import fcntl
import json
from pathlib import Path
import re
import sys
import time


def update(root, owner, detail=None):
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", owner):
        raise ValueError("invalid alert owner")
    root = Path(root)
    alerts = root / "alerts"
    alerts.mkdir(parents=True, exist_ok=True)
    flag = root / "failed"
    with (root / "alerts.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Preserve an older generation's alert on first use of the new format.
        if flag.exists() and not list(alerts.glob("*.json")):
            legacy = flag.read_text().strip().split(":", 1)[0]
            if re.fullmatch(r"[A-Za-z0-9_.@-]+", legacy):
                (alerts / (legacy + ".json")).write_text(json.dumps({"owner": legacy, "detail": "legacy alarm"}))
        target = alerts / (owner + ".json")
        if detail is None:
            target.unlink(missing_ok=True)
        else:
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps({"owner": owner, "detail": detail,
                                             "monotonic_seconds": time.monotonic()}) + "\n")
            temporary.replace(target)
        owners = sorted(path.stem for path in alerts.glob("*.json"))
        if owners:
            temporary = flag.with_suffix(".tmp")
            temporary.write_text("\n".join(owners) + "\n")
            temporary.replace(flag)
        else:
            flag.unlink(missing_ok=True)


if __name__ == "__main__":
    update("/run/netsec", sys.argv[2], sys.argv[3] if sys.argv[1] == "set" else None)
