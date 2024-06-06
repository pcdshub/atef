"""
Widgets used for navigating the tree.

Page widgets should include data widgets inside of their
layouts, using bare ``QWidget`` instances as placeholders
when defined in a ui file.

They may have buttons for adding data row widgets and for
adding nodes to the tree. They are responsible for hooking up
navigation, deletion, and rearrangement controls.

Typically they will be instantiated using dataclasses that
will then be distributed to the data widgets. After instantiation
a page widget will need to be linked up to the tree using the
``link_page`` helper function.
"""
from __future__ import annotations

import dataclasses
import logging
from collections import OrderedDict
from functools import partial
from typing import (TYPE_CHECKING, Any, ClassVar, Dict, List, Optional, Tuple,
                    Type, Union)
from weakref import WeakValueDictionary

from pcdsutils.qt.callbacks import WeakPartialMethodSlot
from qtpy.QtGui import QDropEvent
from qtpy.QtWidgets import (QComboBox, QFrame, QLabel, QMessageBox,
                            QPushButton, QSizePolicy, QStyle, QTableWidget,
                            QToolButton, QTreeWidgetItem, QVBoxLayout, QWidget)

from atef.check import (ALL_COMPARISONS, AnyComparison, AnyValue, Comparison,
                        Equals, Greater, GreaterOrEqual, Less, LessOrEqual,
                        NotEquals, Range, ValueSet)
from atef.config import (Configuration, ConfigurationFile, ConfigurationGroup,
                         DeviceConfiguration, PreparedConfiguration,
                         PreparedGroup, PreparedTemplateConfiguration,
                         PVConfiguration, TemplateConfiguration,
                         ToolConfiguration)
from atef.procedure import (ComparisonToTarget, DescriptionStep, PassiveStep,
                            PreparedDescriptionStep, PreparedPassiveStep,
                            PreparedProcedureStep, PreparedSetValueStep,
                            PreparedTemplateStep, ProcedureFile,
                            ProcedureGroup, ProcedureStep, SetValueStep,
                            TemplateStep)
from atef.tools import Ping, PingResult, Tool, ToolResult
from atef.type_hints import AnyDataclass
from atef.widgets.config.data_active import (CheckRowWidget,
                                             GeneralProcedureWidget,
                                             PassiveEditWidget,
                                             SetValueEditWidget)
from atef.widgets.config.find_replace import FillTemplatePage
from atef.widgets.config.paged_table import SETUP_SLOT_ROLE, PagedTableWidget
from atef.widgets.config.run_active import (DescriptionRunWidget,
                                            PassiveRunWidget,
                                            SetValueRunWidget,
                                            TemplateRunWidget)
from atef.widgets.config.run_base import RunCheck
from atef.widgets.utils import ExpandableFrame, insert_widget

from ..core import DesignerDisplay
from .data_base import DataWidget, NameDescTagsWidget
from .data_passive import (AnyComparisonWidget, AnyValueWidget,
                           ComparisonRowWidget, ConfigurationGroupRowWidget,
                           ConfigurationGroupWidget, DeviceConfigurationWidget,
                           EqualsWidget, GeneralComparisonWidget,
                           GreaterOrEqualWidget, GreaterWidget,
                           LessOrEqualWidget, LessWidget, NotEqualsWidget,
                           PingWidget, PVConfigurationWidget, RangeWidget,
                           ValueSetWidget)
from .utils import (MultiModeValueEdit, TableWidgetWithAddRow, TreeItem,
                    cast_dataclass, describe_comparison_context,
                    describe_step_context, gather_relevant_identifiers,
                    get_comp_field_in_parent)

if TYPE_CHECKING:
    from .window import DualTree


logger = logging.getLogger(__name__)


def setup_multi_mode_edit_widget(
    page: PageWidget,
    target_widget: QWidget,
    value_name: str = "value",
    dynamic_name: str = "value_dynamic",
    specific_widget: str = "specific_comparison_widget"
) -> None:
    """
    Set up a `MultiModeValueEdit`` widget, replacing `target_widget` in
    `page`.`specific_widget`. Hook up the input fields to the `value_name` and
    `dynamic_name` attributes on the QDataclassBridge on `page.tree_item`.

    Parameters
    ----------
    page : PageWidget
        the page widget containing `target_widget` and the identifiers
        (PVs, device/components, etc) needed to specify the comparison.
    target_widget : QWidget
        the placeholder widget in `page`.`specific_widget` to replace.
    value_name : str, optional
        an attribute name corresponding to the static value field on
        `page.tree_item.bridge`, by default "value".
    dynamic_name : str, optional
        an attribute name corresponding to the dynamic value field on
        `page.tree_item.bridge`, by default "value_dynamic".
    specific_widget : str, optional
        an attribute name. child widget of `page` that contains `target_widget`,
        by default "specific_comparison_widget".

    Raises
    ------
    RuntimeError
        if the `page` is not linked to a tree node (:class:`TreeItem`)
    """
    # Find the current node
    curr_parent = page
    node = None
    while curr_parent is not None:
        try:
            node = curr_parent.tree_item
            break
        except AttributeError:
            logger.warning('every widget should have a TreeItem, looking at parent')
            curr_parent = curr_parent.parent()
    if node is None:
        raise RuntimeError(
            "Could not find link to file tree nodes."
        )
    # Travel up the node tree to find the id and devices
    devices = None
    spec_widget = getattr(page, specific_widget)
    comp = spec_widget.data
    # TODO: This is kinda ick, hard coded, will have to change for Active checkouts
    group_node = node.find_ancestor_by_data_type(
        (DeviceConfiguration, PVConfiguration, ToolConfiguration, SetValueStep)
    )
    group = group_node.orig_data

    def gather_ids():
        return gather_relevant_identifiers(comp, group)

    if isinstance(group, DeviceConfiguration):
        devices = group.devices

    value_widget = MultiModeValueEdit(
        bridge=page.tree_item.bridge,
        value_name=value_name,
        dynamic_name=dynamic_name,
        id_fn=gather_ids,
        devices=devices,
        font_pt_size=16,
    )

    if hasattr(page.specific_comparison_widget, 'set_tolerance_visible'):
        value_widget.show_tolerance.connect(
            page.specific_comparison_widget.set_tolerance_visible
        )

    # Finally replace the old placeholder widget with the configured value_widget
    insert_widget(value_widget, target_widget)


def setup_multi_mode_for_widget(page: PageWidget, specific_widget: QWidget) -> None:
    """
    Initializes MultiModeInputWidget for various specific comparison widgets.
    Wraps `setup_multi_mode_edit_widget`, providing widget specific information

    Currently supports: (Equals, NotEquals, GtLtBase, Range) widgets

    Parameters
    ----------
    page : PageWidget
        the page holding the `specific_widget` that needs a
        :class:`MultiModeValueEdit` set up.
    specific_widget : QWidget
        the widget that needs a `MultiModeValueEdit` set up for data entry.
    """
    if isinstance(specific_widget, RangeWidget):
        setup_multi_mode_edit_widget(
            page=page, target_widget=specific_widget.low_widget,
            value_name='low', dynamic_name='low_dynamic'
        )
        setup_multi_mode_edit_widget(
            page=page, target_widget=specific_widget.high_widget,
            value_name='high', dynamic_name='high_dynamic'
        )
        setup_multi_mode_edit_widget(
            page=page, target_widget=specific_widget.warn_low_widget,
            value_name='warn_low', dynamic_name='warn_low_dynamic'
        )
        setup_multi_mode_edit_widget(
            page=page, target_widget=specific_widget.warn_high_widget,
            value_name='warn_high', dynamic_name='warn_high_dynamic'
        )
    elif not isinstance(specific_widget,
                        (ValueSetWidget, AnyValueWidget, AnyComparisonWidget)):
        setup_multi_mode_edit_widget(
            page=page, target_widget=specific_widget.value_widget
        )


