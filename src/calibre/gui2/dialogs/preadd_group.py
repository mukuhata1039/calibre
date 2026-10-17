#!/usr/bin/env python
# calibre pre-add grouped metadata editor — prototype only.
# No changes to the library or source book files.
import json
from calibre.gui2 import gprefs
from copy import deepcopy
import os
import re
import sys

from qt.core import (
    QApplication, QAbstractItemView, QCheckBox, QDialog, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QComboBox,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, Qt, QColor, QShortcut, QKeySequence, QWidget, QInputDialog,
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
        self.library_paths = []
        self.default_library = ''
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
        root.addWidget(QLabel('書籍の情報を確認・変更し、確定するとライブラリへ登録します。\n複数選択した書籍には共通設定を適用できます。'))

        controls = QHBoxLayout()
        add = QPushButton('ファイルを追加')
        add.clicked.connect(self.add_files)
        controls.addWidget(add)
        add_folder = QPushButton('フォルダを追加（サブフォルダも検索）')
        add_folder.clicked.connect(self.add_folder)
        controls.addWidget(add_folder)
        self.remaining = QCheckBox('未設定のみ表示')
        self.remaining.toggled.connect(self.refresh)
        controls.addWidget(self.remaining)
        controls.addStretch()
        root.addLayout(controls)

        self.table = ReorderTable(self)
        self.table.setHorizontalHeaderLabels(['順番', 'ファイル名', 'シリーズ名', '巻数', '著者名', '登録先', '出版社', '状態'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.changed)
        self.table.currentCellChanged.connect(self.show_filename)
        # Keep the library's normal internal format storage as the default.
        # Existing external books remain available via their legacy manifest;
        # newly imported books no longer create external mirrors.
        root.addWidget(QLabel('書籍本体はcalibreライブラリ内に保存します。軽量バックアップでは書籍本体を除外できます。'))

        root.addWidget(self.table)

        self.import_fields = {}
        field_labels = [
            ('title', 'タイトル'), ('authors', '著者'), ('series', 'シリーズ'),
            ('series_index', '巻数'), ('publisher', '出版社'),
            ('tags', '元ファイルのタグ'), ('cover', '表紙'),
            ('pubdate', '出版日'), ('comments', 'あらすじ'), ('identifiers', '識別子'),
        ]
        field_row = QHBoxLayout()
        field_row.addWidget(QLabel('登録する情報'))
        active_fields = gprefs.get('preadd_import_fields', None)
        if active_fields is None:
            active_fields = ['title', 'authors', 'series', 'series_index', 'publisher', 'cover']
        for key, label in field_labels:
            control = QCheckBox(label)
            control.setChecked(key in active_fields)
            self.import_fields[key] = control
            control.toggled.connect(self.persist_import_fields)
            field_row.addWidget(control)
        root.addLayout(field_row)

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel('タイトル'))
        self.title = QLineEdit()
        title_row.addWidget(self.title)
        root.addLayout(title_row)

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
        root.addLayout(row)

        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel('シリーズの読み'))
        self.series_reading = QLineEdit()
        self.series_reading.setPlaceholderText('ひらがな・カタカナ（必要なら修正）')
        self.series_reading.textChanged.connect(self.update_roman_preview)
        sort_row.addWidget(self.series_reading)
        sort_row.addWidget(QLabel('ローマ字ソート'))
        self.series_roman = QLineEdit()
        self.series_roman.setPlaceholderText('alphabetic sort key')
        sort_row.addWidget(self.series_roman)
        root.addLayout(sort_row)

        details = QHBoxLayout()
        details.addWidget(QLabel('著者名'))
        self.author_inputs = []
        self.author_container = QWidget()
        self.author_layout = QVBoxLayout(self.author_container)
        self.author_layout.setContentsMargins(0, 0, 0, 0)
        self.add_author_field()
        details.addWidget(self.author_container)
        add_author_button = QPushButton('＋ 著者を追加')
        add_author_button.clicked.connect(self.add_author_field)
        details.addWidget(add_author_button)
        details.addWidget(QLabel('登録先ライブラリ'))
        self.category = QComboBox()
        self.category.addItems([])
        self.category.currentIndexChanged.connect(self.library_changed)
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

    def set_libraries(self, library_paths, default_library):
        self.library_paths = list(library_paths)
        self.default_library = default_library
        self.category.blockSignals(True)
        self.category.clear()
        for path in self.library_paths:
            self.category.addItem(os.path.basename(os.path.normpath(path)), path)
        idx = self.category.findData(default_library)
        if idx >= 0:
            self.category.setCurrentIndex(idx)
        self.category.blockSignals(False)
        for book in self.books:
            if not book.get('category'):
                book['category'] = default_library

    def romanize(self, text):
        if not text.strip():
            return ''
        try:
            from calibre_extensions.icu import Transliterator
            converter = getattr(self, '_roman_transliterator', None)
            if converter is None:
                converter = Transliterator('preadd_japanese_roman', ':: Any-Latin; :: Latin-ASCII; :: Lower;')
                self._roman_transliterator = converter
            return converter.transliterate(text).strip().lower()
        except Exception:
            return text.strip().lower()

    def update_roman_preview(self, value):
        self.series_roman.setText(self.romanize(value))

    def library_changed(self, _index):
        library = self.category.currentData()
        if not library:
            return
        targets = self.selected() or list(range(len(self.books)))
        if not targets:
            return
        self.save_undo()
        for i in targets:
            self.books[i]['category'] = library
        # Update the visible destination without resetting selections or
        # overwriting any of the other metadata fields.
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell is not None and cell.data(Qt.ItemDataRole.UserRole) in targets:
                destination = self.table.item(row, 5)
                if destination is not None:
                    destination.setText(os.path.basename(os.path.normpath(library)))

    def persist_import_fields(self, *_):
        gprefs['preadd_import_fields'] = [
            name for name, checkbox in self.import_fields.items() if checkbox.isChecked()
        ]

    def metadata_overrides(self):
        return {
            os.path.normcase(os.path.abspath(book['path'])): {
                'title': book.get('title', ''),
                'series': book['series'],
                'series_roman': book.get('series_roman') or self.romanize(book.get('series_reading') or book['series']),
                'series_index': book['number'],
                'authors': book['author'],
                'publisher': book['publisher'],
                'library_path': book['category'],
                'tags': book['tags'],
                'import_fields': [name for name, control in self.import_fields.items() if control.isChecked()],
            }
            for book in self.books
        }

    def add_author_field(self):
        field = QLineEdit()
        field.setPlaceholderText(f'著者 {len(self.author_inputs) + 1}')
        self.author_layout.addWidget(field)
        self.author_inputs.append(field)
        return field

    def author_display_name(self):
        return '×'.join(
            field.text().strip()
            for field in self.author_inputs if field.text().strip()
        )

    def show_filename(self, row, column, previous_row=None, previous_column=None):
        if row < 0:
            self.filename_text.clear()
            return
        item = self.table.item(row, 1)
        self.filename_text.setText(item.text() if item else '')
        self.filename_text.setCursorPosition(0)
        entry = self.table.item(row, 0)
        if entry is None:
            return
        book = self.books[entry.data(Qt.ItemDataRole.UserRole)]
        # Title is manual-only: clicking a book must not populate the edit field.
        self.title.clear()
        self.series.setText(book.get('series', ''))
        self.series_reading.setText(book.get('series_reading', ''))
        self.series_roman.setText(book.get('series_roman', ''))
        self.publisher.setText(book.get('publisher', ''))
        idx = self.category.findData(book.get('category', self.default_library))
        if idx >= 0:
            self.category.blockSignals(True)
            self.category.setCurrentIndex(idx)
            self.category.blockSignals(False)
        names = [x for x in book.get('author', '').split('×') if x]
        while len(self.author_inputs) < max(1, len(names)):
            self.add_author_field()
        for idx, field in enumerate(self.author_inputs):
            field.setText(names[idx] if idx < len(names) else '')

    def copy_filename(self, with_extension=True):
        name = self.filename_text.text()
        if name:
            QApplication.clipboard().setText(
                name if with_extension else os.path.splitext(name)[0]
            )

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '書籍フォルダを選択')
        if folder:
            self.append([folder])

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
        from calibre.gui2.dialogs.preadd_group_metadata import expand_book_paths
        paths = expand_book_paths(paths)
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
            info = {}
            try:
                from calibre.gui2.dialogs.preadd_group_metadata import read_preview_metadata
                info = read_preview_metadata(path)
            except Exception as err:
                print(f'Pre-add metadata extraction failed for {path}: {err}', file=sys.stderr)
            self.books.append({
                'path': path, 'title': info.get('title') or name,
                'series': info.get('series', ''),
                'series_reading': '',
                'series_roman': self.romanize(info.get('series', '')),
                'number': info.get('number', number),
                'tags': info.get('tags', ''),
                'author': '×'.join(info.get('authors', [])),
                'category': self.default_library, 'publisher': info.get('publisher', ''),
                'configured': False
            })
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
            vals = [display_index, os.path.basename(book['path']), book['series'], book['number'], book['author'], os.path.basename(os.path.normpath(book['category'])) if book['category'] else '', book['publisher'],
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
        targets = self.selected()
        for i in targets:
            if len(targets) == 1 and self.title.text().strip():
                self.books[i]['title'] = self.title.text().strip()
            self.books[i]['series'] = self.series.text().strip()
            self.books[i]['series_reading'] = self.series_reading.text().strip()
            self.books[i]['series_roman'] = self.series_roman.text().strip()
            self.books[i]['author'] = self.author_display_name()
            # Destination is updated immediately on combo selection.
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



def preadd_group_dialog(paths, parent=None, library_paths=(), default_library=''):
    dialog = PreAddPreview()
    if parent is not None:
        dialog.setParent(parent)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setWindowFlags(Qt.WindowType.Dialog)
    dialog.set_libraries(library_paths, default_library)
    dialog.append(paths)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.metadata_overrides()

if __name__ == '__main__':
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = PreAddPreview()
    dialog.exec()
