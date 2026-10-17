#!/usr/bin/env python
"""Opt-in reversible external-only CBZ migration, ONE book at a time.

Close all calibre windows and ebook viewers before starting.
Run: calibre-debug -e dev/migrate_external_cbz.py
"""
import os
import sys
from qt.core import QApplication, QFileDialog, QInputDialog, QMessageBox
from calibre.db.legacy import LibraryDatabase
from calibre.gui2.external_book_store import (
    migrate_book_cbz_to_external, restore_external_format_test,
)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    lib = QFileDialog.getExistingDirectory(None, 'calibreライブラリ（metadata.dbのあるフォルダ）を選択')
    if not lib:
        return
    if not os.path.isfile(os.path.join(lib, 'metadata.db')):
        QMessageBox.critical(None, 'エラー', 'metadata.dbが見つかりません。')
        return
    book_id, ok = QInputDialog.getInt(None, '書籍ID', '移行する書籍のID', min=1)
    if not ok:
        return
    options = ['外部CBZだけで管理する（内部CBZは退避）', '内部CBZを退避場所から元に戻す']
    mode, ok = QInputDialog.getItem(None, '外部保存への移行', '操作を選択', options, 0, False)
    if not ok:
        return
    if mode == options[0]:
        answer = QMessageBox.question(
            None, '確認',
            'テスト済みの外部CBZに最新のComicInfo.xmlを書き込み、'
            '内部CBZを復元可能な退避場所へ移します。'
            '\n対象は指定IDのCBZ 1冊だけです。続けますか？'
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
    try:
        if mode == options[1]:
            result = restore_external_format_test(lib, book_id, 'CBZ')
            msg = '内部CBZを復元しました。\n' + result
        else:
            db = LibraryDatabase(lib)
            try:
                result = migrate_book_cbz_to_external(db, book_id)
            finally:
                db.close()
            msg = ('外部保存への移行が完了しました。\n'
                   '外部CBZ：' + result +
                   '\n\n内部CBZは削除せず退避しています。'
                   '問題があれば同じツールの「元に戻す」を実行してください。')
        QMessageBox.information(None, '完了', msg)
    except Exception as err:
        QMessageBox.critical(None, '移行を中止しました', str(err))


if __name__ == '__main__':
    main()
