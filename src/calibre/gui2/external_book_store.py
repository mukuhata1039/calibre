#!/usr/bin/env python
# License: GPLv3
"""External book storage building blocks.

This module does not enable external storage on its own.  In particular it
must not replace calibre's format paths until format operations are adapted.
"""
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

COMIC_FIELDS = ('Title', 'Series', 'Number', 'Writer', 'Publisher', 'Tags')
_PREFIX = re.compile(r'^\s*\([a-z]{1,8}\)\s*', re.I)


def normalized_name(value):
    return ' '.join(_PREFIX.sub('', str(value or '')).casefold().split())


def normalized_number(value):
    try:
        return str(float(value))
    except (TypeError, ValueError):
        return str(value or '').strip()


def comic_identity(info):
    """Human-readable identity, deliberately not a hash or embedded ID."""
    return (
        normalized_name(info.get('Series')),
        normalized_number(info.get('Number')),
        normalized_name(info.get('Title')),
    )


def read_comic_info(cbz):
    """Read ComicInfo.xml from CBZ without extracting its image payloads."""
    with zipfile.ZipFile(cbz) as archive:
        names = [name for name in archive.namelist() if Path(name).name.lower() == 'comicinfo.xml']
        if not names:
            return {}
        if len(names) != 1:
            raise ValueError('Multiple ComicInfo.xml entries in CBZ')
        item = archive.getinfo(names[0])
        if item.file_size > 1024 * 1024:
            raise ValueError('ComicInfo.xml exceeds 1 MiB')
        data = archive.read(item)
    root = ET.fromstring(data)
    return {key: (root.findtext(key) or '').strip() for key in COMIC_FIELDS}


def write_comic_info(cbz, changes):
    """Atomically replace ComicInfo while preserving all other archive entries.

    A ZIP still needs rewriting, but original compressed member bytes are not
    decoded as image data. ZIP recompression can take time on large books.
    The original CBZ is never replaced unless the temporary ZIP validates.
    """
    cbz = os.path.abspath(cbz)
    directory = os.path.dirname(cbz)
    fd, tmp = tempfile.mkstemp(prefix='.calibre-comicinfo-', suffix='.cbz', dir=directory)
    os.close(fd)
    try:
        with zipfile.ZipFile(cbz, 'r') as source:
            matches = [i for i in source.infolist() if Path(i.filename).name.lower() == 'comicinfo.xml']
            if len(matches) > 1:
                raise ValueError('Multiple ComicInfo.xml entries in CBZ')
            old = matches[0] if matches else None
            root = ET.fromstring(source.read(old)) if old else ET.Element('ComicInfo')
            for key, value in changes.items():
                if key not in COMIC_FIELDS:
                    continue
                node = root.find(key)
                if node is None:
                    node = ET.SubElement(root, key)
                node.text = str(value or '')
            payload = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            with zipfile.ZipFile(tmp, 'w') as dest:
                dest.comment = source.comment
                for item in source.infolist():
                    if old is not None and item.filename == old.filename:
                        continue
                    dest.writestr(item, source.read(item))
                dest.writestr(old if old is not None else 'ComicInfo.xml', payload)
        with zipfile.ZipFile(tmp) as check:
            if check.testzip() is not None:
                raise ValueError('CBZ ZIP verification failed')
        shutil.copystat(cbz, tmp)
        os.replace(tmp, cbz)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def copy_to_store(source, store, relative_name):
    """Copy without clobbering a different archived book."""
    target = Path(store) / relative_name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f'External book already exists: {target}')
    fd, tmp = tempfile.mkstemp(prefix='.calibre-import-', dir=str(target.parent))
    try:
        with os.fdopen(fd, 'wb') as out, open(source, 'rb') as inp:
            shutil.copyfileobj(inp, out, 1024 * 1024)
        if os.path.getsize(tmp) != os.path.getsize(source):
            raise IOError('External copy size differs from source')
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return str(target)


# Per-library manifest. It is separate from calibre's format table so existing
# databases and book operations remain unchanged until integration is complete.
import json


def _manifest_path(library_path):
    return os.path.join(os.path.abspath(library_path), 'external-books.json')


def load_manifest(library_path):
    path = _manifest_path(library_path)
    if not os.path.exists(path):
        return {'version': 1, 'storage_root': '', 'books': {}}
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Unsupported external book manifest')
    if not isinstance(data.get('books'), dict):
        raise ValueError('Invalid external book manifest')
    return data


