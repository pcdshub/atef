import math
from typing import Any, Callable, List, Optional

from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import QModelIndex, Qt

from atef.widgets.config.data_passive import ComparisonRowWidget
from atef.widgets.core import DesignerDisplay

USER_DATA_ROLE = Qt.UserRole + 1
SETUP_SLOT_ROLE = Qt.UserRole + 2


class PagedTableWidget(DesignerDisplay, QtWidgets.QWidget):
    table_view: QtWidgets.QTableView
    page_spinbox: QtWidgets.QSpinBox
    next_button: QtWidgets.QToolButton
    prev_button: QtWidgets.QToolButton
    search_edit: QtWidgets.QLineEdit
    page_count_label: QtWidgets.QLabel

    filename = 'paged_table.ui'

    # TODO: Show specific comparison
    # TODO: toggle for auto-page-size

    def __init__(
        self,
        *args,
        title: Optional[str] = None,
        item_list: Optional[List[Any]] = None,
        page_size: Optional[int] = None,
        widget_cls: Optional[QtWidgets.QWidget] = ComparisonRowWidget,
        **kwargs
    ):
        # TODO: remove row numbers
        # TODO: set up title for column
        super().__init__(*args, **kwargs)
        self.page_size = page_size
        self.row_cls = widget_cls

        self.source_model = QtGui.QStandardItemModel(0, 1, parent=self)
        self.proxy_model = PagedProxyModel(self)
        self.proxy_model.setSourceModel(self.source_model)
        self.table_view.setModel(self.proxy_model)

        self.row_delegate = CustDelegate(widget_cls=self.row_cls)
        self.table_view.setItemDelegateForColumn(0, self.row_delegate)
        self.table_view.horizontalHeader().setStretchLastSection(True)
        if title:
            self.source_model.setHeaderData(0, Qt.Horizontal, title, Qt.DisplayRole)
        else:
            self.table_view.horizontalHeader().hide()
        self.table_view.verticalHeader().hide()
        self.proxy_model.sort(-1)

        if item_list:
            for item in item_list:
                self.insert_row(item, self.row_count())

        self.setup_ui()
        self.show_page(1)

    def setup_ui(self) -> None:
        # link spinbox to show_page
        self.page_spinbox.valueChanged.connect(self.show_page)
        self.prev_button.clicked.connect(self.prev_page)
        self.next_button.clicked.connect(self.next_page)
        self.search_edit.textChanged.connect(self.update_table)
        self.update_table()

    def next_page(self, *args, **kwargs) -> None:
        self.page_spinbox.stepUp()

    def prev_page(self, *args, **kwargs) -> None:
        self.page_spinbox.stepDown()

    def update_table(self) -> None:
        # Update search
        self.proxy_model.search_regexp.setPattern(self.search_edit.text())
        self.proxy_model.invalidateFilter()
        # post-model refresh setup
        for i in range(self.proxy_model.rowCount()):
            index = self.proxy_model.index(i, 0)
            # Delegates normally only open editor if requested.  Request all
            # visible delegates by default
            self.table_view.openPersistentEditor(index)

            widget = self.table_view.indexWidget(index)
            if widget:
                self.table_view.setRowHeight(i, widget.sizeHint().height())
        # reset total pages
        self.page_count_label.setText(f'/ {self.proxy_model.total_pages}')
        self.page_spinbox.setMaximum(self.proxy_model.total_pages)
        if self.proxy_model.total_pages > 0:
            self.page_spinbox.setMinimum(1)

    def show_page(self, page_no: int):
        # set page 0 to clear history effects
        # I don't like how this is, but apparently filterAcceptsRow gets called
        # first on the rows that were already showing.  Because the proxy model
        # accepts the first (page_size) valid rows, this breaks the sorting
        # So, we need to clear this history
        self.proxy_model.curr_page = 0
        self.proxy_model.invalidateFilter()  # This shouldn't take long, not drawing

        # set proper page for filter model
        self.proxy_model.curr_page = page_no
        self.update_table()

    def set_page(self, page_no: int) -> None:
        """External facing method"""
        self.page_spinbox.setValue(page_no)

    def show_row_for_data(self, data: Any) -> None:
        orig_page = self.proxy_model.curr_page

        for page_num in range(self.proxy_model.total_pages):
            # reset filter without enabling persistent delegates
            self.proxy_model.curr_page = 0
            self.proxy_model.invalidateFilter()
            # set proper page for filter model
            self.proxy_model.curr_page = page_num + 1
            self.proxy_model.invalidateFilter()

            for row in range(self.proxy_model.rowCount()):
                if self.proxy_model.index(row, 0).data(USER_DATA_ROLE) is data:
                    self.set_page(page_num + 1)
                    return

        # current filters hide ``data``, return to original page
        self.set_page(orig_page)

    def refresh(self) -> None:
        self.show_page(self.proxy_model.curr_page)

    def insert_row(self, data: Any, index: int) -> None:
        # add item to model
        # if widget: self.table_widget.{insertRow -> setRowHeight -> setCellWidget}
        item = QtGui.QStandardItem()
        item.setData(data)  # Qt.UserRole + 1
        item.setData(data.name or '', role=Qt.ToolTipRole)
        self.source_model.insertRow(index, item)

    def insert_setup_row(self, index: int, data: Any, setup_slot: Callable) -> None:
        item = QtGui.QStandardItem()
        item.setData(data, role=USER_DATA_ROLE)  # Qt.UserRole + 1
        item.setData(data.name or '', role=Qt.ToolTipRole)
        item.setData(setup_slot, role=SETUP_SLOT_ROLE)
        self.source_model.insertRow(index, item)
        self.update_table()

    def find_data_index(self, data: Any, role: int = USER_DATA_ROLE) -> QModelIndex:
        """Return index for ``data`` at ``role`` in source model"""
        for row_num in range(self.source_model.rowCount()):
            row_index = self.source_model.index(row_num, 0)
            if row_index.data(role) is data:
                return row_index

    def remove_data(self, data: Any) -> None:
        """Removes ``data`` from source model"""
        index = self.find_data_index(data)
        self.source_model.removeRow(index.row())

    def replace_data(
        self,
        old_data: Any,
        new_data: Any,
        search_role: int = USER_DATA_ROLE,
        repl_role: int = USER_DATA_ROLE
    ) -> None:
        index = self.find_data_index(old_data, search_role)
        item = self.source_model.itemFromIndex(index)
        item.setData(new_data, repl_role)

    def row_count(self) -> int:
        # return total number of rows
        return self.source_model.rowCount()

    def row_data(self, index: int, role: int = USER_DATA_ROLE) -> Any:
        return self.source_model.index(index, 0).data(role)

    def resizeEvent(self, a0: QtGui.QResizeEvent) -> None:
        super().resizeEvent(a0)
        table_height = self.table_view.size().height()
        index = self.proxy_model.index(0, 0)
        row_widget = self.table_view.indexWidget(index)

        if not row_widget:
            return
        row_height = row_widget.sizeHint().height()
        num_rows = table_height // row_height
        self.proxy_model.page_size = num_rows
        self.refresh()
        return


