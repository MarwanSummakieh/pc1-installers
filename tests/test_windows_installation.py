"""Run on Linux: python3 -m unittest discover -s tests -v.

The fake compatibility runner exercises our real worker, process lifecycle and
Xvfb display separation. It does not stand in for the real Proton acceptance run.
"""
import importlib.util
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("windows_manager", ROOT / "os/files/usr/lib/marwanos/windows/manager.py")
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pc1 tests with spaces ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.downloads = self.root / "Downloads"
        self.downloads.mkdir()
        self.source = self.downloads / "installer.exe"
        self.source.write_bytes(b"known installer bytes")
        self.recipe = {
            "id": "example", "title": "Example", "version": "1", "filename": "installer.exe",
            "url": "https://example.invalid/installer.exe", "size": self.source.stat().st_size,
            "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "arguments": ["/S", "/D=C:\\PC1\\App with spaces"],
            "executable": "drive_c/PC1/App with spaces/app.exe", "verify_files": ["drive_c/PC1/App with spaces/data.bin"],
            "input_mode": "pointer", "timeout_seconds": 10,
        }
        recipes = self.root / "recipes.json"
        recipes.write_text(json.dumps([self.recipe]))
        self.worker = manager.Manager(self.root / "state", recipes, [self.downloads])
        self.recipes = recipes
        self.runner = self.root / "fake-runner"
        self.runner.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys, time
prefix = pathlib.Path(os.environ['WINEPREFIX'])
if len(sys.argv) == 2:
    time.sleep(60)
else:
    (prefix / 'invocation.json').write_text(json.dumps({'argv': sys.argv[1:], 'display': os.getenv('DISPLAY'), 'wayland': os.getenv('WAYLAND_DISPLAY'), 'gameid': os.getenv('GAMEID')}))
    directory = prefix / 'drive_c/PC1/App with spaces'
    directory.mkdir(parents=True)
    (directory / 'app.exe').write_bytes(b'MZ app')
    (directory / 'data.bin').write_bytes(b'data')
