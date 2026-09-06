# ADB File Explorer
# Copyright (C) 2022  Azat Aldeshov
import re
import sys
from typing import Any

from PyQt5 import QtCore, QtGui
from PyQt5.QtCore import Qt, QPoint, QModelIndex, QAbstractListModel, QVariant, QRect, QSize, QEvent, QObject, \
    pyqtSignal
from PyQt5.QtGui import QPixmap, QColor, QPalette, QKeySequence
from PyQt5.QtWidgets import QMenu, QAction, QMessageBox, QFileDialog, QStyle, QWidget, QStyledItemDelegate, \
    QStyleOptionViewItem, QApplication, QListView, QVBoxLayout, QLabel, QSizePolicy, QHBoxLayout, QTextEdit, \
    QMainWindow, QLineEdit, QShortcut


def natural_sort_key(text: str) -> list:
    """Split a string so that embedded numbers sort numerically ('f2' < 'f10')."""
    return [int(chunk) if chunk.isdigit() else chunk.lower()
            for chunk in re.split(r'(\d+)', text or '')]

from app.core.configurations import Resources
from app.core.main import Adb
from app.core.managers import Global
from app.data.models import FileType, MessageData, MessageType
from app.data.repositories import FileRepository
from app.gui.explorer.toolbar import ParentButton, UploadTools, PathBar
from app.helpers.tools import AsyncRepositoryWorker, ProgressCallbackHelper, read_string_from_file
from app.gui.widgets.circular_progress import CircularProgress


class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text: str, parent=None):
        super(ClickableLabel, self).__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super(ClickableLabel, self).mouseReleaseEvent(event)


class FileHeaderWidget(QWidget):
    # Emits one of FileListModel.SORT_* when a column header is clicked.
    sort_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super(FileHeaderWidget, self).__init__(parent)
        self.setLayout(QHBoxLayout(self))
        policy = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        self.file = ClickableLabel('File', self)
        self.file.setContentsMargins(45, 0, 0, 0)
        policy.setHorizontalStretch(39)
        self.file.setSizePolicy(policy)
        self.file.clicked.connect(lambda: self.sort_requested.emit(FileListModel.SORT_NAME))
        self.layout().addWidget(self.file)

        self.permissions = ClickableLabel('Permissions', self)
        self.permissions.setAlignment(Qt.AlignCenter)
        policy.setHorizontalStretch(18)
        self.permissions.setSizePolicy(policy)
        self.permissions.clicked.connect(lambda: self.sort_requested.emit(FileListModel.SORT_PERMISSIONS))
        self.layout().addWidget(self.permissions)

        self.size = ClickableLabel('Size', self)
        self.size.setAlignment(Qt.AlignCenter)
        policy.setHorizontalStretch(21)
        self.size.setSizePolicy(policy)
        self.size.clicked.connect(lambda: self.sort_requested.emit(FileListModel.SORT_SIZE))
        self.layout().addWidget(self.size)

        self.date = ClickableLabel('Date', self)
        self.date.setAlignment(Qt.AlignCenter)
        policy.setHorizontalStretch(22)
        self.date.setSizePolicy(policy)
        self.date.clicked.connect(lambda: self.sort_requested.emit(FileListModel.SORT_DATE))
        self.layout().addWidget(self.date)

        self.__titles = {
            FileListModel.SORT_NAME: (self.file, 'File'),
            FileListModel.SORT_PERMISSIONS: (self.permissions, 'Permissions'),
            FileListModel.SORT_SIZE: (self.size, 'Size'),
            FileListModel.SORT_DATE: (self.date, 'Date'),
        }
        self.setStyleSheet(read_string_from_file(Resources.style_file_header))

    def set_sort_indicator(self, key: int, descending: bool):
        arrow = ' ▾' if descending else ' ▴'
        for column, (label, title) in self.__titles.items():
            label.setText(title + arrow if column == key else title)


