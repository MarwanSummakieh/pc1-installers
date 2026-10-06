"""Receipts for completed downloads, installation, and confirmed cleanup."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


def read(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def fingerprint(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('The downloaded file is unavailable.')
    state = path.stat()
    return {'path': str(path), 'device': state.st_dev, 'inode': state.st_ino,
            'size': state.st_size, 'modified_ns': state.st_mtime_ns}


def contained(path, root):
    path, root = Path(path), Path(root).resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Only completed files in Downloads are eligible.')
    # Keep the lexical and real paths identical, excluding redirected parents.
    if path.absolute() != path.resolve():
        raise ValueError('Linked download paths are not eligible.')
    return path.resolve()


def offer(base, root, event):
    root = Path(root).resolve()
    text = event.get('path', event.get('name', ''))
    if not isinstance(text, str) or not text or len(text) > 4096:
        return []
    if text.startswith(('Z:\\', 'z:\\')):
        text = '/' + text[3:].replace('\\', '/')
    path = Path(text)
    if not path.is_absolute():
        if path.name != text or text in ('.', '..'):
            return []
        matches = []
        if root.is_dir():
            for index, (directory, folders, files) in enumerate(os.walk(root, followlinks=False)):
                if index >= 512:
                    break
                if len(Path(directory).relative_to(root).parts) >= 3:
                    folders[:] = []
                if path.name in files or path.name in folders:
                    matches.append(Path(directory) / path.name)
                if len(matches) > 1:
                    return []  # A notification filename cannot resolve ambiguity.
        if len(matches) != 1:
            return []
        path = matches[0]
    path = contained(path, root)
    torrent = event.get('torrent') is True or path.is_dir()
    sources = [path] if path.is_file() else []
    if path.is_dir():
        for index, (directory, folders, names) in enumerate(os.walk(path, followlinks=False)):
            if index >= 512 or len(sources) >= 20:
                break
            if len(Path(directory).relative_to(path).parts) >= 3:
                folders[:] = []
            sources.extend(Path(directory) / name for name in sorted(names)
                           if name.lower().startswith('setup') and name.lower().endswith(('.exe', '.msi')))
        sources = sources[:20]
    result = []
    for source in sources:
        source = contained(source, root)
        if source.suffix.lower() not in ('.exe', '.msi'):
            continue
        with source.open('rb') as stream:
            signature = stream.read(8)
        if not (signature.startswith(b'MZ') or
                source.suffix.lower() == '.msi' and signature == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
            continue
        identity = fingerprint(source)
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        receipt = Path(base) / 'downloads' / (key + '.json')
        if receipt.exists():
            result.append(read(receipt))
            continue
        files = [identity]
        # A nearby BIN/CAB is not proof of package ownership. Keep multipart
        # payloads intact; without a package manifest cleanup covers setup only.
        value = {'id': key, 'source': str(source), 'files': files,
                 'torrent': torrent, 'status': 'ready', 'created_at': time.time()}
        save(receipt, value)
        result.append(value)
    return result


def installed_job(base, receipt):
    candidates = []
    for path in (Path(base) / 'jobs').glob('*.json'):
        job = read(path)
        if (job.get('source') == receipt.get('source') and
                job.get('created_at', 0) >= receipt.get('created_at', float('inf'))):
            candidates.append(job)
    job = max(candidates, key=lambda value: value.get('created_at', 0), default={})
    if job.get('status') != 'done' or job.get('exit_code') not in (0, 3010):
        return {}
    entry = read(Path(base) / 'apps' / (str(job.get('id', '')) + '.json'))
    if entry.get('portable') or entry.get('recipe_id') != job.get('id'):
        return {}
    if not isinstance(entry.get('executable'), str) or not Path(entry['executable']).is_file():
        return {}
    # The installed executable must not be one of the downloads to be removed.
    if str(Path(entry['executable']).resolve()) in [f['path'] for f in receipt.get('files', [])]:
        return {}
    return job


def refresh(base):
    values = []
    for path in sorted((Path(base) / 'downloads').glob('*.json')):
        receipt = read(path)
        if not receipt:
            continue
        if receipt.get('status') in ('ready', 'deferred', 'installing'):
            job = installed_job(base, receipt)
            if job:
                receipt.update(status='installed', job_id=job['id'])
                save(path, receipt)
        values.append(receipt)
    return values[-100:]


def action(base, root, key, verb):
    if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{64}', key):
        raise ValueError('Invalid download receipt.')
    path = Path(base) / 'downloads' / (key + '.json')
    receipt = read(path)
    if verb == 'cleanup':
        if receipt.get('status') != 'installed' or not installed_job(base, receipt):
            raise ValueError('Finish setup and add the installed program before cleanup.')
        if receipt.get('torrent'):
            raise ValueError('Torrent files are kept for seeding. Remove them through FDM after stopping seeding.')
        files = [contained(Path(item['path']), root) for item in receipt['files']]
        # Preflight the whole package: a replacement or moved file cancels cleanup.
        if any(fingerprint(file) != expected for file, expected in zip(files, receipt['files'])):
            raise ValueError('Download files changed; automatic cleanup was cancelled.')
        for file in files:
            file.unlink()
        receipt.update(status='cleaned', cleaned_at=time.time())
    elif verb in ('defer', 'start', 'keep'):
        receipt['status'] = {'defer': 'deferred', 'start': 'installing', 'keep': 'kept'}[verb]
    else:
        raise ValueError('Invalid download action.')
    save(path, receipt)
    return receipt
