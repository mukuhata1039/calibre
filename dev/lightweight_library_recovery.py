#!/usr/bin/env python
"""Metadata-only calibre library backup and disaster recovery.

Usage:
  calibre-debug -e dev/lightweight_library_recovery.py

Close calibre before backup / restore / reattach.
No book formats are copied into the backup.
A backup snapshot contains an SQLite-consistent metadata.db and metadata
sidecars (covers, OPFs, user extra files), excluding calibre format binaries.
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from qt.core import QApplication, QFileDialog, QInputDialog, QMessageBox, QProgressDialog, Qt


EXCLUDED = {'.cbz', '.cbr', '.epub', '.pdf', '.azw', '.azw3', '.mobi', '.fb2',
            '.djvu', '.docx', '.txt', '.html', '.htm', '.rtf', '.zip', '.rar',
            '.7z', '.kepub', '.pdb', '.lit', '.lrf', '.odt', '.mp3', '.m4b'}
MANIFEST = 'calibre-lightweight-backup.json'


def message(title, text):
    QMessageBox.information(None, title, text)


def is_library(path):
    return os.path.isfile(os.path.join(path, 'metadata.db'))


def safe_relative(name):
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError('Unsafe backup entry: ' + name)
    return p


def backup_library(library, outfile):
    from calibre.db.backend import DB
    if not is_library(library):
        raise ValueError('metadata.dbがないライブラリです')
    if os.path.realpath(outfile).startswith(os.path.realpath(library) + os.sep):
        raise ValueError('バックアップはライブラリの外へ保存してください')
    fd, tmpdb = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    try:
        db = DB(library)
        try:
            db.backup_database(tmpdb)
        finally:
            db.close()
        # Write atomically. Do not ever overwrite a previous backup.
        if os.path.lexists(outfile):
            raise FileExistsError(outfile)
        manifest = {
            'version': 1,
            'created': datetime.now().astimezone().isoformat(),
            'books_excluded': True,
            'library_folder': os.path.basename(os.path.normpath(library)),
        }
        # SQLite snapshot and sidecars. Exclude book format payloads.
        with zipfile.ZipFile(outfile, mode='x', compression=zipfile.ZIP_DEFLATED) as z:
            z.write(tmpdb, 'metadata.db')
            for current, dirs, files in os.walk(library, followlinks=False):
                dirs[:] = [d for d in dirs if d not in (
                    '.external-format-test-backup', '.calibre-deleted-books', '.caltrash'
                ) and not os.path.islink(os.path.join(current, d))]
                for name in files:
                    original = os.path.join(current, name)
                    rel = os.path.relpath(original, library).replace(os.sep, '/')
                    if rel in ('metadata.db', MANIFEST, 'external-books.json'):
                        continue
                    # Keep only database/metadata/config/cover files, not formats.
                    if Path(name).suffix.lower() in EXCLUDED:
                        continue
                    if os.path.islink(original):
                        continue
                    z.write(original, 'sidecars/' + rel)
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        return outfile
    finally:
        if os.path.exists(tmpdb):
            os.unlink(tmpdb)


def restore_library(archive, destination):
    if os.path.exists(destination) and os.listdir(destination):
        raise ValueError('復元先は空フォルダにしてください。既存のライブラリを上書きしません')
    with zipfile.ZipFile(archive) as z:
        bad_member = z.testzip()
        if bad_member is not None:
            raise ValueError('バックアップZIPが破損しています: ' + bad_member)
        manifest = json.loads(z.read(MANIFEST))
        if manifest.get('version') != 1 or not manifest.get('books_excluded'):
            raise ValueError('対応する軽量バックアップではありません')
        entries = [i for i in z.infolist() if i.filename == 'metadata.db'
                   or i.filename.startswith('sidecars/')]
        for info in entries:
            if info.is_dir():
                continue
            if info.filename == 'metadata.db':
                relative = Path('metadata.db')
            else:
                relative = safe_relative(info.filename[len('sidecars/'):])
            target = os.path.join(destination, str(relative))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.lexists(target):
                raise FileExistsError(target)
            with z.open(info) as src, open(target, 'xb') as out:
                shutil.copyfileobj(src, out)
    return destination


def reconnect_from_original_tree(library, source, ids=None):
    """Reattach original calibre book files by book-relative directory.

    The restored database already carries original book IDs and format names.
    Restores formats to the existing record, never creates duplicate records.
    """
    from calibre.db.legacy import LibraryDatabase
    if not is_library(library):
        raise ValueError('復元先のmetadata.dbがありません')
    if not os.path.isdir(source):
        raise FileNotFoundError(source)
    db = LibraryDatabase(library)
    outcomes = []
    try:
        cache = db.new_api
        selection = sorted(ids if ids is not None else cache.all_book_ids())
        for book_id in selection:
            book_id = int(book_id)
            try:
                path = cache.field_for('path', book_id)
                formats = cache.formats(book_id)
                if isinstance(formats, str):
                    formats = formats.split(',')
                for fmt in formats or ():
                    ext = '.' + str(fmt).lower()
                    dest = cache.format_abspath(book_id, fmt)
                    if dest and os.path.isfile(dest):
                        outcomes.append([book_id, fmt, 'already-present'])
                        continue
                    folder = os.path.realpath(os.path.join(source, path))
                    root = os.path.realpath(source)
                    if os.path.commonpath([root, folder]) != root:
                        raise ValueError('書籍フォルダの場所が不正です')
                    choices = [p for p in os.scandir(folder) if p.is_file() and p.name.lower().endswith(ext)]
                    if len(choices) != 1:
                        outcomes.append([book_id, fmt, 'missing-or-ambiguous'])
                        continue
                    # Use calibre's format API so the database remains consistent.
                    cache.add_format(book_id, str(fmt).upper(), choices[0].path, replace=True)
                    outcomes.append([book_id, fmt, 'attached'])
            except Exception as err:
                outcomes.append([book_id, '', 'error: ' + str(err)])
    finally:
        db.close()
    return outcomes


def run():
    app = QApplication.instance() or QApplication(sys.argv)
    actions = [
        '① 書籍なしの軽量バックアップを作成',
        '② 軽量バックアップから新規ライブラリを復元',
        '③ 復元したライブラリと書籍フォルダを再接続',
    ]
    action, ok = QInputDialog.getItem(None, 'calibre 軽量バックアップ・復元',
                                      '実行する操作', actions, 0, False)
    if not ok:
        return
    try:
        if action == actions[0]:
            library = QFileDialog.getExistingDirectory(None, 'バックアップするcalibreライブラリ')
            if not library:
                return
            outfile, _ = QFileDialog.getSaveFileName(None, '保存するバックアップ名',
                                                    'calibre-metadata-only.zip', 'ZIP (*.zip)')
            if not outfile:
                return
            result = backup_library(library, outfile)
            message('完了', '書籍本体を含まないバックアップを作成しました。\n' + result)
        elif action == actions[1]:
            archive, _ = QFileDialog.getOpenFileName(None, '軽量バックアップを選択',
                                                     '', 'ZIP (*.zip)')
            if not archive:
                return
            dest = QFileDialog.getExistingDirectory(None, '空の復元先フォルダを選択')
            if not dest:
                return
            restore_library(archive, dest)
            message('完了', '書籍管理情報を復元しました。次に③で書籍本体を再接続してください。')
        else:
            library = QFileDialog.getExistingDirectory(None, '復元済みのライブラリ')
            if not library:
                return
            source = QFileDialog.getExistingDirectory(None, '書籍が入っている元のcalibreフォルダ構成')
            if not source:
                return
            reply = QMessageBox.question(None, '再接続',
                '書籍ファイルを復元済みライブラリへコピーし、元の書籍IDに再接続します。\n'
                '元のファイルは変更しません。続けますか？')
            if reply != QMessageBox.StandardButton.Yes:
                return
            outcomes = reconnect_from_original_tree(library, source)
            report = os.path.join(library, 'reconnect-report.json')
            with open(report, 'w', encoding='utf-8') as out:
                json.dump(outcomes, out, ensure_ascii=False, indent=2)
            done = sum(x[2] == 'attached' for x in outcomes)
            message('完了', f'{done}形式を再接続しました。\n詳細ログ: {report}')
    except Exception as err:
        import traceback
        QMessageBox.critical(None, '処理を中止しました', str(err) + '\n\n' + traceback.format_exc())


if __name__ == '__main__':
    run()
