#!/usr/bin/env python3
"""Run a genuine Win32 exit regression in an explicitly isolated Wine prefix.

Compile setup_bridge_exit.c and the production setup_bridge.c first. The caller
must supply a new WINEPREFIX and separate DISPLAY; this never uses PC1 UI state.
The C fixture demonstrates the old sent-message stall. The second check drives
the production executable through its real page/action files and requires the
installer's genuine exit zero, finished=true, and actual bridge exit zero.
"""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import time


def windows(path):
    return "Z:" + str(path.resolve()).replace("/", "\\")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wine", required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    work = args.work.resolve()
    prefix = Path(os.environ.get("WINEPREFIX", "/nonexistent")).resolve()
    if not os.environ.get("DISPLAY") or not prefix.is_relative_to(work) or prefix == work:
        parser.error("require separate DISPLAY and WINEPREFIX inside --work")
    work.mkdir(parents=True, exist_ok=True)
    page_path, action_path = work / "page.json", work / "action.bin"
    if page_path.exists() or action_path.exists():
        parser.error("use a fresh work directory without existing action/page files")
    with (work / "fixture.log").open("wb") as log:
        result = subprocess.run([args.wine, str(args.fixture), "--check", windows(action_path)],
                                stdout=log, stderr=log, timeout=120)
    if result.returncode:
        raise RuntimeError("native sent/queued comparison failed; inspect fixture.log")
    print("PASS: real Win32 sent Close stalls; production queued Close exits zero; stale action rejected", flush=True)
    with (work / "bridge.log").open("wb") as log:
        process = subprocess.Popen([args.wine, str(args.bridge), windows(args.fixture),
                                    windows(page_path), windows(action_path)], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("bridge exited before fixture Close was visible")
                try:
                    page = json.loads(page_path.read_text())
                except (OSError, ValueError):
                    time.sleep(0.05)
                    continue
                buttons = [item for item in page.get("controls", [])
                           if item.get("kind") == "button" and item.get("text") == "Close" and item.get("enabled")]
                if buttons:
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("fixture Close did not appear in production page")
            revision, control_id = page["page"], int(buttons[0]["id"])
            action_path.write_bytes(struct.pack("<IQII", revision, control_id, 1, 0))
            if process.wait(timeout=15) != 0:
                raise RuntimeError("production bridge did not actually exit zero")
            final = json.loads(page_path.read_text())
            if final.get("finished") is not True or final.get("exit_code") != 0 or final.get("controls"):
                raise RuntimeError("missing genuine zero-exit finished state: " + repr(final))
            print("PASS: production bridge publishes actual installer exit 0 and finished=true, then exits 0", flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
