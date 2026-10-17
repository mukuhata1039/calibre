#!/usr/bin/env python
"""Reversible test of external CBZ reading. Close calibre before using.

Start via calibre-debug -e dev/external_format_test.py
Only the chosen book format is moved. Restore it after testing.
"""
import os
import sys
from qt.core import QApplication, QFileDialog, QInputDialog, QMessageBox

from calibre.db.legacy import LibraryDatabase
from calibre.gui2.external_book_store import (
    stage_external_format_test, restore_external_format_test,
)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    library = QFileDialog.getExistingDirectory(None, 'テストするcalibreライブラリを選択')
    if not library:
        return
    if not os.path.isfile(os.path.join(library, 'metadata.db')):
        QMessageBox.critical(None, 'エラー', 'metadata.dbがないフォルダです。')
        return
    book_id, ok = QInputDialog.getInt(None, '対象の書籍', 'calibreの書籍ID（整数）', min=1)
    if not ok:
        return
    mode, ok = QInputDialog.getItem(
        None, 'テスト操作',
        '実行する操作を選んでください。',
        ['内部CBZを退避して外部参照をテスト', '退避した内部CBZを元に戻す'],
        0, False,
    )
    if not ok:
        return
    try:
        if mode.startswith('退避'):
            path = restore_external_format_test(library, book_id, 'CBZ')
            message = '復元しました。\\n' + path
        else:
            database = LibraryDatabase(library)
            try:
                path = stage_external_format_test(database, book_id, 'CBZ')
            finally:
                database.close()
            message = ('内部のCBZを安全な退避場所へ移しました。\\n'
                       '外部参照先：\\n' + path +
                       '\\n\\ncalibreを起動して本を開き、終わったらcalibreを終了して'
                       'このツールの「元に戻す」を実行してください。')
        QMessageBox.information(None, '完了', message)
    except Exception as exc:
        QMessageBox.critical(None, '失敗（操作中止）', str(exc))


if __name__ == '__main__':
    main()
