#!/usr/bin/env python
# calibre pre-add grouped metadata editor — prototype only.
# No changes to the library or source book files.
import json
from copy import deepcopy
import os
import re
import sys

from qt.core import (
    QApplication, QAbstractItemView, QCheckBox, QDialog, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QComboBox,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, Qt, QColor, QShortcut, QKeySequence,
)

 
class ReorderTable(QTableWidget):
    def __init__(self, owner):
        super().__init__(0, 8)
        self.owner = owner
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)

    def mousePressEvent(self, event):
        if self.owner.order_mode:
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and event.button() == Qt.MouseButton.LeftButton:
                item = self.item(index.row(), 0)
                if item is not None:
                    self.owner.record_order_click(item.data(Qt.ItemDataRole.UserRole))
            event.accept()
            return
        super().mousePressEvent(event)

    def dropEvent(self, event):
        if self.owner.order_mode:
            event.ignore()
            return
        if event.source() is not self:
            event.ignore()
            return
        point = event.position().toPoint()
        target = self.indexAt(point).row()
        pos = self.dropIndicatorPosition()
        if target < 0:
            target = self.rowCount()
        elif pos == QAbstractItemView.DropIndicatorPosition.BelowItem:
            target += 1
        elif pos == QAbstractItemView.DropIndicatorPosition.OnItem:
            target += 1 if point.y() > self.visualRect(self.model().index(target, 0)).center().y() else 0
        if self.owner.reorder_selected(target):
            event.acceptProposedAction()
        else:
            event.ignore()


