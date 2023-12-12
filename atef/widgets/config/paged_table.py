import logging
import math
from typing import Any, Callable, List, Optional

from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import QModelIndex, Qt

from atef.widgets.config.data_passive import ComparisonRowWidget
from atef.widgets.core import DesignerDisplay

logger = logging.getLogger(__name__)

USER_DATA_ROLE = Qt.UserRole + 1
SETUP_SLOT_ROLE = Qt.UserRole + 2


class PagedTableWidget(DesignerDisplay, QtWidgets.QWidget):
    """
    A table widget that separates its contents into pages, and allows seaching
    by text to filter those rows further.

    Major components include:
    - source model: contains all data, sort-filter-proxy model, and table view.

    PagedTableWidget is designed to custom row widgets for each item saved to
    the source model.  These widgets are expected to take the stored data as an
    init argument. Any setup to be performed after that widget is created
    (connections to signals, slots, etc), must by packaged into a function that
    can be stored alongside the data.

    Example setup::

        class MyWidget(QWidget):
            def __init__(self, *args, data_items: List[AnyDataclass], **kwargs):
                self.table = PagedTableWidget()

                for i, data in enumerate(data_items):
                    self.table.insert_setup_row(
                        i,
                        data,
                        self.setup_row_widget
                    )

            def setup_row_widget(self, widget):
                widget.button.clicked.connect(lambda *args, **kwargs: print('hi'))

    """
    table_view: QtWidgets.QTableView
    page_spinbox: QtWidgets.QSpinBox
    next_button: QtWidgets.QToolButton
    prev_button: QtWidgets.QToolButton
    search_edit: QtWidgets.QLineEdit
    page_count_label: QtWidgets.QLabel

    filename = 'paged_table.ui'

    def __init__(
        self,
        *args,
        title: Optional[str] = None,
        item_list: Optional[List[Any]] = None,
        page_size: Optional[int] = None,
        widget_cls: Optional[QtWidgets.QWidget] = ComparisonRowWidget,
        **kwargs
    ):
        """
        Parameters
        ----------
        title : Optional[str], optional
            title to be used as a header label, by default None
        item_list : Optional[List[Any]], optional
            a list of items to add to this table, by default None
        page_size : Optional[int], optional
            number of rows to show per page, by default None
        widget_cls : Optional[QtWidgets.QWidget], optional
            a widget class to create via delegates, by default ComparisonRowWidget
        """
        super().__init__(*args, **kwargs)
        self.page_size = page_size
        self.row_cls = widget_cls

        self.source_model = QtGui.QStandardItemModel(0, 1, parent=self)
        self.proxy_model = PagedProxyModel(self)
        self.proxy_model.setSourceModel(self.source_model)
        self.table_view.setModel(self.proxy_model)
        if page_size is not None:
            self.proxy_model.page_size = self.page_size

        if widget_cls:
            self.row_delegate = CustDelegate(widget_cls=self.row_cls)
            self.table_view.setItemDelegateForColumn(0, self.row_delegate)

        self.set_title(title)
        self.table_view.horizontalHeader().setStretchLastSection(True)
        self.table_view.verticalHeader().hide()
        self.proxy_model.sort(-1)

        if item_list:
            for item in item_list:
                self.insert_row(self.row_count(), item)

        self.setup_ui()
        self.show_page(1)

    def setup_ui(self) -> None:
        """Connect slots to callbacks"""
        self.page_spinbox.valueChanged.connect(self.show_page)
        self.prev_button.clicked.connect(self.prev_page)
        self.next_button.clicked.connect(self.next_page)
        self.search_edit.textChanged.connect(self.update_table)
        self.update_table()

    def set_title(self, title: Optional[str] = None) -> None:
        if title is not None:
            self.source_model.setHeaderData(0, Qt.Horizontal, title, Qt.DisplayRole)
        else:
            self.table_view.horizontalHeader().hide()

    def next_page(self, *args, **kwargs) -> None:
        """Navigate to the next page, constrained by limits of the spinbox"""
        self.page_spinbox.stepUp()

    def prev_page(self, *args, **kwargs) -> None:
        """Navigate to the previous page, constrained by limits of the spinbox"""
        self.page_spinbox.stepDown()

    def update_table(self) -> None:
        """
        Update the proxy model with filter information, re-filter the related
        rows, and enable the delegates for visible rows
        """
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
        """
        Show page #``page_no``.  For use as slot in QSpinBox.valueChanged

        Parameters
        ----------
        page_no : int
            page number to show
        """
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
        """
        External facing method for changing the page, constrained by QSpinBox

        Parameters
        ----------
        page_no : int
            page number to show
        """
        orig_page = self.proxy_model.curr_page
        self.page_spinbox.setValue(page_no)
        if page_no == orig_page:
            self.refresh()

    def show_row_for_data(self, data: Any, role: int = USER_DATA_ROLE) -> None:
        """
        Modify the page to show the row containing ``data``.  If data is hidden
        by the filters

        Parameters
        ----------
        data : Any
            data contained by the displayed row
        role : int, optional
            data-role to look in, by default USER_DATA_ROLE
        """
        orig_page = self.proxy_model.curr_page

        for page_num in range(self.proxy_model.total_pages):
            # reset filter without enabling persistent delegates
            self.proxy_model.curr_page = 0
            self.proxy_model.invalidateFilter()
            # set proper page for filter model
            self.proxy_model.curr_page = page_num + 1
            self.proxy_model.invalidateFilter()

            for row in range(self.proxy_model.rowCount()):
                if self.proxy_model.index(row, 0).data(role) is data:
                    self.set_page(page_num + 1)
                    return

        # current filters hide ``data``, return to original page
        self.set_page(orig_page)

    def refresh(self) -> None:
        """Refresh the widget.  (re-applies filters, returning to current page)"""
        self.show_page(self.proxy_model.curr_page)

    def insert_row(
        self,
        index: int,
        data: Any,
        setup_slot: Optional[Callable[[QtWidgets.QWidget], None]] = None,
        update: bool = False
    ) -> None:
        """
        Add ``data`` to the table's model.
        - ``data`` is stored in ``USER_DATA_ROLE``,
        - ``data.name`` is stored in ``Qt.ToolTipRole`` for searching if available
        - ``setup_slot`` is stored in ``SETUP_SLOT_ROLE`` if provided

        Parameters
        ----------
        index : int
            index to insert data at
        data : Any
            data to be added
        setup_slot : Optional[Callable[[QtWidgets.QWidget], None]]
            a function used to setup the row widget delegate after creation
        """
        logger.debug(f'inserting row ({data} @ {index}), update: {update}')
        item = QtGui.QStandardItem()
        item.setData(data, role=USER_DATA_ROLE)
        item.setData(data.name or '', role=Qt.ToolTipRole)
        if setup_slot is not None:
            item.setData(setup_slot, role=SETUP_SLOT_ROLE)
        self.source_model.insertRow(index, item)
        if update:
            self.update_table()

    def find_data_index(self, data: Any, role: int = USER_DATA_ROLE) -> QModelIndex:
        """
        Return a QModelIndex for ``data`` at ``role`` in source model

        Parameters
        ----------
        data : Any
            data to search for
        role : int, optional
            role to match ``data`` in, by default USER_DATA_ROLE

        Returns
        -------
        QModelIndex
            index of the source model containing ``data`` at ``role``
        """
        for row_num in range(self.source_model.rowCount()):
            row_index = self.source_model.index(row_num, 0)
            if row_index.data(role) is data:
                return row_index

    def remove_data(self, data: Any) -> None:
        """
        Removes ``data`` from source model

        Parameters
        ----------
        data : Any
            data to remove from the source model
        """
        index = self.find_data_index(data)
        self.source_model.removeRow(index.row())

    def replace_data(
        self,
        old_data: Any,
        new_data: Any,
        search_role: int = USER_DATA_ROLE,
        repl_role: int = USER_DATA_ROLE
    ) -> None:
        """
        Search for ``old_data`` in ``search_role``, then replace the data at
        ``repl_role`` with ``new_data``

        This means you can search for a dataclass and replace its setup slot
        or tooltip, for example.

        Parameters
        ----------
        old_data : Any
            data to search for
        new_data : Any
            data to replace once the correct index is found
        search_role : int, optional
            role to search for ``old_data`` in, by default USER_DATA_ROLE
        repl_role : int, optional
            role to replace with ``new_data``, by default USER_DATA_ROLE
        """
        index = self.find_data_index(old_data, search_role)
        item = self.source_model.itemFromIndex(index)
        item.setData(new_data, repl_role)

    def row_count(self) -> int:
        """Return total number of rows"""
        return self.source_model.rowCount()

    def row_data(self, index: int, role: int = USER_DATA_ROLE) -> Any:
        """
        Return the data at row number ``index`` for ``role``.
        References the source model, meaning all rows are available

        Parameters
        ----------
        index : int
            row number to grab data from
        role : int, optional
            role to retrieve data from, by default USER_DATA_ROLE

        Returns
        -------
        Any
            requested data at row ``index`` under ``role``
        """
        return self.source_model.index(index, 0).data(role)

    def resizeEvent(self, a0: QtGui.QResizeEvent) -> None:
        """Dynamically set the page size when the table is resized"""
        super().resizeEvent(a0)
        if self.page_size is not None:
            return
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

    def showEvent(self, a0: QtGui.QShowEvent) -> None:
        """Refresh table whenever revealed"""
        super().showEvent(a0)
        self.refresh()