class FileExplorerToolbar(QWidget):
    def __init__(self, parent=None):
        super(FileExplorerToolbar, self).__init__(parent)
        self.setLayout(QHBoxLayout(self))
        policy = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        policy.setHorizontalStretch(1)

        self.upload_tools = UploadTools(self)
        self.upload_tools.setSizePolicy(policy)
        self.layout().addWidget(self.upload_tools)

        self.parent_button = ParentButton(self)
        self.parent_button.setSizePolicy(policy)
        self.layout().addWidget(self.parent_button)

        self.path_bar = PathBar(self)
        policy.setHorizontalStretch(8)
        self.path_bar.setSizePolicy(policy)
        self.layout().addWidget(self.path_bar)


class FileItemDelegate(QStyledItemDelegate):
    def sizeHint(self, option: 'QStyleOptionViewItem', index: QtCore.QModelIndex) -> QtCore.QSize:
        result = super(FileItemDelegate, self).sizeHint(option, index)
        result.setHeight(40)
        return result

    def setEditorData(self, editor: QWidget, index: QtCore.QModelIndex):
        editor.setText(index.model().data(index, Qt.EditRole))

    def updateEditorGeometry(self, editor: QWidget, option: 'QStyleOptionViewItem', index: QtCore.QModelIndex):
        editor.setGeometry(
            option.rect.left() + 48, option.rect.top(), int(option.rect.width() / 2.5) - 55, option.rect.height()
        )

    def setModelData(self, editor: QWidget, model: QtCore.QAbstractItemModel, index: QtCore.QModelIndex):
        model.setData(index, editor.text(), Qt.EditRole)

    @staticmethod
    def paint_line(painter: QtGui.QPainter, color: QColor, x, y, w, h):
        painter.setPen(color)
        painter.drawLine(x, y, w, h)

    @staticmethod
    def paint_text(painter: QtGui.QPainter, text: str, color: QColor, options, x, y, w, h):
        painter.setPen(color)
        painter.drawText(QRect(x, y, w, h), options, text)

    def paint(self, painter: QtGui.QPainter, option: 'QStyleOptionViewItem', index: QtCore.QModelIndex):
        if not index.data():
            return super(FileItemDelegate, self).paint(painter, option, index)

        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, option, painter, option.widget)

        # Use palette-based divider color instead of hardcoded hex
        line_color = option.palette.color(QPalette.Mid)
        text_color = option.palette.color(QPalette.Normal, QPalette.Text)

        top = option.rect.top()
        bottom = option.rect.height()

        first_start = option.rect.left() + 50
        second_start = option.rect.left() + int(option.rect.width() / 2.5)
        third_start = option.rect.left() + int(option.rect.width() / 1.75)
        fourth_start = option.rect.left() + int(option.rect.width() / 1.25)
        end = option.rect.width() + option.rect.left()

        self.paint_text(
            painter, index.data().name, text_color, option.displayAlignment,
            first_start, top, second_start - first_start - 4, bottom
        )

        self.paint_line(painter, line_color, second_start - 2, top, second_start - 1, bottom)

        self.paint_text(
            painter, index.data().permissions, text_color, Qt.AlignCenter | option.displayAlignment,
            second_start, top, third_start - second_start - 4, bottom
        )

        self.paint_line(painter, line_color, third_start - 2, top, third_start - 1, bottom)

        self.paint_text(
            painter, index.data().size, text_color, Qt.AlignCenter | option.displayAlignment,
            third_start, top, fourth_start - third_start - 4, bottom
        )

        self.paint_line(painter, line_color, fourth_start - 2, top, fourth_start - 1, bottom)

        self.paint_text(
            painter, index.data().date, text_color, Qt.AlignCenter | option.displayAlignment,
            fourth_start, top, end - fourth_start, bottom
        )


