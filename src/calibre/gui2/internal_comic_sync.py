#!/usr/bin/env python
"""Sync metadata edits to ComicInfo.xml in calibre-INTERNAL CBZ formats.

This module deliberately does not use external storage manifests. calibre's
database is authoritative. Pending book IDs are retained until a successful
atomic rewrite. Only the internal CBZ at cache.format_abspath() is edited.
"""
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import zipfile
from xml.etree import ElementTree as ET

_pending = {}
_active = {}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='calibre-comicinfo')
_lock = threading.RLock()
_FIELDS = frozenset(('title', 'authors', 'series', 'series_index', 'publisher', 'tags'))


def queue(library_path, book_ids, field):
    if field not in _FIELDS and field != '#subtitle':
        return
    key = os.path.normcase(os.path.realpath(library_path))
    with _lock:
        versions = _pending.setdefault(key, {})
        for book_id in book_ids:
            i = int(book_id)
            versions[i] = versions.get(i, 0) + 1


def pending(library_path):
    key = os.path.normcase(os.path.realpath(library_path))
    with _lock:
        return set(_pending.get(key, ()))


def _set(root, name, value):
    node = root.find(name)
    if node is None:
        node = ET.SubElement(root, name)
    node.text = str(value or '')


def _sync_file(path, data):
    """Worker thread: only local ZIP IO, never touch calibre DB or Qt."""
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
        for name, value in data.items():
            _set(root, name, value)
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
            # Windows cannot replace a CBZ while its original ZIP handle is open.
            source.close()
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return True


def _write_epub_subtitle(epub_path, subtitle):
    """Update the subtitle in the package metadata without changing book content."""
    from io import BytesIO
    from lxml import etree
    from calibre.utils.zipfile import safe_replace

    ns_dc = 'http://purl.org/dc/elements/1.1/'
    ns_opf = 'http://www.idpf.org/2007/opf'
    ns_container = 'urn:oasis:names:tc:opendocument:xmlns:container'
    with zipfile.ZipFile(epub_path) as archive:
        container = etree.fromstring(archive.read('META-INF/container.xml'))
        roots = container.xpath('.//c:rootfile', namespaces={'c': ns_container})
        if not roots:
            raise ValueError('EPUB package path is missing')
        opf_path = roots[0].get('full-path')
        if not opf_path:
            raise ValueError('EPUB package path is empty')
        root = etree.fromstring(archive.read(opf_path))
    metadata = root.find('{%s}metadata' % ns_opf)
    if metadata is None:
        raise ValueError('EPUB metadata element is missing')
    # Remove previous dedicated subtitle elements while keeping main title intact.
    old_ids = set()
    for meta in list(metadata):
        if meta.tag == '{%s}meta' % ns_opf and meta.get('property') == 'title-type' and (meta.text or '').strip() == 'subtitle':
            ref = (meta.get('refines') or '').lstrip('#')
            if ref:
                old_ids.add(ref)
            metadata.remove(meta)
        elif meta.tag == '{%s}meta' % ns_opf and meta.get('name') == 'subtitle':
            metadata.remove(meta)
    for title in list(metadata):
        if title.tag == '{%s}title' % ns_dc and title.get('id') in old_ids:
            metadata.remove(title)
    if subtitle:
        if root.get('version', '2').startswith('3'):
            used = {node.get('id') for node in metadata.iter() if node.get('id')}
            sid = 'calibre-subtitle'
            n = 2
            while sid in used:
                sid = 'calibre-subtitle-' + str(n)
                n += 1
            node = etree.SubElement(metadata, '{%s}title' % ns_dc)
            node.set('id', sid)
            node.text = subtitle
            ref = etree.SubElement(metadata, '{%s}meta' % ns_opf)
            ref.set('refines', '#' + sid)
            ref.set('property', 'title-type')
            ref.text = 'subtitle'
        else:
            node = etree.SubElement(metadata, '{%s}meta' % ns_opf)
            node.set('name', 'subtitle')
            node.set('content', subtitle)
    new_xml = etree.tostring(root, encoding='utf-8', xml_declaration=True)
    safe_replace(epub_path, opf_path, BytesIO(new_xml))


