#!/usr/bin/env python
"""Manually flush queued ComicInfo.xml metadata updates for one library.

Close calibre before running this tool, then execute:
calibre-debug -e dev/sync_external_comicinfo.py
"""
import os
import sys

from qt.core import QApplication, QFileDialog, QMessageBox

from calibre.db.legacy import LibraryDatabase
from calibre.gui2.external_book_store import pending_comic_info_sync, sync_queued_comic_info


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    library = QFileDialog.getExistingDirectory(None, '同期するcalibreライブラリを選択')
    if not library:
        return
    if not os.path.isfile(os.path.join(library, 'metadata.db')):
        QMessageBox.critical(None, 'ライブラリが無効', 'metadata.dbのあるライブラリを選択してください。')
        return
    try:
        pending = pending_comic_info_sync(library)
        if not pending:
            QMessageBox.information(None, '同期', '同期待ちのCBZはありません。')
            return
        answer = QMessageBox.question(
            None, 'ComicInfo.xmlの遅延同期',
            f'{len(pending)}冊の外部CBZのComicInfo.xmlを書き換えます。'
            '処理中は外部ディスクを取り外さないでください。続けますか？',
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        db = LibraryDatabase(library)
        try:
            synced, failed = sync_queued_comic_info(db)
        finally:
            db.close()
        result = f'{len(synced)}冊を同期しました。失敗: {len(failed)}冊'
        if failed:
            result += '\n' + '\n'.join(f'{book_id}: {error}' for book_id, error in failed.items())
        QMessageBox.information(None, '同期結果', result)
    except Exception as error:
        QMessageBox.critical(None, '同期失敗', str(error))


if __name__ == '__main__':
    main()
