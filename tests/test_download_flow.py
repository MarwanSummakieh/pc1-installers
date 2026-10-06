import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).parents[1] / 'os/files/usr/lib/marwanos/windows/download_flow.py'
spec = importlib.util.spec_from_file_location('download_flow', MODULE)
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


class DownloadFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.downloads = self.root / 'Downloads'
        self.base = self.root / 'state'
        self.package = self.downloads / 'package'
        self.package.mkdir(parents=True)
        self.source = self.package / 'setup.exe'
        self.source.write_bytes(b'MZdownload fixture')
        self.companion = self.package / 'data.bin'
        self.companion.write_bytes(b'multipart')

    def receipt(self, **event):
        return flow.offer(self.base, self.downloads, dict(path=str(self.source), **event))[0]

    def installed(self, receipt, exit_code=0, portable=False):
        executable = self.root / 'Games' / 'Game.exe'
        executable.parent.mkdir(exist_ok=True)
        executable.write_bytes(b'MZinstalled')
        flow.save(self.base / 'jobs/local-test.json', {'id': 'local-test', 'status': 'done',
            'source': str(self.source), 'created_at': receipt['created_at'] + 1, 'exit_code': exit_code})
        flow.save(self.base / 'apps/local-test.json', {'recipe_id': 'local-test',
            'executable': str(executable), 'portable': portable})
        return flow.refresh(self.base)[0]

    def test_complete_to_install_cleanup_preserves_program_and_unrelated_files(self):
        unrelated = self.package / 'notes.txt'
        unrelated.write_text('keep')
        receipt = self.receipt()
        self.assertEqual(len(receipt['files']), 1)
        self.assertEqual(self.receipt()['id'], receipt['id'])
        self.assertEqual(self.installed(receipt)['status'], 'installed')
        result = flow.action(self.base, self.downloads, receipt['id'], 'cleanup')
        self.assertEqual(result['status'], 'cleaned')
        self.assertFalse(self.source.exists())
        self.assertTrue(self.companion.exists())
        self.assertTrue((self.root / 'Games/Game.exe').exists())
        self.assertTrue(unrelated.exists())

    def test_failure_cancellation_and_portable_registration_keep_download(self):
        for code, portable in [(1, False), (-15, False), (0, True)]:
            receipt = self.receipt()
            self.assertEqual(self.installed(receipt, code, portable)['status'], 'ready')
            with self.assertRaises(ValueError):
                flow.action(self.base, self.downloads, receipt['id'], 'cleanup')
            self.assertTrue(self.source.exists())

    def test_torrent_content_is_never_deleted_while_seeding(self):
        receipt = self.receipt(torrent=True)
        self.installed(receipt)
        with self.assertRaisesRegex(ValueError, 'seeding'):
            flow.action(self.base, self.downloads, receipt['id'], 'cleanup')
        self.assertTrue(self.source.exists())

    def test_replaced_package_file_cancels_entire_cleanup(self):
        receipt = self.receipt()
        self.installed(receipt)
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            flow.action(self.base, self.downloads, receipt['id'], 'cleanup')
        self.assertTrue(self.source.exists())

    def test_old_job_cannot_accept_new_download_at_same_path(self):
        receipt = self.receipt()
        self.installed(receipt)
        self.source.write_bytes(b'MZnew download')
        newer = self.receipt()
        self.assertNotEqual(newer['id'], receipt['id'])
        job_path = self.base / 'jobs/local-test.json'
        job = flow.read(job_path)
        job['created_at'] = receipt['created_at'] - 1
        flow.save(job_path, job)
        self.assertEqual(flow.installed_job(self.base, newer), {})
        with self.assertRaises(ValueError):
            flow.action(self.base, self.downloads, newer['id'], 'cleanup')
        self.assertTrue(self.source.exists())

    def test_ambiguous_names_external_files_and_links_are_rejected(self):
        (self.downloads / 'setup.exe').write_bytes(b'MZanother')
        self.assertEqual(flow.offer(self.base, self.downloads, {'path': 'setup.exe'}), [])
        outside = self.root / 'setup.exe'
        outside.write_bytes(b'MZoutside')
        with self.assertRaises(ValueError):
            flow.offer(self.base, self.downloads, {'path': str(outside)})
        link = self.downloads / 'linked.exe'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('Symlinks unavailable')
        with self.assertRaises(ValueError):
            flow.offer(self.base, self.downloads, {'path': str(link)})

    def test_legacy_fdm_torrent_completion_resolves_unique_folder(self):
        receipt = flow.offer(self.base, self.downloads, {'path': 'package'})[0]
        self.assertTrue(receipt['torrent'])
        self.assertEqual(receipt['source'], str(self.source))

    def test_shared_downloads_root_does_not_capture_unrelated_companions(self):
        source = self.downloads / 'program.exe'
        source.write_bytes(b'MZprogram')
        (self.downloads / 'unrelated.bin').write_bytes(b'keep')
        receipt = flow.offer(self.base, self.downloads, {'path': str(source)})[0]
        self.assertEqual(len(receipt['files']), 1)


if __name__ == '__main__':
    unittest.main()
