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

    # TODO mimic QTableWidget methods used

    def __init__(
        self,
        *args,
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

        self.source_model = QtGui.QStandardItemModel(1, 1, parent=self)
        self.proxy_model = PagedProxyModel(self)
        self.proxy_model.setSourceModel(self.source_model)
        self.table_view.setModel(self.proxy_model)

        self.row_delegate = CustDelegate(widget_cls=self.row_cls)
        self.table_view.setItemDelegateForColumn(0, self.row_delegate)
        self.table_view.horizontalHeader().setStretchLastSection(True)
        self.table_view.setSortingEnabled(True)
        self.proxy_model.sort(-1)

        if item_list:
            for item in item_list:
                self.insertRow(item, self.rowCount())

        self.show_page(1)

        self.setup_ui()

    def setup_ui(self) -> None:
        # link spinbox to show_page
        self.page_spinbox.valueChanged.connect(self.show_page)
        self.prev_button.clicked.connect(self.prev_page)
        self.next_button.clicked.connect(self.next_page)
        self.search_edit.editingFinished.connect(self.update_table)
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

    def insertRow(self, data: Any, index: int) -> None:
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
        # TODO: Figure out how to ensure

    def remove_data(self, data: Any) -> None:
        """Removes ``data`` from source model"""
        for row_num in range(self.source_model.rowCount()):
            row_index = self.source_model.index(row_num, 0)
            if row_index.data(USER_DATA_ROLE) is data:
                self.source_model.removeRow(row_num)
                return

    def get_widget_for_data(self, data: Any) -> Optional[Any]:
        """Return widget for with supplied data"""
        # Delegate creates and destroys widgets, no bueno
        raise NotImplementedError

    def cellWidget(self, row: int, column: int) -> None:
        # not necessary probably
        pass

    def rowCount(self) -> int:
        # return total number of rows
        return self.source_model.rowCount()

    def selectedIndexes(self) -> QtCore.QModelIndex:
        pass

    def indexAt(self) -> QtCore.QModelIndex:
        pass


class PagedProxyModel(QtCore.QSortFilterProxyModel):
    def __init__(self, *args, page_size=3, **kwargs):
        super().__init__(*args, **kwargs)
        self.page_size = page_size
        self.curr_page = 1
        self.search_regexp = QtCore.QRegularExpression()

        self.total_pages = 0

        self.total_displayed = 0
        self.total_allowed = 0
        self.min_count = self.curr_page * self.page_size
        self.max_count = self.min_count + self.page_size

    def invalidateFilter(self) -> None:
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
        print(f'pr: ({accepted, allowed, reason})')
        if accepted and not allowed:
            self.total_displayed += 1

        if allowed:
            self.total_allowed += 1
            self.total_pages = math.ceil(
                self.total_allowed / self.page_size
            )
        return accepted

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        print(f'far: {source_row}')
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