class FileListModel(QAbstractListModel):
    SORT_NAME = 0
    SORT_PERMISSIONS = 1
    SORT_SIZE = 2
    SORT_DATE = 3

    # Scaled QPixmap cache shared by every model instance — the icon set is
    # tiny and fixed, so there is no reason to re-read/re-scale SVGs while
    # scrolling a large directory.
    __pixmap_cache = {}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.__all_items = []      # everything returned for the current folder
        self.items = []            # filtered + sorted view actually shown
        self.__filter = ''
        self.__sort_key = self.SORT_NAME
        self.__sort_descending = False

    def clear(self):
        self.beginResetModel()
        self.__all_items = []
        self.items = []
        self.endResetModel()

    def populate(self, files: list):
        self.beginResetModel()
        self.__all_items = list(files)
        self.__rebuild()
        self.endResetModel()

    @property
    def total_count(self) -> int:
        return len(self.__all_items)

    @property
    def filter_text(self) -> str:
        return self.__filter

    @property
    def sort_key(self) -> int:
        return self.__sort_key

    @property
    def sort_descending(self) -> bool:
        return self.__sort_descending

    def set_filter(self, text: str):
        text = (text or '').strip().lower()
        if text == self.__filter:
            return
        self.__filter = text
        self.beginResetModel()
        self.__rebuild()
        self.endResetModel()

    def toggle_sort(self, key: int):
        if key == self.__sort_key:
            self.__sort_descending = not self.__sort_descending
        else:
            self.__sort_key = key
            self.__sort_descending = False
        self.beginResetModel()
        self.__rebuild()
        self.endResetModel()

    def __rebuild(self):
        items = self.__all_items
        if self.__filter:
            items = [f for f in items if self.__filter in (f.name or '').lower()]

        name_key = lambda f: natural_sort_key(f.name)
        if self.__sort_key == self.SORT_SIZE:
            key = lambda f: (f.raw_size or 0, name_key(f))
        elif self.__sort_key == self.SORT_DATE:
            key = lambda f: (f.raw_date.timestamp() if f.raw_date else 0.0, name_key(f))
        elif self.__sort_key == self.SORT_PERMISSIONS:
            key = lambda f: ((f.permissions or '').lower(), name_key(f))
        else:
            key = name_key

        items = sorted(items, key=key, reverse=self.__sort_descending)
        # Keep directories grouped above files regardless of sort direction.
        items = sorted(items, key=lambda f: 0 if f.isdir else 1)
        self.items = items

    def rowCount(self, parent: QModelIndex = ...) -> int:
        return len(self.items)

    @classmethod
    def __icon_path_for(cls, file) -> str:
        file_type = file.type
        if file_type == FileType.DIRECTORY:
            return Resources.icon_folder
        elif file_type == FileType.FILE:
            return Resources.icon_file
        elif file_type == FileType.LINK:
            link_type = file.link_type
            if link_type == FileType.DIRECTORY:
                return Resources.icon_link_folder
            elif link_type == FileType.FILE:
                return Resources.icon_link_file
            return Resources.icon_link_file_unknown
        return Resources.icon_file_unknown

    def __icon_pixmap(self, file) -> QPixmap:
        path = self.__icon_path_for(file)
        pixmap = self.__pixmap_cache.get(path)
        if pixmap is None:
            pixmap = QPixmap(path).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.__pixmap_cache[path] = pixmap
        return pixmap

    def icon_path(self, index: QModelIndex = ...):
        return self.__icon_path_for(self.items[index.row()])

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags

        return Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def setData(self, index: QModelIndex, value: Any, role: int = ...) -> bool:
        if role == Qt.EditRole and value:
            data, error = FileRepository.rename(self.items[index.row()], value)
            if error:
                Global().communicate.notification.emit(
                    MessageData(
                        timeout=10000,
                        title="Rename",
                        body=str(error),
                        message_type=MessageType.ERROR_MESSAGE,
                    )
                )
            Global.communicate.files__refresh.emit()
        return super(FileListModel, self).setData(index, value, role)

    def data(self, index: QModelIndex, role: int = ...) -> Any:
        if not index.isValid():
            return QVariant()

        if role == Qt.DisplayRole:
            return self.items[index.row()]
        elif role == Qt.EditRole:
            return self.items[index.row()].name
        elif role == Qt.DecorationRole:
            return self.__icon_pixmap(self.items[index.row()])
        return QVariant()


