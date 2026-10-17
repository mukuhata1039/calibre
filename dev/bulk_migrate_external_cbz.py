#!/usr/bin/env python
"""Reversible bulk migration of existing calibre CBZs.

Run with calibre closed:
    calibre-debug -e dev/bulk_migrate_external_cbz.py
Only CBZ formats are changed. Originals are kept in quarantine.
"""
import json
import os
import sys
from datetime import datetime

from qt.core import QApplication, QFileDialog, QInputDialog, QMessageBox, QProgressDialog, Qt
from calibre.db.legacy import LibraryDatabase
from calibre.gui2.external_book_store import (
    external_cbz_migration_state, migrate_existing_cbz,
    restore_external_format_test,
)


def message(title, body):
    QMessageBox.information(None, title, body)


def ids_from_text(text):
    ids = set()
    for part in text.replace('、', ',').replace(' ', ',').split(','):
        if part.strip():
            ids.add(int(part.strip()))
    if not ids or min(ids) < 1:
        raise ValueError('正の書籍IDを入力してください')
    return sorted(ids)


def run_batch(app, library, ids, mode, destination=None):
    progress = QProgressDialog('準備中', '残りを中止', 0, len(ids))
    progress.setWindowTitle('外部CBZの一括移行' if mode == 'migrate' else '内部CBZの一括復元')
    progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.show()
    results = []
    db = None
    try:
        if mode == 'migrate':
            db = LibraryDatabase(library)
        for index, book_id in enumerate(ids):
            progress.setLabelText(f'{index + 1}/{len(ids)} 冊目  ID: {book_id}')
            progress.setValue(index)
            app.processEvents()
            if progress.wasCanceled():
                break
            record = {'id': book_id}
            try:
                if mode == 'restore':
                    record['path'] = restore_external_format_test(library, book_id, 'CBZ')
                    record['status'] = 'restored'
                elif external_cbz_migration_state(library, book_id) == 'migrated':
                    record['status'] = 'skipped_already_migrated'
                else:
                    record['path'] = migrate_existing_cbz(db, book_id, destination)
                    record['status'] = 'migrated'
            except Exception as err:
                record['status'] = 'failed'
                record['error'] = str(err)
            results.append(record)
        progress.setValue(len(ids))
    finally:
        if db is not None:
            db.close()
        progress.close()
    log = {
        'started_at': datetime.now().astimezone().isoformat(),
        'operation': mode,
        'library': library,
        'destination': destination,
        'requested': len(ids),
        'cancelled': len(results) < len(ids),
        'results': results,
    }
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    log_path = os.path.join(library, f'external-cbz-batch-{stamp}.json')
    with open(log_path, 'x', encoding='utf-8') as out:
        json.dump(log, out, ensure_ascii=False, indent=2)
    counts = {status: sum(r['status'] == status for r in results) for status in (
        'migrated', 'restored', 'skipped_already_migrated', 'failed'
    )}
    message(
        '一括処理結果',
        f"移行: {counts['migrated']}冊 / 復元: {counts['restored']}冊 / "
        f"移行済みスキップ: {counts['skipped_already_migrated']}冊 / "
        f"失敗: {counts['failed']}冊"
        + ('\n途中で中止されました。' if len(results) < len(ids) else '')
        + '\n処理ログ: ' + log_path
        + '\n\n内部CBZは削除せず退避しています。'
    )


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    library = QFileDialog.getExistingDirectory(None, 'metadata.dbのあるcalibreライブラリを選択')
    if not library:
        return
    if not os.path.isfile(os.path.join(library, 'metadata.db')):
        QMessageBox.critical(None, 'エラー', 'metadata.dbのあるライブラリを選択してください')
        return
    options = ['全書籍のCBZを外部保管庫へ移行', '書籍IDを指定して移行', '書籍IDを指定して内部CBZを復元']
    action, ok = QInputDialog.getItem(None, '一括移行・復元', '操作を選択してください', options, 0, False)
    if not ok:
        return
    mode = 'restore' if action == options[2] else 'migrate'
    dest = None
    try:
        if action == options[0]:
            db = LibraryDatabase(library)
            try:
                ids = sorted(int(x) for x in db.new_api.all_book_ids() if 'CBZ' in db.new_api.formats(int(x)))
            finally:
                db.close()
        else:
            entered, ok = QInputDialog.getMultiLineText(
                None, '対象書籍', '書籍IDをカンマ区切りで入力（例：12,13,18）'
            )
            if not ok:
                return
            ids = ids_from_text(entered)
        if not ids:
            message('対象なし', '対象となるCBZが見つかりませんでした。')
            return
        if mode == 'migrate':
            dest = QFileDialog.getExistingDirectory(None, '外部保管庫を選択（既存の保存先と同じ場所）')
            if not dest:
                return
        question = (
            f'{len(ids)}冊を処理します。\n'
            + ('既存の内部CBZを外部へコピー・検証し、復元できる場所へ退避します。'
               if mode == 'migrate' else '退避済みの内部CBZを復元します。')
            + '\n\n処理中はcalibreを起動しないでください。続けますか？'
        )
        answer = QMessageBox.question(None, '実行確認', question)
        if answer != QMessageBox.StandardButton.Yes:
            return
        run_batch(app, library, ids, mode, dest)
    except Exception as err:
        QMessageBox.critical(None, '一括処理を開始できません', str(err))


if __name__ == '__main__':
    main()
