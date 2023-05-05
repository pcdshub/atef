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
from collections import OrderedDict
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, Union
from weakref import WeakSet, WeakValueDictionary

from qtpy.QtGui import QDropEvent
from qtpy.QtWidgets import (QComboBox, QFrame, QMessageBox, QPushButton,
                            QSizePolicy, QStyle, QTableWidget, QToolButton,
                            QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from atef.check import (AnyComparison, AnyValue, Comparison, Equals, Greater,
                        GreaterOrEqual, Less, LessOrEqual, NotEquals, Range,
                        ValueSet)
from atef.config import (Configuration, ConfigurationGroup,
                         DeviceConfiguration, PVConfiguration,
                         ToolConfiguration)
from atef.procedure import (ComparisonToTarget, DescriptionStep, PassiveStep,
                            PreparedDescriptionStep, PreparedPassiveStep,
                            PreparedProcedureStep, PreparedSetValueStep,
                            ProcedureGroup, ProcedureStep, SetValueStep)
from atef.tools import Ping, PingResult, Tool, ToolResult
from atef.type_hints import AnyDataclass
from atef.widgets.config.data_active import (CheckRowWidget, ExpandableFrame,
                                             GeneralProcedureWidget,
                                             PassiveEditWidget,
                                             SetValueEditWidget)
from atef.widgets.config.run_active import (DescriptionRunWidget,
                                            PassiveRunWidget,
                                            SetValueRunWidget)
from atef.widgets.config.run_base import RunCheck

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
from .utils import (TableWidgetWithAddRow, cast_dataclass,
                    describe_comparison_context, describe_step_context)


def link_page(item: AtefItem, widget: PageWidget) -> None:
    """
    Link a page widget to an atef tree item.

    All linkage calls should go through here to remove ambiguity
    about ordering, etc. and so each object only has to worry about
    how to update itself.

    Parameters
    ----------
    item : AtefItem
        The tree item to link.
    widget : PageWidget
        The widget to link.
    """
    item.assign_widget(widget)
    widget.assign_tree_item(item)


def replace_in_list(old: Any, new: Any, item_list: List[Any]):
    index = item_list.index(old)
    item_list[index] = new


class AtefItem(QTreeWidgetItem):
    """
    A QTreeWidget item with some convenience methods.

    Must be assigned a page using ``link_page``.

    Parameters
    ----------
    tree_parent : AtefItem or QTreeWidget
        The node on the tree above this node.
        Passing a QTreeWidget means that this is a top-level node.
    name : str
        The text on the left column of the tree view.
    func_name : str
        The text on the right column of the tree view.
    """
    widget: Optional[PageWidget]
    parent_tree_item: QTreeWidgetItem
    full_tree: QTreeWidget

    def __init__(
        self,
        tree_parent: Union[AtefItem, QTreeWidget],
        name: str,
        func_name: Optional[str] = None,
    ):
        super().__init__()
        self.widget = None
        self.setText(0, name)
        if func_name is not None:
            self.setText(1, func_name)
        if isinstance(tree_parent, QTreeWidget):
            self.parent_tree_item = tree_parent.invisibleRootItem()
            self.full_tree = tree_parent
        else:
            self.parent_tree_item = tree_parent
            self.full_tree = tree_parent.full_tree
        self.parent_tree_item.addChild(self)

    def assign_widget(self, widget: PageWidget) -> None:
        """
        Updates this tree item with a reference to the corresponding page.

        Parameters
        ----------
        widget : PageWidget
            The page to show when this tree item is selected.
        """
        self.widget = widget

    def find_ancestor_by_widget(self, cls: Type[QWidget]) -> Optional[AtefItem]:
        """Find an ancestor widget of the given type."""
        ancestor = self.parent_tree_item
        while hasattr(ancestor, "parent_tree_item"):
            widget = getattr(ancestor, "widget", None)
            if isinstance(widget, cls):
                return ancestor
            ancestor = ancestor.parent_tree_item

        return None

    def find_ancestor_by_item(self, cls: Type[AtefItem]) -> Optional[AtefItem]:
        """Find an ancestor widget of the given type."""
        ancestor = self.parent_tree_item
        while hasattr(ancestor, "parent_tree_item"):
            if isinstance(ancestor, cls):
                return ancestor
            ancestor = ancestor.parent_tree_item

        return None


class PageWidget(QWidget):
    """
    Base class for widgets that correspond to a tree node.

    Contains utilities for navigating and manipulating
    the tree and for loading data widgets into placeholders.

    Must be linked up to the tree using the ``link_page``
    function after being instantiated, not during.
    """
    # Linkage attributes
    tree_item: AtefItem
    parent_tree_item: AtefItem
    full_tree: QTreeWidget

    # Common placeholder defined in the page ui files
    name_desc_tags_placeholder: QWidget
    name_desc_tags_widget: NameDescTagsWidget

    # Set during runtime
    data: AnyDataclass
    parent_button: Optional[QToolButton]

    def __init__(self, data: AnyDataclass, **kwargs):
        super().__init__(**kwargs)
        self.data = data
        self.parent_button = None
        self.child_button_map = WeakValueDictionary()
        self.has_connected_tree = False
        self.comp_table_setup_done = False

    def assign_tree_item(self, item: AtefItem) -> None:
        """
        Updates this page with references to the tree.

        Parameters
        ----------
        item : AtefItem
            The item that should be showing this page.
        """
        self.tree_item = item
        self.parent_tree_item = item.parent_tree_item
        self.full_tree = item.full_tree
        # Make sure we update our parent button's tooltip on tree changes
        if not self.has_connected_tree:
            self.full_tree.itemChanged.connect(
                self._update_parent_tooltip_from_tree,
            )
            self.has_connected_tree = True

    def setup_cleanup(self) -> None:
        """
        Disconnect any slots that interact with the tree widget.  These slots
        can persist on the QDataclassBridge after the tree has been deleted,
        causing RuntimeErrors.

        In general, need to disconnect slots that connect bridges to tree_items

        Should be invoked at the end of the assign_tree_item, since we want to
        prepare the cleanup only when we have finished the setup
        """
        def disconnect_name_widget():
            bridge = self.name_desc_tags_widget.bridge
            bridge.name.changed_value.disconnect(self.set_new_node_name)

        self.full_tree.destroyed.connect(disconnect_name_widget)

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
            f"{nav_parent.text(0)} "
            f"({nav_parent.text(1)})"
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

    def setup_child_button(self, button: QToolButton, item: AtefItem) -> None:
        """
        Set up a button's style and make it navigate to a specific child page.
        """
        # Create a function that navigates to the right page
        def inner_navigate(*args, **kwargs):
            self.navigate_to(item)

        # Make the button work
        button.clicked.connect(inner_navigate)
        # Add the appropriate symbol
        icon = self.style().standardIcon(QStyle.SP_ArrowRight)
        button.setIcon(icon)
        # Make sure the tooltip is helpful
        button.setToolTip(
            f"Navigate to child {item.text(1)}"
        )

    def navigate_to(self, item: AtefItem, *args, **kwargs) -> None:
        """
        Make the tree switch to a specific item.

        This can be used to navigate to child items, for example.

        Parameters
        ----------
        item : AtefItem
            The tree node to navigate to.
        """
        self.full_tree.setCurrentItem(item)

    def navigate_to_parent(self, *args, **kwargs) -> None:
        """
        Make the tree switch to this widget's parent in the tree.
        """
        self.navigate_to(self.get_nav_parent())

    def get_nav_parent(self) -> AtefItem:
        """
        Get the navigation parent target item.

        This is self.parent_tree_item normally except when we are
        a top-level item, in which case the target should be the
        overview widget because otherwise there isn't any parent
        to navigate to.
        """
        if isinstance(self.parent_tree_item, AtefItem):
            return self.parent_tree_item
        else:
            return self.full_tree.topLevelItem(0)

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

    def connect_tree_node_name(self, widget: DataWidget) -> None:
        """
        Helper function for causing the tree name to update when name updates.
        """
        widget.bridge.name.changed_value.connect(self.set_new_node_name)

    def set_new_node_name(self, name: str) -> None:
        """
        Change the name of our node in the tree widget.
        """
        self.tree_item.setText(0, name)

    def setup_delete_row_button(
        self,
        table: QTableWidget,
        item: AtefItem,
        row: DataWidget,
    ) -> None:
        """
        Configure a row's delete button to delete itself.
        """
        delete_icon = self.style().standardIcon(QStyle.SP_TitleBarCloseButton)
        row.delete_button.setIcon(delete_icon)

        def inner_delete(*args, **kwargs):
            self.delete_table_row(
                table=table,
                row=row,
                item=item,
            )

        row.delete_button.clicked.connect(inner_delete)

    def delete_table_row(
        self,
        table: QTableWidget,
        item: AtefItem,
        row: DataWidget,
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
                f'{item.text(1)} named "{item.text(0)}"? '
                'Note that this will delete any child nodes in the tree.'
            ),
        )
        if reply != QMessageBox.Yes:
            return
        # Get the identity of the data
        data = row.bridge.data
        # Remove item from the tree
        self.tree_item.removeChild(item)
        # Remove row from the table
        for row_index in range(table.rowCount()):
            widget = table.cellWidget(row_index, 0)
            if widget is row:
                table.removeRow(row_index)
                break
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
        item: AtefItem,
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
        item : AtefItem
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

        Run this after super().assign_tree_item in a PageWidget subclass
        to initialize the header if the page has a name/desc/tags widget.
        """
        self.setup_parent_button(self.name_desc_tags_widget.parent_button)
        self.connect_tree_node_name(self.name_desc_tags_widget)

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
                    )
            for config in self.data.shared:
                self.add_comparison_row(
                    attr='shared',
                    comparison=config,
                )
            # Allow the user to add more rows
            self.add_comparison_button.clicked.connect(self.add_comparison_row)
            if data_widget is not None:
                # When the attrs update, update the allowed attrs in each row
                getattr(data_widget.bridge, by_attr_key).updated.connect(
                    self.update_combo_attrs
                )
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

    def update_comparison_dicts(self) -> None:
        """
        Rebuild by_attr/by_pv and shared when user changes anything.
        """
        raise NotImplementedError()


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

    def assign_tree_item(self, item: AtefItem) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().assign_tree_item(item)
        if not self.setup_done:
            # Fill in the rows from the initial data
            for config in self.data.configs:
                self.add_config_row(config=config)
            self.setup_done = True
        self.setup_name_desc_tags_link()
        self.setup_cleanup()

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
        config_page = PAGE_MAP[type(config)](data=config)
        config_item = AtefItem(
            tree_parent=self.tree_item,
            name=config.name or 'untitled',
            func_name=type(config).__name__,
        )
        link_page(item=config_item, widget=config_page)
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


class DeviceConfigurationPage(DesignerDisplay, PageWidget):
    """
    Page that handles all components of a DeviceConfiguration.
    """
    filename = 'device_configuration_page.ui'

    device_widget_placeholder: QWidget
    device_config_widget: DeviceConfigurationWidget

    comparisons_table: QTableWidget
    add_comparison_button: QPushButton

    attr_selector_cache: WeakSet[QComboBox]

    def __init__(self, data: DeviceConfiguration, **kwargs):
        super().__init__(data=data, **kwargs)
        # Create the static sub-widgets and place them
        self.attr_selector_cache = WeakSet()
        self.setup_name_desc_tags_init()
        self.device_config_widget = DeviceConfigurationWidget(data=data)
        self.insert_widget(
            self.device_config_widget,
            self.device_widget_placeholder,
        )

    def assign_tree_item(self, item: AtefItem) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().assign_tree_item(item)
        self.setup_comparison_table_link(
            by_attr_key='by_attr',
            data_widget=self.device_config_widget,
        )
        self.setup_name_desc_tags_link()
        self.setup_cleanup()

    def add_comparison_row(
        self,
        checked: bool = False,
        attr: str = '',
        comparison: Optional[Comparison] = None,
    ) -> None:
        """
        Add a new row to the comparison table.

        See PageWidget for full docstring.
        """
        if comparison is None:
            # New comparison
            comparison = Equals(name='untitled')
            self.data.shared.append(comparison)
        comp_row = ComparisonRowWidget(data=comparison)
        comp_page = ComparisonPage(data=comparison)
        comp_item = AtefItem(
            tree_parent=self.tree_item,
            name=comparison.name or 'untitled',
            func_name=type(comparison).__name__,
        )
        link_page(item=comp_item, widget=comp_page)
        self.setup_row_buttons(
            row_widget=comp_row,
            item=comp_item,
            table=self.comparisons_table,
        )
        self.attr_selector_cache.add(comp_row.attr_combo)
        comp_row.attr_combo.activated.connect(self.update_comparison_dicts)
        self.update_combo_attrs()
        comp_row.attr_combo.setCurrentText(attr)
        row_count = self.comparisons_table.rowCount()
        self.comparisons_table.insertRow(row_count)
        self.comparisons_table.setRowHeight(row_count, comp_row.sizeHint().height())
        self.comparisons_table.setCellWidget(row_count, 0, comp_row)

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

    def update_combo_attrs(self) -> None:
        """
        For every row combobox, set the allowed values.
        """
        for combo in self.attr_selector_cache:
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

    def update_comparison_dicts(self, *args, **kwargs) -> None:
        """
        Rebuild by_attr and shared when user changes anything
        """
        unsorted: List[Tuple[str, Comparison]] = []

        for row_index in range(self.comparisons_table.rowCount()):
            row_widget = self.comparisons_table.cellWidget(row_index, 0)
            unsorted.append(
                (row_widget.attr_combo.currentText(), row_widget.data)
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

    def replace_comparison(
        self,
        old_comparison: Comparison,
        new_comparison: Comparison,
        comp_item: AtefItem,
    ) -> None:
        """
        Find old_comparison and replace it with new_comparison.

        Also finds the row widget and replaces it with a new row widget.
        """
        try:
            replace_in_list(
                old=old_comparison,
                new=new_comparison,
                item_list=self.data.shared,
            )
        except ValueError:
            for comp_list in self.data.by_attr.values():
                try:
                    replace_in_list(
                        old=old_comparison,
                        new=new_comparison,
                        item_list=comp_list,
                    )
                except ValueError:
                    continue
                else:
                    break

        found_row = None
        prev_attr_index = 0
        for row_index in range(self.comparisons_table.rowCount()):
            widget = self.comparisons_table.cellWidget(row_index, 0)
            if widget.data is old_comparison:
                found_row = row_index
                prev_attr_index = widget.attr_combo.currentIndex()
                break
        if found_row is None:
            return
        comp_row = ComparisonRowWidget(data=new_comparison)
        self.setup_row_buttons(
            row_widget=comp_row,
            item=comp_item,
            table=self.comparisons_table,
        )
        self.attr_selector_cache.add(comp_row.attr_combo)
        comp_row.attr_combo.activated.connect(self.update_comparison_dicts)
        self.comparisons_table.setCellWidget(found_row, 0, comp_row)
        self.update_combo_attrs()
        comp_row.attr_combo.setCurrentIndex(prev_attr_index)


class PVConfigurationPage(DesignerDisplay, PageWidget):
    """
    Page that handles all components of a PVConfiguration.
    """
    filename = 'pv_configuration_page.ui'

    pv_widget_placeholder: QWidget
    pv_configuration_widget: PVConfigurationWidget

    comparisons_table: QTableWidget
    add_comparison_button: QPushButton

    attr_selector_cache: WeakSet[QComboBox]

    def __init__(self, data: PVConfiguration, **kwargs):
        super().__init__(data=data, **kwargs)
        # Create the static sub-widgets and place them
        self.attr_selector_cache = WeakSet()
        self.setup_name_desc_tags_init()
        self.pv_configuration_widget = PVConfigurationWidget(data=data)
        self.insert_widget(
            self.pv_configuration_widget,
            self.pv_widget_placeholder,
        )

    def assign_tree_item(self, item: AtefItem) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().assign_tree_item(item)
        self.setup_comparison_table_link(
            by_attr_key='by_pv',
            data_widget=self.pv_configuration_widget,
        )
        self.setup_name_desc_tags_link()
        self.setup_cleanup()

    def add_comparison_row(
        self,
        checked: bool = False,
        attr: str = '',
        comparison: Optional[Comparison] = None,
    ):
        """
        Add a new row to the comparison table.

        See PageWidget for full docstring.
        """
        if comparison is None:
            # New comparison
            comparison = Equals(name='untitled')
            self.data.shared.append(comparison)
        comp_row = ComparisonRowWidget(data=comparison)
        comp_page = ComparisonPage(data=comparison)
        comp_item = AtefItem(
            tree_parent=self.tree_item,
            name=comparison.name or 'untitled',
            func_name=type(comparison).__name__,
        )
        link_page(item=comp_item, widget=comp_page)
        self.setup_row_buttons(
            row_widget=comp_row,
            item=comp_item,
            table=self.comparisons_table,
        )
        self.attr_selector_cache.add(comp_row.attr_combo)
        comp_row.attr_combo.activated.connect(self.update_comparison_dicts)
        self.update_combo_attrs()
        comp_row.attr_combo.setCurrentText(attr)
        row_count = self.comparisons_table.rowCount()
        self.comparisons_table.insertRow(row_count)
        self.comparisons_table.setRowHeight(row_count, comp_row.sizeHint().height())
        self.comparisons_table.setCellWidget(row_count, 0, comp_row)

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

    def update_combo_attrs(self) -> None:
        """
        For every row combobox, set the allowed values.
        """
        for combo in self.attr_selector_cache:
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

    def update_comparison_dicts(self, *args, **kwargs) -> None:
        """
        Rebuild by_attr and shared when user changes anything
        """
        unsorted: List[Tuple[str, Comparison]] = []

        for row_index in range(self.comparisons_table.rowCount()):
            row_widget = self.comparisons_table.cellWidget(row_index, 0)
            unsorted.append(
                (row_widget.attr_combo.currentText(), row_widget.data)
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

    def replace_comparison(
        self,
        old_comparison: Comparison,
        new_comparison: Comparison,
        comp_item: AtefItem,
    ) -> None:
        """
        Find old_comparison and replace it with new_comparison.

        Also finds the row widget and replaces it with a new row widget.
        """
        try:
            replace_in_list(
                old=old_comparison,
                new=new_comparison,
                item_list=self.data.shared,
            )
        except ValueError:
            for comp_list in self.data.by_pv.values():
                try:
                    replace_in_list(
                        old=old_comparison,
                        new=new_comparison,
                        item_list=comp_list,
                    )
                except ValueError:
                    continue
                else:
                    break

        found_row = None
        prev_attr_index = 0
        for row_index in range(self.comparisons_table.rowCount()):
            widget = self.comparisons_table.cellWidget(row_index, 0)
            if widget.data is old_comparison:
                found_row = row_index
                prev_attr_index = widget.attr_combo.currentIndex()
                break
        if found_row is None:
            return
        comp_row = ComparisonRowWidget(data=new_comparison)
        self.setup_row_buttons(
            row_widget=comp_row,
            item=comp_item,
            table=self.comparisons_table,
        )
        self.attr_selector_cache.add(comp_row.attr_combo)
        comp_row.attr_combo.activated.connect(self.update_comparison_dicts)
        self.comparisons_table.setCellWidget(found_row, 0, comp_row)
        self.update_combo_attrs()
        comp_row.attr_combo.setCurrentIndex(prev_attr_index)


class ToolConfigurationPage(DesignerDisplay, PageWidget):
    """
    Page that handles all components of a ToolConfiguration.

    Currently this is just the "Ping" tool but other tools
    can be added.
    """
    filename = 'tool_configuration_page.ui'

    tool_placeholder: QWidget
    tool_widget: DataWidget

    comparisons_table: QTableWidget
    add_comparison_button: QPushButton
    tool_select_combo: QComboBox

    attr_selector_cache: WeakSet[QComboBox]

    # Defines the valid tools, their result structs, and edit widgets
    tool_map: ClassVar[Dict[Type[Tool], Tuple[Type[ToolResult], Type[DataWidget]]]] = {
        Ping: (PingResult, PingWidget),
    }
    tool_names: Dict[str, Type[Tool]]

    def __init__(self, data: ToolConfiguration, **kwargs):
        super().__init__(data=data, **kwargs)
        # Create the static sub-widgets and place them
        self.attr_selector_cache = WeakSet()
        self.setup_name_desc_tags_init()

    def assign_tree_item(self, item: AtefItem) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().assign_tree_item(item)
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
        self.setup_name_desc_tags_link()
        self.setup_cleanup()

    def add_comparison_row(
        self,
        checked: bool = False,
        attr: str = '',
        comparison: Optional[Comparison] = None,
    ) -> None:
        """
        Add a new row to the comparison table.

        See PageWidget for the full docstring.
        """
        if comparison is None:
            # New comparison
            comparison = Equals(name='untitled')
            self.data.shared.append(comparison)
        comp_row = ComparisonRowWidget(data=comparison)
        comp_page = ComparisonPage(data=comparison)
        comp_item = AtefItem(
            tree_parent=self.tree_item,
            name=comparison.name or 'untitled',
            func_name=type(comparison).__name__,
        )
        link_page(item=comp_item, widget=comp_page)
        self.setup_row_buttons(
            row_widget=comp_row,
            item=comp_item,
            table=self.comparisons_table,
        )
        self.attr_selector_cache.add(comp_row.attr_combo)
        comp_row.attr_combo.activated.connect(self.update_comparison_dicts)
        self.update_combo_attrs()
        comp_row.attr_combo.setCurrentText(attr)
        row_count = self.comparisons_table.rowCount()
        self.comparisons_table.insertRow(row_count)
        self.comparisons_table.setRowHeight(row_count, comp_row.sizeHint().height())
        self.comparisons_table.setCellWidget(row_count, 0, comp_row)

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

    def update_combo_attrs(self) -> None:
        """
        For every row combobox, set the allowed values.
        """
        for combo in self.attr_selector_cache:
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

    def update_comparison_dicts(self, *args, **kwargs) -> None:
        """
        Rebuild by_attr and shared when user changes anything
        """
        unsorted: List[Tuple[str, Comparison]] = []

        for row_index in range(self.comparisons_table.rowCount()):
            row_widget = self.comparisons_table.cellWidget(row_index, 0)
            unsorted.append(
                (row_widget.attr_combo.currentText(), row_widget.data)
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
        comp_item: AtefItem,
    ) -> None:
        """
        Find old_comparison and replace it with new_comparison.

        Also finds the row widget and replaces it with a new row widget.
        """
        try:
            replace_in_list(
                old=old_comparison,
                new=new_comparison,
                item_list=self.data.shared,
            )
        except ValueError:
            for comp_list in self.data.by_attr.values():
                try:
                    replace_in_list(
                        old=old_comparison,
                        new=new_comparison,
                        item_list=comp_list,
                    )
                except ValueError:
                    continue
                else:
                    break

        found_row = None
        prev_attr_index = 0
        for row_index in range(self.comparisons_table.rowCount()):
            widget = self.comparisons_table.cellWidget(row_index, 0)
            if widget.data is old_comparison:
                found_row = row_index
                prev_attr_index = widget.attr_combo.currentIndex()
                break
        if found_row is None:
            return
        comp_row = ComparisonRowWidget(data=new_comparison)
        self.setup_row_buttons(
            row_widget=comp_row,
            item=comp_item,
            table=self.comparisons_table,
        )
        self.attr_selector_cache.add(comp_row.attr_combo)
        comp_row.attr_combo.activated.connect(self.update_comparison_dicts)
        self.comparisons_table.setCellWidget(found_row, 0, comp_row)
        self.update_combo_attrs()
        comp_row.attr_combo.setCurrentIndex(prev_attr_index)

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
        # Update the selection choices to match the tool
        self.update_combo_attrs()

    def new_tool_selected(self, tool_name: str) -> None:
        """
        Slot for when the user selects a new tool type from the combo box.
        """
        tool_type = self.tool_names[tool_name]
        if isinstance(self.data.tool, tool_type):
            return
        new_tool = tool_type()
        self.new_tool_widget(new_tool)


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
            SetValueStep
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

    def assign_tree_item(self, item: AtefItem) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().assign_tree_item(item)
        if not self.setup_done:
            # Fill in the rows from the initial data
            for config in self.data.steps:
                self.add_config_row(config=config)
            self.setup_done = True
        self.setup_name_desc_tags_link()
        self.setup_cleanup()

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
        config_page = PAGE_MAP[type(config)](data=config)
        config_item = AtefItem(
            tree_parent=self.tree_item,
            name=config.name or 'untitled',
            func_name=type(config).__name__,
        )
        link_page(item=config_item, widget=config_page)
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
        comp_item: AtefItem
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
        comp_item : AtefItem
            AtefItem holding the old comparison and widget
        """
        replace_in_list(old_step, new_step, self.data.steps)

        # go through rows
        found_row = None
        for row_index in range(self.procedure_table.rowCount()):
            widget = self.procedure_table.cellWidget(row_index, 0)
            if widget.data is old_step:
                found_row = row_index
                break
        if found_row is None:
            return

        step_row = ComparisonRowWidget(data=new_step)
        self.setup_row_buttons(
            row_widget=step_row,
            item=comp_item,
            table=self.procedure_table,
        )
        self.procedure_table.setCellWidget(found_row, 0, step_row)


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
        SetValueStep: SetValueEditWidget
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

    def assign_tree_item(self, item: AtefItem) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().assign_tree_item(item)
        self.setup_name_desc_tags_link()

        # extra setup for SetValueStep.  Reminiscent of AnyComparison
        if isinstance(self.data, SetValueStep):
            self.setup_set_value_step()
        self.setup_cleanup()

    def new_step(self, step: ProcedureStep) -> None:
        """
        Set up the widgets for a new step and save it as self.data.

        ComparisonPage is unique in that the comparison can be swapped out
        while the page is loaded. This method doesn't handle the complexity
        of how to manage this in the Configuration instance, but it does
        make sure all the widgets on this page connect to the new
        comparison.

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
        try:
            item = self.tree_item
        except AttributeError:
            pass
        else:
            # Reinitialize this for the new name/desc/tags widget
            self.assign_tree_item(item)

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
        self.parent_tree_item.widget.replace_step(
            old_step=self.data,
            new_step=step,
            comp_item=self.tree_item,
        )
        self.tree_item.setText(1, new_type.__name__)
        self.new_step(step=step)
        self.update_context()

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
        parent_widget = self.parent_tree_item.widget
        if isinstance(parent_widget, StepPage):
            parent_widget.update_context()
            self.name_desc_tags_widget.extra_text_label.setText(
                parent_widget.name_desc_tags_widget.extra_text_label.text()
            )
            return
        config = self.parent_tree_item.widget.data
        attr = ''

        desc = describe_step_context(attr=attr, step=config)
        self.name_desc_tags_widget.extra_text_label.setText(desc)
        self.name_desc_tags_widget.extra_text_label.setToolTip(desc)
        self.name_desc_tags_widget.init_viewer(attr, config)

    def setup_set_value_step(self) -> None:
        self.update_subpages()
        self.specific_procedure_widget.bridge.success_criteria.updated.connect(
            self.update_subpages
        )

    def setup_cleanup(self):
        super().setup_cleanup()
        if isinstance(self.data, SetValueStep):
            def disconnect_subpages():
                bridge = self.specific_procedure_widget.bridge
                bridge.success_criteria.updated.disconnect(
                    self.update_subpages
                )

            # disconnect all bridge-related signals
            self.full_tree.destroyed.connect(disconnect_subpages)

    def update_subpages(self) -> None:
        """
        Update nodes based on the current SetValueStep state.

        This may add or remove pages as appropriate.

        The node order should match the sequence in the table
        """
        # Cache the previous selection
        pre_selected = self.full_tree.selectedItems()
        display_order = OrderedDict()
        table = self.specific_procedure_widget.checks_table
        for row_index in range(table.rowCount()):
            widget = table.cellWidget(row_index, 0)
            comp = widget.data.comparison
            display_order[id(comp)] = comp
        # Pull off all of the existing items
        old_items = self.tree_item.takeChildren()
        old_item_map = {
            id(item.widget.data): item for item in old_items
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
        # Fix selection if it changed
        post_selected = self.full_tree.selectedItems()
        if (
            new_item is not None
            and pre_selected
            and post_selected
            and pre_selected[0] is not post_selected[0]
        ):
            # Selection normal and changed, usually the new item
            self.full_tree.setCurrentItem(new_item)

    def add_sub_comparison_node(self, comparison: Comparison) -> AtefItem:
        """
        For the AnyComparison, add a sub-comparison.
        """
        page = ComparisonPage(data=comparison)
        item = AtefItem(
            tree_parent=self.tree_item,
            name=comparison.name,
            func_name=type(comparison).__name__,
        )
        link_page(item=item, widget=page)
        self.setup_set_value_check_row_buttons(
            comparison=comparison,
            item=item,
        )
        return item

    def setup_set_value_check_row_buttons(
        self,
        comparison: Comparison,
        item: AtefItem
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
        # item.widget will be a ComparisonPage
        desc_update_slot = self.specific_procedure_widget.update_all_desc
        comp_page_widget = item.widget.specific_comparison_widget
        # subscribe to the relevant comparison signals
        for field in ('value', 'low', 'high', 'description'):
            if hasattr(comp_page_widget.bridge, field):
                getattr(comp_page_widget.bridge, field).changed_value.connect(
                    desc_update_slot
                )

    def replace_comparison(
        self,
        old_comparison: Comparison,
        new_comparison: Comparison,
        comp_item: AtefItem,
    ) -> None:
        """
        Find old_comparison and replace it with new_comparison.

        Also finds the row widget and replaces it with a new row widget.
        """
        if isinstance(self.specific_procedure_widget, SetValueEditWidget):
            table: TableWidgetWithAddRow = self.specific_procedure_widget.checks_table
            row_widget_cls = CheckRowWidget
            row_data_cls = ComparisonToTarget
            data_list = self.data.success_criteria
            comp_list = [comptotarget.comparison for comptotarget in data_list]
            index = comp_list.index(old_comparison)

        found_row = None
        for row_index in range(table.rowCount()):
            widget = table.cellWidget(row_index, 0)
            if widget.data.comparison is old_comparison:
                found_row = row_index
                break
        if found_row is None:
            return

        # replace in dataclass
        new_data = row_data_cls(comparison=new_comparison)
        data_list[index] = new_data
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
    }

    def __init__(self, *args, data, **kwargs):
        super().__init__(*args, data, **kwargs)
        self.run_check = RunCheck(data=[data])
        self.insert_widget(self.run_check, self.run_check_placeholder)
        # gather run_widget
        run_widget_cls = self.run_widget_map[type(data)]
        self.run_widget = run_widget_cls(data=data)

        self.insert_widget(self.run_widget, self.run_widget_placeholder)

        if isinstance(data, PreparedPassiveStep):
            self.run_check.run_button.clicked.connect(self.run_widget.run_config)
        elif isinstance(data, PreparedSetValueStep):
            self.run_check.busy_thread.task_finished.connect(
                self.run_widget.update_statuses
            )

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


PAGE_MAP = {
    # Passive Pages
    ConfigurationGroup: ConfigurationGroupPage,
    DeviceConfiguration: DeviceConfigurationPage,
    PVConfiguration: PVConfigurationPage,
    ToolConfiguration: ToolConfigurationPage,
    # Active Pages
    ProcedureGroup: ProcedureGroupPage,
    DescriptionStep: StepPage,
    PassiveStep: StepPage,
    SetValueStep: StepPage
}


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
        self.new_comparison(comparison=data)
        self.specific_combo.activated.connect(self.select_comparison_type)

    def assign_tree_item(self, item: AtefItem) -> None:
        """
        Link-time setup of existing sub-nodes and navigation.
        """
        super().assign_tree_item(item)
        self.setup_name_desc_tags_link()
        # Extra setup and/or teardown from AnyComparison
        self.clean_up_any_comparison()
        if isinstance(self.data, AnyComparison):
            self.setup_any_comparison()

        self.setup_cleanup()

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
        try:
            item = self.tree_item
        except AttributeError:
            pass
        else:
            # Reinitialize this for the new name/desc/tags widget
            self.assign_tree_item(item)
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
        self.parent_tree_item.widget.replace_comparison(
            old_comparison=self.data,
            new_comparison=comparison,
            comp_item=self.tree_item,
        )
        self.tree_item.setText(1, new_type.__name__)
        self.new_comparison(comparison=comparison)
        self.update_context()

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
        parent_widget = self.parent_tree_item.widget
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

        config = self.parent_tree_item.widget.data
        attr = ''
        if self.data in config.shared:
            attr = 'shared'
        else:
            try:
                attr_dict = config.by_attr
            except AttributeError:
                attr_dict = config.by_pv
            for attr_name, comparisons in attr_dict.items():
                if self.data in comparisons:
                    attr = attr_name
                    break
        desc = describe_comparison_context(attr=attr, config=config)
        self.name_desc_tags_widget.extra_text_label.setText(desc)
        self.name_desc_tags_widget.extra_text_label.setToolTip(desc)
        self.name_desc_tags_widget.init_viewer(attr, config)

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
        pre_selected = self.full_tree.selectedItems()
        display_order = OrderedDict()
        table = self.specific_comparison_widget.comparisons_table
        for row_index in range(table.rowCount()):
            widget = table.cellWidget(row_index, 0)
            comp = widget.data
            display_order[id(comp)] = comp
        # Pull off all of the existing items
        old_items = self.tree_item.takeChildren()
        old_item_map = {
            id(item.widget.data): item for item in old_items
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
        # Fix selection if it changed
        post_selected = self.full_tree.selectedItems()
        if (
            new_item is not None
            and pre_selected
            and post_selected
            and pre_selected[0] is not post_selected[0]
        ):
            # Selection normal and changed, usually the new item
            self.full_tree.setCurrentItem(new_item)

    def add_sub_comparison_node(self, comparison: Comparison) -> AtefItem:
        """
        For the AnyComparison, add a sub-comparison.
        """
        page = ComparisonPage(data=comparison)
        item = AtefItem(
            tree_parent=self.tree_item,
            name=comparison.name,
            func_name=type(comparison).__name__,
        )
        link_page(item=item, widget=page)
        self.setup_any_comparison_row_buttons(
            comparison=comparison,
            item=item,
        )
        return item

    def replace_comparison(
        self,
        old_comparison: Comparison,
        new_comparison: Comparison,
        comp_item: AtefItem,
    ) -> None:
        """
        Find old_comparison and replace it with new_comparison.

        Also finds the row widget and replaces it with a new row widget
        via calling methods on the AnyComparison widget.

        This is only valid when our data type is AnyComparison
        """
        replace_in_list(
            old=old_comparison,
            new=new_comparison,
            item_list=self.data.comparisons,
        )
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
        item: AtefItem,
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
