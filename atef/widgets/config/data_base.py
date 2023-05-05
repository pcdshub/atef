"""
Widgets used for manipulating the configuration data.
"""
from __future__ import annotations

import logging
from typing import ClassVar, List, Tuple
from weakref import WeakValueDictionary

from qtpy.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
                            QMessageBox, QPlainTextEdit, QStyle, QToolButton,
                            QVBoxLayout, QWidget)

from atef.config import Configuration, ToolConfiguration
from atef.qt_helpers import QDataclassBridge, QDataclassList
from atef.type_hints import AnyDataclass
from atef.widgets.archive_viewer import get_archive_viewer
from atef.widgets.core import DesignerDisplay
from atef.widgets.utils import FrameOnEditFilter, match_line_edit_text_width

from .utils import get_relevant_pvs

logger = logging.getLogger(__name__)


class DataWidget(QWidget):
    """
    Base class for widgets that manipulate dataclasses.

    Defines the init args for all data widgets and handles synchronization
    of the ``QDataclassBridge`` instances. This is done so that only data
    widgets need to consider how to handle bridges and the page classes
    simply need to pass in data structures, rather than needing to keep track
    of how two widgets editing the same data structure must share the same
    bridge object.

    Parameters
    ----------
    data : any dataclass
        The dataclass that the widget needs to manipulate. Most widgets are
        expecting either specific dataclasses or dataclasses that have
        specific matching fields.
    kwargs : QWidget kwargs
        Passed directly to QWidget's __init__. Likely unused in most cases.
        Even parent is unlikely to see use because parent is set automatically
        when a widget is inserted into a layout.
    """
    _bridge_cache: ClassVar[
        WeakValueDictionary[int, QDataclassBridge]
    ] = WeakValueDictionary()
    bridge: QDataclassBridge
    data: AnyDataclass

    def __init__(self, data: AnyDataclass, **kwargs):
        super().__init__(**kwargs)
        self.data = data
        try:
            # TODO figure out better way to cache these
            # TODO worried about strange deallocation timing race conditions
            self.bridge = self._bridge_cache[id(data)]
        except KeyError:
            bridge = QDataclassBridge(data)
            self._bridge_cache[id(data)] = bridge
            self.bridge = bridge


class NameMixin:
    """
    Mixin class for distributing init_name
    """
    def init_name(self) -> None:
        """
        Set up the name_edit widget appropriately.
        """
        # Load starting text
        load_name = self.bridge.name.get() or ''
        self.last_name = load_name
        self.name_edit.setText(load_name)
        # Set up the saving/loading
        self.name_edit.textEdited.connect(self.update_saved_name)
        self.bridge.name.changed_value.connect(self.apply_new_name)

    def update_saved_name(self, name: str) -> None:
        """
        When the user edits the name, write to the config.
        """
        self.last_name = self.name_edit.text()
        self.bridge.name.put(name)

    def apply_new_name(self, text: str) -> None:
        """
        If the text changed in the data, update the widget.

        Only run if needed to avoid annoyance with cursor repositioning.
        """
        if text != self.last_name:
            self.name_edit.setText(text)


