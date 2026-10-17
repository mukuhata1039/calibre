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
_PREFIX = re.compile(r'^\\s*\\([a-z]{1,8}\\)\\s*', re.I)


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