class PreAddPreview(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('calibre 登録前グループ設定 — 試作版')
        self.resize(1350, 700)
        self.books = []
        self.order_mode = False
        self.order_candidates = set()
        self.order_clicks = []
        self.undo_stack = []
        self.redo_stack = []
        self.undo_shortcut = QShortcut(QKeySequence('Ctrl+Z'), self)
        self.undo_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.undo_shortcut.activated.connect(self.undo)
        self.redo_shortcuts = []
        for sequence in ('Ctrl+Y', 'Ctrl+Shift+Z'):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(self.redo)
            self.redo_shortcuts.append(shortcut)
        root = QVBoxLayout(self)
        root.addWidget(QLabel('書籍をここにドロップ。複数選択し、グループごとに共通情報を設定します。\n試作版：ライブラリへの登録はしません。'))

        controls = QHBoxLayout()
        add = QPushButton('ファイルを追加')
        add.clicked.connect(self.add_files)
        controls.addWidget(add)
        self.remaining = QCheckBox('未設定のみ表示')
        self.remaining.toggled.connect(self.refresh)
        controls.addWidget(self.remaining)
        controls.addStretch()
        root.addLayout(controls)

        self.table = ReorderTable(self)
        self.table.setHorizontalHeaderLabels(['順番', 'ファイル名', 'シリーズ名', '巻数', '著者名', '作品区分', '出版社', '状態'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.changed)
        self.table.currentCellChanged.connect(self.show_filename)
        root.addWidget(self.table)

        filename_row = QHBoxLayout()
        filename_row.addWidget(QLabel('ファイル名（文字を選択して Ctrl+C でコピー）'))
        self.filename_text = QLineEdit()
        self.filename_text.setReadOnly(True)
        self.filename_text.setPlaceholderText('一覧でファイルをクリックすると、ここに名前が表示されます')
        filename_row.addWidget(self.filename_text)
        copy_name = QPushButton('ファイル名をコピー')
        copy_name.clicked.connect(self.copy_filename)
        filename_row.addWidget(copy_name)
        copy_stem = QPushButton('拡張子なしでコピー')
        copy_stem.clicked.connect(lambda: self.copy_filename(with_extension=False))
        filename_row.addWidget(copy_stem)
        root.addLayout(filename_row)

        row = QHBoxLayout()
        row.addWidget(QLabel('シリーズ名'))
        self.series = QLineEdit()
        row.addWidget(self.series)
        row.addWidget(QLabel('共通タグ'))
        self.tags = QLineEdit()
        row.addWidget(self.tags)
        root.addLayout(row)

        details = QHBoxLayout()
        details.addWidget(QLabel('著者名'))
        self.author = QLineEdit()
        details.addWidget(self.author)
        details.addWidget(QLabel('作品区分'))
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems(['', '小説', '漫画', '同人', 'ライトノベル', '雑誌', '実用書', 'その他'])
        details.addWidget(self.category)
        details.addWidget(QLabel('出版社'))
        self.publisher = QLineEdit()
        details.addWidget(self.publisher)
        root.addLayout(details)

        row = QHBoxLayout()
        apply = QPushButton('選択した書籍に適用')
        apply.clicked.connect(self.apply)
        row.addWidget(apply)
        row.addWidget(QLabel('開始巻数'))
        self.start = QSpinBox()
        self.start.setRange(0, 9999)
        self.start.setValue(1)
        row.addWidget(self.start)
        number = QPushButton('表示順に連番を設定')
        number.clicked.connect(self.number)
        row.addWidget(number)
        self.click_order_button = QPushButton('クリック順で巻数指定')
        self.click_order_button.clicked.connect(self.begin_order_mode)
        row.addWidget(self.click_order_button)
        row.addStretch()
        root.addLayout(row)

        order_controls = QHBoxLayout()
        self.order_status = QLabel('複数選択して「クリック順で巻数指定」を押してください。')
        order_controls.addWidget(self.order_status)
        self.undo_order_button = QPushButton('ひとつ戻す')
        self.undo_order_button.clicked.connect(self.undo_order)
        order_controls.addWidget(self.undo_order_button)
        self.cancel_order_button = QPushButton('キャンセル')
        self.cancel_order_button.clicked.connect(self.cancel_order)
        order_controls.addWidget(self.cancel_order_button)
        self.confirm_order_button = QPushButton('順番を確定')
        self.confirm_order_button.clicked.connect(self.confirm_order)
        order_controls.addWidget(self.confirm_order_button)
        root.addLayout(order_controls)
        self.update_order_controls()

        row = QHBoxLayout()
        row.addWidget(QLabel('巻数はセルをダブルクリックして個別に変更できます。'))
        row.addStretch()
        export = QPushButton('設定内容をJSON出力（テスト用）')
        export.clicked.connect(self.export)
        row.addWidget(export)
        root.addLayout(row)
        actions = QHBoxLayout()
        self.cancel_import = QPushButton('登録をキャンセル')
        self.cancel_import.clicked.connect(self.reject)
        actions.addWidget(self.cancel_import)
        self.confirm_import = QPushButton('この設定でライブラリに登録')
        self.confirm_import.clicked.connect(self.accept)
        actions.addWidget(self.confirm_import)
        root.addLayout(actions)
        self.setAcceptDrops(True)

    def metadata_overrides(self):
        return {
            os.path.normcase(os.path.abspath(book['path'])): {
                'series': book['series'],
                'series_index': book['number'],
                'authors': book['author'],
                'publisher': book['publisher'],
                'category': book['category'],
                'tags': book['tags'],
            }
            for book in self.books
        }

    def show_filename(self, row, column, previous_row=None, previous_column=None):
        if row < 0:
            self.filename_text.clear()
            return
        item = self.table.item(row, 1)
        self.filename_text.setText(item.text() if item else '')
        self.filename_text.setCursorPosition(0)

    def copy_filename(self, with_extension=True):
        name = self.filename_text.text()
        if name:
            QApplication.clipboard().setText(
                name if with_extension else os.path.splitext(name)[0]
            )

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '書籍を選択', '',
            '電子書籍 (*.epub *.azw3 *.mobi *.pdf *.cbz *.txt);;すべて (*.*)'
        )
        self.append(paths)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.append([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()

    def append(self, paths):
        seen = {os.path.normcase(x['path']) for x in self.books}
        for path in paths:
            path = os.path.abspath(path)
            key = os.path.normcase(path)
            if not os.path.isfile(path) or key in seen:
                continue
            seen.add(key)
            name = os.path.splitext(os.path.basename(path))[0]
            m = re.search(r'(?:第\s*(\d+)\s*巻|(?:vol\.?|volume)\s*0*(\d+)|[ _（(]0*(\d{1,3})[）)]?$)', name, re.I)
            number = next((int(v) for v in m.groups() if v), None) if m else None
            self.books.append({'path': path, 'series': '', 'number': number, 'tags': '', 'author': '', 'category': '', 'publisher': '', 'configured': False})
        self.save_undo()
        self.refresh()

    def refresh(self, *_):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for i, book in enumerate(self.books):
            if self.remaining.isChecked() and book['configured']:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            rank = self.order_clicks.index(i) + 1 if i in self.order_clicks else None
            display_index = f'✓ {self.order_start + rank - 1}巻' if self.order_mode and rank is not None else i + 1
            vals = [display_index, os.path.basename(book['path']), book['series'], book['number'], book['author'], book['category'], book['publisher'],
                    '設定済' if book['configured'] else '未設定']
            for col, value in enumerate(vals):
                item = QTableWidgetItem('' if value is None else str(value))
                item.setData(Qt.ItemDataRole.UserRole, i)
                if col != 3 or self.order_mode:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if self.order_mode:
                    if i in self.order_clicks:
                        item.setBackground(QColor('#237347'))
                        item.setForeground(QColor('#ffffff'))
                    elif i in self.order_candidates:
                        item.setBackground(QColor('#235c9f'))
                        item.setForeground(QColor('#ffffff'))
                    else:
                        item.setBackground(QColor('#42464c'))
                        item.setForeground(QColor('#aeb4bd'))
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        if self.order_mode:
            self.table.clearSelection()

    def selected(self):
        return [self.table.item(idx.row(), 0).data(Qt.ItemDataRole.UserRole)
                for idx in self.table.selectionModel().selectedRows()]

    def snapshot(self):
        return (deepcopy(self.books), self.order_mode,
                set(self.order_candidates), list(self.order_clicks),
                getattr(self, 'order_start', self.start.value()))

    def restore(self, state):
        self.books, self.order_mode, candidates, clicks, self.order_start = deepcopy(state)
        self.order_candidates = set(candidates)
        self.order_clicks = list(clicks)
        self.update_order_controls()
        self.refresh()

    def save_undo(self):
        self.undo_stack.append(self.snapshot())
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self.restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            if len(self.undo_stack) > 100:
                self.undo_stack.pop(0)
            self.restore(self.redo_stack.pop())

    def apply(self):
        if not self.selected():
            return
        self.save_undo()
        for i in self.selected():
            self.books[i]['series'] = self.series.text().strip()
            self.books[i]['tags'] = self.tags.text().strip()
            self.books[i]['author'] = self.author.text().strip()
            self.books[i]['category'] = self.category.currentText().strip()
            self.books[i]['publisher'] = self.publisher.text().strip()
            self.books[i]['configured'] = True
        self.refresh()

    def number(self):
        if not self.selected():
            return
        self.save_undo()
        # Current table order, not filename order.
        selected = set(self.selected())
        ordered = [self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                   for row in range(self.table.rowCount())
                   if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) in selected]
        for offset, i in enumerate(ordered):
            self.books[i]['number'] = self.start.value() + offset
            self.books[i]['configured'] = True
        self.refresh()

    def changed(self, item):
        if item.column() != 3:
            return
        i = item.data(Qt.ItemDataRole.UserRole)
        try:
            parsed = float(item.text()) if item.text().strip() else None
            if self.books[i]['number'] != parsed:
                self.save_undo()
            self.books[i]['number'] = float(item.text()) if item.text().strip() else None
        except ValueError:
            QMessageBox.warning(self, '入力エラー', '巻数は数字で入力してください。')
            self.refresh()

    def update_order_controls(self):
        active = self.order_mode
        self.click_order_button.setEnabled(not active)
        self.undo_order_button.setEnabled(active and bool(self.order_clicks))
        self.cancel_order_button.setEnabled(active)
        self.confirm_order_button.setEnabled(active and len(self.order_clicks) == len(self.order_candidates))
        self.remaining.setEnabled(not active)
        self.start.setEnabled(not active)
        self.table.setDragEnabled(not active)
        if active:
            self.order_status.setText(
                f'クリック順を指定中: {len(self.order_clicks)} / {len(self.order_candidates)}冊'
                '（青＝対象、緑＝指定済み、灰＝対象外）'
            )
        else:
            self.order_status.setText('複数選択して「クリック順で巻数指定」を押してください。')

    def begin_order_mode(self):
        candidates = set(self.selected())
        if not candidates:
            QMessageBox.information(self, '巻数指定', '先に巻数を設定する書籍を選択してください。')
            return
        self.save_undo()
        self.order_mode = True
        self.order_candidates = candidates
        self.order_clicks = []
        self.order_start = self.start.value()
        self.update_order_controls()
        self.refresh()

    def record_order_click(self, book_index):
        if book_index not in self.order_candidates:
            return
        self.save_undo()
        if book_index in self.order_clicks:
            # Click again to undo this book and any later sequence numbers.
            pos = self.order_clicks.index(book_index)
            self.order_clicks = self.order_clicks[:pos]
        else:
            self.order_clicks.append(book_index)
        self.update_order_controls()
        self.refresh()

    def undo_order(self):
        if self.order_clicks:
            self.save_undo()
            self.order_clicks.pop()
            self.update_order_controls()
            self.refresh()

    def cancel_order(self):
        if not self.order_mode:
            return
        self.save_undo()
        self.order_mode = False
        self.order_candidates.clear()
        self.order_clicks.clear()
        self.update_order_controls()
        self.refresh()

    def confirm_order(self):
        if not self.order_mode or len(self.order_clicks) != len(self.order_candidates):
            return
        self.save_undo()
        for offset, idx in enumerate(self.order_clicks):
            self.books[idx]['number'] = self.order_start + offset
            self.books[idx]['configured'] = True
        self.order_mode = False
        self.order_candidates.clear()
        self.order_clicks.clear()
        self.update_order_controls()
        self.refresh()

    def reorder_selected(self, target_row):
        chosen = set(self.selected())
        if not chosen:
            return False
        visible = [self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                   for row in range(self.table.rowCount())]
        moving = [i for i in visible if i in chosen]
        remaining = [i for i in visible if i not in chosen]
        insert_at = sum(i not in chosen for i in visible[:target_row])
        reordered_visible = remaining[:insert_at] + moving + remaining[insert_at:]
        if reordered_visible == visible:
            return False
        self.save_undo()
        # When filtering, leave hidden books in place.
        iterator = iter(self.books[i] for i in reordered_visible)
        visible_set = set(visible)
        self.books = [next(iterator) if i in visible_set else book
                      for i, book in enumerate(self.books)]
        self.refresh()
        return True

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, '設定を書き出す', 'preadd-settings.json', 'JSON (*.json)')
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.books, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, '保存', '設定のみ書き出しました。書籍は未登録です。')



def preadd_group_dialog(paths, parent=None):
    dialog = PreAddPreview()
    if parent is not None:
        dialog.setParent(parent)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setWindowFlags(Qt.WindowType.Dialog)
    dialog.append(paths)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.metadata_overrides()

if __name__ == '__main__':
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = PreAddPreview()
    dialog.exec()
