from __future__ import annotations

import enum
import logging
import re
from functools import partial
from typing import (Any, Callable, ClassVar, Dict, List, Optional, Union,
                    get_args, get_origin)

from bluesky_queueserver.manager.profile_ops import construct_parameters
from qtpy import QtCore, QtWidgets
from qtpy.QtCore import Signal as QSignal

from atef import util
from atef.widgets.core import DesignerDisplay
from atef.widgets.happi import HappiDeviceComponentWidget
from atef.widgets.ophyd import OphydAttributeData
from atef.widgets.utils import ExpandableFrame, insert_widget

logger = logging.getLogger(__file__)


class PlanEntryWidget(DesignerDisplay, QtWidgets.QWidget):
    """holds many ArgumentEntryWidget's and supplies a plan item"""

    filename = 'plan_entry_widget.ui'

    args_layout: QtWidgets.QVBoxLayout
    optional_args_frame: QtWidgets.QFrame

    def __init__(self, *args, plan: Dict[str, Any], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.plan_info = plan
        self.arg_widgets = {}
        parameters = construct_parameters(plan['parameters'])

        # setup optional args frame
        optional_frame = ExpandableFrame(text='Optional Arguments')
        optional_widget = QtWidgets.QWidget()
        self.optional_layout = QtWidgets.QVBoxLayout()
        optional_widget.setLayout(self.optional_layout)
        optional_frame.add_widget(optional_widget)
        insert_widget(optional_frame, self.optional_args_frame)

        # gather both the annotation from the parameter and
        # original text annotation
        for param, info in zip(parameters, self.plan_info['parameters']):
            anno = info.get('annotation')
            if anno:
                anno = anno['type']
            arg_widget = ArgumentEntryWidget.from_hint_info(
                param.annotation, anno, name=info['name']
            )
            if arg_widget.is_optional:
                self.optional_layout.addWidget(arg_widget)
            else:
                self.args_layout.addWidget(arg_widget)

            if isinstance(arg_widget, ArgumentEntryWidget):
                self.arg_widgets[param.name] = arg_widget

        # TODO: add optional metadata in expandable section?

    def plan_item(self) -> Dict[str, Any]:
        """ Returns a plan item in the form {...}"""
        plan_item = {'name': self.plan_info['name'], 'args': [], 'kwargs': {},
                     'user_group': 'root'}

        # TODO: consider required arguments that don't get values?  leave to validation?
        for param_info, arg_name in zip(self.plan_info['parameters'], self.arg_widgets):
            value = self.arg_widgets[arg_name].value()
            if param_info['kind']['name'] == 'KEYWORD_ONLY':
                plan_item['kwargs'][arg_name] = value
            elif param_info['kind']['name'] == 'POSITIONAL_OR_KEYWORD':
                plan_item['args'].append(value)

        return plan_item


class ArgumentEntryWidget(QtWidgets.QWidget):
    """Base class for various input widgets? needed?"""
    # `info` is expected to be dictionary containing parameter info keys:
    # - "kind": positional or keyword, etc
    # - "description": a text description
    # - "annotaiton": a dictionary itself, with a type annotation

    # .value(): giving plan - compatible value
    # .arg_label: arg name
    # arg_changed: QSignal
    arg_label: QtWidgets.QLabel
    arg_changed: ClassVar[QtCore.Signal] = QSignal()

    def __init__(self, *args, name: Optional[str] = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.name = name
        self.is_optional = False

        self.hlayout = QtWidgets.QHBoxLayout()
        self.setLayout(self.hlayout)

        if self.name:
            self.arg_label = QtWidgets.QLabel()
            self.arg_label.setText(self.name)
            self.hlayout.addWidget(self.arg_label)

        # TODO: set description as tooltip?

    def value(self) -> Any:
        """Return the entered value as string."""
        raise NotImplementedError

    @classmethod
    def from_hint_info(
        cls,
        type_hint: Any,
        annotation: str,
        name: Optional[str] = None
    ) -> ArgumentEntryWidget:
        """ Create the appropriate arg widget given the type hint and info"""
        # simple cases
        if type_hint is Any:
            # no annotation, just use a simple text edit
            return BasicArgEdit(arg_type=str, name=name)
        elif type_hint in (int, str, float):
            return BasicArgEdit(arg_type=type_hint, name=name)
        elif annotation is None:
            return BasicArgEdit(arg_type=str, name=name)

        # more complicated cases
        origin = get_origin(type_hint)
        args = get_args(type_hint)

        if origin is list:
            match = re.search(r'^typing.List\[(.*)\]$', annotation)
            return ListArgWidget.from_hint_info(
                type_hint=args[0], annotation=match[1], name=name
            )
        elif (origin is Union) and (type(None) not in args):
            match = re.search(r'^typing.Union\[(.*)\]$', annotation)
            return LabelArgWidget(
                name=name,
                label_text=f'Argument type not supported: ({type_hint})'
            )

        elif (origin is Union) and (type(None) in args):
            # handle optional case
            match = re.search(r'^typing.Optional\[(.*)\]$', annotation)
            widget = ArgumentEntryWidget.from_hint_info(
                type_hint=args[0], annotation=match[1], name=name
            )
            widget.is_optional = True
            return widget

        elif origin is dict:
            return DictArgWidget.from_hint_info(
                type_hint=args, annotation=annotation, name=name
            )

        # Currently don't support unions
        return LabelArgWidget(
            name=name,
            label_text=f'Could not identify argument type {type_hint}'
        )

# Default is str, just leave as str, let qserver construct plan
# Read enum from annotation first


class BasicArgEdit(ArgumentEntryWidget):

    edit_widget: QtWidgets.QWidget

    def __init__(self, *args, arg_type: Any, name: Optional[str] = None, **kwargs) -> None:
        super().__init__(*args, name=name, **kwargs)
        if arg_type is int:
            self.edit_widget = QtWidgets.QSpinBox()
            self.edit_widget.setRange(-2147483647, 2147483647)
        elif arg_type is float:
            self.edit_widget = QtWidgets.QDoubleSpinBox()
            self.edit_widget.setRange(-2147483647, 2147483647)
        elif arg_type is str:
            self.edit_widget = QtWidgets.QLineEdit()
        else:
            raise RuntimeError(f'invalid type provided {arg_type}')

        self.hlayout.addWidget(self.edit_widget)

    def value(self) -> str:
        if isinstance(self.edit_widget, QtWidgets.QAbstractSpinBox):
            return self.edit_widget.value()
        elif isinstance(self.edit_widget, QtWidgets.QLineEdit):
            return self.edit_widget.text()


class DeviceChoiceWidget(ArgumentEntryWidget):
    """For arguments that require devices"""
    _search_widget: Optional[HappiDeviceComponentWidget] = None
    combo_box: Optional[QtWidgets.QComboBox] = None
    signal_button: Optional[QtWidgets.QPushButton] = None

    DEFAULT_TEXT = 'select a device'

    def __init__(self, *args, arg_type: Optional[Any] = None, name: Optional[str] = None, **kwargs) -> None:
        super().__init__(*args, name=name, **kwargs)
        self._device = None
        self._signal_attr = None

        if isinstance(arg_type, enum.EnumMeta):
            # set up a combo box with given values
            self.combo_box = QtWidgets.QComboBox()
            for name in [en.value for en in arg_type]:
                self.combo_box.addItem(name)

            # TODO: Connect signal to arg_changed ?
            self.hlayout.addWidget(self.combo_box)

        else:
            # No pre-determined values, pick from general devices
            self.signal_button = QtWidgets.QPushButton(self.DEFAULT_TEXT)
            self.signal_button.clicked.connect(self.pick_signal)
            self.hlayout.addWidget(self.signal_button)

    def pick_signal(self) -> None:
        """
        Slot for signal_button.  Opens the HappiDeviceComponentWidget and
        configures it to send the signal selection to this widget
        """
        if self._search_widget is None:
            widget = HappiDeviceComponentWidget(
                client=util.get_happi_client(), parent=self
            )

            # clear previous cache state
            self._device = None
            self._signal_attr = None
            # look at connecting widget.attributes_selected -> List[OphydAttributeData]
            widget.item_search_widget.happi_items_chosen.connect(self.set_device)
            widget.device_widget.attributes_selected.connect(self.set_signal)

            # prevent multiple selection
            self._search_widget: QtWidgets.QWidget = widget

        self._search_widget.show()
        self._search_widget.activateWindow()
        self._search_widget.setWindowState(QtCore.Qt.WindowActive)

    def set_device(self, device_selected: List[str]) -> None:
        """
        Slot to be connected to
        HappiDeviceComponentWidget.item_search_widget.happi_items_chosen.
        Can only take the first device in the list received
        """
        dev = device_selected[0]
        logger.debug(f'found device: {dev}')
        self._device = dev
        self.signal_button.setText(self._device)

    def set_signal(self, attr_selected: List[OphydAttributeData]) -> None:
        """
        Slot to be connected to
        HappiDeviceComponentWidget.device_widget.attributes_selected.
        """
        attr = attr_selected[0]
        if self._device is None:
            # simulate click on device to store device name
            self._search_widget.item_search_widget.button_choose.clicked.emit()
        logger.debug(f'found attr: {attr}')
        self._signal_attr = attr.attr
        self.signal_button.setText(f'{self._device}.{self._signal_attr}')

    def value(self) -> str:
        if self.combo_box:
            device_name = self.combo_box.currentText()
        elif self.signal_button:
            device_name = self.signal_button.text()
        else:
            raise RuntimeError('no entry widgets available')

        if device_name == self.DEFAULT_TEXT:
            return None

        return device_name


class CloseEmitWidget(QtWidgets.QWidget):

    widget_closed: ClassVar[QtCore.Signal] = QSignal()

    def closeEvent(self, *args, **kwargs):
        self.widget_closed.emit()
        super().closeEvent(*args, **kwargs)


class ListArgWidget(ArgumentEntryWidget):
    """A table view widget that holds other ArgumentEntryWidget's"""

    def __init__(
        self,
        *args,
        widget_factory: Callable[[], ArgumentEntryWidget],
        name: Optional[str] = None,
        compact: bool = False,
        **kwargs
    ) -> None:
        super().__init__(*args, name=name, **kwargs)
        self.widget_factory = widget_factory
        self.compact = compact
        self.table_widget = CloseEmitWidget()
        self.show_button = None
        self.setup_ui()

    @classmethod
    def from_hint_info(
        cls,
        type_hint: Any,
        annotation: str,
        name: str | None = None,
    ) -> ListArgWidget:
        origin = get_origin(type_hint)

        if not origin:
            # we have reached the bottom, should just have a single type
            if '__DEVICE__' in annotation:
                widget_factory = DeviceChoiceWidget
                return cls(widget_factory=widget_factory, name=name)
            else:
                widget_factory = partial(BasicArgEdit, arg_type=type_hint)
                return cls(widget_factory=widget_factory, name=name)

        if origin is list:
            args = get_args(type_hint)
            sub_anno = re.search(r'^typing.List\[(.*)\]$', annotation)[1]

            def get_sub_widget_callable(type_hint: Any, anno: str):
                """
                Recurse through nested lists and return the appropriately
                wrapped widget_factory
                """
                origin = get_origin(type_hint)
                args = get_args(type_hint)
                sub_anno = re.search(r'^typing.List\[(.*)\]$', anno)
                if not origin:
                    # final list, no need to compact
                    return partial(BasicArgEdit, arg_type=type_hint)
                else:
                    sub_widget = get_sub_widget_callable(args[0], sub_anno[1])
                    if isinstance(sub_widget, ListArgWidget):
                        compact = True
                    else:
                        compact = False
                    return partial(ListArgWidget,
                                   widget_factory=sub_widget, compact=compact,
                                   arg_type=args[0])

            sub_widget_factory = get_sub_widget_callable(args[0], sub_anno)
            widget_factory = partial(ListArgWidget,
                                     widget_factory=sub_widget_factory,
                                     compact=True)
            return cls(widget_factory=widget_factory, name=name)

        if origin is Union:
            return LabelArgWidget(
                name=name, label_text=f'List[Union] not supported yet {type_hint}'
            )

    def setup_ui(self) -> None:
        self.table_widget.setLayout(QtWidgets.QHBoxLayout())

        self.arg_table = QtWidgets.QTableWidget(parent=self)
        self.arg_table.setRowCount(1)
        self.arg_table.setColumnCount(1)
        self.arg_table.setCellWidget(0, 0, self.widget_factory())
        self.arg_table.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.AdjustToContents
        )
        self.arg_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.arg_table.horizontalHeader().setStretchLastSection(True)
        self.arg_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.arg_table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.arg_table.setHorizontalHeaderLabels(['args'])
        # run with every item add?

        self.button_vlayout = QtWidgets.QVBoxLayout()
        self.add_button = QtWidgets.QPushButton('+')
        self.add_button.clicked.connect(self.add_row)
        self.del_button = QtWidgets.QPushButton('-')
        self.del_button.clicked.connect(self.del_row)
        self.button_vlayout.addWidget(self.add_button)
        self.button_vlayout.addWidget(self.del_button)

        self.table_widget.layout().addWidget(self.arg_table)
        self.table_widget.layout().addLayout(self.button_vlayout)

        if self.compact:
            self.show_button = QtWidgets.QPushButton('Open list Editor')
            self.show_button.clicked.connect(self.show_list_edit)
            self.hlayout.addWidget(self.show_button)

            def update_button_text():
                self.show_button.setText(str(self.value()))

            self.table_widget.widget_closed.connect(update_button_text)
        else:
            self.hlayout.addWidget(self.table_widget)

    def add_row(self) -> None:
        self.arg_table.insertRow(self.arg_table.rowCount())
        self.arg_table.setCellWidget(self.arg_table.rowCount() - 1, 0,
                                     self.widget_factory())
        self.arg_table.verticalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )

    def del_row(self) -> None:
        self.arg_table.removeRow(self.arg_table.currentRow())

    def show_list_edit(self) -> None:
        self.table_widget.show()
        self.table_widget.activateWindow()
        self.table_widget.setWindowState(QtCore.Qt.WindowActive)

    def value(self) -> List[Any]:
        args = []
        for i in range(self.arg_table.rowCount()):
            cell_value = self.arg_table.cellWidget(i, 0).value()
            if cell_value is not None:
                args.append(cell_value)
        return args


