#!/usr/bin/env python
"""Sync metadata edits to ComicInfo.xml in calibre-INTERNAL CBZ formats.

This module deliberately does not use external storage manifests. calibre's
database is authoritative. Pending book IDs are retained until a successful
atomic rewrite. Only the internal CBZ at cache.format_abspath() is edited.
"""
import os
import tempfile
import threading
import zipfile
from xml.etree import ElementTree as ET

_pending = {}
_lock = threading.RLock()
_FIELDS = frozenset(('title', 'authors', 'series', 'series_index', 'publisher', 'tags'))


def queue(library_path, book_ids, field):
    if field not in _FIELDS:
        return
    key = os.path.normcase(os.path.realpath(library_path))
    with _lock:
        _pending.setdefault(key, set()).update(int(x) for x in book_ids)


def pending(library_path):
    key = os.path.normcase(os.path.realpath(library_path))
    with _lock:
        return set(_pending.get(key, ()))


def _set(root, name, value):
    node = root.find(name)
    if node is None:
        node = ET.SubElement(root, name)
    node.text = str(value or '')


def _sync_one(cache, book_id):
    # Never follow external-only references. The file must be inside the library.
    path = cache.format_abspath(book_id, 'CBZ')
    if not path or not os.path.isfile(path):
        return False
    library = os.path.realpath(cache.backend.library_path)
    real_path = os.path.realpath(path)
    if os.path.commonpath((library, real_path)) != library:
        return False
    mi = cache.get_metadata(book_id)
    with zipfile.ZipFile(path, 'r') as source:
        infos = source.infolist()
        comic_entries = [i for i in infos if i.filename.casefold() == 'comicinfo.xml']
        if len(comic_entries) > 1:
            raise ValueError('Multiple ComicInfo.xml entries in ' + path)
        if comic_entries:
            raw = source.read(comic_entries[0])
            root = ET.fromstring(raw)
            if root.tag != 'ComicInfo':
                raise ValueError('Unexpected ComicInfo root in ' + path)
        else:
            root = ET.Element('ComicInfo')
        _set(root, 'Title', mi.title)
        _set(root, 'Series', mi.series)
        _set(root, 'Number', ('%g' % mi.series_index) if mi.series else '')
        _set(root, 'Writer', ', '.join(mi.authors or ()))
        _set(root, 'Publisher', mi.publisher)
        _set(root, 'Tags', ', '.join(mi.tags or ()))
        updated = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        fd, temporary = tempfile.mkstemp(prefix='.comicinfo-', suffix='.cbz', dir=os.path.dirname(path))
        os.close(fd)
        try:
            with zipfile.ZipFile(temporary, 'w') as target:
                for item in infos:
                    if item in comic_entries:
                        continue
                    target.writestr(item, source.read(item))
                if comic_entries:
                    target.writestr(comic_entries[0], updated)
                else:
                    target.writestr('ComicInfo.xml', updated, compress_type=zipfile.ZIP_DEFLATED)
            with zipfile.ZipFile(temporary) as check:
                if check.testzip() is not None:
                    raise ValueError('CBZ integrity check failed')
                ET.fromstring(check.read('ComicInfo.xml' if not comic_entries else comic_entries[0].filename))
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return True


def flush(cache):
    library = cache.backend.library_path
    key = os.path.normcase(os.path.realpath(library))
    done, failed = [], {}
    for book_id in sorted(pending(library)):
        try:
            if _sync_one(cache, book_id):
                done.append(book_id)
                with _lock:
                    _pending.get(key, set()).discard(book_id)
            else:
                # No internal CBZ, so this record has nothing to sync.
                with _lock:
                    _pending.get(key, set()).discard(book_id)
        except Exception as err:
            failed[book_id] = str(err)
    return done, failed