def save_manifest(library_path, data):
    """Atomically persist only references; never delete or move book formats."""
    directory = os.path.abspath(library_path)
    if not os.path.isfile(os.path.join(directory, 'metadata.db')):
        raise ValueError('Not an existing calibre library')
    fd, tmp = tempfile.mkstemp(prefix='.external-books-', suffix='.json', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _manifest_path(directory))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def set_storage_root(library_path, storage_root):
    root = os.path.abspath(os.path.expanduser(storage_root))
    if not os.path.isdir(root):
        raise NotADirectoryError(root)
    data = load_manifest(library_path)
    data['storage_root'] = root
    save_manifest(library_path, data)


def register_external_format(library_path, book_id, fmt, relative_path):
    """Register an ALREADY copied format, without altering calibre's database."""
    data = load_manifest(library_path)
    root = data.get('storage_root')
    if not root:
        raise ValueError('External storage root is not configured')
    target = _safe_external_path(root, relative_path)
    if not os.path.isfile(target):
        raise FileNotFoundError(target)
    key = str(int(book_id))
    formats = data['books'].setdefault(key, {})
    formats[str(fmt).upper().lstrip('.')] = os.path.relpath(target, root)
    save_manifest(library_path, data)


def _safe_external_path(root, relative_path):
    root = os.path.realpath(root)
    if os.path.isabs(relative_path):
        raise ValueError('Absolute paths are not allowed in the manifest')
    target = os.path.realpath(os.path.join(root, relative_path))
    if os.path.commonpath((root, target)) != root:
        raise ValueError('External path escapes storage root')
    return target


def resolve_external_format(library_path, book_id, fmt):
    """Return the registered external file, or None if absent/unavailable."""
    data = load_manifest(library_path)
    relpath = data['books'].get(str(int(book_id)), {}).get(str(fmt).upper().lstrip('.'))
    if not relpath or not data.get('storage_root'):
        return None
    target = _safe_external_path(data['storage_root'], relpath)
    return target if os.path.isfile(target) else None


def _quarantine_path(library_path, book_id, fmt):
    """Backup outside calibre's book folders, retaining original filename."""
    return os.path.join(
        os.path.abspath(library_path), '.external-format-test-backup',
        str(int(book_id)), str(fmt).upper() + '.json'
    )


def stage_external_format_test(db, book_id, fmt):
    """Move ONE internal format to a reversible quarantine for external-read testing.

    The external format must already be registered and readable. This is a
    test-only action; the calibre database and format entry are unchanged.
    """
    library_path = os.path.abspath(db.library_path)
    fmt = str(fmt).upper().lstrip('.')
    external = resolve_external_format(library_path, book_id, fmt)
    if not external:
        raise FileNotFoundError('External format is missing: ' + fmt)
    original = db.new_api.format_abspath(int(book_id), fmt)
    if not original:
        raise FileNotFoundError('Internal book format not found')
    if os.path.normcase(os.path.realpath(original)) == os.path.normcase(os.path.realpath(external)):
        raise ValueError('Internal and external files resolve to the same path')
    import json
    metadata = _quarantine_path(library_path, book_id, fmt)
    if os.path.exists(metadata):
        raise FileExistsError('An unfinished external-format test already exists')
    backup = metadata[:-5] + '.original'
    os.makedirs(os.path.dirname(metadata), exist_ok=True)
    if os.path.exists(backup):
        raise FileExistsError(backup)
    # Keep the original in the library volume so moving usually stays atomic.
    os.replace(original, backup)
    try:
        record = {'original': original, 'backup': backup, 'external': external}
        with open(metadata, 'x', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False)
    except Exception:
        os.replace(backup, original)
        raise
    return external


def restore_external_format_test(library_path, book_id, fmt):
    """Restore the original format from quarantine; never overwrite another file."""
    import json
    metadata = _quarantine_path(library_path, book_id, fmt)
    with open(metadata, encoding='utf-8') as f:
        record = json.load(f)
    original, backup = record['original'], record['backup']
    if os.path.exists(original):
        raise FileExistsError('Original path is occupied: ' + original)
    if not os.path.isfile(backup):
        raise FileNotFoundError(backup)
    os.replace(backup, original)
    os.unlink(metadata)
    return original