class NameDescTagsWidget(DesignerDisplay, NameMixin, DataWidget):
    """
    Widget for displaying and editing the name, description, and tags fields.

    Any of these will be automatically disabled if the data source is missing
    the corresponding field.

    As a convenience, this widget also holds a parent_button in a convenient
    place for page layouts, since it is expected that this will be near the
    top of the page, and an "extra_text_label" QLabel for general use.
    """
    filename = 'name_desc_tags_widget.ui'

    name_edit: QLineEdit
    name_frame: QFrame
    desc_edit: QPlainTextEdit
    desc_frame: QFrame
    tags_content: QVBoxLayout
    add_tag_button: QToolButton
    tags_frame: QFrame
    parent_button: QToolButton
    action_button: QToolButton
    extra_text_label: QLabel

    last_name: str
    last_desc: str
    pvs: List[Tuple[str, str]]  # (pv, attrname)

    def __init__(self, data: AnyDataclass, **kwargs):
        super().__init__(data=data, **kwargs)
        try:
            self.bridge.name
        except AttributeError:
            self.name_frame.hide()
        else:
            self.init_name()
        try:
            self.bridge.description
        except AttributeError:
            self.desc_frame.hide()
        else:
            self.init_desc()
        try:
            self.bridge.tags
        except AttributeError:
            self.tags_frame.hide()
        else:
            self.init_tags()

        # if there's a pv, show the button for archive widget.
        # info would be filled in after init... don't show at start
        self.action_button.hide()
        self._viewer_initialized = False

    def init_desc(self) -> None:
        """
        Set up the desc_edit widget appropriately.
        """
        # Load starting text
        load_desc = self.bridge.description.get() or ''
        self.last_desc = load_desc
        self.desc_edit.setPlainText(load_desc)
        # Setup the saving/loading
        self.desc_edit.textChanged.connect(self.update_saved_desc)
        self.bridge.description.changed_value.connect(self.apply_new_desc)
        self.desc_edit.textChanged.connect(self.update_text_height)

    def update_saved_desc(self) -> None:
        """
        When the user edits the desc, write to the config.
        """
        self.last_desc = self.desc_edit.toPlainText()
        self.bridge.description.put(self.last_desc)

    def apply_new_desc(self, desc: str) -> None:
        """
        When some other widget updates the description, update it here.
        """
        if desc != self.last_desc:
            self.desc_edit.setPlainText(desc)

    def showEvent(self, *args, **kwargs) -> None:
        """
        Override showEvent to update the desc height when we are shown.
        """
        try:
            self.update_text_height()
        except AttributeError:
            pass
        return super().showEvent(*args, **kwargs)

    def resizeEvent(self, *args, **kwargs) -> None:
        """
        Override resizeEvent to update the desc height when we resize.
        """
        try:
            self.update_text_height()
        except AttributeError:
            pass
        return super().resizeEvent(*args, **kwargs)

    def update_text_height(self) -> None:
        """
        When the user edits the desc, make the text box the correct height.
        """
        line_count = max(self.desc_edit.document().size().toSize().height(), 1)
        self.desc_edit.setFixedHeight(line_count * 13 + 12)

    def init_tags(self) -> None:
        """
        Set up the various tags widgets appropriately.
        """
        tags_list = TagsWidget(
            data_list=self.bridge.tags,
            layout=QHBoxLayout(),
        )
        self.tags_content.addWidget(tags_list)

        def add_tag() -> None:
            if tags_list.widgets and not tags_list.widgets[-1].line_edit.text().strip():
                # Don't add another tag if we haven't filled out the last one
                return

            elem = tags_list.add_item('')
            elem.line_edit.setFocus()

        self.add_tag_button.clicked.connect(add_tag)

    def init_viewer(self, attr: str, config: Configuration) -> None:
        """ Set up the archive viewer button """
        if self._viewer_initialized:
            # make sure this only happens once per instance
            return

        if ((hasattr(config, 'by_attr') or hasattr(config, 'by_pv'))
                and not isinstance(config, ToolConfiguration)):
            icon = self.style().standardIcon(QStyle.SP_FileDialogContentsView)
            self.action_button.setIcon(icon)
            self.action_button.setToolTip('Open Archive Viewer with '
                                          'relevant signals')
            self.action_button.show()

        def open_arch_viewer(*args, **kwargs):
            # only query PV info once requested.  grabbing devices and
            # their relevant PVs can be time consuming
            pv_list = get_relevant_pvs(attr, config)
            if len(pv_list) == 0:
                QMessageBox.information(
                    self,
                    'No Archived PVs',
                    'No valid PVs found to plot with archive viewer. '
                    'Signal may be a derived signal'
                )
                self.action_button.hide()
                return
            widget = get_archive_viewer()
            for pv, dev_attr in pv_list:
                widget.add_signal(pv, dev_attr=dev_attr, update_curves=False)
            widget.update_curves()
            widget.show()

        self.action_button.clicked.connect(open_arch_viewer)
        self._viewer_initialized = True


