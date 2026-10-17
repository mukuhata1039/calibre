#!/usr/bin/env python
# calibre pre-add grouped metadata editor — prototype only.
# No changes to the library or source book files.
import json
import os
import re
import sys

from qt.core import (
    QApplication, QAbstractItemView, QCheckBox, QDialog, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QComboBox,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, Qt,
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

    def dropEvent(self, event):
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
        root.addWidget(self.table)

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
        row.addStretch()
        root.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel('巻数はセルをダブルクリックして個別に変更できます。'))
        row.addStretch()
        export = QPushButton('設定内容をJSON出力（テスト用）')
        export.clicked.connect(self.export)
        row.addWidget(export)
        root.addLayout(row)
        self.setAcceptDrops(True)

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
        self.refresh()

    def refresh(self, *_):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for i, book in enumerate(self.books):
            if self.remaining.isChecked() and book['configured']:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            vals = [i + 1, os.path.basename(book['path']), book['series'], book['number'], book['author'], book['category'], book['publisher'],
                    '設定済' if book['configured'] else '未設定']
            for col, value in enumerate(vals):
                item = QTableWidgetItem('' if value is None else str(value))
                item.setData(Qt.ItemDataRole.UserRole, i)
                if col != 3:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)

    def selected(self):
        return [self.table.item(idx.row(), 0).data(Qt.ItemDataRole.UserRole)
                for idx in self.table.selectionModel().selectedRows()]

    def apply(self):
        for i in self.selected():
            self.books[i]['series'] = self.series.text().strip()
            self.books[i]['tags'] = self.tags.text().strip()
            self.books[i]['author'] = self.author.text().strip()
            self.books[i]['category'] = self.category.currentText().strip()
            self.books[i]['publisher'] = self.publisher.text().strip()
            self.books[i]['configured'] = True
        self.refresh()

    def number(self):
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
            self.books[i]['number'] = float(item.text()) if item.text().strip() else None
        except ValueError:
            QMessageBox.warning(self, '入力エラー', '巻数は数字で入力してください。')
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


if __name__ == '__main__':
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = PreAddPreview()
    dialog.exec()