class PagedProxyModel(QtCore.QSortFilterProxyModel):
    def __init__(self, *args, page_size=3, max_page_size=50, **kwargs):
        super().__init__(*args, **kwargs)
        self.page_size = page_size
        self.max_page_size = max_page_size
        self.curr_page = 1
        self.search_regexp = QtCore.QRegularExpression()

        self.total_pages = 0

        self.total_displayed = 0
        self.total_allowed = 0
        self.min_count = self.curr_page * self.page_size
        self.max_count = self.min_count + self.page_size

    def invalidateFilter(self) -> None:
        self.page_size = min(self.page_size, self.max_page_size)
        self.total_pages = 0

        self.total_displayed = 0
        self.total_allowed = 0

        self.min_count = (self.curr_page - 1) * self.page_size
        self.max_count = self.min_count + self.page_size

        return super().invalidateFilter()

    def process_row(
        self,
        accepted: bool,
        allowed: bool,
        reason: Optional[str] = None
    ) -> bool:
        """
        Process the row, and remember how many rows passed.
        Rows can be valid but not shown, reducing the page count.

        accept: row is accepted, will be displayed
        allowed: row passes filter, will be considered in page count
        reason: reason for decision
        """
        if accepted and not allowed:
            self.total_displayed += 1

        if allowed:
            self.total_allowed += 1
            self.total_pages = math.ceil(
                self.total_allowed / self.page_size
            )
        return accepted

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        source = self.sourceModel()
        index = source.index(source_row, self.filterKeyColumn(), source_parent)
        # TODO: Include basic search text (for name field?)
        inside_page_range = ((self.total_allowed >= self.min_count)
                             and (self.total_allowed < self.max_count))
        row_text = source.data(index, Qt.ToolTipRole)
        text_match = self.search_regexp.match(row_text).hasMatch()

        if not source.data(index, Qt.UserRole+1):
            return self.process_row(False, False, reason='No Data, ignoring row')
        elif not text_match:
            return self.process_row(False, False, reason="Text match failed")
        elif (self.total_displayed >= self.page_size):
            return self.process_row(False, True, reason='Outside page range')
        elif inside_page_range and text_match:
            return self.process_row(True, True, reason="Inside Page Range, text match")

        return self.process_row(False, True, reason="Default")


class CustDelegate(QtWidgets.QStyledItemDelegate):
    # An edit-mode delegate
    def __init__(self, *args, widget_cls=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.widget_cls = widget_cls

    def createEditor(
        self,
        parent: QtWidgets.QWidget,
        option,
        index: QtCore.QModelIndex
    ) -> QtWidgets.QWidget:
        row_widget = self.widget_cls(index.data(USER_DATA_ROLE), parent=parent)
        setup_slot = index.data(SETUP_SLOT_ROLE)
        if callable(setup_slot):
            setup_slot(row_widget)
        return row_widget
