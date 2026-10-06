"""General setup uses the session display and explicit library selection."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("local_manager", Path(__file__).resolve().parents[1] /
    "os/files/usr/lib/marwanos/windows/manager.py")
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


def pe():
    header = bytearray(64)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 60, 64)
    return header + b"PE\0\0"


class LocalSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="local setup spaces ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        home_patch = patch.dict(os.environ, {"HOME": str(self.root)})
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.base = self.root / "state"
        self.source = self.root / "Game $(literal) ' Setup.EXE"
        self.source.write_bytes(pe())
        (self.root / "setup.bin").write_bytes(b"multipart data")
        self.runner = self.root / "runner"
        self.runner.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, time
p = pathlib.Path(os.environ['WINEPREFIX'])
(p / 'invocation.json').write_text(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'display': os.getenv('DISPLAY'), 'wayland': os.getenv('WAYLAND_DISPLAY'), 'sibling': pathlib.Path('setup.bin').exists(), 'destination': os.getenv('MARWANOS_SETUP_DESTINATION'), 'inno': os.getenv('MARWANOS_SETUP_INNO')}))
if os.getenv('HANG_SETUP'):
    subprocess = __import__('subprocess')
    child = subprocess.Popen(['/bin/sleep', '60'])
    (p / 'child.pid').write_text(str(child.pid))
    time.sleep(60)
if os.getenv('EMPTY_SETUP'):
    sys.exit(0)
for name in ['Program Files/Example/Game.exe', 'Program Files/Example/unins000.exe', 'windows/notepad.exe', 'users/steamuser/AppData/Local/Example/Client.exe']:
    exe = p / 'drive_c' / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(pathlib.Path(os.environ['FIXTURE_EXE']).read_bytes())
if os.getenv('HANG_AFTER_INSTALL'):
    (p / 'installed.ready').write_text('ready')
    time.sleep(60)
if os.getenv('FINISHED_GUIDED'):
    page = pathlib.Path(sys.argv[-2][2:].replace('\\\\', '/'))
    page.write_text(json.dumps({'finished': True, 'exit_code': 0}))
    time.sleep(60)  # Model an installed service retaining Proton's wrapper.
sys.exit(int(os.getenv('SETUP_EXIT', '0')))
''')
        self.runner.chmod(0o755)
        self.env = dict(os.environ, MARWANOS_WINDOWS_HOME=str(self.base),
                        HOME=str(self.root),
                        MARWANOS_WINDOWS_RUNTIME=str(self.runner), FIXTURE_EXE=str(self.source),
                        DISPLAY=":fixture-tv", WAYLAND_DISPLAY="wayland-fixture")

    def run_setup(self, key="local-test", source=None, command="setup", **env):
        return subprocess.run([sys.executable, str(manager.__file__), command, key, str(source or self.source)],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=15)

    def job(self, key="local-test"):
        return manager.read_json(self.base / "jobs" / (key + ".json"), {})

    def test_unknown_exe_session_display_siblings_selection_and_library(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        job = self.job()
        self.assertEqual(job['status'], 'select')
        self.assertEqual(len(job['choices']), 2)
        self.assertFalse(list((self.base / 'apps').glob('*.json')))
        invocation = manager.read_json(self.base / 'prefixes/local-test/invocation.json', {})
        self.assertEqual(invocation['argv'], [str(self.source)])
        self.assertEqual(invocation['cwd'], str(self.root))
        self.assertEqual(invocation['display'], ':fixture-tv')
        self.assertEqual(invocation['wayland'], 'wayland-fixture')
        self.assertTrue(invocation['sibling'])
        choice = next(c for c in job['choices'] if c['title'] == 'Game')
        self.assertEqual(manager.register_local(self.base, 'local-test', choice['id']), 0)
        entry = manager.read_json(self.base / 'apps/local-test.json', {})
        self.assertEqual(entry['title'], 'Game')
        self.assertEqual(entry['exec'], [manager.HELPER, 'launch', 'local-test'])
        self.assertEqual(entry['prefix'], str(self.base / 'prefixes/local-test'))

    def test_games_mapping_discovery_registration_launch_and_removal(self):
        self.assertEqual(self.run_setup().returncode, 0)
        prefix = self.base / 'prefixes/local-test'
        games = self.root / 'Games/local-test'
        self.assertEqual((prefix / 'drive_c/Games').resolve(), games)
        self.assertEqual(self.job()['install_directory'], str(games))
        game = games / 'Fixture Game/Game.exe'
        game.parent.mkdir()
        game.write_bytes(pe())
        # Foreign links under the game directory must remain undiscoverable.
        (games / 'foreign').symlink_to(self.root, target_is_directory=True)
        choice = next(c for c in manager.local_candidates(prefix) if c['id'] == 'drive_c/Games/Fixture Game/Game.exe')
        self.assertEqual(manager.register_local(self.base, 'local-test', choice['id']), 0)
        entry = manager.read_json(self.base / 'apps/local-test.json', {})
        self.assertEqual(Path(entry['executable']).resolve(), game)
        # Exercise launch validation and actual runtime invocation.
        with patch.object(manager.subprocess, 'Popen') as launch:
            launch.return_value.pid = 999999
            launch.return_value.poll.return_value = 0
            launch.return_value.returncode = 0
            self.assertEqual(manager.launch(self.base, 'local-test'), 0)
            self.assertEqual(launch.call_args.args[0], [manager.RUNNER, str(game)])
        manager.remove_app(self.base, 'local-test')
        self.assertFalse(games.exists())
        self.assertTrue(self.source.exists())
        self.assertTrue((self.root / 'setup.bin').exists())

    def test_game_registration_preserves_native_controller_and_marks_history_kind(self):
        self.assertEqual(self.run_setup().returncode, 0)
        choice = self.job()['choices'][0]['id']
        self.assertEqual(manager.register_local(self.base, 'local-test', choice, 'gamepad'), 0)
        entry = manager.read_json(self.base / 'apps/local-test.json', {})
        self.assertEqual(entry['input_mode'], '')
        self.assertEqual(entry['kind'], 'game')
        self.assertEqual(self.run_setup(key='local-app').returncode, 0)
        choice = self.job('local-app')['choices'][0]['id']
        self.assertEqual(manager.register_local(self.base, 'local-app', choice, 'pointer'), 0)
        entry = manager.read_json(self.base / 'apps/local-app.json', {})
        self.assertEqual(entry['input_mode'], 'pointer')
        self.assertEqual(entry['kind'], 'application')

    def test_games_mapping_tampering_cannot_delete_another_folder(self):
        self.run_setup()
        prefix = self.base / 'prefixes/local-test'
        games = self.root / 'Games/local-test'
        games.rmdir()
        games.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(manager.InstallError):
            manager.discard_setup(self.base, 'local-test')
        self.assertTrue(self.source.exists())
        self.assertTrue(prefix.exists())

    def test_discard_removes_only_this_attempt_in_home_games(self):
        self.run_setup()
        neighbour = self.root / 'Games/Existing game'
        neighbour.mkdir()
        (neighbour / 'keep').write_text('keep')
        manager.discard_setup(self.base, 'local-test')
        self.assertFalse((self.root / 'Games/local-test').exists())
        self.assertEqual((neighbour / 'keep').read_text(), 'keep')

    def test_msi_uses_msiexec_and_argument_boundaries(self):
        source = self.root / 'installer with spaces.MSI'
        source.write_bytes(bytes.fromhex('d0cf11e0a1b11ae1') + b'fixture')
        result = self.run_setup(source=source)
        self.assertEqual(result.returncode, 0, result.stderr)
        invocation = manager.read_json(self.base / 'prefixes/local-test/invocation.json', {})
        self.assertEqual(invocation['argv'], ['msiexec', '/i', 'Z:' + str(source).replace('/', '\\')])

    def test_original_inno_setup_defaults_to_mapped_games_without_skipping_pages(self):
        self.source.write_bytes(pe() + b'Inno Setup Setup Data (6.0.0)')
        self.assertEqual(self.run_setup().returncode, 0)
        invocation = manager.read_json(self.base / 'prefixes/local-test/invocation.json', {})
        self.assertEqual(invocation['argv'], [str(self.source), r'/DIR=C:\Games'])
        self.assertEqual((self.base / 'prefixes/local-test/drive_c/Games').resolve(), self.root / 'Games/local-test')

    def test_unwritable_default_fails_before_launching_installer(self):
        with patch.object(manager.tempfile, 'TemporaryFile', side_effect=PermissionError('read-only folder')):
            self.assertEqual(manager.local_setup(self.base, 'local-test', str(self.source)), 1)
        self.assertEqual(self.job()['status'], 'failed')
        self.assertIn('default installation folder is not writable', self.job()['detail'])
        self.assertFalse((self.base / 'prefixes/local-test/invocation.json').exists())

    def test_guided_inno_initializes_writable_default_before_the_first_page(self):
        self.source.write_bytes(pe() + b'Inno Setup Setup Data (6.0.0)')
        bridge = self.root / 'setup bridge.exe'
        bridge.write_bytes(pe())
        result = self.run_setup(command='guided', MARWANOS_SETUP_BRIDGE=str(bridge))
        self.assertEqual(result.returncode, 0, result.stderr)
        prefix = self.base / 'prefixes/local-test'
        invocation = manager.read_json(prefix / 'invocation.json', {})
        self.assertEqual(invocation['destination'], r'C:\Games')
        self.assertEqual(invocation['inno'], '1')
        (prefix / 'drive_c/Games/write-check').write_text('writable')
        self.assertEqual((self.root / 'Games/local-test/write-check').read_text(), 'writable')

    def test_guided_setup_preserves_source_and_siblings_on_a_hidden_display(self):
        bridge = self.root / 'setup bridge.exe'
        bridge.write_bytes(pe())
        result = self.run_setup(command='guided', MARWANOS_SETUP_BRIDGE=str(bridge))
        self.assertEqual(result.returncode, 0, result.stderr)
        invocation = manager.read_json(self.base / 'prefixes/local-test/invocation.json', {})
        ui = self.base / 'setup-ui/local-test'
        win = lambda path: 'Z:' + str(path).replace('/', '\\')
        self.assertEqual(invocation['argv'], [str(ui / 'setup-bridge.exe'), win(self.source), win(ui / 'page.json'), win(ui / 'action.bin')])
        self.assertEqual((ui / 'setup-bridge.exe').read_bytes(), bridge.read_bytes())
        self.assertNotEqual(invocation['display'], ':fixture-tv')
        self.assertIsNone(invocation['wayland'])
        self.assertTrue(invocation['sibling'])
        self.assertEqual(invocation['inno'], '0')
        self.assertEqual(invocation['cwd'], str(self.root))
        self.assertTrue(self.job()['guided'])
        self.assertEqual(self.job()['status'], 'select')
        self.assertFalse((self.base / 'running/local-test.json').exists())

    def test_guided_setup_without_bridge_fails_before_running_installer(self):
        result = self.run_setup(command='guided', MARWANOS_SETUP_BRIDGE=str(self.root / 'missing.exe'))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.job()['status'], 'failed')
        self.assertFalse((self.base / 'prefixes/local-test/invocation.json').exists())

    def test_guided_finish_does_not_wait_for_installed_background_services(self):
        bridge = self.root / 'bridge.exe'
        bridge.write_bytes(pe())
        result = self.run_setup(command='guided', MARWANOS_SETUP_BRIDGE=str(bridge), FINISHED_GUIDED='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.job()['status'], 'select')
        self.assertEqual(self.job()['exit_code'], 0)
        self.assertFalse((self.base / 'running/local-test.json').exists())

    def test_guided_ui_symlink_is_rejected_without_removing_external_files(self):
        self.run_setup()
        external = self.root / 'external-ui'
        external.mkdir()
        (external / 'keep').write_text('keep')
        (self.base / 'setup-ui').symlink_to(external)
        with self.assertRaises(manager.InstallError):
            manager.discard_setup(self.base, 'local-test')
        self.assertTrue((external / 'keep').exists())
        self.assertTrue((self.base / 'prefixes/local-test').exists())

    def test_closing_setup_retains_installed_program_choices(self):
        process = subprocess.Popen([sys.executable, str(manager.__file__), 'setup', 'local-test', str(self.source)],
                                   env=dict(self.env, HANG_AFTER_INSTALL='1'))
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        ready = self.base / 'prefixes/local-test/installed.ready'
        for _ in range(200):
            if ready.exists():
                break
            time.sleep(.02)
        self.assertTrue(ready.exists())
        manager.stop_app(self.base, 'local-test')
        process.wait(timeout=10)
        self.assertEqual(self.job()['status'], 'select')
        self.assertEqual(len(self.job()['choices']), 2)
        self.assertFalse(list((self.base / 'apps').glob('*.json')))
        self.assertEqual(manager.register_local(self.base, 'local-test', self.job()['choices'][0]['id']), 0)

    def test_desktop_shortcut_provides_friendly_name_and_first_choice(self):
        self.run_setup()
        prefix = self.base / 'prefixes/local-test'
        path = prefix / 'drive_c/users/Public/Desktop/Free Download Manager.lnk'
        path.parent.mkdir(parents=True)
        header = bytearray(76)
        header[:20] = bytes.fromhex('4c0000000114020000000000c000000000000046')
        struct.pack_into('<I', header, 20, 2)
        target = b'C:\\Program Files\\Example\\Game.exe\0'
        info = struct.pack('<7I', 29 + len(target), 28, 1, 0, 28, 0, 28 + len(target)) + target + b'\0'
        path.write_bytes(header + info)
        choices = manager.local_candidates(prefix)
        self.assertEqual(choices[0]['title'], 'Free Download Manager')
        self.assertTrue(choices[0]['shortcut'])
        self.assertEqual(manager.register_local(self.base, 'local-test', choices[0]['id']), 0)
        self.assertEqual(manager.read_json(self.base / 'apps/local-test.json', {})['title'], 'Free Download Manager')

    def test_malformed_and_escaping_shortcuts_are_ignored(self):
        path = self.root / 'bad.lnk'
        path.write_bytes(bytes(76))
        self.assertIsNone(manager.shortcut_target(path))
        header = bytearray(76)
        header[:20] = bytes.fromhex('4c0000000114020000000000c000000000000046')
        struct.pack_into('<I', header, 20, 2)
        for target in (b'C:\\..\\outside.exe\0', b'Z:\\outside.exe\0'):
            info = struct.pack('<7I', 29 + len(target), 28, 1, 0, 28, 0, 28 + len(target)) + target + b'\0'
            path.write_bytes(header + info)
            self.assertIsNone(manager.shortcut_target(path))
        path.write_bytes(header + struct.pack('<7I', 999999, 28, 1, 0, 28, 0, 28))
        self.assertIsNone(manager.shortcut_target(path))

    def test_invalid_file_and_fifo_never_run(self):
        self.source.write_bytes(b'not a windows file')
        self.assertEqual(self.run_setup().returncode, 1)
        self.assertEqual(self.job()['status'], 'failed')
        fifo = self.root / 'fifo.exe'
        os.mkfifo(fifo)
        self.assertEqual(self.run_setup('local-fifo', fifo).returncode, 1)
        self.assertFalse((self.base / 'prefixes/local-fifo/invocation.json').exists())

    def test_failure_and_empty_setup_do_not_publish(self):
        self.run_setup(SETUP_EXIT='1')
        self.assertEqual(self.job()['status'], 'failed')
        self.assertFalse(list((self.base / 'apps').glob('*.json')))
        self.run_setup('local-empty', EMPTY_SETUP='1')
        self.assertEqual(self.job('local-empty')['status'], 'empty')
        self.assertFalse(list((self.base / 'apps').glob('*.json')))

    def test_selection_rejects_traversal_and_symlinks(self):
        self.run_setup()
        prefix = self.base / 'prefixes/local-test'
        (prefix / 'drive_c/linked.exe').symlink_to(self.source)
        (prefix / 'drive_c/outside').symlink_to(self.root, target_is_directory=True)
        builtin = prefix / 'drive_c/Program Files/Internet Explorer/iexplore.exe'
        builtin.parent.mkdir(parents=True)
        builtin.write_bytes(pe() + b'Wine builtin DLL')
        self.assertEqual(manager.register_local(self.base, 'local-test', '../../bad.exe'), 1)
        self.assertEqual(manager.register_local(self.base, 'local-test', 'drive_c/linked.exe'), 1)
        self.assertEqual(len(manager.local_candidates(prefix)), 2)

    def test_portable_keeps_original_folder_and_does_not_execute(self):
        result = self.run_setup(command='portable')
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = manager.read_json(self.base / 'apps/local-test.json', {})
        self.assertEqual(entry['executable'], str(self.source))
        self.assertFalse((self.base / 'prefixes/local-test/invocation.json').exists())

    def test_close_cancels_setup_and_keeps_partial_prefix(self):
        process = subprocess.Popen([sys.executable, str(manager.__file__), 'setup', 'local-test', str(self.source)],
                                   env=dict(self.env, HANG_SETUP='1'))
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        child_path = self.base / 'prefixes/local-test/child.pid'
        for _ in range(200):
            if child_path.exists():
                break
            time.sleep(.02)
        self.assertTrue(child_path.exists())
        manager.stop_app(self.base, 'local-test')
        process.wait(timeout=10)
        self.assertEqual(self.job()['status'], 'cancelled')
        self.assertFalse(list((self.base / 'apps').glob('*.json')))
        self.assertTrue(child_path.parent.exists())
        self.assertFalse((self.base / 'running/local-test.json').exists())

    def test_remove_managed_app_deletes_only_its_prefix(self):
        self.run_setup()
        choice = self.job()['choices'][0]['id']
        manager.register_local(self.base, 'local-test', choice)
        unrelated = self.base / 'prefixes/local-other'
        unrelated.mkdir()
        manager.remove_app(self.base, 'local-test')
        self.assertFalse((self.base / 'prefixes/local-test').exists())
        self.assertFalse((self.base / 'apps/local-test.json').exists())
        self.assertFalse((self.base / 'jobs/local-test.json').exists())
        self.assertTrue(self.source.exists())
        self.assertTrue(unrelated.exists())

    def test_remove_portable_preserves_original_files(self):
        self.run_setup(command='portable')
        manager.remove_app(self.base, 'local-test')
        self.assertEqual(self.source.read_bytes(), pe())
        self.assertTrue((self.root / 'setup.bin').exists())

    def test_remove_rejects_external_prefix_and_symlink(self):
        self.run_setup(command='portable')
        entry_path = self.base / 'apps/local-test.json'
        entry = manager.read_json(entry_path, {})
        entry['prefix'] = str(self.root)
        manager.atomic_json(entry_path, entry)
        with self.assertRaises(manager.InstallError):
            manager.remove_app(self.base, 'local-test')
        entry['prefix'] = str(self.base / 'prefixes/local-test')
        manager.atomic_json(entry_path, entry)
        (self.base / 'prefixes/local-test').rmdir()
        (self.base / 'prefixes/local-test').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(manager.InstallError):
            manager.remove_app(self.base, 'local-test')
        self.assertTrue(self.source.exists())
        self.assertTrue(entry_path.exists())

    def test_discard_partial_setup_keeps_installer(self):
        self.run_setup(EMPTY_SETUP='1')
        manager.discard_setup(self.base, 'local-test')
        self.assertFalse((self.base / 'prefixes/local-test').exists())
        self.assertFalse((self.base / 'jobs/local-test.json').exists())
        self.assertTrue(self.source.exists())

    def test_interrupted_local_setup_becomes_discardable_with_choices(self):
        self.run_setup()
        job = self.job()
        job.update(status='installing', created_at=time.time() - 60)
        manager.atomic_json(self.base / 'jobs/local-test.json', job)
        manager.cleanup_orphans(self.base)
        self.assertEqual(self.job()['status'], 'failed')
        self.assertEqual(len(self.job()['choices']), 2)

    def test_missing_prefix_metadata_cannot_leave_removal_in_progress(self):
        manager.atomic_json(self.base / 'apps/local-test.json', {'id': 'managed.local-test'})
        with self.assertRaises(manager.InstallError):
            manager.remove_app(self.base, 'local-test')

    def test_orphan_cleanup_signals_only_verified_runtime(self):
        process = subprocess.Popen(['/bin/sleep', '60'], start_new_session=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        running = self.base / 'running/local-orphan.json'
        manager.atomic_json(running, {'pid': process.pid, 'start': manager.proc_start(process.pid),
                                     'owner': 2147483647, 'owner_start': 'missing'})
        manager.cleanup_orphans(self.base)
        process.wait(timeout=10)
        self.assertFalse(running.exists())
        self.assertNotEqual(process.returncode, 0)

    def test_orphan_cleanup_does_not_race_launch_record_replacement(self):
        process = subprocess.Popen(['/bin/sleep', '60'], start_new_session=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        running = self.base / 'running/local-orphan.json'
        manager.atomic_json(running, {'pid': process.pid, 'start': manager.proc_start(process.pid),
                                     'owner': 2147483647, 'owner_start': 'missing'})
        with manager.exclusive(running.with_suffix('.lock')):
            manager.cleanup_orphans(self.base)
            self.assertTrue(running.exists())
            self.assertIsNone(process.poll())
        manager.cleanup_orphans(self.base)
        process.wait(timeout=10)
        self.assertFalse(running.exists())

    def test_stop_signals_live_owner_even_when_leader_already_exited(self):
        manager.atomic_json(self.base / 'running/local-test.json',
                            {'pid': 2147483647, 'start': 'gone', 'owner': 567,
                             'owner_start': 'owner-start'})
        with patch.object(os, 'kill') as kill, patch.object(os, 'killpg') as killpg, \
                patch.object(manager, 'proc_start', side_effect=lambda pid: 'owner-start' if pid == 567 else None):
            manager.stop_app(self.base, 'local-test')
        kill.assert_called_once_with(567, manager.signal.SIGTERM)
        killpg.assert_not_called()

    def test_orphan_cleanup_reaps_descendant_after_group_leader_exits(self):
        prefix = self.base / 'prefixes/local-orphan'
        prefix.mkdir(parents=True)
        leader = subprocess.Popen([sys.executable, '-c',
            'import subprocess,time; p=subprocess.Popen(["/bin/sleep", "60"], stdout=subprocess.DEVNULL); print(p.pid, flush=True); time.sleep(.2)'],
            env=dict(os.environ, WINEPREFIX=str(prefix)), stdout=subprocess.PIPE, text=True, start_new_session=True)
        started = manager.proc_start(leader.pid)
        child = int(leader.stdout.readline())
        self.addCleanup(lambda: os.kill(child, manager.signal.SIGKILL) if manager.proc_start(child) else None)
        leader.wait(timeout=5)
        leader.stdout.close()
        running = self.base / 'running/local-orphan.json'
        manager.atomic_json(running, {'pid': leader.pid, 'start': started, 'owner': 2147483647,
                                     'owner_start': 'missing', 'prefix': str(prefix)})
        manager.cleanup_orphans(self.base)
        self.assertFalse(running.exists())
        for tick in range(50):
            state = Path(f'/proc/{child}/stat')
            if not state.exists() or state.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                break
            time.sleep(.02)
        else:
            self.fail('Orphan descendant remained alive')


if __name__ == '__main__':
    unittest.main()