class PagedProxyModel(QtCore.QSortFilterProxyModel):
    """
    A QSortFilterProxyModel that filters based on search text and the set page
    size.  Page size determines the number of rows per page.
    """
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
        """Reset count variables and set the count range for the current page"""
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

        Parameters
        ----------
        accepted : bool
            row is accepted, will be displayed
        allowed : bool
            row has passed filter criteria, but is not necessarily displayed
        reason : Optional[str], optional
            reason for the decision, for logging purposes, by default None

        Returns
        -------
        bool
            ``accepted``, whether or not the row will be displayed
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
        """
        Assess whether the row at ``source_row`` will be displayed

        Parameters
        ----------
        source_row : int
            row in the source model
        source_parent : QModelIndex
            parent index.  Unused here, used in TreeViews

        Returns
        -------
        bool
            whether the row will be displayed
        """
        source = self.sourceModel()
        index = source.index(source_row, self.filterKeyColumn(), source_parent)

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
    """A Custom delegate that creates ``widget_cls`` for each row"""
    def __init__(self, *args, widget_cls=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.widget_cls = widget_cls

    def createEditor(
        self,
        parent: QtWidgets.QWidget,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex
    ) -> QtWidgets.QWidget:
        """
        Create the row widget and set it up using the setup slot

        Parameters
        ----------
        parent : QtWidgets.QWidget
            the parent of the widget
        option : QtWidgets.QStyleOptionViewItem
            Option to determine how widget appears
        index : QtCore.QModelIndex
            Index from the model to show

        Returns
        -------
        QtWidgets.QWidget
            the requested widget
        """
        if self.widget_cls is None:
            return QtWidgets.QLabel('no widget class found', parent=parent)
        row_widget = self.widget_cls(index.data(USER_DATA_ROLE), parent=parent)
        setup_slot = index.data(SETUP_SLOT_ROLE)
        if callable(setup_slot):
            setup_slot(row_widget)
        return row_widget