class TagsWidget(QWidget):
    """
    A widget used to edit a QDataclassList tags field.

    Aims to emulate the look and feel of typical tags fields
    in online applications.

    Parameters
    ----------
    data_list : QDataclassList
        The dataclass list to edit using this widget.
    layout : QLayout
        The layout to use to arrange our labels. This should be an
        instantiated but not placed layout. This lets us have some
        flexibility in whether we arrange things horizontally,
        vertically, etc.
    """
    widgets: List[TagsElem]

    def __init__(
        self,
        data_list: QDataclassList,
        layout: QLayout,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.data_list = data_list
        self.setLayout(layout)
        self.widgets = []
        starting_list = data_list.get()
        if starting_list is not None:
            for starting_value in starting_list:
                self.add_item(starting_value, init=True)

    def add_item(
        self,
        starting_value: str,
        init: bool = False,
        **kwargs,
    ) -> TagsElem:
        """
        Create and add new editable widget element to this widget's layout.

        This can either be an existing string on the dataclass list to keep
        track of, or it can be used to add a new string to the dataclass list.

        This method will also set up the signals and slots for the new widget.

        Parameters
        ----------
        starting_value : str
            The starting text value for the new widget element.
            This should match the text exactly for tracking existing
            strings.
        checked : bool, optional
            This argument is unused, but it will be sent by various button
            widgets via the "clicked" signal so it must be present.
        init : bool, optional
            Whether or not this is the initial initialization of this widget.
            This will be set to True in __init__ so that we don't mutate
            the underlying dataclass. False, the default, means that we're
            adding a new string to the dataclass, which means we should
            definitely append it.
        **kwargs : from qt signals
            Other kwargs sent along with qt signals will be ignored.

        Returns
        -------
        strlistelem : StrListElem
            The widget created by this function call.
        """
        new_widget = TagsElem(starting_value, self)
        self.widgets.append(new_widget)
        if not init:
            self.data_list.append(starting_value)
        self.layout().addWidget(new_widget)
        return new_widget

    def save_item_update(self, item: TagsElem, new_value: str) -> None:
        """
        Update the dataclass as appropriate when the user submits a new value.

        Parameters
        ----------
        item : StrListElem
            The widget that the user has edited.
        new_value : str
            The value that the user has submitted.
        """
        index = self.widgets.index(item)
        self.data_list.put_to_index(index, new_value)

    def remove_item(self, item: TagsElem) -> None:
        """
        Update the dataclass as appropriate when the user removes a value.

        Parameters
        ----------
        item : StrListElem
            The widget that the user has clicked the delete button for.
        """
        index = self.widgets.index(item)
        self.widgets.remove(item)
        self.data_list.remove_index(index)
        item.deleteLater()


class TagsElem(DesignerDisplay, QWidget):
    """
    A single element for the TagsWidget.

    Has a QLineEdit for changing the text and a delete button.
    Changes its style to no frame when it has text and is out of focus.
    Only shows the delete button when the text is empty.

    Parameters
    ----------
    start_text : str
        The starting text for this tag.
    tags_widget : TagsWidget
        A reference to the TagsWidget that contains this widget.
    """
    filename = 'tags_elem.ui'

    line_edit: QLineEdit
    del_button: QToolButton

    def __init__(self, start_text: str, tags_widget: TagsWidget, **kwargs):
        super().__init__(**kwargs)
        self.line_edit.setText(start_text)
        self.tags_widget = tags_widget
        edit_filter = FrameOnEditFilter(parent=self)
        edit_filter.set_no_edit_style(self.line_edit)
        self.line_edit.installEventFilter(edit_filter)
        self.on_text_changed(start_text)
        self.line_edit.textChanged.connect(self.on_text_changed)
        self.line_edit.textEdited.connect(self.on_text_edited)
        self.del_button.clicked.connect(self.on_del_clicked)
        icon = self.style().standardIcon(QStyle.SP_TitleBarCloseButton)
        self.del_button.setIcon(icon)

    def on_text_changed(self, text: str) -> None:
        """
        Edit our various visual elements when the text changes.

        This will do all of the following:
        - make the delete button show only when the text field is empty
        - adjust the size of the text field to be roughly the size of the
          string we've inputted
        """
        # Show or hide the del button as needed
        self.del_button.setVisible(not text)
        # Adjust the width to match the text
        match_line_edit_text_width(self.line_edit, text=text)

    def on_data_changed(self, data: str) -> None:
        """
        Change the text displayed here using new data, if needed.
        """
        if self.line_edit.text() != data:
            self.line_edit.setText(data)

    def on_text_edited(self, text: str) -> None:
        """
        Update the dataclass when the user edits the text.
        """
        self.tags_widget.save_item_update(
            item=self,
            new_value=text,
        )

    def on_del_clicked(self, **kwargs) -> None:
        """
        Tell the QTagsWidget when our delete button is clicked.
        """
        self.tags_widget.remove_item(self)


class SimpleRowWidget(NameMixin, DataWidget):
    """
    Common behavior for these simple rows included on the various pages.
    """
    name_edit: QLineEdit
    child_button: QToolButton
    delete_button: QToolButton

    def setup_row(self) -> None:
        """
        Make the commonalities in simple row widgets functional.
        """
        self.init_name()
        self.edit_filter = FrameOnEditFilter(parent=self)
        self.name_edit.installEventFilter(self.edit_filter)
        self.name_edit.textChanged.connect(self.on_name_edit_text_changed)
        self.on_name_edit_text_changed()

    def adjust_edit_filter(self) -> None:
        """
        Toggle between edit/no edit style modes based on having a valid name.
        """
        if self.bridge.name.get():
            self.edit_filter.set_no_edit_style(self.name_edit)
        else:
            self.edit_filter.set_edit_style(self.name_edit)

    def on_name_edit_text_changed(self, **kwargs) -> None:
        """
        Updates the style of our name edit appropriately on text change.
        """
        match_line_edit_text_width(self.name_edit)
        if not self.name_edit.hasFocus():
            self.adjust_edit_filter()