""")
        self.runner.chmod(0o755)
        self.patcher = patch.object(manager, "RUNNER", str(self.runner))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def run_success(self):
        if not shutil.which("Xvfb"):
            self.skipTest("Xvfb is required")
        self.worker.install(self.recipe, self.source)
        self.assertEqual(self.worker.state["status"], "done", self.worker.state)
        return self.worker.library()[0]

    def test_install_hidden_verify_publish_and_reuse_prefix_for_launch(self):
        with patch.dict(os.environ, {"DISPLAY": ":987", "WAYLAND_DISPLAY": "wayland-tv", "STEAM_GAME": "769"}):
            entry = self.run_success()
        invocation = json.loads((Path(entry["prefix"]) / "invocation.json").read_text())
        self.assertNotEqual(invocation["display"], ":987")
        self.assertIsNone(invocation["wayland"])
        self.assertEqual(invocation["gameid"], "0")
        self.assertEqual(invocation["argv"][1:], self.recipe["arguments"])
        self.assertEqual(manager.runtime_env(Path(entry["prefix"]))["WINEPREFIX"], entry["prefix"])
        self.assertEqual(entry["exec"], [manager.HELPER, "launch", "example"])
        self.assertEqual(entry["stop_exec"], [manager.HELPER, "stop", "example"])
        self.worker.publish()
        self.assertEqual(json.loads((self.worker.base / "state.json").read_text())["library"][0]["id"], "managed.example")

    def test_wrong_hash_does_not_execute_or_publish(self):
        self.source.write_bytes(b"untrusted installer!!")
        with patch.object(self.worker, "run_installer") as run:
            self.worker.install(self.recipe, self.source)
            run.assert_not_called()
        self.assertEqual(self.worker.state["status"], "failed")
        self.assertEqual(self.worker.library(), [])
        self.assertEqual(list((self.worker.base / "prefixes").iterdir()), [])

    def test_zero_exit_without_expected_files_is_failure(self):
        with patch.object(self.worker, "run_installer"):
            self.worker.install(self.recipe, self.source)
        self.assertEqual(self.worker.state["status"], "failed")
        self.assertFalse(self.worker.library())

    def test_cancel_before_install_keeps_library_empty(self):
        self.worker.cancel.set()
        self.worker.install(self.recipe, self.source)
        self.assertEqual(self.worker.state["status"], "cancelled")
        self.assertFalse(self.worker.library())

    def test_hanging_installer_times_out_and_removes_prefix(self):
        if not shutil.which("Xvfb"):
            self.skipTest("Xvfb is required")
        self.runner.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n")
        self.recipe["timeout_seconds"] = 0.2
        started = time.monotonic()
        self.worker.install(self.recipe, self.source)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(self.worker.state["status"], "failed")
        self.assertIn("too long", self.worker.state["detail"])
        self.assertFalse(self.worker.library())

    def test_stale_cancel_cannot_cancel_a_new_job(self):
        self.worker.update(job_id="new", status="installing")
        self.worker.handle({"verb": "cancel", "job_id": "old"})
        self.assertFalse(self.worker.cancel.is_set())
        self.worker.handle({"verb": "cancel", "job_id": "new"})
        self.assertTrue(self.worker.cancel.is_set())

    def test_controller_cancel_stops_a_running_installer(self):
        if not shutil.which("Xvfb"):
            self.skipTest("Xvfb is required")
        self.runner.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n")
        source_id = next(iter(self.worker.sources))
        self.worker.handle({"verb": "install", "recipe_id": "example", "source_id": source_id})
        for _ in range(100):
            if self.worker.state["status"] == "installing":
                break
            time.sleep(0.02)
        self.assertEqual(self.worker.state["status"], "installing")
        manager.atomic_json(self.worker.requests / "cancel.json", {
            "verb": "cancel", "job_id": self.worker.state["job_id"]})
        self.worker.consume()
        self.worker.thread.join(timeout=10)
        self.assertFalse(self.worker.thread.is_alive())
        self.assertEqual(self.worker.state["status"], "cancelled")
        self.assertFalse(self.worker.library())

    def test_unknown_recipe_or_source_cannot_execute(self):
        self.worker.handle({"verb": "install", "recipe_id": "../../bin/sh"})
        self.assertEqual(self.worker.state["status"], "failed")
        self.worker.handle({"verb": "install", "recipe_id": "example", "source_id": "/etc/passwd"})
        self.assertEqual(self.worker.state["status"], "failed")
        self.assertIsNone(self.worker.thread)

    def test_discovery_shows_unsupported_and_rejects_symlinks(self):
        (self.downloads / "unknown.exe").write_bytes(b"unknown")
        (self.downloads / "linked.exe").symlink_to(self.source)
        self.worker.scan()
        sources = list(self.worker.sources.values())
        self.assertEqual(len(sources), 2)
        self.assertEqual(next(s for s in sources if s["name"] == "unknown.exe")["recipe_id"], "")

    def test_interrupted_job_is_retryable_on_restart(self):
        manager.atomic_json(self.worker.base / "state.json", {"status": "installing"})
        restarted = manager.Manager(self.worker.base, self.recipes, [])
        self.assertEqual(restarted.state["status"], "failed")
        self.assertIn("interrupted", restarted.state["detail"])

    def test_duplicate_install_does_not_replace_working_app(self):
        entry = self.run_success()
        self.worker.handle({"verb": "install", "recipe_id": "example"})
        self.assertIsNone(self.worker.thread)
        self.assertEqual(self.worker.library()[0], entry)

    def test_worker_removal_request_deletes_committed_app(self):
        entry = self.run_success()
        self.worker.handle({'verb': 'remove', 'app_id': 'example'})
        self.worker.thread.join(timeout=10)
        self.assertFalse(self.worker.thread.is_alive())
        self.assertEqual(self.worker.state['status'], 'done')
        self.assertEqual(self.worker.state['operation'], 'remove')
        self.assertEqual(self.worker.library(), [])
        self.assertFalse(Path(entry['prefix']).exists())

    def test_missing_app_stays_visible_for_removal(self):
        entry = self.run_success()
        Path(entry['executable']).unlink()
        self.assertEqual(len(self.worker.library()), 1)
        self.assertIn('unavailable', self.worker.library()[0]['subtitle'])
        manager.remove_app(self.worker.base, 'example')
        self.assertEqual(self.worker.library(), [])

    def test_launch_and_stop_reap_the_running_application(self):
        self.run_success()
        env = dict(os.environ, MARWANOS_WINDOWS_HOME=str(self.worker.base), MARWANOS_WINDOWS_RUNTIME=str(self.runner))
        process = subprocess.Popen([sys.executable, str(manager.__file__), "launch", "example"], env=env)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        running = self.worker.base / "running/example.json"
        for _ in range(100):
            if running.exists():
                break
            time.sleep(0.02)
        self.assertTrue(running.exists())
        pid = json.loads(running.read_text())["pid"]
        manager.stop_app(self.worker.base, "example")
        process.wait(timeout=10)
        self.assertFalse(running.exists())
        self.assertIsNone(manager.proc_start(pid))

    def test_stale_pid_record_never_signals_another_process(self):
        manager.atomic_json(self.worker.base / "running/example.json", {"pid": os.getpid(), "start": "wrong"})
        with patch.object(os, "killpg") as kill:
            manager.stop_app(self.worker.base, "example")
            kill.assert_not_called()

    def test_malformed_request_is_consumed_without_crashing(self):
        (self.worker.requests / "bad.json").write_text("not json")
        self.worker.consume()
        self.assertFalse(list(self.worker.requests.iterdir()))

    def test_request_fifo_does_not_block_worker(self):
        os.mkfifo(self.worker.requests / "fifo.json")
        self.worker.consume()
        self.assertFalse(list(self.worker.requests.iterdir()))


if __name__ == "__main__":
    unittest.main()
