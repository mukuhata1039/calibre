#!/usr/bin/env python
"""Regression tests for the optional external book store foundation.

Run with calibre's Python environment:
    calibre-debug -e dev/external_book_store_smoke.py
"""
import os
import tempfile
import unittest
import zipfile

from calibre.gui2.external_book_store import (
    comic_identity, copy_to_store, read_comic_info, write_comic_info,\n    set_storage_root, register_external_format, resolve_external_format,
)


class ExternalStoreSmoke(unittest.TestCase):

    def test_comic_info_round_trip_and_image_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cbz = os.path.join(tmp, 'book.cbz')
            image = b'jpeg-content-test'
            with zipfile.ZipFile(cbz, 'w', zipfile.ZIP_DEFLATED) as z:
                z.writestr('001.jpg', image)
                z.writestr('ComicInfo.xml', '<ComicInfo><Series>(am)作品</Series><Number>3.0</Number><Title>作品 3</Title><Tags>manga</Tags></ComicInfo>')
            write_comic_info(cbz, {'Series': '作品', 'Writer': '筆者'})
            info = read_comic_info(cbz)
            self.assertEqual(info['Series'], '作品')
            self.assertEqual(info['Writer'], '筆者')
            self.assertEqual(info['Tags'], 'manga')
            with zipfile.ZipFile(cbz) as z:
                self.assertEqual(z.read('001.jpg'), image)
            self.assertEqual(comic_identity(info), ('作品', '3.0', '作品 3'))

    def test_backend_external_path_resolution(self):
        from calibre.db.backend import DB
        with tempfile.TemporaryDirectory() as tmp:
            lib = os.path.join(tmp, 'library')
            store = os.path.join(tmp, 'store')
            os.makedirs(lib)
            os.makedirs(store)
            with open(os.path.join(lib, 'metadata.db'), 'wb') as f:
                f.write(b'test')
            set_storage_root(lib, store)
            target = os.path.join(store, 'book.cbz')
            with open(target, 'wb') as f:
                f.write(b'test cbz')
            register_external_format(lib, 9, 'cbz', 'book.cbz')
            fake = type('BackendStub', (), {'library_path': lib})()
            self.assertEqual(DB.external_format_abspath(fake, 9, '.cbz'), target)
            self.assertIsNone(DB.external_format_abspath(fake, 10, '.cbz'))
            os.remove(target)
            self.assertIsNone(DB.external_format_abspath(fake, 9, '.cbz'))

    def test_no_replacement_of_existing_external_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'source.cbz')
            with open(src, 'wb') as f:
                f.write(b'book')
            store = os.path.join(tmp, 'external')
            path = copy_to_store(src, store, 'manga/book.cbz')
            self.assertEqual(open(path, 'rb').read(), b'book')
            with self.assertRaises(FileExistsError):
                copy_to_store(src, store, 'manga/book.cbz')

    def test_manifest_resolves_external_file_and_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = os.path.join(tmp, 'library')
            store = os.path.join(tmp, 'store')
            os.makedirs(lib)
            os.makedirs(store)
            with open(os.path.join(lib, 'metadata.db'), 'wb') as f:
                f.write(b'test-only')
            source = os.path.join(tmp, 'book.cbz')
            with open(source, 'wb') as f:
                f.write(b'test cbz')
            set_storage_root(lib, store)
            path = copy_to_store(source, store, 'volume/book.cbz')
            register_external_format(lib, 42, 'cbz', 'volume/book.cbz')
            self.assertEqual(resolve_external_format(lib, 42, 'CBZ'), path)
            self.assertIsNone(resolve_external_format(lib, 43, 'CBZ'))
            with self.assertRaises(ValueError):
                register_external_format(lib, 42, 'CBZ', '../book.cbz')


if __name__ == '__main__':
    unittest.main()
