#!/usr/bin/env python3
"""PC1's user-owned Windows install worker and managed application launcher.

Recipes provide optional unattended installs on a separate X server. General
EXE/MSI setup runs as the player on the shell's display, with explicit library
selection afterwards. Arguments are always passed as arrays, never shell code.
Wine prefixes are compatibility environments, not security sandboxes.
"""

import argparse
import importlib.util
import contextlib
import fcntl
import hashlib
import io
import itertools
import json
import os
from pathlib import Path, PureWindowsPath
import re
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid


BASE = Path(os.environ.get("MARWANOS_WINDOWS_HOME", str(Path.home() / ".local/share/marwanos/windows")))
RECIPES = Path(os.environ.get("MARWANOS_WINDOWS_RECIPES", str(Path(__file__).with_name("recipes.json"))))
RUNNER = os.environ.get("MARWANOS_WINDOWS_RUNTIME", "umu-run")
HELPER = str(Path(__file__).resolve())
ACTIVE = {"queued", "downloading", "verifying", "installing", "removing"}


def download_flow():
    spec = importlib.util.spec_from_file_location('pc1_download_flow', Path(__file__).with_name('download_flow.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path, fallback):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return fallback


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def executable_icons(executable):
    """Rebuild ICO files from the executable's own group/icon resources."""
    try:
        import pefile
    except ImportError:
        return  # Sidecar artwork can still be decoded without a PE parser.

    pe = None
    try:
        # Filename loading uses pefile's mmap; fast_load avoids scanning the
        # executable's unrelated sections or copying large apps into memory.
        pe = pefile.PE(str(executable), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        resources = {}
        root = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
        remaining = 16 * 1024**2
        for kind in root.entries if root else []:
            if kind.id not in (3, 14):  # RT_ICON and RT_GROUP_ICON
                continue
            for item in kind.directory.entries[:64]:
                for language in item.directory.entries[:16]:
                    resource = language.data.struct
                    if 0 < resource.Size <= min(4 * 1024**2, remaining):
                        resources[kind.id, item.id, language.id] = pe.get_data(resource.OffsetToData, resource.Size)
                        remaining -= resource.Size
        for (kind, _identifier, language), group in resources.items():
            if kind != 14 or len(group) < 6:
                continue
            reserved, icon_type, count = struct.unpack_from("<HHH", group)
            if reserved or icon_type != 1 or not 0 < count <= 64 or len(group) < 6 + count * 14:
                continue
            entries, images = [], []
            offset = 6 + count * 16
            for index in range(count):
                record = group[6 + index * 14:6 + (index + 1) * 14]
                size, identifier = struct.unpack_from("<IH", record, 8)
                image = resources.get((3, identifier, language))
                if image is None:
                    image = next((blob for (kind, key, _lang), blob in resources.items()
                                  if kind == 3 and key == identifier), None)
                if image is None or len(image) != size:
                    break
                entries.append(record[:12] + struct.pack("<I", offset))
                images.append(image)
                offset += size
            else:
                yield group[:6] + b"".join(entries + images)
    except (pefile.PEFormatError, OSError, AttributeError, IndexError, ValueError, struct.error):
        return
    finally:
        if pe is not None:
            pe.close()


def application_icon(base, key, executable):
    """Cache actual Windows app artwork as a PNG the shell can load.

    Existing installations use the same path as newly registered ones. Cache
    misses are remembered until the executable or matching sidecar changes.
    """
    if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9-]+", key):
        return ""
    executable = Path(executable)
    icons = base / "icons"
    output = icons / (key + ".png")
    temporary = None
    try:
        from PIL import Image

        if executable.is_symlink() or icons.is_symlink():
            return ""
        if not executable.is_file():
            return str(output) if output.is_file() and not output.is_symlink() else ""
        sidecars = sorted(path for path in executable.parent.iterdir()
                          if path.stem.casefold() == executable.stem.casefold()
                          and path.suffix.casefold() in {".ico", ".png", ".bmp"}
                          and not path.is_symlink() and path.is_file())
        signature = [[str(path), path.stat().st_size, path.stat().st_mtime_ns]
                     for path in [executable, *sidecars]]
        metadata = icons / (key + ".json")
        cached = read_json(metadata, {})
        if isinstance(cached, dict) and cached.get("source") == signature:
            if cached.get("found") and output.is_file() and not output.is_symlink():
                return str(output)
            if not cached.get("found"):
                return ""
        icons.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Embedded icons belong to the selected app; matching adjacent images
        # support portable applications which ship their artwork separately.
        sources = (io.BytesIO(blob) for blob in executable_icons(executable))
        for source in itertools.chain(sources, sidecars):
            try:
                if isinstance(source, Path) and source.stat().st_size > 4 * 1024**2:
                    continue
                with Image.open(source) as image:
                    if image.width > 4096 or image.height > 4096:
                        continue
                    image = image.convert("RGBA")
                    image.thumbnail((256, 256), Image.Resampling.LANCZOS)
                    temporary = output.with_name(output.name + "." + uuid.uuid4().hex + ".tmp")
                    with temporary.open("xb") as stream:
                        os.chmod(temporary, 0o600)
                        image.save(stream, format="PNG")
                    temporary.replace(output)
                atomic_json(metadata, {"source": signature, "found": True})
                return str(output)
            except (OSError, ValueError, EOFError, SyntaxError, IndexError, struct.error,
                    Image.DecompressionBombError):
                if temporary is not None:
                    with contextlib.suppress(OSError):
                        temporary.unlink(missing_ok=True)
                    temporary = None
                continue
        atomic_json(metadata, {"source": signature, "found": False})
    except (ImportError, OSError, ValueError):
        pass  # Missing or damaged artwork must never prevent app installation.
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
    return ""


@contextlib.contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def runtime_env(prefix, installing=False):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("STEAM_", "Steam", "GAMESCOPE_")) or key in {
            "WINEPREFIX", "GAMEID", "PROTON_VERB", "ENABLE_GAMESCOPE_WSI"
        }:
            env.pop(key, None)
    env.update(WINEPREFIX=str(prefix), GAMEID="0", PROTON_VERB="waitforexitandrun")
    if installing:
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
            env.pop(key, None)
        env.update(WINEDLLOVERRIDES="winemenubuilder.exe=d", WINEDEBUG="-all")
    return env


def stop_group(process):
    # Always sweep the group, even if its leader exited before its children.
    with contextlib.suppress(ProcessLookupError):
        if process.poll() is None:
            # umu forwards the signal to its runtime. Simultaneously signalling
            # every child makes its forwarding handler race already-dead PIDs.
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


class Cancelled(Exception):
    pass


class InstallError(Exception):
    pass


@contextlib.contextmanager
def hidden_display(log):
    reader, writer = os.pipe()
    process = None
    try:
        process = subprocess.Popen(
            ["Xvfb", "-displayfd", str(writer), "-screen", "0", "1280x720x24", "-nolisten", "tcp", "-extension", "GLX"],
            pass_fds=(writer,), stdout=log, stderr=log, start_new_session=True,
        )
        os.close(writer)
        writer = -1
        with selectors.DefaultSelector() as selector:
            selector.register(reader, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise InstallError("Could not prepare the background installer. Try again.")
        number = os.read(reader, 64).decode().strip()
        if not number.isdigit():
            raise InstallError("Could not prepare the background installer. Try again.")
        yield ":" + number
    finally:
        os.close(reader)
        if writer >= 0:
            os.close(writer)
        if process is not None:
            stop_group(process)


class Manager:
    def __init__(self, base=BASE, recipes_path=RECIPES, roots=None):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        cleanup_orphans(self.base)
        self.requests = self.base / "requests"
        self.requests.mkdir(exist_ok=True)
        self.recipes = {r["id"]: r for r in json.loads(Path(recipes_path).read_text())}
        for key, recipe in self.recipes.items():
            if not re.fullmatch(r"[a-z0-9-]+", key) or not re.fullmatch(r"[a-f0-9]{64}", recipe["sha256"]):
                raise ValueError("Invalid recipe identifier or checksum")
            for relative in [recipe["executable"], *recipe["verify_files"]]:
                if Path(relative).is_absolute() or ".." in Path(relative).parts:
                    raise ValueError("Recipe path escapes the application prefix")
        self.roots = roots if roots is not None else [Path.home() / "Downloads", Path("/run/media/player")]
        self.state = read_json(self.base / "state.json", {})
        if not isinstance(self.state, dict):
            self.state = {}
        if self.state.get("status") in ACTIVE:
            self.state.update(status="failed", detail="Installation was interrupted. Select the installer to retry.")
        self.state.setdefault("status", "idle")
        self.state.setdefault("detail", "Choose an app to install.")
        self.cancel = threading.Event()
        self.thread = None
        self.guard = threading.Lock()
        self.sources = {}
        self.scan()

    def update(self, **values):
        with self.guard:
            self.state.update(values)

    def scan(self):
        sources = {}
        # Bounded traversal: removable storage may contain millions of files.
        for root in self.roots:
            root = Path(root)
            if not root.is_dir():
                continue
            visited = 0
            for directory, folders, files in os.walk(root, followlinks=False):
                visited += 1
                if visited > 512:
                    break
                if len(Path(directory).relative_to(root).parts) >= 3:
                    folders[:] = []
                folders[:] = sorted(f for f in folders if not f.startswith("."))
                for name in sorted(files):
                    if not name.lower().endswith((".exe", ".msi")):
                        continue
                    path = Path(directory) / name
                    if path.is_symlink() or not path.is_file():
                        continue
                    if not path.resolve().is_relative_to(root.resolve()):
                        continue
                    recipe = next((r for r in self.recipes.values() if r["filename"] == name), None)
                    source_id = hashlib.sha256(str(path).encode()).hexdigest()
                    sources[source_id] = {
                        "id": source_id, "path": str(path), "name": name,
                        "recipe_id": recipe["id"] if recipe else "",
                        "location": root.name,
                    }
                    if len(sources) >= 200:
                        break
                if len(sources) >= 200:
                    break
        self.sources = sources

    def library(self):
        result = []
        for path in sorted((self.base / "apps").glob("*.json")):
            entry = read_json(path, {})
            if isinstance(entry, dict) and isinstance(entry.get("executable"), str) and entry.get("id"):
                if not entry.get("icon") or Path(str(entry["icon"])).parent == self.base / "icons":
                    entry["icon"] = application_icon(self.base, path.stem, entry["executable"])
                if not Path(entry["executable"]).is_file():
                    entry = dict(entry, subtitle="App file unavailable. Reconnect its drive or remove this app.")
                result.append(entry)
        return result

    def publish(self):
        with self.guard:
            snapshot = dict(self.state)
        snapshot.update(
            heartbeat=time.time(), library=self.library(), candidates=list(self.sources.values()),
            downloads=download_flow().refresh(self.base),
            recipes=[{k: r[k] for k in ("id", "title", "version", "filename")} for r in self.recipes.values()],
        )
        atomic_json(self.base / "state.json", snapshot)

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled()

    def handle(self, request):
        if not isinstance(request, dict):
            return
        verb = request.get("verb")
        if verb in {"download", "download-action"}:
            try:
                flow = download_flow()
                root = Path.home() / "Downloads"
                if verb == "download":
                    flow.offer(self.base, root, request)
                else:
                    flow.action(self.base, root, request.get("download_id"), request.get("action"))
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.update(detail=str(error))
            return
        if verb in {"remove", "discard"}:
            if self.thread is not None and self.thread.is_alive():
                return
            key = request.get("app_id", "")
            if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9-]+", key):
                self.update(status="failed", detail="Invalid application identifier.")
                return
            self.update(status="removing", detail="Removing application…", progress=-1, operation=verb)
            def remove():
                try:
                    (remove_app if verb == "remove" else discard_setup)(self.base, key)
                    self.update(status="done", detail="Application removed." if verb == "remove" else "Setup files removed.")
                except (OSError, InstallError, BlockingIOError) as error:
                    self.update(status="failed", detail=str(error) if isinstance(error, InstallError) else
                                "Could not remove the application. Close it and try again.")
            self.thread = threading.Thread(target=remove, daemon=False)
            self.thread.start()
            return
        if verb == "cancel":
            if request.get("job_id") == self.state.get("job_id"):
                self.cancel.set()
            return
        if verb != "install" or self.thread is not None and self.thread.is_alive():
            return
        key = request.get("recipe_id")
        source = request.get("source_id", "download")
        if not isinstance(key, str) or key not in self.recipes:
            self.update(status="failed", detail="This installer is not supported yet.")
            return
        if source != "download" and (not isinstance(source, str) or source not in self.sources or self.sources[source]["recipe_id"] != key):
            self.update(status="failed", detail="The installer is no longer available. Reconnect the drive and try again.")
            return
        if any(e.get("recipe_id") == key for e in self.library()):
            self.update(status="done", detail="Already installed. Open it from the library.")
            return
        self.cancel.clear()
        self.update(status="queued", detail="Preparing installation", recipe_id=key, progress=-1, job_id=uuid.uuid4().hex, operation="install")
        path = None if source == "download" else Path(self.sources[source]["path"])
        self.thread = threading.Thread(target=self.install, args=(self.recipes[key], path), daemon=False)
        self.thread.start()

    def consume(self):
        for path in sorted(self.requests.glob("*.json"))[:32]:
            try:
                if not path.is_symlink() and path.is_file() and path.stat().st_size <= 4096:
                    self.handle(read_json(path, {}))
            except FileNotFoundError:
                pass
            finally:
                path.unlink(missing_ok=True)

    def copy_installer(self, recipe, source, target):
        self.update(status="downloading" if source is None else "verifying",
                    detail="Downloading installer" if source is None else "Checking installer", progress=0)
        digest = hashlib.sha256()
        if source is None:
            if not recipe["url"].startswith("https://"):
                raise InstallError("The download source is invalid.")
            stream = urllib.request.urlopen(recipe["url"], timeout=15)
            if not stream.geturl().startswith("https://"):
                stream.close()
                raise InstallError("The download source is invalid.")
        else:
            stream = source.open("rb")
        count = 0
        deadline = time.monotonic() + 300
        with stream, target.open("xb") as output:
            while True:
                self.check_cancel()
                if time.monotonic() > deadline:
                    raise InstallError("The download took too long. Check your connection and retry.")
                chunk = stream.read(65536)
                if not chunk:
                    break
                count += len(chunk)
                if count > recipe["size"]:
                    raise InstallError("This installer does not match the supported version.")
                output.write(chunk)
                digest.update(chunk)
                self.update(progress=int(count * 100 / recipe["size"]))
        self.check_cancel()
        if count != recipe["size"] or digest.hexdigest() != recipe["sha256"]:
            raise InstallError("This installer does not match the supported version. Download a fresh copy and retry.")

    def run_installer(self, recipe, installer, prefix, log):
        env = runtime_env(prefix, installing=True)
        with hidden_display(log) as display:
            env["DISPLAY"] = display
            process = subprocess.Popen([RUNNER, str(installer), *recipe["arguments"]],
                                       env=env, cwd=installer.parent, stdout=log, stderr=log, start_new_session=True)
            deadline = time.monotonic() + recipe["timeout_seconds"]
            try:
                while process.poll() is None:
                    self.check_cancel()
                    if time.monotonic() >= deadline:
                        raise InstallError("Installation took too long. Select the installer to retry.")
                    time.sleep(0.2)
                if process.returncode != 0:
                    raise InstallError("Installation failed. Check your connection and free space, then retry.")
            finally:
                stop_group(process)

    def install(self, recipe, source):
        attempt = self.base / "prefixes" / (recipe["id"] + "-" + uuid.uuid4().hex)
        committed = False
        try:
            attempt.mkdir(parents=True)
            if shutil.disk_usage(self.base).free < 2 * 1024**3:
                raise InstallError("At least 2 GB of free space is needed. Free some space and retry.")
            with tempfile.TemporaryDirectory(prefix="pc1-install-") as work:
                installer = Path(work) / recipe["filename"]
                self.copy_installer(recipe, source, installer)
                self.check_cancel()
                self.update(status="installing", detail="Installing in the background. First-time setup may take several minutes.", progress=-1)
                logs = self.base / "logs"
                logs.mkdir(exist_ok=True)
                with (logs / (recipe["id"] + ".log")).open("wb") as log:
                    self.run_installer(recipe, installer, attempt, log)
                self.check_cancel()
                for relative in [recipe["executable"], *recipe["verify_files"]]:
                    installed = attempt / relative
                    if not installed.is_file() or installed.stat().st_size == 0 or not installed.resolve().is_relative_to(attempt.resolve()):
                        raise InstallError("Installation did not finish correctly. Select the installer to retry.")
                executable = attempt / recipe["executable"]
                entry = {
                    "id": "managed." + recipe["id"], "recipe_id": recipe["id"],
                    "title": recipe["title"], "version": recipe["version"],
                    "prefix": str(attempt), "executable": str(executable),
                    "input_mode": recipe["input_mode"], "state": "installed",
                    "exec": [HELPER, "launch", recipe["id"]],
                    "stop_exec": [HELPER, "stop", recipe["id"]],
                    "subtitle": "Windows app", "icon": application_icon(self.base, recipe["id"], executable),
                }
                atomic_json(self.base / "apps" / (recipe["id"] + ".json"), entry)
                committed = True
                self.update(status="done", detail=recipe["title"] + " is ready in your library.", progress=100)
        except Cancelled:
            self.update(status="cancelled", detail="Installation cancelled. You can start it again whenever you like.", progress=-1)
        except Exception as error:
            print("Windows installation failed:", repr(error), flush=True)
            detail = str(error) if isinstance(error, InstallError) else "Could not install. Check your connection and free space, then retry."
            self.update(status="failed", detail=detail, progress=-1)
        finally:
            if not committed and attempt.exists():
                # attempt is created by this worker, beneath its own prefixes root.
                try:
                    shutil.rmtree(attempt)
                except OSError as error:
                    print("Could not remove incomplete prefix:", repr(error), flush=True)

    def serve(self):
        stopping = threading.Event()
        def stop(_signum, _frame):
            stopping.set()
            self.cancel.set()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        next_scan = 0
        try:
            while not stopping.is_set():
                if time.monotonic() >= next_scan:
                    cleanup_orphans(self.base)
                    self.scan()
                    next_scan = time.monotonic() + 5
                self.consume()
                self.publish()
                stopping.wait(0.5)
        finally:
            self.cancel.set()
            if self.thread:
                self.thread.join()
            self.publish()


def proc_start(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def managed_prefix(base, key, entry=None):
    """Only a direct, real directory owned by this manager may be removed."""
    if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9-]+", key):
        raise InstallError("Invalid application identifier.")
    root = base / "prefixes"
    if any((base / folder).is_symlink() for folder in ("apps", "jobs", "running")):
        raise InstallError("The application's stored folder is invalid. Nothing was removed.")
    if entry and (not isinstance(entry, dict) or not isinstance(entry.get("prefix"), str)):
        raise InstallError("The application's stored folder is invalid. Nothing was removed.")
    prefix = Path(entry["prefix"]) if entry else root / key
    expected = prefix.name == key if key.startswith("local-") else bool(
        re.fullmatch(re.escape(key) + r"-[a-f0-9]{32}", prefix.name))
    if root.is_symlink() or prefix.is_symlink() or prefix.parent != root or not expected:
        raise InstallError("The application's stored folder is invalid. Nothing was removed.")
    if prefix.exists() and (not prefix.is_dir() or prefix.resolve().parent != root.resolve()):
        raise InstallError("The application's stored folder is invalid. Nothing was removed.")
    return prefix


def managed_games(prefix, create=False):
    """The one owned game folder mapped into this attempt's C: drive."""
    marker = prefix / "games.json"
    if not create and not marker.exists():
        return None  # Older installs and portable apps keep their existing layout.
    root = Path.home() / "Games"
    destination = root / prefix.name
    if not re.fullmatch(r"local-[a-z0-9-]+", prefix.name) or root.is_symlink() or destination.is_symlink():
        raise InstallError("The game's stored folder is invalid. Nothing was removed.")
    if destination.exists() and (not destination.is_dir() or destination.resolve().parent != root.resolve()):
        raise InstallError("The game's stored folder is invalid. Nothing was removed.")
    if create:
        destination.mkdir(parents=True, exist_ok=False)
        atomic_json(marker, {"directory": str(destination)})
        drive = prefix / "drive_c"
        drive.mkdir(exist_ok=True)
        (drive / "Games").symlink_to(destination, target_is_directory=True)
        try:
            # Test the actual mapped path, rather than just permission bits.
            with tempfile.TemporaryFile(dir=drive / "Games", prefix=".marwanos-write-") as probe:
                probe.write(b"MarwanOS installation directory check")
                probe.flush()
        except OSError as error:
            raise InstallError(f"The default installation folder is not writable: {destination}") from error
    else:
        stored = read_json(marker, {})
        if marker.is_symlink() or not isinstance(stored, dict) or stored.get("directory") != str(destination):
            raise InstallError("The game's stored folder is invalid. Nothing was removed.")
    return destination


def remove_app(base, key):
    # Stop first; then acquire the same lock as launch so deletion cannot race it.
    path = base / "apps" / (key + ".json")
    entry = read_json(path, {})
    if not isinstance(entry, dict) or entry.get("id") != "managed." + key:
        raise InstallError("This application is no longer in the library.")
    prefix = managed_prefix(base, key, entry)
    ui = setup_ui_directory(base, key)
    games = managed_games(prefix)
    stop_app(base, key)
    with exclusive(base / "running" / (key + ".lock")):
        if games is not None and games.exists():
            shutil.rmtree(games)
        if prefix.exists():
            shutil.rmtree(prefix)
        path.unlink()
        (base / "jobs" / (key + ".json")).unlink(missing_ok=True)
        (base / "running" / (key + ".json")).unlink(missing_ok=True)
        if ui.exists():
            shutil.rmtree(ui)
    # Portable files are outside the owned prefix and are never deleted.
    return 0


def discard_setup(base, key):
    if (base / "apps" / (key + ".json")).exists():
        raise InstallError("Remove the installed application from its library card.")
    with exclusive(base / "local-setup.lock"):
        prefix = managed_prefix(base, key)
        ui = setup_ui_directory(base, key)
        games = managed_games(prefix)
        if games is not None and games.exists():
            shutil.rmtree(games)
        if prefix.exists():
            shutil.rmtree(prefix)
        (base / "jobs" / (key + ".json")).unlink(missing_ok=True)
        if ui.exists():
            shutil.rmtree(ui)
    return 0


def setup_ui_directory(base, key):
    root = base / "setup-ui"
    ui = root / key
    if root.is_symlink() or ui.is_symlink() or (ui.exists() and (not ui.is_dir() or ui.resolve().parent != root.resolve())):
        raise InstallError("The setup's stored folder is invalid. Nothing was removed.")
    return ui


def cleanup_orphans(base):
    for path in (base / "running").glob("*.json"):
        try:
            # Launch holds this lock before publishing its record. Reading and
            # stopping without it could race a fresh launch replacing an orphan.
            with exclusive(path.with_suffix(".lock")):
                state = read_json(path, {})
                if not isinstance(state, dict):
                    continue
                owner = state.get("owner", 0)
                if isinstance(owner, int) and owner > 1 and state.get("owner_start"):
                    if proc_start(owner) == state["owner_start"]:
                        continue
                    stop_app(base, path.stem)
                    path.unlink(missing_ok=True)
        except BlockingIOError:
            continue
    for path in (base / "jobs").glob("local-*.json"):
        job = read_json(path, {})
        if not isinstance(job, dict) or job.get("status") not in {"preparing", "installing"}:
            continue
        created = job.get("created_at", 0)
        if isinstance(created, (int, float)) and time.time() - created < 30:
            continue
        state = read_json(base / "running" / path.name, {})
        if isinstance(state, dict) and state.get("owner_start") and proc_start(state.get("owner", 0)) == state["owner_start"]:
            continue
        try:
            prefix = managed_prefix(base, path.stem)
            job.update(status="failed", detail="Setup was interrupted. Add an installed program, retry, or remove setup files.",
                       choices=local_candidates(prefix))
            atomic_json(path, job)
        except (InstallError, OSError):
            continue


def windows_file(source):
    """Validate a local Windows file, without a recipe or location allowlist."""
    path = Path(source)
    if not path.is_absolute() or path.suffix.lower() not in {".exe", ".msi"}:
        raise InstallError("Choose a Windows EXE or MSI file.")
    path = path.resolve(strict=True)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise InstallError("Choose a regular installer file.")
        header = stream.read(64)
        if Path(source).suffix.lower() == ".msi":
            valid = header[:8] == bytes.fromhex("d0cf11e0a1b11ae1")
        else:
            valid = False
            if len(header) == 64 and header[:2] == b"MZ":
                stream.seek(struct.unpack_from("<I", header, 60)[0])
                valid = stream.read(4) == b"PE\0\0"
        if not valid:
            raise InstallError("This file is incomplete or is not a Windows installer. Download it again.")
    return path


def shortcut_target(path):
    """Read a local C: executable from MS-SHLLINK LinkInfo, never a command.

    Unsupported links fall back to executable discovery. All offsets and string
    terminators are bounded by LinkInfoSize; network and traversal targets are
    excluded. Candidate validation below still owns the execution boundary.
    """
    try:
        with path.open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or len(data) < 76 or data[:20] != bytes.fromhex(
                "4c0000000114020000000000c000000000000046"):
            return None
        flags = struct.unpack_from("<I", data, 20)[0]
        offset = 76
        if flags & 1:
            offset += 2 + struct.unpack_from("<H", data, offset)[0]
        if not flags & 2:
            return None
        size, header, info_flags = struct.unpack_from("<III", data, offset)
        if header < 28 or size < header or offset + size > len(data) or not info_flags & 1:
            return None
        info = data[offset:offset + size]
        def text(field, unicode=False):
            start = struct.unpack_from("<I", info, field)[0]
            if start < header or start >= size:
                raise ValueError("Invalid link string offset")
            width = 2 if unicode else 1
            end = start
            while end + width <= size and info[end:end + width] != b"\0" * width:
                end += width
            if end + width > size:
                raise ValueError("Unterminated link string")
            return info[start:end].decode("utf-16-le" if unicode else "cp1252")
        target = text(28, True) if header >= 36 and struct.unpack_from("<I", info, 28)[0] else text(16)
        suffix = text(32, True) if header >= 36 and struct.unpack_from("<I", info, 32)[0] else text(24)
        if suffix and not target.casefold().endswith(suffix.casefold()):
            target = str(PureWindowsPath(target) / suffix)
        target = PureWindowsPath(target)
        if not target.is_absolute() or target.drive.casefold() != "c:" or ".." in target.parts:
            return None
        return str(Path("drive_c", *target.parts[1:]))
    except (OSError, ValueError, IndexError, struct.error):
        return None


def local_candidates(prefix):
    """Discover launch targets only inside C:, excluding Wine and maintenance tools.

    Only the explicitly owned Games mapping may be traversed outside C:.
    Return relative names so library choices cannot become arbitrary commands.
    """
    drive = prefix / "drive_c"
    if drive.is_symlink() or not drive.is_dir():
        return []
    result = []
    shortcuts = {}
    games = managed_games(prefix)
    mapping = drive / "Games"
    mapped = games is not None and mapping.is_symlink() and mapping.resolve() == games.resolve()
    directories = itertools.chain(os.walk(drive, followlinks=False),
        os.walk(mapping, followlinks=False) if mapped else [])
    for directory, folders, files in directories:
        relative = Path(directory).relative_to(drive)
        folders[:] = sorted(f for f in folders if not (Path(directory) / f).is_symlink()
                            and f.lower() not in {"windows", "$recycle.bin", "temp", "installer"})
        for name in sorted(files):
            path = Path(directory) / name
            if name.lower().endswith(".lnk") and not path.is_symlink() and (
                    "desktop" in {part.lower() for part in relative.parts} or
                    "start menu" in {part.lower() for part in relative.parts}):
                target = shortcut_target(path)
                if target:
                    shortcuts.setdefault(target.casefold(), Path(name).stem)
                continue
            if not name.lower().endswith(".exe") or re.match(r"(?i)(unins|uninstall|setup|vcredist|vc_redist)", name):
                continue
            if path.is_symlink() or not (path.resolve().is_relative_to(drive.resolve()) or
                    (mapped and path.resolve().is_relative_to(games.resolve()))):
                continue
            try:
                windows_file(path)
                # Proton seeds programs such as WordPad and Internet Explorer
                # outside C:\windows too. They are runtime files, not installs.
                with path.open("rb") as stream:
                    if b"Wine builtin DLL" in stream.read(128):
                        continue
            except (OSError, InstallError):
                continue
            result.append({"id": str(path.relative_to(prefix)), "title": Path(name).stem,
                           "detail": str(relative / name)})
    for candidate in result:
        title = shortcuts.get(candidate["id"].casefold())
        if title:
            candidate.update(title=title, shortcut=True)
    return sorted(result, key=lambda item: (not item.get("shortcut", False), item["title"].casefold()))


def inno_installer(path):
    """Identify Inno's loader data before using its documented /DIR option."""
    if path.suffix.lower() != ".exe":
        return False
    marker = b"Inno Setup Setup Data ("
    tail = b""
    with path.open("rb") as stream:
        # Inno's loader signature precedes its embedded installation payload.
        for _ in range(64):
            block = stream.read(1024 * 1024)
            if not block:
                break
            if marker in tail + block:
                return True
            tail = block[-len(marker):]
    return False


def local_setup(base, key, source, portable=False, guided=False):
    """Called by Launcher in the player session, never by the hidden daemon."""
    job_path = base / "jobs" / (key + ".json")
    prefix = base / "prefixes" / key
    job = {"id": key, "source": source, "created_at": time.time(), "status": "preparing", "choices": [],
           "detail": "Preparing Windows setup. First-time setup may take several minutes.", "guided": guided}
    with exclusive(base / "local-setup.lock"):
        # A new key per attempt preserves partial installations and previous apps.
        if job_path.exists() or prefix.exists():
            raise InstallError("This installation attempt already exists. Start a new attempt.")
        atomic_json(job_path, job)
        running = base / "running" / (key + ".json")
        cancelled = threading.Event()
        def stop(_signum, _frame):
            cancelled.set()
        old_signals = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
        # Close is available during hidden-display preparation, before umu exists.
        atomic_json(running, {"pid": os.getpid(), "start": proc_start(os.getpid()),
                              "owner": os.getpid(), "owner_start": proc_start(os.getpid()),
                              "prefix": str(prefix)})
        try:
            installer = windows_file(source)
            prefix.mkdir(parents=True)
            if portable:
                if Path(source).suffix.lower() != ".exe":
                    raise InstallError("Portable apps must be EXE files.")
                job.update(status="select", portable=str(installer), choices=[{
                    "id": "portable", "title": installer.stem, "detail": str(installer)}])
                atomic_json(job_path, job)
                return register_local(base, key, "portable")
            games = managed_games(prefix, create=True)
            job["install_directory"] = str(games)
            atomic_json(job_path, job)
            env = runtime_env(prefix)
            env.update(WINEDLLOVERRIDES="winemenubuilder.exe=d")
            # Keep adjacent CAB/BIN files in place: multipart installers need them.
            args = [RUNNER, str(installer)]
            inno = inno_installer(installer)
            if not guided and inno:
                args.append(r"/DIR=C:\Games")
            if Path(source).suffix.lower() == ".msi":
                args = [RUNNER, "msiexec", "/i", "Z:" + str(installer).replace("/", "\\")]
            logs = base / "logs"
            logs.mkdir(exist_ok=True)
            with (logs / (key + ".log")).open("wb") as log:
                with contextlib.ExitStack() as stack:
                    if guided:
                        bridge = Path(os.environ.get("MARWANOS_SETUP_BRIDGE", str(Path(__file__).with_name("setup-bridge.exe"))))
                        if not bridge.is_file():
                            raise InstallError("Controller setup needs the updated system helper. Use Original Windows setup.")
                        ui = setup_ui_directory(base, key)
                        ui.mkdir(parents=True, mode=0o700)
                        # Steam's runtime replaces /usr. Put the image-owned
                        # adapter in user data, which is visible inside it.
                        runtime_bridge = ui / "setup-bridge.exe"
                        shutil.copyfile(bridge, runtime_bridge)
                        runtime_bridge.chmod(0o600)
                        env = runtime_env(prefix, installing=True)
                        env["MARWANOS_SETUP_DESTINATION"] = r"C:\Games"
                        env["MARWANOS_SETUP_HOST_DIRECTORY"] = str(games)
                        env["MARWANOS_SETUP_INNO"] = "1" if inno else "0"
                        env["DISPLAY"] = stack.enter_context(hidden_display(log))
                        windows_path = lambda path: "Z:" + str(path).replace("/", "\\")
                        args = [RUNNER, str(runtime_bridge), windows_path(installer),
                                windows_path(ui / "page.json"), windows_path(ui / "action.bin")]
                        job["guided"] = True
                    process = subprocess.Popen(args, env=env, cwd=installer.parent,
                                               stdout=log, stderr=log, start_new_session=True)
                    # Keep the hidden display alive for the entire runtime below.
                    display_cleanup = stack.pop_all()
                # Record the wrapper too: Close must stop discovery/publication,
                # as well as every installer process in the runtime group.
                atomic_json(running, {"pid": process.pid, "start": proc_start(process.pid),
                                     "owner": os.getpid(), "owner_start": proc_start(os.getpid()),
                                     "prefix": str(prefix)})
                job.update(status="installing", detail="Complete the Windows setup window, then close it to continue.")
                atomic_json(job_path, job)
                setup_exit = None
                try:
                    while process.poll() is None and not cancelled.wait(0.2):
                        if guided:
                            page = read_json(ui / "page.json", {})
                            if (isinstance(page, dict) and page.get("finished") is True
                                    and type(page.get("exit_code")) is int and 0 <= page["exit_code"] <= 0xffffffff):
                                # Installed Windows services can keep umu's
                                # waitforexit wrapper alive after the wizard ends.
                                setup_exit = page["exit_code"]
                                break
                finally:
                    stop_group(process)
                    display_cleanup.close()
                    running.unlink(missing_ok=True)
                choices = local_candidates(prefix)
                exit_code = process.returncode if setup_exit is None else setup_exit
                if cancelled.is_set():
                    job.update(status="select" if choices else "cancelled", choices=choices,
                               detail="Setup was closed. Choose an installed program to add to your library." if choices else
                               "Setup was closed. You can start it again.")
                elif exit_code not in (0, 3010):
                    job.update(status="failed", detail="Windows setup exited with an error (%s). You can retry." % exit_code,
                               choices=choices)
                else:
                    job.update(status="select" if choices else "empty", choices=choices,
                               detail="Choose the program to add to your library." if choices else
                               "No installed program was found. Retry setup, or use Add as portable app for a standalone EXE.")
                job["exit_code"] = exit_code
                atomic_json(job_path, job)
                return 0
        except (OSError, InstallError) as error:
            job.update(status="failed", detail=str(error), choices=[])
            atomic_json(job_path, job)
            return 1
        finally:
            running.unlink(missing_ok=True)
            for sig, handler in old_signals.items():
                signal.signal(sig, handler)


def register_local(base, key, choice, input_mode="pointer"):
    job_path = base / "jobs" / (key + ".json")
    job = read_json(job_path, {})
    try:
        if input_mode not in {"pointer", "gamepad", "keys"}:
            raise InstallError("Choose a supported controller profile.")
        if job.get("status") not in {"select", "failed"}:
            raise InstallError("Finish setup before adding a program.")
        prefix = base / "prefixes" / key
        candidates = job.get("choices", []) if job.get("portable") else local_candidates(prefix)
        selected = next((item for item in candidates if item["id"] == choice), None)
        if not selected:
            raise InstallError("That program is no longer available. Run setup again.")
        executable = windows_file(job["portable"] if job.get("portable") else prefix / choice)
        entry = {"id": "managed." + key, "recipe_id": key, "title": selected["title"],
                 "prefix": str(prefix), "executable": str(executable),
                 "input_mode": "" if input_mode == "gamepad" else input_mode,
                 "kind": "game" if input_mode == "gamepad" else "application",
                 "state": "installed", "subtitle": "Windows app", "icon": application_icon(base, key, executable),
                 "exec": [HELPER, "launch", key], "stop_exec": [HELPER, "stop", key]}
        entry["portable"] = bool(job.get("portable"))
        atomic_json(base / "apps" / (key + ".json"), entry)
        job.update(status="done", detail=entry["title"] + " is ready in your library.", choices=[])
        atomic_json(job_path, job)
        return 0
    except (OSError, InstallError) as error:
        job.update(status="failed", detail=str(error))
        atomic_json(job_path, job)
        return 1


def launch(base, key):
    entry = read_json(base / "apps" / (key + ".json"), {})
    if not isinstance(entry, dict):
        raise InstallError("Application is missing. Install it again.")
    executable = Path(entry.get("executable", ""))
    if not executable.is_file():
        raise InstallError("Application is missing. Install it again.")
    prefix = managed_prefix(base, key, entry)
    job = read_json(base / "jobs" / (key + ".json"), {})
    portable = entry.get("portable") or (isinstance(job, dict) and job.get("portable") == str(executable))
    games = managed_games(prefix)
    if not portable and not (executable.resolve().is_relative_to(prefix.resolve()) or
            (games is not None and executable.resolve().is_relative_to(games.resolve()))):
        raise InstallError("The application's stored program is invalid.")
    with exclusive(base / "running" / (key + ".lock")):
        process = subprocess.Popen([RUNNER, str(executable)], env=runtime_env(Path(entry["prefix"])),
                                   cwd=executable.parent, start_new_session=True)
        running = base / "running" / (key + ".json")
        atomic_json(running, {"pid": process.pid, "start": proc_start(process.pid),
                             "owner": os.getpid(), "owner_start": proc_start(os.getpid()),
                             "prefix": str(prefix)})
        cancelled = threading.Event()
        def stop(_signum, _frame):
            cancelled.set()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            while process.poll() is None and not cancelled.wait(0.2):
                pass
            return process.returncode if process.returncode is not None else 0
        finally:
            stop_group(process)
            running.unlink(missing_ok=True)


def stop_app(base, key):
    state = read_json(base / "running" / (key + ".json"), {})
    if not isinstance(state, dict):
        return
    pid = state.get("pid", 0)
    if not isinstance(pid, int) or pid <= 1 or not state.get("start"):
        return
    watched_pid, watched_start = pid, state["start"]
    owner = state.get("owner", 0)
    owner_signalled = False
    if isinstance(owner, int) and owner > 1 and state.get("owner_start") and proc_start(owner) == state["owner_start"]:
        with contextlib.suppress(ProcessLookupError):
            os.kill(owner, signal.SIGTERM)
            owner_signalled = True
    if proc_start(pid) != state["start"]:
        # A runtime leader can exit before its descendants. Verify a surviving
        # member's session, start time and prefix before touching the group.
        expected = state.get("prefix")
        if not isinstance(expected, str):
            return
        entry = read_json(base / "apps" / (key + ".json"), {})
        try:
            prefix = managed_prefix(base, key, entry or None)
        except (InstallError, KeyError):
            return
        if str(prefix) != expected:
            return
        verified = False
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
                if proc.stat().st_uid != os.getuid() or int(fields[2]) != pid or int(fields[3]) != pid:
                    continue
                if int(fields[19]) < int(state["start"]):
                    continue
                if os.fsencode("WINEPREFIX=" + expected) in (proc / "environ").read_bytes().split(b"\0"):
                    verified = True
                    watched_pid, watched_start = int(proc.name), fields[19]
                    break
            except (OSError, IndexError, ValueError):
                continue
        if not verified:
            return
    with contextlib.suppress(ProcessLookupError):
        if not owner_signalled:
            os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while proc_start(watched_pid) == watched_start and time.monotonic() < deadline:
        time.sleep(0.1)
    if proc_start(watched_pid) == watched_start:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["daemon", "launch", "stop", "setup", "guided", "portable", "register", "remove", "discard", "icon"])
    parser.add_argument("app", nargs="?")
    parser.add_argument("source", nargs="?")
    parser.add_argument("input_mode", nargs="?", choices=["pointer", "gamepad", "keys"], default="pointer")
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "daemon":
        with exclusive(BASE / "worker.lock"):
            Manager().serve()
        return 0
    if not args.app or not re.fullmatch(r"[a-z0-9-]+", args.app):
        parser.error("an application identifier is required")
    if args.command == "icon":
        if not args.source or not Path(args.source).is_absolute():
            parser.error("an absolute executable path is required")
        print(application_icon(BASE, args.app, args.source))
        return 0
    if args.command in {"setup", "guided", "portable", "register"}:
        if not re.fullmatch(r"local-[a-z0-9-]+", args.app) or not args.source:
            parser.error("a local attempt identifier and source are required")
        if args.command == "register":
            return register_local(BASE, args.app, args.source, args.input_mode)
        return local_setup(BASE, args.app, args.source, portable=args.command == "portable", guided=args.command == "guided")
    if args.command == "launch":
        return launch(BASE, args.app)
    if args.command == "remove":
        return remove_app(BASE, args.app)
    if args.command == "discard":
        return discard_setup(BASE, args.app)
    stop_app(BASE, args.app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
