"""Real PE-resource/ICO decoding and Windows library artwork caching."""
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("windows_icons_manager", ROOT / "os/files/usr/lib/marwanos/windows/manager.py")
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


def icon_executable(color=(24, 80, 160, 220), bitmap=False):
    """Small valid PE32 fixture with real RT_ICON and RT_GROUP_ICON resources."""
    stream = io.BytesIO()
    image = Image.new("RGBA", (64, 64), color)
    options = {"bitmap_format": "bmp"} if bitmap else {}
    image.save(stream, format="ICO", sizes=[(16, 16), (64, 64)], **options)
    ico = stream.getvalue()
    count = struct.unpack_from("<H", ico, 4)[0]
    group, icons = bytearray(ico[:6]), {}
    for index in range(count):
        record = ico[6 + index * 16:6 + (index + 1) * 16]
        size, offset = struct.unpack_from("<II", record, 8)
        identifier = index + 1
        group.extend(record[:12] + struct.pack("<H", identifier))
        icons[identifier] = {1033: ico[offset:offset + size]}
    tree = {3: icons, 14: {1: {1033: bytes(group)}}}
    resources = bytearray()

    def allocate(size):
        offset = len(resources)
        resources.extend(b"\0" * size)
        return offset

    def directory(children):
        start = allocate(16 + 8 * len(children))
        struct.pack_into("<H", resources, start + 14, len(children))
        for index, (identifier, child) in enumerate(children.items()):
            if isinstance(child, dict):
                offset = directory(child) | 0x80000000
            else:
                offset = allocate(16)
                payload = allocate(len(child))
                resources[payload:payload + len(child)] = child
                struct.pack_into("<IIII", resources, offset, 0x1000 + payload, len(child), 0, 0)
            struct.pack_into("<II", resources, start + 16 + index * 8, identifier, offset)
        return start

    directory(tree)
    raw_size = (len(resources) + 511) // 512 * 512
    result = bytearray(512 + raw_size)
    result[:2] = b"MZ"
    struct.pack_into("<I", result, 60, 128)
    result[128:132] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", result, 132, 0x14C, 1, 0, 0, 0, 224, 0x0102)
    optional = 152
    struct.pack_into("<H", result, optional, 0x10B)
    for offset, value in {28: 0x400000, 32: 4096, 36: 512, 56: 8192, 60: 512, 92: 16}.items():
        struct.pack_into("<I", result, optional + offset, value)
    struct.pack_into("<II", result, optional + 96 + 2 * 8, 0x1000, len(resources))
    section = optional + 224
    struct.pack_into("<8sIIIIIIHHI", result, section, b".rsrc\0\0\0", len(resources), 0x1000,
                     raw_size, 512, 0, 0, 0, 0, 0x40000040)
    result[512:512 + len(resources)] = resources
    return bytes(result)


class WindowsIconTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "state"
        self.base.mkdir()
        self.executable = self.root / "App.exe"

    def test_embedded_png_icon_preserves_artwork_and_uses_largest_size(self):
        self.executable.write_bytes(icon_executable())
        output = manager.application_icon(self.base, "example", self.executable)
        with Image.open(output) as image:
            self.assertEqual(image.size, (64, 64))
            self.assertEqual(image.getpixel((32, 32)), (24, 80, 160, 220))
        with patch.object(manager, "executable_icons", side_effect=AssertionError("cache must be reused")):
            self.assertEqual(manager.application_icon(self.base, "example", self.executable), output)

    def test_embedded_bitmap_icon_also_decodes(self):
        self.executable.write_bytes(icon_executable(bitmap=True))
        output = manager.application_icon(self.base, "example", self.executable)
        with Image.open(output) as image:
            self.assertEqual(image.size, (64, 64))
            self.assertEqual(image.getpixel((32, 32))[:3], (24, 80, 160))

    def test_large_executable_keeps_embedded_icon_without_eager_file_read(self):
        self.executable.write_bytes(icon_executable())
        with self.executable.open("r+b") as stream:
            stream.truncate(160 * 1024**2)
        output = manager.application_icon(self.base, "large-example", self.executable)
        with Image.open(output) as image:
            self.assertEqual(image.size, (64, 64))
            self.assertEqual(image.getpixel((32, 32)), (24, 80, 160, 220))

    def test_changed_executable_refreshes_cached_icon(self):
        self.executable.write_bytes(icon_executable())
        output = manager.application_icon(self.base, "example", self.executable)
        self.executable.write_bytes(icon_executable((240, 180, 80, 255)))
        self.assertEqual(manager.application_icon(self.base, "example", self.executable), output)
        with Image.open(output) as image:
            self.assertEqual(image.getpixel((32, 32)), (240, 180, 80, 255))

    def test_sidecar_fallback_and_negative_cache(self):
        self.executable.write_bytes(b"MZ no resources")
        self.assertEqual(manager.application_icon(self.base, "example", self.executable), "")
        with patch.object(manager, "executable_icons", side_effect=AssertionError("negative cache must be reused")):
            self.assertEqual(manager.application_icon(self.base, "example", self.executable), "")
        Image.new("RGBA", (100, 100), (180, 20, 50, 255)).save(self.root / "APP.PNG")
        output = manager.application_icon(self.base, "example", self.executable)
        with Image.open(output) as image:
            self.assertEqual(image.getpixel((50, 50)), (180, 20, 50, 255))

    def test_sidecar_fallback_without_optional_pe_parser(self):
        self.executable.write_bytes(b"MZ no resources")
        Image.new("RGBA", (64, 64), (40, 130, 200, 255)).save(self.root / "App.png")
        with patch.dict(sys.modules, {"pefile": None}):
            output = manager.application_icon(self.base, "example", self.executable)
        with Image.open(output) as image:
            self.assertEqual(image.getpixel((32, 32)), (40, 130, 200, 255))

    def test_existing_library_is_backfilled_and_offline_portable_icon_remains(self):
        self.executable.write_bytes(icon_executable())
        recipes = self.root / "recipes.json"
        recipes.write_text("[]")
        manager.atomic_json(self.base / "apps/local-example.json", {
            "id": "managed.local-example", "executable": str(self.executable), "icon": ""})
        worker = manager.Manager(self.base, recipes, [])
        output = worker.library()[0]["icon"]
        self.assertTrue(Path(output).is_file())
        self.executable.unlink()
        self.assertEqual(worker.library()[0]["icon"], output)

    def test_new_recipe_and_portable_registrations_publish_extracted_icons(self):
        self.executable.write_bytes(icon_executable())
        recipe = {"id": "example", "title": "Example", "version": "1", "filename": "App.exe",
                  "sha256": hashlib.sha256(self.executable.read_bytes()).hexdigest(),
                  "size": self.executable.stat().st_size, "executable": "drive_c/App.exe",
                  "verify_files": [], "input_mode": "pointer"}
        recipes = self.root / "recipes.json"
        recipes.write_text(json.dumps([recipe]))
        worker = manager.Manager(self.base, recipes, [])

        def installed(_recipe, _installer, prefix, _log):
            (prefix / "drive_c").mkdir()
            (prefix / recipe["executable"]).write_bytes(self.executable.read_bytes())

        with patch.object(worker, "run_installer", side_effect=installed):
            worker.install(recipe, self.executable)
        self.assertEqual(worker.state["status"], "done")
        self.assertTrue(Path(worker.library()[0]["icon"]).is_file())
        manager.atomic_json(self.base / "jobs/local-example.json", {
            "status": "select", "portable": str(self.executable),
            "choices": [{"id": "portable", "title": "Portable Example"}]})
        self.assertEqual(manager.register_local(self.base, "local-example", "portable"), 0)
        portable = manager.read_json(self.base / "apps/local-example.json", {})
        self.assertTrue(Path(portable["icon"]).is_file())

    def test_invalid_identifiers_symlinks_and_broken_artwork_are_ignored(self):
        self.executable.write_bytes(b"MZ bad executable")
        (self.root / "App.ico").write_bytes(b"bad icon")
        self.assertEqual(manager.application_icon(self.base, "../../escape", self.executable), "")
        self.assertEqual(manager.application_icon(self.base, "example", self.executable), "")
        linked = self.root / "linked.exe"
        linked.symlink_to(self.executable)
        self.assertEqual(manager.application_icon(self.base, "linked", linked), "")
        other = self.root / "other"
        other.mkdir()
        (self.base / "icons").rename(self.base / "old-icons")
        (self.base / "icons").symlink_to(other, target_is_directory=True)
        self.assertEqual(manager.application_icon(self.base, "example", self.executable), "")
        self.assertEqual(list(other.iterdir()), [])

    def test_icon_cli_extracts_without_installing_or_running_an_application(self):
        self.executable.write_bytes(icon_executable())
        result = subprocess.run([sys.executable, str(manager.__file__), "icon", "legacy-example", str(self.executable)],
                                env=dict(os.environ, MARWANOS_WINDOWS_HOME=str(self.base)),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path(result.stdout.strip()).is_file())
        self.assertFalse((self.base / "apps").exists())
        self.assertFalse((self.base / "prefixes").exists())
        self.executable.write_bytes(b"not an application")
        missing = subprocess.run([sys.executable, str(manager.__file__), "icon", "legacy-missing", str(self.executable)],
                                 env=dict(os.environ, MARWANOS_WINDOWS_HOME=str(self.base)),
                                 text=True, capture_output=True)
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertEqual(missing.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