def _sync_epub(path, data):
    """Write OPF using calibre's EPUB writer on a safe temporary copy."""
    from calibre.ebooks.metadata.book.base import Metadata
    from calibre.ebooks.metadata.epub import set_metadata
    mi = Metadata(data['Title'], authors=data['Authors'])
    mi.series = data['Series'] or None
    mi.series_index = data['SeriesIndex']
    mi.publisher = data['Publisher'] or None
    mi.tags = data['Tags']
    fd, temporary = tempfile.mkstemp(prefix='.epub-metadata-', suffix='.epub', dir=os.path.dirname(path))
    os.close(fd)
    try:
        shutil.copy2(path, temporary)
        with open(temporary, 'r+b') as stream:
            set_metadata(stream, mi, apply_null=False, add_missing_cover=False)
        if data.get('Subtitle') is not None:
            _write_epub_subtitle(temporary, data['Subtitle'])
        with zipfile.ZipFile(temporary) as check:
            bad = check.testzip()
            if bad is not None:
                raise ValueError('EPUB integrity check failed: ' + bad)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sync_book(jobs):
    for fmt, path, data in jobs:
        if fmt == 'CBZ':
            _sync_file(path, data)
        else:
            _sync_epub(path, data)


def _snapshot(cache, book_id):
    """GUI thread only: read database metadata once, before background IO."""
    library = os.path.realpath(cache.backend.library_path)
    mi = cache.get_metadata(book_id)
    subtitle = cache.field_for('#subtitle', book_id) if '#subtitle' in cache.field_metadata else None
    formats = []
    for fmt in ('CBZ', 'EPUB'):
        path = cache.format_abspath(book_id, fmt)
        if not path or not os.path.isfile(path):
            continue
        path = os.path.realpath(path)
        if os.path.commonpath((library, path)) != library:
            continue
        if fmt == 'CBZ':
            data = {
                'Title': mi.title,
                'Series': mi.series,
                'Number': ('%g' % mi.series_index) if mi.series else '',
                'Writer': ', '.join(mi.authors or ()),
                'Publisher': mi.publisher,
                'Tags': ', '.join(mi.tags or ()),
            }
        else:
            data = {
                'Title': mi.title,
                'Series': mi.series,
                'SeriesIndex': mi.series_index,
                'Authors': list(mi.authors or ()),
                'Publisher': mi.publisher,
                'Tags': list(mi.tags or ()),
                'Subtitle': subtitle,
            }
        formats.append((fmt, path, data))
    return formats


def poll(cache):
    """Called by the calibre GUI timer. File rewrites happen in the worker."""
    key = os.path.normcase(os.path.realpath(cache.backend.library_path))
    done, failed = [], {}
    with _lock:
        running = _active.get(key)
    if running:
        book_id, version, future = running
        if not future.done():
            return done, failed
        try:
            future.result()
            done.append(book_id)
            with _lock:
                versions = _pending.get(key, {})
                if versions.get(book_id) == version:
                    versions.pop(book_id, None)
        except Exception as err:
            failed[book_id] = str(err)
            # Failed jobs remain pending. Do not create a tight retry loop.
        with _lock:
            _active.pop(key, None)
        if failed:
            return done, failed
    with _lock:
        if key in _active:
            return done, failed
        todo = sorted(_pending.get(key, {}).items())
    for book_id, version in todo:
        try:
            snap = _snapshot(cache, book_id)
        except Exception as err:
            failed[book_id] = str(err)
            break
        if not snap:
            with _lock:
                if _pending.get(key, {}).get(book_id) == version:
                    _pending[key].pop(book_id, None)
            continue
        with _lock:
            _active[key] = (book_id, version, _executor.submit(_sync_book, snap))
        break
    return done, failed


def flush(cache):
    """Drain background writes before calibre closes its database."""
    from concurrent.futures import wait
    library = cache.backend.library_path
    done, failed = [], {}
    while True:
        completed, errors = poll(cache)
        done.extend(completed)
        failed.update(errors)
        if errors:
            return done, failed
        key = os.path.normcase(os.path.realpath(library))
        with _lock:
            active = _active.get(key)
            remaining = bool(_pending.get(key))
        if not active and not remaining:
            return done, failed
        if active:
            wait([active[2]])