class FileExplorerWidget(QWidget):
    FILES_WORKER_ID = 300
    DOWNLOAD_WORKER_ID = 399

    def __init__(self, parent=None):
        super(FileExplorerWidget, self).__init__(parent)
        self.main_layout = QVBoxLayout(self)

        self.toolbar = FileExplorerToolbar(self)
        self.main_layout.addWidget(self.toolbar)

        self.filter_bar = QLineEdit(self)
        self.filter_bar.setClearButtonEnabled(True)
        self.filter_bar.setPlaceholderText("Filter files by name…  (Ctrl+F)")
        self.filter_bar.textChanged.connect(self.__on_filter_changed)
        self.filter_bar.installEventFilter(self)
        self.main_layout.addWidget(self.filter_bar)

        self.header = FileHeaderWidget(self)
        self.header.sort_requested.connect(self.__on_sort_requested)
        self.main_layout.addWidget(self.header)

        self.list = QListView(self)
        self.model = FileListModel(self.list)
        self.header.set_sort_indicator(self.model.sort_key, self.model.sort_descending)

        self.list.setSpacing(1)
        self.list.setModel(self.model)
        self.list.installEventFilter(self)
        self.list.doubleClicked.connect(self.open)
        self.list.setItemDelegate(FileItemDelegate(self.list))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.context_menu)
        self.list.setStyleSheet(read_string_from_file(Resources.style_file_list))
        self.list.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        # Every row is a fixed 40px (see FileItemDelegate.sizeHint); telling the
        # view lets it skip per-row measuring and render big folders smoothly.
        self.list.setUniformItemSizes(True)
        self.list.setLayoutMode(QListView.Batched)
        self.list.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.layout().addWidget(self.list)

        self.loading = CircularProgress(size=48, thickness=3, parent=self)
        self.loading.setVisible(False)
        self.main_layout.addWidget(self.loading, alignment=Qt.AlignCenter)

        self.empty_label = QLabel("Folder is empty", self)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(read_string_from_file(Resources.style_empty_label))
        self.layout().addWidget(self.empty_label)

        self.main_layout.setStretch(self.layout().count() - 1, 1)
        self.main_layout.setStretch(self.layout().count() - 2, 1)

        self.text_view_window = None
        self.setLayout(self.main_layout)

        QShortcut(QKeySequence.Find, self, activated=self.__focus_filter)
        QShortcut(QKeySequence.Refresh, self, activated=Global().communicate.files__refresh.emit)
        QShortcut(QKeySequence("F5"), self, activated=Global().communicate.files__refresh.emit)
        QShortcut(QKeySequence.SelectAll, self.list, activated=self.list.selectAll)

        Global().communicate.files__refresh.connect(self.update)

    def __focus_filter(self):
        self.filter_bar.setFocus()
        self.filter_bar.selectAll()

    def __on_filter_changed(self, text: str):
        self.model.set_filter(text)
        self.__refresh_placeholder()

    def __on_sort_requested(self, key: int):
        self.model.toggle_sort(key)
        self.header.set_sort_indicator(self.model.sort_key, self.model.sort_descending)

    def __refresh_placeholder(self):
        showing = self.model.rowCount()
        total = self.model.total_count
        if total and not showing and self.model.filter_text:
            self.empty_label.setText("No files match '%s'" % self.model.filter_text)
            self.list.setHidden(True)
            self.empty_label.setHidden(False)
        elif total:
            self.list.setHidden(False)
            self.empty_label.setHidden(True)
            if showing != total:
                Global().communicate.status_bar.emit(
                    "Showing %d of %d items" % (showing, total), 4000
                )

    @property
    def file(self):
        if self.list and self.list.currentIndex():
            return self.model.items[self.list.currentIndex().row()]

    @property
    def files(self):
        if self.list and len(self.list.selectedIndexes()) > 0:
            return map(lambda index: self.model.items[index.row()], self.list.selectedIndexes())

    def update(self):
        super(FileExplorerWidget, self).update()
        worker = AsyncRepositoryWorker(
            name="Files",
            worker_id=self.FILES_WORKER_ID,
            repository_method=FileRepository.files,
            response_callback=self._async_response,
            arguments=()
        )
        if Adb.worker().work(worker):
            # First Setup loading view
            self.filter_bar.blockSignals(True)
            self.filter_bar.clear()
            self.filter_bar.blockSignals(False)
            self.model.set_filter('')
            self.model.clear()
            self.list.setHidden(True)
            self.loading.setHidden(False)
            self.empty_label.setHidden(True)
            self.loading.start()

            # Then start async worker
            worker.start()
            Global().communicate.path_toolbar__refresh.emit()

    def close(self) -> bool:
        Global().communicate.files__refresh.disconnect()
        return super(FileExplorerWidget, self).close()

    def _async_response(self, files: list, error: str):
        self.loading.stop()
        self.loading.setHidden(True)

        if error:
            print(error, file=sys.stderr)
            if not files:
                Global().communicate.notification.emit(
                    MessageData(
                        title='Files',
                        timeout=15000,
                        body=str(error),
                        message_type=MessageType.ERROR_MESSAGE,
                    )
                )
        if not files:
            self.empty_label.setHidden(False)
        else:
            self.list.setHidden(False)
            self.model.populate(files)
            self.list.setFocus()

    def eventFilter(self, obj: 'QObject', event: 'QEvent') -> bool:
        if obj == self.list and \
                event.type() == QEvent.KeyPress and \
                event.matches(QKeySequence.InsertParagraphSeparator) and \
                not self.list.isPersistentEditorOpen(self.list.currentIndex()):
            self.open(self.list.currentIndex())

        if obj == self.filter_bar and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                # Swallow Escape so it clears the filter instead of navigating up.
                if self.filter_bar.text():
                    self.filter_bar.clear()
                else:
                    self.list.setFocus()
                return True
            if event.key() in (Qt.Key_Down, Qt.Key_Up) and self.model.rowCount():
                self.list.setFocus()
                if not self.list.currentIndex().isValid():
                    self.list.setCurrentIndex(self.model.index(0))
                return True

        return super(FileExplorerWidget, self).eventFilter(obj, event)

    def open(self, index: QModelIndex = ...):
        if Adb.manager().open(self.model.items[index.row()]):
            Global().communicate.files__refresh.emit()

    def context_menu(self, pos: QPoint):
        menu = QMenu()
        menu.addSection("Actions")

        action_copy = QAction('Copy to...', self)
        action_copy.setDisabled(True)
        menu.addAction(action_copy)

        action_move = QAction('Move to...', self)
        action_move.setDisabled(True)
        menu.addAction(action_move)

        action_rename = QAction('Rename', self)
        action_rename.triggered.connect(self.rename)
        menu.addAction(action_rename)

        action_open_file = QAction('Open', self)
        action_open_file.triggered.connect(self.open_file)
        menu.addAction(action_open_file)

        action_delete = QAction('Delete', self)
        action_delete.triggered.connect(self.delete)
        menu.addAction(action_delete)

        action_download = QAction('Download', self)
        action_download.triggered.connect(self.download_files)
        menu.addAction(action_download)

        action_download_to = QAction('Download to...', self)
        action_download_to.triggered.connect(self.download_to)
        menu.addAction(action_download_to)

        menu.addSeparator()

        action_properties = QAction('Properties', self)
        action_properties.triggered.connect(self.file_properties)
        menu.addAction(action_properties)

        menu.exec(self.mapToGlobal(pos))

    @staticmethod
    def default_response(data, error):
        if error:
            Global().communicate.notification.emit(
                MessageData(
                    title='Download error',
                    timeout=15000,
                    body=str(error),
                    message_type=MessageType.ERROR_MESSAGE,
                )
            )
        if data:
            Global().communicate.notification.emit(
                MessageData(
                    title='Downloaded',
                    timeout=15000,
                    body=data
                )
            )

    def rename(self):
        self.list.edit(self.list.currentIndex())

    def open_file(self):
        # QDesktopServices.openUrl(QUrl.fromLocalFile("downloaded_path")) open via external app
        if not self.file.isdir:
            data, error = FileRepository.open_file(self.file)
            if error:
                Global().communicate.notification.emit(
                    MessageData(
                        title='File',
                        timeout=15000,
                        body=str(error),
                        message_type=MessageType.ERROR_MESSAGE,
                    )
                )
            else:
                self.text_view_window = TextView(self.file.name, data)
                self.text_view_window.show()

    def delete(self):
        file_names = ', '.join(map(lambda f: f.name, self.files))
        reply = QMessageBox.critical(
            self,
            'Delete',
            "Do you want to delete '%s'? It cannot be undone!" % file_names,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            for file in self.files:
                data, error = FileRepository.delete(file)
                if data:
                    Global().communicate.notification.emit(
                        MessageData(
                            timeout=10000,
                            title="Delete",
                            body=data,
                        )
                    )
                if error:
                    Global().communicate.notification.emit(
                        MessageData(
                            timeout=10000,
                            title="Delete",
                            body=str(error),
                            message_type=MessageType.ERROR_MESSAGE,
                        )
                    )
            Global.communicate.files__refresh.emit()

    def download_to(self):
        dir_name = QFileDialog.getExistingDirectory(self, 'Download to', '~')
        if dir_name:
            self.download_files(dir_name)

    def download_files(self, destination: str = None):
        for file in self.files:
            helper = ProgressCallbackHelper()
            worker = AsyncRepositoryWorker(
                worker_id=self.DOWNLOAD_WORKER_ID,
                name="Download",
                repository_method=FileRepository.download,
                response_callback=self.default_response,
                arguments=(
                    helper.progress_callback.emit, file.path, destination
                )
            )
            if Adb.worker().work(worker):
                Global().communicate.notification.emit(
                    MessageData(
                        title="Downloading to",
                        message_type=MessageType.LOADING_MESSAGE,
                        message_catcher=worker.set_loading_widget
                    )
                )
                helper.setup(worker, worker.update_loading_widget)
                worker.start()

    def file_properties(self):
        file, error = FileRepository.file(self.file.path)
        file = file if file else self.file

        if error:
            Global().communicate.notification.emit(
                MessageData(
                    timeout=10000,
                    title="Opening folder",
                    body=str(error),
                    message_type=MessageType.ERROR_MESSAGE,
                )
            )

        info = "<br/><u><b>%s</b></u><br/>" % str(file)
        info += "<pre>Name:        %s</pre>" % file.name or '-'
        info += "<pre>Owner:       %s</pre>" % file.owner or '-'
        info += "<pre>Group:       %s</pre>" % file.group or '-'
        info += "<pre>Size:        %s</pre>" % file.raw_size or '-'
        info += "<pre>Permissions: %s</pre>" % file.permissions or '-'
        info += "<pre>Date:        %s</pre>" % file.raw_date or '-'
        info += "<pre>Type:        %s</pre>" % file.type or '-'

        if file.type == FileType.LINK:
            info += "<pre>Links to:    %s</pre>" % file.link or '-'

        properties = QMessageBox(self)
        properties.setStyleSheet(read_string_from_file(Resources.style_properties_dialog))
        properties.setIconPixmap(
            QPixmap(self.model.icon_path(self.list.currentIndex())).scaled(128, 128, Qt.KeepAspectRatio)
        )
        properties.setWindowTitle('Properties')
        properties.setInformativeText(info)
        properties.exec_()


class TextView(QMainWindow):
    def __init__(self, filename, data):
        QMainWindow.__init__(self)

        self.setMinimumSize(QSize(500, 300))
        self.setWindowTitle(filename)

        self.text_edit = QTextEdit(self)
        self.setCentralWidget(self.text_edit)
        self.text_edit.insertPlainText(data)
        self.text_edit.move(10, 10)
