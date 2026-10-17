#!/usr/bin/env python
"""Remove ONLY empty folders in a configured external book store."""
import os
import sys
from qt.core import QApplication, QFileDialog, QMessageBox
from calibre.gui2.external_book_store import clean_empty_external_directories, load_manifest


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    library = QFileDialog.getExistingDirectory(None, 'calibreライブラリを選択（metadata.dbの場所）')
    if not library:
        return
    if not os.path.isfile(os.path.join(library, 'metadata.db')):
        QMessageBox.warning(None, 'エラー', 'metadata.dbが見つかりません')
        return
    try:
        root = load_manifest(library).get('storage_root')
        if not root or not os.path.isdir(root):
            raise FileNotFoundError('設定済みの外部保管庫が見つかりません')
        reply = QMessageBox.question(None, '空フォルダを整理', '外部保管庫の空フォルダだけ削除します。\n' + root + '\nファイルは削除しません。よろしいですか？')
        if reply != QMessageBox.StandardButton.Yes:
            return
        removed = clean_empty_external_directories(library)
        QMessageBox.information(None, '完了', f'{len(removed)}個の空フォルダを削除しました。')
    except Exception as err:
        QMessageBox.critical(None, 'エラー', str(err))


if __name__ == '__main__':
    main()
