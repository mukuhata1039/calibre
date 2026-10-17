#!/usr/bin/env python
"""Relocate or recover external book references without copying book files.

Close calibre first. Start with calibre-debug -e dev/reconnect_external_books.py
"""
import json
import os
import sys
from qt.core import QApplication, QFileDialog, QMessageBox
from calibre.db.legacy import LibraryDatabase
from calibre.gui2.external_book_store import (
    inspect_external_reconnection, apply_external_reconnection,
)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    library = QFileDialog.getExistingDirectory(
        None, '復旧するcalibreライブラリを選択（metadata.dbのある場所）'
    )
    if not library:
        return
    if not os.path.isfile(os.path.join(library, 'metadata.db')):
        QMessageBox.critical(None, 'エラー', 'metadata.dbが見つかりません')
        return
    new_root = QFileDialog.getExistingDirectory(None, '移動後の外部保管庫を選択')
    if not new_root:
        return
    db = None
    try:
        db = LibraryDatabase(library)
        plan = inspect_external_reconnection(db, new_root)
        report_path = os.path.join(library, 'external-reconnection-preview.json')
        with open(report_path, 'w', encoding='utf-8') as output:
            json.dump(plan, output, ensure_ascii=False, indent=2)
        found = len(plan['links'])
        missing = len(plan['missing'])
        ambiguous = len(plan['ambiguous'])
        dialog = QMessageBox()
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle('外部保管庫の再接続')
        dialog.setText(
            f'再接続できる候補: {found}形式\n'
            f'見つからない参照: {missing}形式\n'
            f'曖昧な候補: {ambiguous}件\n\n'
            '再接続可能な参照だけ保存します。'
            '見つからない参照は消さずに記録を残します。\n'
            '実行してもCBZ自体は移動・コピー・削除しません。'
        )
        dialog.setInformativeText('詳細レポート: ' + report_path)
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        dialog.button(QMessageBox.StandardButton.Yes).setText('再接続する')
        dialog.button(QMessageBox.StandardButton.No).setText('キャンセル')
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            return
        connected = apply_external_reconnection(db, plan)
        QMessageBox.information(
            None, '完了',
            f'{connected}形式の外部参照を設定しました。\n'
            f'未接続: {missing}形式 / 曖昧: {ambiguous}件\n'
            'calibreを起動して閲覧できるか確認してください。'
        )
    except Exception as err:
        import traceback
        QMessageBox.critical(None, '外部保管庫の復旧に失敗', str(err) + '\n\n' + traceback.format_exc())
    finally:
        if db is not None:
            db.close()


if __name__ == '__main__':
    main()