class PageWidget(QWidget):
    """
    Base class for widgets that correspond to a tree node.

    Contains utilities for navigating and manipulating
    the tree and for loading data widgets into placeholders.

    Must be linked up to the tree using the ``link_page``
    function after being instantiated, not during.
    """
    # Linkage attributes
    tree_item: TreeItem
    full_tree: DualTree

    # Common placeholder defined in the page ui files
    name_desc_tags_placeholder: QWidget
    name_desc_tags_widget: NameDescTagsWidget

    # Set during runtime
    data: AnyDataclass
    parent_button: Optional[QToolButton]

    def __init__(
        self,
        data: AnyDataclass,
        tree_item: Optional[TreeItem] = None,
        full_tree: Optional[DualTree] = None,
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.data = data
        if tree_item is not None:
            # assign tree item and check
            self.tree_item = tree_item
        else:
            self.tree_item = TreeItem(data=data)
        self.parent_tree_item = self.tree_item.parent()
        self.full_tree = full_tree

        self.parent_button = None
        self.child_button_map = WeakValueDictionary()
        self.has_connected_tree = False
        self.comp_table_setup_done = False
        self._partial_slots = []

    def post_tree_setup(self) -> None:
        """
        Perform setup that requires the full_tree (DualTree).
        Call this at the end of init in subclasses, or if the tree has updated.

        Parameters
        ----------
        item : TreeItem
            The item that should be showing this page.

        tree : DualTree
            Dual tree with treeview and seletion helpers
        """
        # Make sure we update our parent button's tooltip on tree changes
        if not self.has_connected_tree:
            self.full_tree.model.dataChanged.connect(
                self._update_parent_tooltip_from_tree
            )
            self.has_connected_tree = True

    def _update_parent_tooltip_from_tree(
        self,
        item: QTreeWidgetItem,
        **kwargs,
    ) -> None:
        """
        Update the parent tooltip if our parent's name changes.
        """
        if self.parent_button is None:
            return
        if item is self.parent_tree_item:
            self.update_parent_tooltip()

    def update_parent_tooltip(self) -> None:
        """
        Ensure that the to-parent tooltip is updated, accurate, and helpful.
        """
        if self.parent_button is None:
            return
        nav_parent = self.get_nav_parent()
        self.parent_button.setToolTip(
            "Navigate to parent item "
            f"{nav_parent.data(0)} "
            f"({nav_parent.data(2)})"
        )

    def setup_parent_button(self, button: QToolButton) -> None:
        """
        Set up a button's style and make it navigate to our parent page.

        We can only have exactly one parent button at a time.
        """
        # Retain a reference to this button for later
        self.parent_button = button
        # Make the button work
        button.clicked.connect(self.navigate_to_parent)
        # Add the appropriate symbol
        icon = self.style().standardIcon(QStyle.SP_FileDialogToParent)
        button.setIcon(icon)
        # Make sure the button's starting tooltip is correct
        self.update_parent_tooltip()

    def setup_child_button(self, button: QToolButton, item: TreeItem) -> None:
        """
        Set up a button's style and make it navigate to a specific child page.
        """
        navigate_slot = WeakPartialMethodSlot(button, button.clicked,
                                              self.navigate_to, item)
        self._partial_slots.append(navigate_slot)

        # Add the appropriate symbol
        icon = self.style().standardIcon(QStyle.SP_ArrowRight)
        button.setIcon(icon)
        # Make sure the tooltip is helpful
        button.setToolTip(
            f"Navigate to child {item.data(0)}"
        )

    def navigate_to(self, item: TreeItem, *args, **kwargs) -> None:
        """
        Make the tree switch to a specific item.

        This can be used to navigate to child items, for example.

        Parameters
        ----------
        item : TreeItem
            The tree node to navigate to.
        """
        self.full_tree.select_by_item(item)

    def navigate_to_parent(self, *args, **kwargs) -> None:
        """
        Make the tree switch to this widget's parent in the tree.
        """
        self.navigate_to(self.get_nav_parent())

    def get_nav_parent(self) -> TreeItem:
        """
        Get the navigation parent target item.

        This is self.parent_tree_item normally except when we are
        a top-level item, in which case the target should be the
        overview widget because otherwise there isn't any parent
        to navigate to.
        """
        if isinstance(self.parent_tree_item, TreeItem):
            return self.parent_tree_item
        else:
            return None  # self.full_tree.topLevelItem(0)

    def insert_widget(self, widget: QWidget, placeholder: QWidget) -> None:
        """
        Helper function for slotting e.g. data widgets into placeholders.
        """
        if placeholder.layout() is None:
            layout = QVBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            placeholder.setLayout(layout)
        else:
            old_widget = placeholder.layout().takeAt(0).widget()
            if old_widget is not None:
                # old_widget.setParent(None)
                old_widget.deleteLater()
        placeholder.layout().addWidget(widget)

    def setup_delete_row_button(
        self,
        table: QTableWidget,
        item: TreeItem,
        row: DataWidget,
    ) -> None:
        """
        Configure a row's delete button to delete itself.
        """
        delete_icon = self.style().standardIcon(QStyle.SP_TitleBarCloseButton)
        row.delete_button.setIcon(delete_icon)

        delete_slot = WeakPartialMethodSlot(
            row.delete_button, row.delete_button.clicked,
            self.delete_table_row, table=table, row=row, item=item
        )
        self._partial_slots.append(delete_slot)

    def delete_table_row(
        self,
        *args,
        table: PagedTableWidget,
        item: TreeItem,
        row: DataWidget,
        **kwargs
    ) -> None:
        """
        Delete a row from the table and unlink the corresponding item nodes.
        """
        # Confirmation dialog
        reply = QMessageBox.question(
            self,
            'Confirm deletion',
            (
                'Are you sure you want to delete the '
                f'{item.data(2)} named "{item.data(0)}"? '
                'Note that this will delete any child nodes in the tree.'
            ),
        )
        if reply != QMessageBox.Yes:
            return
        # Get the identity of the data
        data = row.bridge.data
        # Remove item from the tree
        with self.full_tree.modifies_tree():
            try:
                self.tree_item.removeChild(item)
            except ValueError:
                pass

        # Remove row from the table
        table.remove_data(row.data)
        # Remove configuration from the data structure
        self.remove_table_data(data)

    def remove_table_data(self, data: Any) -> None:
        """
        Implement in subclass to remove the data after delete_table_row.
        """
        raise NotImplementedError()

    def setup_row_buttons(
        self,
        row_widget: DataWidget,
        item: TreeItem,
        table: QTableWidget,
    ) -> None:
        """
        Make the child navigation and delete buttons work on a table row.

        This is valid for any PageWidget that has a QTableWidget that
        contains row widgets that have "child_button" and "delete_button"
        attributes. It is a shortcut that calls setup_child_button and
        setup_delete_row_button in one go.

        Parameters
        ----------
        row_widget : DataWidget
            The widget that we need to modify. Should have a "child_button"
            and a "delete_button" attribute.
        item : TreeItem
            The item corresponding to the page associated with the row widget.
        table : QTableWidget
            The table that the widget exists in.
        """
        self.setup_child_button(
            button=row_widget.child_button,
            item=item,
        )
        self.setup_delete_row_button(
            table=table,
            item=item,
            row=row_widget,
        )

    def setup_name_desc_tags_init(self) -> None:
        """
        Common init-time setup for the name/desc/tags header widgets.

        Run this after super().__init__ in a PageWidget subclass
        to initialize the header if the page has a name/desc/tags widget.
        """
        widget = NameDescTagsWidget(data=self.data)
        self.parent_button = widget.parent_button
        self.insert_widget(
            widget,
            self.name_desc_tags_placeholder,
        )
        # Assign last for deallocation order concerns when running this twice
        self.name_desc_tags_widget = widget

    def setup_name_desc_tags_link(self) -> None:
        """
        Common link-time setup for the name/desc/tags header widgets.

        Run this after super().post_tree_setup in a PageWidget subclass
        to initialize the header if the page has a name/desc/tags widget.
        """
        self.setup_parent_button(self.name_desc_tags_widget.parent_button)
        # self.connect_tree_node_name(self.name_desc_tags_widget)

    def configure_row_widget(
        self,
        widget: ComparisonRowWidget,
        comparison: Comparison,
        comp_item: TreeItem
    ) -> None:
        """
        Function that finishes initialization of a row widget, connecting buttons,
        adding slots etc.

        Relies on ``update_comparison_attr`` and ``update_combo_attrs`` methods,
        assumes they are implemented

        This is to be held by a PagedTableWidget, but is itself not a qt slot.
        Thus there is no need to WeakPartialMethodSlot it
        """
        self.setup_row_buttons(
            row_widget=widget,
            item=comp_item,
            table=self.comparisons_table,
        )
        attr = get_comp_field_in_parent(comparison, self.data)

        self.update_combo_attrs(widget)
        widget.attr_combo.setCurrentText(attr)

        update_slot = WeakPartialMethodSlot(
            widget.attr_combo, widget.attr_combo.currentTextChanged,
            self.update_comparison_attr, comparison=comparison,
        )
        # TODO: Figure out how to clean these up, delegates should be deleted
        self._partial_slots.append(update_slot)

    def setup_comparison_table_link(
        self,
        by_attr_key: str,
        data_widget: Optional[DataWidget],
    ) -> bool:
        """
        Common link-time setup for the comparison tables.

        This is used for pages that contain comparison instances. All of these
        pages need to:
        - read the starting configuration
        - fill the table widgets appropriately
        - setup the add button
        - make sure the options for comparison target attrs update appropriately
        - make sure the stored data updates when the user manipulates the table

        For this method to work, the page needs the following:
        - Methods named "add_comparison_row", "update_combo_attrs", and
          "update_comparison_dicts" with signatures matching the not
          not implement stub methods here.
        - A button with the "clicked" signal named "add_comparison_button".

        Parameters
        ----------
        by_attr_key : str
            Either 'by_attr' or 'by_pv', the attr we need to use to find the
            data structure and the bridge.
        data_widget : DataWidget or None.
            A widget with a QDataclassBridge named bridge to the underlying
            dataclass. If None, we'll skip some of the setup here.

        Returns
        -------
        did_setup : bool
            True if we ran the setup routines here.
        """
        if not self.comp_table_setup_done:
            for attr, configs in getattr(self.data, by_attr_key).items():
                for config in configs:
                    self.add_comparison_row(
                        attr=attr,
                        comparison=config,
                        update=False
                    )
            for config in self.data.shared:
                self.add_comparison_row(
                    attr='shared',
                    comparison=config,
                    update=False
                )
            # Allow the user to add more rows
            self.add_comparison_button.clicked.connect(self.add_comparison_row)
            if data_widget is not None:
                # When the attrs update, update the allowed attrs in each row
                getattr(data_widget.bridge, by_attr_key).updated.connect(
                    self.update_comparison_dicts
                )
            self.comp_table_setup_done = True
            return True
        return False

    def add_comparison_row(
        self,
        checked: bool = False,
        attr: str = '',
        comparison: Optional[Comparison] = None,
        update: bool = True
    ) -> None:
        """
        Add a new row to the comparison table.

        This also creates a default Equals instance if necessary, creates
        the corresponding page, and does all the related setup.

        Parameters
        ----------
        checked : bool, optional
            Unused. Button "clicked" signals often pass this as the first
            positional argument.
        attr : str, optional
            The signal attr name associated with this comparison.
        config : Comparison, optional
            The comparison to add. If omitted, we'll create a blank
            Equals comparison.
        """
        raise NotImplementedError()

    def update_combo_attrs(self) -> None:
        """
        For every row combobox, set the allowed values.
        """
        raise NotImplementedError()

    def update_comparison_attr(self) -> None:
        """
        Update the comparison's attr in this dataclass
        """
        raise NotImplementedError()

    def update_comparison_dicts(self) -> None:
        """
        Rebuild by_attr/by_pv and shared when user changes anything.
        """
        raise NotImplementedError()


class FailPage(DesignerDisplay, DataWidget):
    """Page for any step / configuration that fails preparation (run conversion)"""
    filename = 'failed_prep_page.ui'

    fail_title: QLabel
    fail_desc: QLabel

    def __init__(self, *args, data=None, reason: Exception, **kwargs):
        super().__init__(*args, data=data, **kwargs)
        self._ex = reason
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.fail_desc.setText(str(self._ex))


class ConfigurationGroupPage(DesignerDisplay, PageWidget):
    """
    Page that handles all components of a ConfigurationGroup.
    """
    filename = 'configuration_group_page.ui'

    config_group_placeholder: QWidget
    config_group_widget: ConfigurationGroupWidget
    config_table: QTableWidget
    add_row_button: QPushButton
    add_row_type_combo: QComboBox

    config_cls_options: ClassVar[Dict[str, Type[Configuration]]] = {
        cls.__name__: cls for cls in (
            ConfigurationGroup,
            DeviceConfiguration,
            PVConfiguration,
            ToolConfiguration,
            TemplateConfiguration,
        )
    }

    def __init__(self, data: ConfigurationGroup, **kwargs):
        super().__init__(data=data, **kwargs)
        self.setup_done = False
        # Create the static sub-widgets and place them
        self.setup_name_desc_tags_init()
        self.config_group_widget = ConfigurationGroupWidget(data=data)
        self.insert_widget(
            self.config_group_widget,
            self.config_group_placeholder,
        )
        # Allow the user to add more rows
        self.add_row_button.clicked.connect(self.add_config_row)
        # Fill in the row type selector box
        for option in self.config_cls_options:
            self.add_row_type_combo.addItem(option)
        self.config_table.dropEvent = self.table_drop_event

        self.post_tree_setup()

    def post_tree_setup(self) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().post_tree_setup()
        if not self.setup_done:
            # Fill in the rows from the initial data
            for config in self.data.configs:
                self.add_config_row(config=config)
            self.setup_done = True
        self.setup_name_desc_tags_link()

    def add_config_row(
        self,
        checked: bool = False,
        config: Optional[Configuration] = None,
        **kwargs,
    ) -> None:
        """
        Add a new row to the configuration table.

        This also creates a Configuration instance if necessary, creates
        the corresponding page, and does all the related setup.

        Parameters
        ----------
        checked : bool, optional
            Unused. Button "clicked" signals often pass this as the first
            positional argument.
        config : Configuration, optional
            The configuration to add. If omitted, we'll create a blank
            configuration of the selected type from the add_row_type_combo.
        """
        if config is None:
            # New configuration
            config = self.config_cls_options[
                self.add_row_type_combo.currentText()
            ]()
            self.data.configs.append(config)
        config_row = ConfigurationGroupRowWidget(data=config)
        config_item = None
        for child_item in self.tree_item.get_children():
            if child_item.orig_data is config:
                config_item = child_item
                break
        if config_item is None:
            # make a new config, modifies tree
            with self.full_tree.modifies_tree():
                config_item = TreeItem(
                    data=config,
                    tree_parent=self.tree_item,
                )

        self.setup_row_buttons(
            row_widget=config_row,
            item=config_item,
            table=self.config_table,
        )
        row_count = self.config_table.rowCount()
        self.config_table.insertRow(row_count)
        self.config_table.setRowHeight(row_count, config_row.sizeHint().height())
        self.config_table.setCellWidget(row_count, 0, config_row)

    def remove_table_data(self, data: Configuration) -> None:
        """
        Remove the data associated with a given configuration.
        """
        self.data.configs.remove(data)

    def move_config_row(self, source: int, dest: int) -> None:
        """
        Move the row at index source to index dest.

        Rearanges the table, the file, and the tree.
        """
        # Skip if into the same index
        if source == dest:
            return
        config_data = self.data.configs.pop(source)
        self.data.configs.insert(dest, config_data)
        # Rearrange the tree
        with self.full_tree.modifies_tree():
            config_item = self.tree_item.takeChild(source)
            self.tree_item.insertChild(dest, config_item)
        # Rearrange the table: need a whole new widget or else segfault
        self.config_table.removeRow(source)
        self.config_table.insertRow(dest)
        config_row = ConfigurationGroupRowWidget(data=config_data)
        self.setup_row_buttons(
            row_widget=config_row,
            item=config_item,
            table=self.config_table,
        )
        self.config_table.setRowHeight(dest, config_row.sizeHint().height())
        self.config_table.setCellWidget(dest, 0, config_row)

    def table_drop_event(self, event: QDropEvent) -> None:
        """
        Monkeypatch onto the table to allow us to drag/drop rows.

        Shoutouts to stackoverflow
        """
        if event.source() is self.config_table:
            selected_indices = self.config_table.selectedIndexes()
            if not selected_indices:
                return
            selected_row = selected_indices[0].row()
            dest_row = self.config_table.indexAt(event.pos()).row()
            if dest_row == -1:
                dest_row = self.config_table.rowCount() - 1
            self.move_config_row(selected_row, dest_row)

    def delete_table_row(
        self,
        *args,
        table: QTableWidget,
        item: TreeItem,
        row: DataWidget,
        **kwargs
    ) -> None:
        # Use old QTableWidget handling
        # Confirmation dialog
        reply = QMessageBox.question(
            self,
            'Confirm deletion',
            (
                'Are you sure you want to delete the '
                f'{item.data(2)} named "{item.data(0)}"? '
                'Note that this will delete any child nodes in the tree.'
            ),
        )
        if reply != QMessageBox.Yes:
            return
        # Get the identity of the data
        data = row.bridge.data
        # Remove item from the tree
        with self.full_tree.modifies_tree():
            try:
                self.tree_item.removeChild(item)
            except ValueError:
                pass

        # Remove row from the table
        for row_index in range(table.rowCount()):
            widget = table.cellWidget(row_index, 0)
            if widget is row:
                table.removeRow(row_index)
                break
        # Remove configuration from the data structure
        self.remove_table_data(data)


class DeviceConfigurationPage(DesignerDisplay, PageWidget):
    """
    Page that handles all components of a DeviceConfiguration.
    """
    filename = 'device_configuration_page.ui'

    device_widget_placeholder: QWidget
    device_config_widget: DeviceConfigurationWidget

    comparisons_table: PagedTableWidget
    add_comparison_button: QPushButton

    def __init__(self, data: DeviceConfiguration, **kwargs):
        super().__init__(data=data, **kwargs)
        self.setup_name_desc_tags_init()
        self.device_config_widget = DeviceConfigurationWidget(data=data)
        self.insert_widget(
            self.device_config_widget,
            self.device_widget_placeholder,
        )

        self.comparisons_table.set_title("Comparisons")
        self.post_tree_setup()

    def post_tree_setup(self) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().post_tree_setup()
        self.setup_comparison_table_link(
            by_attr_key='by_attr',
            data_widget=self.device_config_widget,
        )
        self.comparisons_table.set_page(1)
        self.setup_name_desc_tags_link()

    def add_comparison_row(
        self,
        checked: bool = False,
        attr: str = '',
        comparison: Optional[Comparison] = None,
        update: bool = True
    ) -> None:
        """
        Add a new row to the comparison table.

        See PageWidget for full docstring.
        """
        if comparison is None:
            # New comparison
            comparison = Equals(name='untitled')
            self.data.shared.append(comparison)

        comp_item = None
        for child_item in self.tree_item.get_children():
            if child_item.orig_data is comparison:
                comp_item = child_item
                break
        if comp_item is None:
            with self.full_tree.modifies_tree():
                comp_item = TreeItem(
                    data=comparison,
                    tree_parent=self.tree_item,
                )

        row_count = self.comparisons_table.row_count()
        self.comparisons_table.insert_row(
            row_count,
            comparison,
            partial(self.configure_row_widget,
                    comparison=comparison,
                    comp_item=comp_item),
            update=update
        )
        if update:
            self.comparisons_table.show_row_for_data(comparison)

    def remove_table_data(self, data: Comparison) -> None:
        """
        Remove the data associated with a given configuration.
        """
        # This could be in several different places!
        try:
            self.data.shared.remove(data)
        except ValueError:
            for comp_list in self.data.by_attr.values():
                try:
                    comp_list.remove(data)
                except ValueError:
                    pass
                else:
                    break

    def update_combo_attrs(self, row_widget: ComparisonRowWidget) -> None:
        """
        Set the allowed values for ``attr_combo`` in ``row_widget``
        """
        combo = row_widget.attr_combo
        orig_value = combo.currentText()
        combo.clear()
        found_attr = False
        for index, attr in enumerate(self.data.by_attr):
            combo.addItem(attr)
            if orig_value == attr:
                combo.setCurrentIndex(index)
                found_attr = True
        combo.addItem('shared')
        if not found_attr:
            # Should be shared
            combo.setCurrentIndex(combo.count() - 1)

    def update_comparison_attr(self, text: str, *args, comparison: Comparison, **kwargs) -> None:
        """
        Update comparison location in parent config
        """
        self.data.move_comparison(comparison, text)
        return

    def update_comparison_dicts(self, *args, **kwargs) -> None:
        """
        Rebuild the comparison lists when anything changes.  Refresh the
        comparisons table to reflect the changes.
        """
        unsorted: List[Tuple[str, Comparison]] = []

        for row_index in range(self.comparisons_table.row_count()):
            comp = self.comparisons_table.row_data(row_index)
            curr_attr = get_comp_field_in_parent(comp, self.data)
            unsorted.append(
                (curr_attr, comp)
            )

        def get_sort_key(elem: Tuple[str, Comparison]):
            return (elem[0], elem[1].name)

        by_attr = {
            signal_name: []
            for signal_name in sorted(
                self.device_config_widget.component_name_list.get()
            )
        }
        shared = []
        for attr, comp in sorted(unsorted, key=get_sort_key):
            if attr == 'shared':
                shared.append(comp)
            else:
                by_attr[attr].append(comp)
        self.data.by_attr = by_attr
        self.data.shared = shared

        self.comparisons_table.refresh()

    def replace_comparison(
        self,
        old_comparison: Comparison,
        new_comparison: Comparison,
        comp_item: TreeItem,
    ) -> None:
        """
        Find old_comparison and replace it with new_comparison.

        Also finds the row widget and replaces it with a new row widget.
        """
        self.comparisons_table.replace_data(old_comparison, new_comparison)

        self.comparisons_table.replace_data(
            new_comparison,
            partial(self.configure_row_widget,
                    comparison=new_comparison,
                    comp_item=comp_item),
            repl_role=SETUP_SLOT_ROLE
        )


class PVConfigurationPage(DesignerDisplay, PageWidget):
    """
    Page that handles all components of a PVConfiguration.
    """
    filename = 'pv_configuration_page.ui'

    pv_widget_placeholder: QWidget
    pv_configuration_widget: PVConfigurationWidget

    comparisons_table: PagedTableWidget
    add_comparison_button: QPushButton

    def __init__(self, data: PVConfiguration, **kwargs):
        super().__init__(data=data, **kwargs)
        self.setup_name_desc_tags_init()
        self.pv_configuration_widget = PVConfigurationWidget(data=data)
        self.insert_widget(
            self.pv_configuration_widget,
            self.pv_widget_placeholder,
        )

        self.comparisons_table.set_title('Comparisons')
        self.post_tree_setup()

    def post_tree_setup(self) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().post_tree_setup()
        self.setup_comparison_table_link(
            by_attr_key='by_pv',
            data_widget=self.pv_configuration_widget,
        )
        self.comparisons_table.set_page(1)
        self.setup_name_desc_tags_link()

    def add_comparison_row(
        self,
        checked: bool = False,
        attr: str = '',
        comparison: Optional[Comparison] = None,
        update: bool = True
    ):
        """
        Add a new row to the comparison table.

        See PageWidget for full docstring.
        """
        if comparison is None:
            # New comparison
            comparison = Equals(name='untitled')
            self.data.shared.append(comparison)

        comp_item = None
        for child_item in self.tree_item.get_children():
            if child_item.orig_data is comparison:
                comp_item = child_item
                break
        if comp_item is None:
            # make a new comparison, modifies tree
            with self.full_tree.modifies_tree():
                comp_item = TreeItem(
                    data=comparison,
                    tree_parent=self.tree_item,
                )

        row_count = self.comparisons_table.row_count()
        self.comparisons_table.insert_row(
            row_count,
            comparison,
            partial(self.configure_row_widget,
                    comparison=comparison,
                    comp_item=comp_item),
            update=update
        )
        if update:
            self.comparisons_table.show_row_for_data(comparison)

    def remove_table_data(self, data: Comparison) -> None:
        """
        Remove the data associated with a given configuration.
        """
        # This could be in several different places!
        try:
            self.data.shared.remove(data)
        except ValueError:
            for comp_list in self.data.by_pv.values():
                try:
                    comp_list.remove(data)
                except ValueError:
                    pass
                else:
                    break

    def update_combo_attrs(self, row_widget: ComparisonRowWidget) -> None:
        """
        Set the allowed values for ``attr_combo`` in ``row_widget``
        """
        combo = row_widget.attr_combo
        orig_value = combo.currentText()
        combo.clear()
        found_attr = False
        for index, attr in enumerate(self.data.by_pv):
            combo.addItem(attr)
            if orig_value == attr:
                combo.setCurrentIndex(index)
                found_attr = True
        combo.addItem('shared')
        if not found_attr:
            # Should be shared
            combo.setCurrentIndex(combo.count() - 1)

    def update_comparison_attr(self, text: str, *args, comparison: Comparison, **kwargs) -> None:
        """
        Update comparison location in parent config
        """
        self.data.move_comparison(comparison, text)

    def update_comparison_dicts(self, *args, **kwargs) -> None:
        """
        Rebuild by_attr and shared when user changes anything (e.g. a PV is deleted)
        """
        unsorted: List[Tuple[str, Comparison]] = []

        for row_index in range(self.comparisons_table.row_count()):
            comp = self.comparisons_table.row_data(row_index)
            curr_attr = get_comp_field_in_parent(comp, self.data)
            unsorted.append(
                (curr_attr, comp)
            )

        def get_sort_key(elem: Tuple[str, Comparison]):
            return (elem[0], elem[1].name)

        by_pv = {
            pvname: []
            for pvname in sorted(
                self.pv_configuration_widget.pvname_list.get()
            )
        }
        shared = []
        for attr, comp in sorted(unsorted, key=get_sort_key):
            if attr == 'shared':
                shared.append(comp)
            else:
                by_pv[attr].append(comp)
        self.data.by_pv = by_pv
        self.data.shared = shared

        self.comparisons_table.refresh()

    def replace_comparison(
        self,
        old_comparison: Comparison,
        new_comparison: Comparison,
        comp_item: TreeItem,
    ) -> None:
        """
        Finds the row widget and replaces it with a new row widget.
        """
        self.comparisons_table.replace_data(old_comparison, new_comparison)

        self.comparisons_table.replace_data(
            new_comparison,
            partial(self.configure_row_widget,
                    comparison=new_comparison,
                    comp_item=comp_item),
            repl_role=SETUP_SLOT_ROLE
        )


class ToolConfigurationPage(DesignerDisplay, PageWidget):
    """
    Page that handles all components of a ToolConfiguration.

    Currently this is just the "Ping" tool but other tools
    can be added.
    """
    filename = 'tool_configuration_page.ui'

    tool_placeholder: QWidget
    tool_widget: DataWidget

    comparisons_table: PagedTableWidget
    add_comparison_button: QPushButton
    tool_select_combo: QComboBox

    # Defines the valid tools, their result structs, and edit widgets
    tool_map: ClassVar[Dict[Type[Tool], Tuple[Type[ToolResult], Type[DataWidget]]]] = {
        Ping: (PingResult, PingWidget),
    }
    tool_names: Dict[str, Type[Tool]]

    def __init__(self, data: ToolConfiguration, **kwargs):
        super().__init__(data=data, **kwargs)
        self.setup_name_desc_tags_init()

        self.comparisons_table.set_title('Comparisons')
        self.post_tree_setup()

    def post_tree_setup(self) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().post_tree_setup()
        if self.setup_comparison_table_link(
            by_attr_key='by_attr',
            data_widget=None,
        ):
            # Set up our specific tool handling (must be after filling rows)
            self.new_tool(self.data.tool)
            self.tool_names = {}
            for tool in self.tool_map:
                self.tool_select_combo.addItem(tool.__name__)
                self.tool_names[tool.__name__] = tool
            self.tool_select_combo.activated.connect(self.new_tool_selected)

        self.comparisons_table.set_page(1)
        self.setup_name_desc_tags_link()

    def add_comparison_row(
        self,
        checked: bool = False,
        attr: str = '',
        comparison: Optional[Comparison] = None,
        update: bool = True
    ) -> None:
        """
        Add a new row to the comparison table.

        See PageWidget for the full docstring.
        """
        if comparison is None:
            # New comparison
            comparison = Equals(name='untitled')
            self.data.shared.append(comparison)

        comp_item = None
        for child_item in self.tree_item.get_children():
            if child_item.orig_data is comparison:
                comp_item = child_item
                break
        if comp_item is None:
            with self.full_tree.modifies_tree():
                comp_item = TreeItem(
                    data=comparison,
                    tree_parent=self.tree_item,
                )

        row_count = self.comparisons_table.row_count()
        self.comparisons_table.insert_row(
            row_count,
            comparison,
            partial(self.configure_row_widget,
                    comparison=comparison,
                    comp_item=comp_item),
            update=update
        )
        if update:
            self.comparisons_table.show_row_for_data(comparison)

    def remove_table_data(self, data: Comparison) -> None:
        """
        Remove the data associated with a given configuration.
        """
        # This could be in several different places!
        try:
            self.data.shared.remove(data)
        except ValueError:
            for comp_list in self.data.by_attr.values():
                try:
                    comp_list.remove(data)
                except ValueError:
                    pass
                else:
                    break

    def update_combo_attrs(self, row_widget: ComparisonRowWidget) -> None:
        """
        Set the allowed values for ``attr_combo`` in ``row_widget``
        """
        combo = row_widget.attr_combo
        orig_value = combo.currentText()
        combo.clear()
        found_attr = False
        for index, attr in enumerate(self.data.by_attr):
            combo.addItem(attr)
            if orig_value == attr:
                combo.setCurrentIndex(index)
                found_attr = True
        combo.addItem('shared')
        if not found_attr:
            # Should be shared
            combo.setCurrentIndex(combo.count() - 1)

    def update_comparison_attr(self, text: str, *args, comparison: Comparison, **kwargs) -> None:
        """
        Update comparison location in parent config
        """
        self.data.move_comparison(comparison, text)
        return

    def update_comparison_dicts(self, *args, **kwargs) -> None:
        """
        Rebuild by_attr and shared when user changes anything
        """
        unsorted: List[Tuple[str, Comparison]] = []

        for row_index in range(self.comparisons_table.row_count()):
            comp = self.comparisons_table.row_data(row_index)
            curr_attr = get_comp_field_in_parent(comp, self.data)
            unsorted.append(
                (curr_attr, comp)
            )

        def get_sort_key(elem: Tuple[str, Comparison]):
            return (elem[0], elem[1].name)

        result_type, _ = self.tool_map[type(self.data.tool)]
        gui_compatible_fields = set((
            int, float, str, bool,
            'int', 'float', 'str', 'bool',
        ))
        field_names = sorted(
            field.name
            for field in dataclasses.fields(result_type)
            if field.type in gui_compatible_fields
        )
        by_attr = {name: [] for name in field_names}
        shared = []
        for attr, comp in sorted(unsorted, key=get_sort_key):
            if attr == 'shared':
                shared.append(comp)
            else:
                by_attr[attr].append(comp)
        self.data.by_attr = by_attr
        self.data.shared = shared

    def replace_comparison(
        self,
        old_comparison: Comparison,
        new_comparison: Comparison,
        comp_item: TreeItem,
    ) -> None:
        """
        Replaces row widget in this page
        """
        self.comparisons_table.replace_data(old_comparison, new_comparison)

        self.comparisons_table.replace_data(
            new_comparison,
            partial(self.configure_row_widget,
                    comparison=new_comparison,
                    comp_item=comp_item),
            repl_role=SETUP_SLOT_ROLE
        )

    def new_tool(self, tool: Tool) -> None:
        """
        Replace the loaded tool (if applicable) with a new tool instance.

        This will update the data class and the GUI widgets appropriately.
        """
        # Replace the tool data structure
        self.data.tool = tool
        # Look up our tool
        _, widget_type = self.tool_map[type(tool)]
        # Create a new tool widget and place it
        new_widget = widget_type(data=tool)
        self.insert_widget(
            new_widget,
            self.tool_placeholder,
        )
        # Replace reference to old tool widget
        self.tool_widget = new_widget
        # Set by_attr correctly to match the result type
        # Also migrates lost comparisons to shared
        self.update_comparison_dicts()
        # refresh table, updating choices
        self.comparisons_table.refresh()

    def new_tool_selected(self, tool_name: str) -> None:
        """
        Slot for when the user selects a new tool type from the combo box.
        """
        tool_type = self.tool_names[tool_name]
        if isinstance(self.data.tool, tool_type):
            return
        new_tool = tool_type()
        self.new_tool_widget(new_tool)


class TemplateConfigurationPage(DesignerDisplay, PageWidget):
    """Widget for configuring Templated checkouts within other checkouts"""
    filename = "template_group_page.ui"

    template_page_widget: FillTemplatePage
    template_page_placeholder: QWidget

    data: Union[TemplateConfiguration, TemplateStep]
    ALLOWED_TYPE_MAP: ClassVar[Dict[Any, Tuple[Any, ...]]] = {
        TemplateConfiguration: (ConfigurationFile,),
        TemplateStep: (ConfigurationFile, ProcedureFile)
    }

    def __init__(self, data: Union[TemplateConfiguration, TemplateStep], **kwargs):
        super().__init__(data=data, **kwargs)
        self.setup_name_desc_tags_init()
        self.setup_template_widget_init()
        self.post_tree_setup()

    def setup_template_widget_init(self) -> None:
        self.template_page_widget = FillTemplatePage(
            allowed_types=self.ALLOWED_TYPE_MAP[type(self.data)]
        )

        def finish_widget_setup(*args, **kwargs):
            # only run this once, when we're loading an existing template checkout
            # subsequent opening of files do not populate staged list
            self.template_page_widget.data_updated.disconnect(finish_widget_setup)

            target = getattr(self.template_page_widget, 'orig_file', None)
            if target is not None:
                for regexFR in self.data.edits:
                    action = regexFR.to_action(target=target)
                    self.template_page_widget.stage_edit(action)
                self.template_page_widget.refresh_staged_table()

            # setup update data with each change to staged, new file
            self.template_page_widget.data_updated.connect(self.update_data)

        self.template_page_widget.data_updated.connect(finish_widget_setup)
        self.template_page_widget.open_file(filename=self.data.filename)

        # remove save as button
        self.template_page_widget.save_button.hide()

        self.insert_widget(self.template_page_widget, self.template_page_placeholder)

    def update_data(self) -> None:
        """Update the dataclass with information from the FillTemplatePage widget"""
        # FillTemplatePage is not a normal datawidget, and does not have a bridge.
        # Luckily there isn't much to track, via children, so we can do it manually
        self.data.filename = self.template_page_widget.fp
        staged_list = self.template_page_widget.staged_list
        edits = []
        for idx in range(staged_list.count()):
            row_data = staged_list.itemWidget(staged_list.item(idx)).data
            edits.append(row_data.origin)

        self.data.edits = edits

    def post_tree_setup(self) -> None:
        super().post_tree_setup()

        self.setup_name_desc_tags_link()


class ProcedureGroupPage(DesignerDisplay, PageWidget):
    """
    Top level page for Procedures (active checkout)

    currently nearly identical to ConfigurationGroupPage, with minor changes
    to account for ProcedureGroup dataclass.
    """
    filename = 'procedure_group_page.ui'

    # currently not set, left for future use if desired
    procedure_group_placeholder: QWidget
    procedure_table: QTableWidget
    add_row_button: QPushButton
    add_row_type_combo: QComboBox

    config_cls_options: ClassVar[Dict[str, Type[ProcedureStep]]] = {
        cls.__name__: cls for cls in (
            ProcedureGroup,
            DescriptionStep,
            PassiveStep,
            SetValueStep,
            TemplateStep,
        )
    }

    def __init__(self, data: ProcedureGroup, **kwargs):
        super().__init__(data=data, **kwargs)
        self.setup_done = False
        # Create the static sub-widgets and place them
        self.setup_name_desc_tags_init()

        # set up general step settings
        general_widget = GeneralProcedureWidget(data=data)
        self.insert_widget(general_widget, self.procedure_group_placeholder)

        # Allow the user to add more rows
        self.add_row_button.clicked.connect(self.add_config_row)
        # Fill in the row type selector box
        for option in self.config_cls_options:
            self.add_row_type_combo.addItem(option)
        self.procedure_table.dropEvent = self.table_drop_event

        self.post_tree_setup()

    def post_tree_setup(self) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().post_tree_setup()
        if not self.setup_done:
            # Fill in the rows from the initial data
            for config in self.data.steps:
                self.add_config_row(config=config)
            self.setup_done = True
        self.setup_name_desc_tags_link()

    def add_config_row(
        self,
        checked: bool = False,
        config: Optional[ProcedureStep] = None,
        **kwargs,
    ) -> None:
        """
        Add a new row to the configuration table.

        This also creates a Configuration instance if necessary, creates
        the corresponding page, and does all the related setup.

        Parameters
        ----------
        checked : bool, optional
            Unused. Button "clicked" signals often pass this as the first
            positional argument.
        config : Configuration, optional
            The configuration to add. If omitted, we'll create a blank
            configuration of the selected type from the add_row_type_combo.
        """
        if config is None:
            # New configuration
            config = self.config_cls_options[
                self.add_row_type_combo.currentText()
            ]()
            self.data.steps.append(config)
        # configuration group still works, only looks at name and class
        config_row = ConfigurationGroupRowWidget(data=config)

        config_item = None
        for child_item in self.tree_item.get_children():
            if child_item.orig_data is config:
                config_item = child_item
                break
        if config_item is None:
            # make a new config, modifies tree
            with self.full_tree.modifies_tree():
                config_item = TreeItem(
                    data=config,
                    tree_parent=self.tree_item,
                )

        self.setup_row_buttons(
            row_widget=config_row,
            item=config_item,
            table=self.procedure_table,
        )
        row_count = self.procedure_table.rowCount()
        self.procedure_table.insertRow(row_count)
        self.procedure_table.setRowHeight(row_count, config_row.sizeHint().height())
        self.procedure_table.setCellWidget(row_count, 0, config_row)

    def remove_table_data(self, data: ProcedureStep) -> None:
        """
        Remove the data associated with a given configuration.
        """
        self.data.steps.remove(data)

    def move_config_row(self, source: int, dest: int) -> None:
        """
        Move the row at index source to index dest.

        Rearanges the table, the file, and the tree.
        """
        # Skip if into the same index
        if source == dest:
            return
        config_data = self.data.steps.pop(source)
        self.data.steps.insert(dest, config_data)
        # Rearrange the tree
        with self.full_tree.modifies_tree():
            config_item = self.tree_item.takeChild(source)
            self.tree_item.insertChild(dest, config_item)
        # Rearrange the table: need a whole new widget or else segfault
        self.procedure_table.removeRow(source)
        self.procedure_table.insertRow(dest)
        config_row = ConfigurationGroupRowWidget(data=config_data)
        self.setup_row_buttons(
            row_widget=config_row,
            item=config_item,
            table=self.procedure_table,
        )
        self.procedure_table.setRowHeight(dest, config_row.sizeHint().height())
        self.procedure_table.setCellWidget(dest, 0, config_row)

    def table_drop_event(self, event: QDropEvent) -> None:
        """
        Monkeypatch onto the table to allow us to drag/drop rows.

        Shoutouts to stackoverflow
        """
        if event.source() is self.procedure_table:
            selected_indices = self.procedure_table.selectedIndexes()
            if not selected_indices:
                return
            selected_row = selected_indices[0].row()
            dest_row = self.procedure_table.indexAt(event.pos()).row()
            if dest_row == -1:
                dest_row = self.procedure_table.rowCount() - 1
            self.move_config_row(selected_row, dest_row)

    def replace_step(
        self,
        old_step: ProcedureStep,
        new_step: ProcedureStep,
        comp_item: TreeItem
    ) -> None:
        """
        Find old_step and replace it with new_step
        Also finds the row widget and replaces with a new row widget

        Parameters
        ----------
        old_step : ProcedureStep
            old ProcedureStep, to be replaced
        new_step : ProcedureStep
            new ProcedureStep to replace old_step with
        comp_item : TreeItem
            TreeItem holding the old comparison and widget
        """
        # go through rows
        found_row = None
        for row_index in range(self.procedure_table.rowCount()):
            widget = self.procedure_table.cellWidget(row_index, 0)
            if widget.data is old_step:
                found_row = row_index
                break
        if found_row is None:
            return

        step_row = ConfigurationGroupRowWidget(data=new_step)
        self.setup_row_buttons(
            row_widget=step_row,
            item=comp_item,
            table=self.procedure_table,
        )
        self.procedure_table.setCellWidget(found_row, 0, step_row)

    def delete_table_row(
        self,
        *args,
        table: QTableWidget,
        item: TreeItem,
        row: DataWidget,
        **kwargs
    ) -> None:
        # Use old QTableWidget handling
        # Confirmation dialog
        reply = QMessageBox.question(
            self,
            'Confirm deletion',
            (
                'Are you sure you want to delete the '
                f'{item.data(2)} named "{item.data(0)}"? '
                'Note that this will delete any child nodes in the tree.'
            ),
        )
        if reply != QMessageBox.Yes:
            return
        # Get the identity of the data
        data = row.bridge.data
        # Remove item from the tree
        with self.full_tree.modifies_tree():
            try:
                self.tree_item.removeChild(item)
            except ValueError:
                pass

        # Remove row from the table
        for row_index in range(table.rowCount()):
            widget = table.cellWidget(row_index, 0)
            if widget is row:
                table.removeRow(row_index)
                break
        # Remove configuration from the data structure
        self.remove_table_data(data)


class StepPage(DesignerDisplay, PageWidget):
    """
    Page that handles any single ProcedureStep instance.

    Contains a selector, placeholder for the specific step,
    and general verification settings

    Carries many methods that may or may not apply to active checkout steps.
    Consider refactoring in the future?
    """
    filename = 'step_page.ui'

    specific_procedure_placeholder: QWidget
    specific_procedure_widget: DataWidget
    general_procedure_placeholder: QFrame
    general_procedure_widget: QWidget
    bottom_spacer: QWidget

    specific_combo: QComboBox

    step_map: ClassVar[Dict[ProcedureStep, DataWidget]] = {
        DescriptionStep: None,
        PassiveStep: PassiveEditWidget,
        SetValueStep: SetValueEditWidget,
    }
    step_types: Dict[str, ProcedureStep]

    def __init__(self, data: ProcedureStep, **kwargs):
        super().__init__(data=data, **kwargs)
        self.step_types = {}
        for index, step_type in enumerate(self.step_map):
            self.specific_combo.addItem(step_type.__name__)
            if isinstance(data, step_type):
                self.specific_combo.setCurrentIndex(index)
            self.step_types[step_type.__name__] = step_type
        self.new_step(step=data)
        self.specific_combo.activated.connect(self.select_step_type)
        self._partial_slots = []

        self.post_tree_setup()

    def post_tree_setup(self) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().post_tree_setup()
        self.setup_name_desc_tags_link()

        # extra setup for SetValueStep.  Reminiscent of AnyComparison
        if isinstance(self.data, SetValueStep):
            self.setup_set_value_step()

    def new_step(self, step: ProcedureStep) -> None:
        """
        Set up the widgets for a new step and save it as self.data.

        Configures specific_procedure_widget

        This is accomplished by discarding the old widgets in favor
        of new widgets.
        """
        general_frame = ExpandableFrame(text='General Settings')
        general_widget = GeneralProcedureWidget(data=step)
        general_frame.add_widget(general_widget)
        self.insert_widget(
            general_frame,
            self.general_procedure_placeholder,
        )
        SpecificWidgetType = self.step_map[type(step)]
        if SpecificWidgetType:
            new_specific_widget = SpecificWidgetType(data=step)
        else:
            new_specific_widget = QWidget()

        self.insert_widget(
            new_specific_widget,
            self.specific_procedure_placeholder,
        )

        self.general_procedure_widget = general_widget
        self.specific_procedure_widget = new_specific_widget
        self.data = step
        self.setup_name_desc_tags_init()
        # Reinitialize this for the new name/desc/tags widget
        self.post_tree_setup()

    def select_step_type(self, new_type_index: int) -> None:
        """
        The user wants to change to a different step type.

        This needs to do the following:
        - create a new dataclass with as much overlap with
          the previous dataclass as possible, but using the new type
        - replace references in the configuration to the old dataclass
          with the new dataclass, including in the row widgets
        - call self.new_step to update the edit widgets here
        """
        new_type_name = self.specific_combo.itemText(new_type_index)
        new_type = self.step_types[new_type_name]
        if isinstance(self.data, new_type):
            return

        step = cast_dataclass(data=self.data, new_type=new_type)
        with self.full_tree.modifies_tree(select_prev=False):
            # Assumes self.parent_tree_item.widget: ProcedureGroupPage
            # put another way, this assumes steps cannot be parent of other steps
            self.parent_tree_item.orig_data.replace_step(
                old_step=self.data,
                new_step=step
            )

            # replace tree item
            new_item = TreeItem(
                data=step,
            )
            self.parent_tree_item.replaceChild(self.tree_item, new_item)
            self.tree_item = new_item

            parent_widget = self.full_tree.maybe_get_widget(self.parent_tree_item)
            if parent_widget is not None:
                parent_widget.replace_step(
                    old_step=self.data,
                    new_step=step,
                    comp_item=self.tree_item,
                )
            # remove old children, no longer needed.
            self.new_step(step=step)
            self.update_context()

        self.full_tree.select_by_item(new_item)

    def showEvent(self, *args, **kwargs) -> None:
        """
        Whenever the page is shown, update the context text.
        """
        self.update_context()
        return super().showEvent(*args, **kwargs)

    def update_context(self) -> None:
        """
        Update the context text in the top right of the page.

        This will then have information about which signals, PVs, or
        other data is being compared against.
        """
        parent_widget = self.full_tree.maybe_get_widget(self.parent_tree_item)
        if parent_widget is not None:
            if isinstance(parent_widget, StepPage):
                parent_widget.update_context()
                self.name_desc_tags_widget.extra_text_label.setText(
                    parent_widget.name_desc_tags_widget.extra_text_label.text()
                )
                return

        config = self.parent_tree_item.orig_data
        attr = ''

        desc = describe_step_context(attr=attr, step=config)
        self.name_desc_tags_widget.extra_text_label.setText(desc)
        self.name_desc_tags_widget.extra_text_label.setToolTip(desc)
        self.name_desc_tags_widget.init_viewer(self.data, config)

    def setup_set_value_step(self) -> None:
        self.update_subpages()
        self.specific_procedure_widget.bridge.success_criteria.updated.connect(
            self.update_subpages
        )

    def update_subpages(self) -> None:
        """
        Update nodes based on the current SetValueStep state.

        This may add or remove pages as appropriate.

        The node order should match the sequence in the table
        """
        # Cache the previous selection
        pre_selected = self.full_tree.current_item.orig_data
        display_order = OrderedDict()
        table = self.specific_procedure_widget.checks_table
        for row_index in range(table.rowCount()):
            widget = table.cellWidget(row_index, 0)
            comp = widget.data.comparison
            display_order[id(comp)] = (comp, widget)

        with self.full_tree.modifies_tree():
            # Pull off all of the existing items
            old_items = self.tree_item.takeChildren()
            old_item_map = {
                id(item.orig_data): item for item in old_items
            }
            # Add items back as needed, may be a mix of old and new
            new_item = None
            for ident, (comp, row_widget) in display_order.items():
                try:
                    item = old_item_map[ident]
                except KeyError:
                    # Need to make a new page/item
                    new_item = self.add_sub_comparison_node(comp)
                else:
                    # An old item existed, add it again
                    self.tree_item.addChild(item)
                    # setup child button for each existing row widget
                    # This isn't handled by tablewidgetwithAddRow
                    self.setup_child_button(
                        button=row_widget.child_button,
                        item=item,
                    )
        if not new_item:
            self.full_tree.select_by_data(pre_selected)
            return
        self.full_tree.select_by_item(new_item)

    def add_sub_comparison_node(self, comparison: Comparison) -> TreeItem:
        """
        Add a sub-comparison.  Expected to be called inside a DualTree.modifies_tree
        context manager.
        """
        item = TreeItem(
            data=comparison,
            tree_parent=self.tree_item,
        )

        self.setup_set_value_check_row_buttons(
            comparison=comparison,
            item=item,
        )
        return item

    def setup_set_value_check_row_buttons(
        self,
        comparison: Comparison,
        item: TreeItem
    ) -> None:
        table: QTableWidget = self.specific_procedure_widget.checks_table
        for index in range(table.rowCount()):
            row_widget = table.cellWidget(index, 0)
            if row_widget.data.comparison is comparison:
                break
        if row_widget.data.comparison is not comparison:
            return
        self.setup_child_button(
            button=row_widget.child_button,
            item=item,
        )
        # setup callback to update description if comparison page changes
        # gets a bit invasive here, assumes links between ComparisonPage and
        # the atef item have been made
        comp_widget = self.full_tree.maybe_get_widget(item=item)
        if comp_widget:
            # let creation handle this
            self.setup_set_value_check_row_callbacks(comp_widget)

    def setup_set_value_check_row_callbacks(self, comp_widget: ComparisonPage) -> None:
        # TODO: should we move this to the specific widget?
        spec_comp_widget = comp_widget.specific_comparison_widget
        desc_update_slot = self.specific_procedure_widget.update_all_desc
        # subscribe to the relevant comparison signals
        for field in ('value', 'low', 'high', 'description'):
            if hasattr(spec_comp_widget.bridge, field):
                getattr(spec_comp_widget.bridge, field).changed_value.connect(
                    desc_update_slot
                )

                # disconnect slots from bridge on destruction
                # bridges may not persist as much with lazy loading, idk
                comp_disconn_slot = WeakPartialMethodSlot(
                    comp_widget, comp_widget.destroyed,
                    self.teardown_set_value_check_row_callbacks,
                    spec_comp_widget, field
                )
                self._partial_slots.append(comp_disconn_slot)

                self_disconn_slot = WeakPartialMethodSlot(
                    self, self.destroyed,
                    self.teardown_set_value_check_row_callbacks,
                    spec_comp_widget, field
                )
                self._partial_slots.append(self_disconn_slot)

    def teardown_set_value_check_row_callbacks(
        self,
        *args,
        spec_comp_widget,
        field: str,
        **kwargs
    ) -> None:
        try:
            getattr(spec_comp_widget.bridge, field).changed_value.disconnect(
                self.teardown_set_value_check_row_callbacks
            )
        except Exception as ex:
            logger.warning(f'unable to disconnect signal from bridge {ex}')

    def setup_comp_callbacks(self, comp_page_widget: ComparisonPage) -> None:
        """
        Set up callbacks connecting a child comparison page this
        Specific here to SetValueStep, but could be expanded later
        """
        if isinstance(self.specific_procedure_widget, SetValueEditWidget):
            self.setup_set_value_check_row_callbacks(comp_page_widget)

    def replace_comparison(
        self,
        old_comparison: Comparison,
        new_comparison: Union[Comparison, ComparisonToTarget],
        comp_item: TreeItem,
    ) -> None:
        """
        replaces the relevant row widget.
        """
        if isinstance(self.specific_procedure_widget, SetValueEditWidget):
            table: TableWidgetWithAddRow = self.specific_procedure_widget.checks_table
            row_widget_cls = CheckRowWidget
            new_data = new_comparison

        # Get info and location of comparison in table
        found_row = None
        for row_index in range(table.rowCount()):
            widget = table.cellWidget(row_index, 0)
            if widget.data.comparison is old_comparison:
                found_row = row_index
                break
        if found_row is None:
            return

        # create new row
        comp_row = row_widget_cls(data=new_data)

        self.setup_row_buttons(
            row_widget=comp_row,
            item=comp_item,
            table=table,
        )
        table.setCellWidget(found_row, 0, comp_row)
        self.update_subpages()

    def remove_table_data(self, data: Any):
        if isinstance(self.data, SetValueStep):
            self.data.success_criteria.remove(data)


class RunConfigPage(DesignerDisplay, PageWidget):
    """
    Base Widget for running active checkout steps and displaying their
    results

    Will always have a RunCheck widget, which should be connected after
    instantiation via ``RunCheck.setup_buttons()``

    Contains a placeholder for a DataWidget
    """
    filename = 'run_step_page.ui'

    run_widget_placeholder: QWidget
    run_widget: DataWidget
    run_check_placeholder: QWidget
    run_check: RunCheck

    run_widget_map: ClassVar[Dict[Union[PreparedConfiguration, PreparedGroup], DataWidget]] = {
        PreparedTemplateConfiguration: TemplateRunWidget,
    }

    def __init__(self, *args, data, **kwargs):
        super().__init__(*args, data, **kwargs)
        self.run_check = RunCheck(data=[data])
        self.insert_widget(self.run_check, self.run_check_placeholder)
        # gather run_widget
        run_widget_cls = self.run_widget_map[type(data)]
        self.run_widget = run_widget_cls(data=data)

        self.insert_widget(self.run_widget, self.run_widget_placeholder)

        if isinstance(data, PreparedTemplateConfiguration):
            self.run_check.run_button.clicked.connect(self.run_widget.run_config)

        self.post_tree_setup()


class RunStepPage(DesignerDisplay, PageWidget):
    """
    Base Widget for running active checkout steps and displaying their
    results

    Will always have a RunCheck widget, which should be connected after
    instantiation via ``RunCheck.setup_buttons()``

    Contains a placeholder for a DataWidget
    """
    filename = 'run_step_page.ui'

    run_widget_placeholder: QWidget
    run_widget: DataWidget
    run_check_placeholder: QWidget
    run_check: RunCheck

    run_widget_map: ClassVar[Dict[PreparedProcedureStep, DataWidget]] = {
        PreparedDescriptionStep: DescriptionRunWidget,
        PreparedPassiveStep: PassiveRunWidget,
        PreparedSetValueStep: SetValueRunWidget,
        PreparedTemplateStep: TemplateRunWidget,
    }

    def __init__(self, *args, data, **kwargs):
        super().__init__(*args, data, **kwargs)
        self.run_check = RunCheck(data=[data])
        self.insert_widget(self.run_check, self.run_check_placeholder)
        # gather run_widget
        run_widget_cls = self.run_widget_map[type(data)]
        self.run_widget = run_widget_cls(data=data)

        self.insert_widget(self.run_widget, self.run_widget_placeholder)

        if isinstance(data, (PreparedPassiveStep, PreparedTemplateStep)):
            self.run_check.run_button.clicked.connect(self.run_widget.run_config)
        elif isinstance(data, PreparedSetValueStep):
            self.run_check.busy_thread.task_finished.connect(
                self.run_widget.update_statuses
            )

        self.post_tree_setup()

    def link_children(self) -> None:
        """
        A helper method to link children tree items with their parent.
        Children can get orphaned during the edit->run transition.
        """
        if isinstance(self.data, PreparedSetValueStep):
            # set up children
            child_items = self.tree_item.takeChildren()
            n_rows = self.run_widget.checks_table.rowCount()
            for row_ind, item in zip(range(n_rows), child_items):
                row_widget = self.run_widget.checks_table.cellWidget(row_ind, 0)
                self.setup_child_button(row_widget.child_button, item)
                self.tree_item.addChild(item)


class ComparisonPage(DesignerDisplay, PageWidget):
    """
    Page that handles any comparison instance.

    Contains a selector for switching which comparison type
    we're using that will cause the type to change and the
    active widget to be replaced with the specific widget.

    Also contains standard fields for name, desc as appropriate
    and fields common to all comparison instances at the bottom.
    """
    filename = 'comparison_page.ui'

    specific_comparison_placeholder: QWidget
    specific_comparison_widget: DataWidget
    general_comparison_placeholder: QWidget
    general_comparison_widget: GeneralComparisonWidget
    bottom_spacer: QWidget

    specific_combo: QComboBox

    # Defines the valid comparisons and their edit widgets
    comp_map: ClassVar[Dict[Comparison, DataWidget]] = {
        Equals: EqualsWidget,
        NotEquals: NotEqualsWidget,
        Greater: GreaterWidget,
        GreaterOrEqual: GreaterOrEqualWidget,
        Less: LessWidget,
        LessOrEqual: LessOrEqualWidget,
        Range: RangeWidget,
        ValueSet: ValueSetWidget,
        AnyValue: AnyValueWidget,
        AnyComparison: AnyComparisonWidget,
    }
    comp_types: Dict[str, Comparison]

    def __init__(self, data: Comparison, **kwargs):
        super().__init__(data=data, **kwargs)
        self.comp_types = {}
        for index, comp_type in enumerate(self.comp_map):
            self.specific_combo.addItem(comp_type.__name__)
            if isinstance(data, comp_type):
                self.specific_combo.setCurrentIndex(index)
            self.comp_types[comp_type.__name__] = comp_type
        self.mode_input_setup = False
        self.new_comparison(comparison=data)
        self.specific_combo.activated.connect(self.select_comparison_type)

        self.post_tree_setup()

    def post_tree_setup(self) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().post_tree_setup()
        self.setup_name_desc_tags_link()
        # Extra setup and/or teardown from AnyComparison
        if isinstance(self.data, AnyComparison):
            self.setup_any_comparison()

        if not self.mode_input_setup:
            setup_multi_mode_for_widget(
                page=self, specific_widget=self.specific_comparison_widget
            )
            self.mode_input_setup = True

        self.setup_parent_callbacks()

    def new_comparison(self, comparison: Comparison) -> None:
        """
        Set up the widgets for a new comparison and save it as self.data.

        ComparisonPage is unique in that the comparison can be swapped out
        while the page is loaded. This method doesn't handle the complexity
        of how to manage this in the Configuration instance, but it does
        make sure all the widgets on this page connect to the new
        comparison.

        This is accomplished by discarding the old widgets in favor
        of new widgets.
        """
        general_widget = GeneralComparisonWidget(data=comparison)
        self.insert_widget(
            general_widget,
            self.general_comparison_placeholder,
        )
        SpecificWidgetType = self.comp_map[type(comparison)]
        new_specific_widget = SpecificWidgetType(data=comparison)
        self.insert_widget(
            new_specific_widget,
            self.specific_comparison_placeholder,
        )
        self.general_comparison_widget = general_widget
        self.specific_comparison_widget = new_specific_widget
        self.data = comparison
        self.setup_name_desc_tags_init()
        # Reinitialize this for the new name/desc/tags widget
        self.post_tree_setup()

        # Fix the layout spacing, some comparisons want spacing and some don't
        if isinstance(comparison, (ValueSet, AnyValue, AnyComparison)):
            # Maximum = "shrink spacer to the size hint (0, 0)"
            self.bottom_spacer.setSizePolicy(
                QSizePolicy.Maximum,
                QSizePolicy.Maximum,
            )
        else:
            # Expanding = "make spacer take up as much space as possible"
            self.bottom_spacer.setSizePolicy(
                QSizePolicy.Expanding,
                QSizePolicy.Expanding,
            )

    def select_comparison_type(self, new_type_index: int) -> None:
        """
        The user wants to change to a different comparison type.

        This needs to do the following:
        - create a new dataclass with as much overlap with
          the previous dataclass as possible, but using the new type
        - replace references in the configuration to the old dataclass
          with the new dataclass, including in the row widgets
        - call self.new_comparison to update the edit widgets here
        """
        new_type_name = self.specific_combo.itemText(new_type_index)
        new_type = self.comp_types[new_type_name]
        if isinstance(self.data, new_type):
            return
        if isinstance(self.data, (ValueSet, AnyValue, AnyComparison)):
            # These can have lots of sub-items and will need a warning
            type_name = type(self.data).__name__
            reply = QMessageBox.question(
                self,
                'Confirm type change',
                (
                    'Are you sure you want to change the comparison type? '
                    f'{type_name} may have many sub-items, '
                    'Which will be deleted if you change the type '
                    f'to {new_type_name}.'
                ),
            )
            if reply != QMessageBox.Yes:
                # Reset the combo box, the user cancelled
                self.specific_combo.setCurrentText(type_name)
                return
        comparison = cast_dataclass(data=self.data, new_type=new_type)
        parent_widget = self.full_tree.maybe_get_widget(self.parent_tree_item)

        with self.full_tree.modifies_tree(select_prev=False):
            # Replace comparison in dataclass, get new comparison
            self.parent_tree_item.orig_data.replace_comparison(
                old_comp=self.data,
                new_comp=comparison
            )

            # replace tree item
            new_item = TreeItem(
                data=comparison,
            )
            self.parent_tree_item.replaceChild(self.tree_item, new_item)
            self.tree_item = new_item

            # if parent widget exists, replace the comparison row there
            if parent_widget is not None:
                # row here actually holds a ComparisonToTarget, get that data
                if isinstance(self.parent_tree_item.orig_data, SetValueStep):
                    parent_data = self.parent_tree_item.orig_data
                    comp_list = [crit.comparison for crit in parent_data.success_criteria]
                    idx = comp_list.index(comparison)
                    new_comp_row_data = parent_data.success_criteria[idx]
                else:
                    new_comp_row_data = comparison

                parent_widget.replace_comparison(
                    old_comparison=self.data,
                    new_comparison=new_comp_row_data,
                    comp_item=self.tree_item,
                )
        # old item will be missing, select this one
        self.full_tree.select_by_item(new_item)

    def showEvent(self, *args, **kwargs) -> None:
        """
        Whenever the page is shown, update the context text.
        """
        self.update_context()
        return super().showEvent(*args, **kwargs)

    def update_context(self) -> None:
        """
        Update the context text in the top right of the page.

        This will then have information about which signals, PVs, or
        other data is being compared against.
        """
        parent_widget = self.full_tree.maybe_get_widget(self.parent_tree_item)
        if parent_widget is not None:
            if isinstance(parent_widget, ComparisonPage):
                parent_widget.update_context()
                self.name_desc_tags_widget.extra_text_label.setText(
                    parent_widget.name_desc_tags_widget.extra_text_label.text()
                )
                return
            if isinstance(parent_widget, (StepPage, RunStepPage)):
                # No-op to let this be used in active checkouts without
                # passive checkout config files in the parent widget
                return

        config = self.parent_tree_item.orig_data

        desc = describe_comparison_context(comp=self.data, parent=config)
        self.name_desc_tags_widget.extra_text_label.setText(desc)
        self.name_desc_tags_widget.extra_text_label.setToolTip(desc)
        self.name_desc_tags_widget.init_viewer(self.data, config)

    def setup_parent_callbacks(self) -> None:
        """Call parent's setup_comp_callbacks method"""
        parent_widget = self.full_tree.maybe_get_widget(self.tree_item.parent())
        if parent_widget is None:
            return

        if hasattr(parent_widget, 'setup_comp_callbacks'):
            parent_widget.setup_comp_callbacks(self)

    def setup_any_comparison(self) -> None:
        """
        Special setup for when an AnyComparison is added.

        Assumes that we already have widgets loaded and data initialized
        for the AnyComparison.

        - Adds and initializes a sub-node for each comparison.
        - Makes sure sub-nodes are managed properly as the AnyComparison
          updates.
        """
        self.update_subpages()
        self.specific_comparison_widget.bridge.comparisons.updated.connect(
            self.update_subpages,
        )

    def update_subpages(self) -> None:
        """
        Update nodes based on the current AnyComparison state.

        This may add or remove pages as appropriate.

        The node order should match the sequence in the table,
        even though this sequence is arbitrary and the user is not
        in control of it.
        """
        # Cache the previous selection
        pre_selected = self.full_tree.current_item.orig_data
        display_order = OrderedDict()
        table = self.specific_comparison_widget.comparisons_table
        for row_index in range(table.rowCount()):
            widget = table.cellWidget(row_index, 0)
            comp = widget.data
            display_order[id(comp)] = comp
        with self.full_tree.modifies_tree(select_prev=False):
            # Pull off all of the existing items
            old_items = self.tree_item.takeChildren()
            old_item_map = {
                id(item.orig_data): item for item in old_items
            }
            # Add items back as needed, may be a mix of old and new
            new_item = None
            for ident, comp in display_order.items():
                try:
                    item = old_item_map[ident]
                except KeyError:
                    # Need to make a new page/item
                    new_item = self.add_sub_comparison_node(comp)
                else:
                    # An old item existed, add it again
                    self.tree_item.addChild(item)
                    self.setup_any_comparison_row_buttons(
                        comparison=comp,
                        item=item,
                    )

        # Fix selection if it changed
        if not new_item:
            self.full_tree.select_by_data(pre_selected)
            return
        self.full_tree.select_by_data(comp)

    def add_sub_comparison_node(self, comparison: Comparison) -> TreeItem:
        """
        For the AnyComparison, add a sub-comparison.
        Expected to be called inside a DualTree.modfies_tree context manager
        """
        item = TreeItem(
            data=comparison,
            tree_parent=self.tree_item,
        )

        self.setup_any_comparison_row_buttons(
            comparison=comparison,
            item=item,
        )
        return item

    def replace_comparison(
        self,
        old_comparison: Comparison,
        new_comparison: Comparison,
        comp_item: TreeItem,
    ) -> None:
        """
        Find old_comparison and replace it with new_comparison.

        Also finds the row widget and replaces it with a new row widget
        via calling methods on the AnyComparison widget.

        This is only valid when our data type is AnyComparison
        """
        if not isinstance(self, AnyComparison):
            logger.warning('Expected an AnyComparison, not replacing comparison')
        self.specific_comparison_widget.replace_row_widget(
            old_comparison=old_comparison,
            new_comparison=new_comparison,
        )
        self.setup_any_comparison_row_buttons(
            comparison=new_comparison,
            item=comp_item,
        )

    def setup_any_comparison_row_buttons(
        self,
        comparison: Comparison,
        item: TreeItem,
    ) -> None:
        """
        Find the row widget and set up the buttons.

        Only valid when we use AnyComparison.

        - The child button should navigate to the child page
        - The delete button exists but should need no extra handling here
        """
        table: QTableWidget = self.specific_comparison_widget.comparisons_table
        for index in range(table.rowCount()):
            row_widget = table.cellWidget(index, 0)
            if row_widget.data is comparison:
                break
        if row_widget.data is not comparison:
            return
        self.setup_child_button(
            button=row_widget.child_button,
            item=item,
        )

    def clean_up_any_comparison(self) -> None:
        """
        Special teardown for when an AnyComparison is removed.

        - Cleans up all the sub-nodes.
        """
        self.tree_item.takeChildren()


PAGE_MAP = {
    # Passive Pages
    ConfigurationGroup: ConfigurationGroupPage,
    DeviceConfiguration: DeviceConfigurationPage,
    PVConfiguration: PVConfigurationPage,
    ToolConfiguration: ToolConfigurationPage,
    TemplateConfiguration: TemplateConfigurationPage,
    # Active Pages
    ProcedureGroup: ProcedureGroupPage,
    DescriptionStep: StepPage,
    PassiveStep: StepPage,
    SetValueStep: StepPage,
    TemplateStep: TemplateConfigurationPage,
}

# add comparison pages
for comp_type in ALL_COMPARISONS:
    PAGE_MAP[comp_type] = ComparisonPage
