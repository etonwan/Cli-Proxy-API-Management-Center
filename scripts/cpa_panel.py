"""Manually install, promote, and roll back the panel on the CPA Linux host."""

import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import urllib.request


DATA_ROOT = Path("/www/data")
PORTS = {"dev": 8318, "prod": 8317}


def digest(content):
    return hashlib.sha256(content).hexdigest()


def panel_path(environment):
    return DATA_ROOT / f"cpa-{environment}" / "panel/management.html"


def previous_path(environment):
    return DATA_ROOT / f"cpa-{environment}" / "panel-previous.html"


def atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(prefix=".panel-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def verify_served(environment, content):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f"http://127.0.0.1:{PORTS[environment]}/management.html"
    with opener.open(url, timeout=10) as response:
        if response.read() != content:
            raise ValueError(f"{environment} is not serving the expected panel; check its mount")


def install(environment, content):
    if not content.lstrip().lower().startswith(b"<!doctype html") or b"</html>" not in content.lower():
        raise ValueError("Expected a complete single-file HTML build, not an API error or archive")
    target = panel_path(environment)
    current = target.read_bytes()
    if content != current:
        atomic_write(previous_path(environment), current)
        atomic_write(target, content)
    print(f"{environment} SHA256: {digest(content)}")
    try:
        verify_served(environment, content)
    except Exception:
        print(f"HTTP verification failed. Files may already be updated; inspect {target}. "
              f"Use rollback {environment} if needed; no automatic rollback was attempted.", file=sys.stderr)
        raise
    print("HTTP content matches. Refresh the browser and check the panel's functions separately.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show installed and previous panel hashes")
    commands.add_parser("install-dev", help="Install a local build or downloaded release in dev").add_argument("file", type=Path)
    commands.add_parser("promote", help="Copy the exact accepted dev panel to prod").add_argument("sha256")
    commands.add_parser("rollback", help="Swap the current and previous panel").add_argument("environment", choices=PORTS)
    args = parser.parse_args(argv)

    with (DATA_ROOT / ".cpa-panel.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == "status":
            for environment in PORTS:
                for label, path in (("current", panel_path(environment)), ("previous", previous_path(environment))):
                    print(f"{environment} {label}: {digest(path.read_bytes()) if path.exists() else 'not installed'}")
            return
        if args.command == "install-dev":
            environment, content = "dev", args.file.read_bytes()
        elif args.command == "promote":
            environment, content = "prod", panel_path("dev").read_bytes()
            if digest(content) != args.sha256:
                raise ValueError("Dev has changed since acceptance; test it again and use its current SHA256")
            verify_served("dev", content)
        else:
            environment = args.environment
            content = previous_path(environment).read_bytes()

        if environment == "prod":
            print(f"Production panel will become SHA256 {digest(content)}. Backend image, credentials and config stay unchanged.")
            if input("Type prod to confirm (anything else cancels): ") != "prod":
                print("Cancelled. No panel files were changed.")
                return
        install(environment, content)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, EOFError) as error:
        sys.exit(str(error))