class DictArgWidget(ArgumentEntryWidget):

    def __init__(
        self,
        *args,
        arg_type: Optional[Any] = None,
        name: Optional[str] = None,
        compact: bool = False,
        **kwargs
    ) -> None:
        super().__init__(*args, name=name, **kwargs)
        self.arg_type = arg_type
        self.compact = compact
        self.table_widget = CloseEmitWidget()
        self.show_button = None
        self.setup_ui()

    @classmethod
    def from_hint_info(
        cls,
        type_hint: Any,
        annotation: str,
        name: str | None = None,
        compact: bool = False
    ) -> ListArgWidget:
        key_hint = type_hint[0]
        val_hint = type_hint[1]

        if (key_hint is not str) or (val_hint is not Any):
            raise RuntimeError(f'only string keys/values permitted ({type_hint})')

        return cls(name=name, compact=compact)

    def setup_ui(self) -> None:
        self.table_widget.setLayout(QtWidgets.QHBoxLayout())

        self.arg_table = QtWidgets.QTableWidget(parent=self)
        self.arg_table.setRowCount(1)
        self.arg_table.setColumnCount(2)
        self.arg_table.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.AdjustToContents
        )
        self.arg_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.arg_table.horizontalHeader().setStretchLastSection(True)
        self.arg_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.arg_table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.arg_table.setHorizontalHeaderLabels(['key', 'value'])
        # run with every item add?

        self.button_vlayout = QtWidgets.QVBoxLayout()
        self.add_button = QtWidgets.QPushButton('+')
        self.add_button.clicked.connect(self.add_row)
        self.del_button = QtWidgets.QPushButton('-')
        self.del_button.clicked.connect(self.del_row)
        self.button_vlayout.addWidget(self.add_button)
        self.button_vlayout.addWidget(self.del_button)

        self.table_widget.layout().addWidget(self.arg_table)
        self.table_widget.layout().addLayout(self.button_vlayout)

        if self.compact:
            self.show_button = QtWidgets.QPushButton('Open Dict Editor')
            self.show_button.clicked.connect(self.show_list_edit)
            self.hlayout.addWidget(self.show_button)

            def update_button_text():
                self.show_button.setText(str(self.value()))

            self.table_widget.widget_closed.connect(update_button_text)
        else:
            self.hlayout.addWidget(self.table_widget)

    def add_row(self) -> None:
        self.arg_table.insertRow(self.arg_table.rowCount())
        self.arg_table.verticalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )

    def del_row(self) -> None:
        self.arg_table.removeRow(self.arg_table.currentRow())

    def show_list_edit(self) -> None:
        self.table_widget.show()
        self.table_widget.activateWindow()
        self.table_widget.setWindowState(QtCore.Qt.WindowActive)

    def value(self) -> List[Any]:
        args = {}
        for i in range(self.arg_table.rowCount()):
            if not (self.arg_table.item(i, 0) and self.arg_table.item(i, 1)):
                continue
            key = self.arg_table.item(i, 0).text()
            value = self.arg_table.item(i, 1).text()
            args[key] = value
        return args


class UnionArgWidget(ArgumentEntryWidget):
    @classmethod
    def from_hint_info(
        cls,
        type_hint: Any,
        annotation: str,
        name: str | None = None
    ) -> ArgumentEntryWidget:
        return QtWidgets.QLabel('Union here')


class LabelArgWidget(ArgumentEntryWidget):
    def __init__(
        self,
        *args,
        label_text: str,
        name: Optional[str] = None,
        **kwargs
    ) -> None:
        super().__init__(*args, name=name, **kwargs)
        self.label = QtWidgets.QLabel(label_text)
        self.hlayout.addWidget(self.label)

    def value(self):
        return None
