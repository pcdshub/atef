from __future__ import annotations

import logging
from typing import Any, ClassVar, Dict, List, Optional

from qtpy import QtCore, QtWidgets
from qtpy.QtCore import Signal as QSignal

from atef import util
from atef.widgets.happi import HappiDeviceComponentWidget
from atef.widgets.ophyd import OphydAttributeData

logger = logging.getLogger(__file__)


class PlanEntryWidget(QtWidgets.QWidget):
    """ holds many ArgumentEntryWidget's and supplies a plan item """

    def __init__(self, *args, plan: Dict[str, Any], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.plan_info = plan
        self.vlayout = QtWidgets.QVBoxLayout()
        self.setLayout(self.vlayout)

        self.arg_widgets = []
        for param_info in plan['parameters']:
            arg_widget = build_arg_widget(param_info)
            self.arg_widgets.append(arg_widget)
            self.vlayout.addWidget(arg_widget)

    def plan_item(self) -> Dict[str, Any]:
        """ Returns a plan item in the form {...}"""
        plan_item = {'name': self.plan_info['name'], 'args': [], 'kwargs': {},
                     'user_group': 'root'}
        for param_info, arg_widget in zip(self.plan_info['parameters'], self.arg_widgets):
            value = arg_widget.value()
            if param_info['kind']['name'] == 'KEYWORD_ONLY':
                arg_name = param_info['name']
                plan_item['kwargs'][arg_name] = value
            elif param_info['kind']['name'] == 'POSITIONAL_OR_KEYWORD':
                plan_item['args'].append(value)

        return plan_item


def build_arg_widget(info: Dict[str, Any]) -> QtWidgets.QWidget:
    """ Intended to be a factory function """
    if not info.get('annotation'):
        # no annotation, just use a simple text edit
        return BasicArgEdit(name=info['name'], info=info)
    elif info['annotation']['type'] in ('str'):
        return BasicArgEdit(name=info['name'], info=info)
    elif '__DEVICE__' in info['annotation']['type']:
        return DeviceChoiceWidget(name=info['name'], info=info)
    return QtWidgets.QLabel('Could not identify argument type')


class ArgumentEntryWidget(QtWidgets.QWidget):
    """ Base class for various input widgets? needed? """
    # `info` is expected to be dictionary containing parameter info keys:
    # - "kind": positional or keyword, etc
    # - "description": a text description
    # - "annotaiton": a dictionary itself, with a type annotation

    # .value(): giving plan - compatible value
    # .arg_label: arg name
    # arg_changed: QSignal
    arg_label: QtWidgets.QLabel
    arg_changed: ClassVar[QtCore.Signal] = QSignal()

    def __init__(self, *args, info: Dict[str, Any], name: Optional[str] = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.name = name
        self.info = info

        self.hlayout = QtWidgets.QHBoxLayout()
        self.setLayout(self.hlayout)

        if self.name:
            self.arg_label = QtWidgets.QLabel()
            self.arg_label.setText(self.name)
            self.hlayout.addWidget(self.arg_label)

        # TODO: set description as tooltip?

    def value(self) -> str:
        """ Return the entered value as string. """
        raise NotImplementedError

# Default is str, just leave as str, let qserver construct plan
# Read enum from annotation first
# List -> table widget?.....
# primitives -> specific edits
# __DEVICE__ -> happi selector


class BasicArgEdit(ArgumentEntryWidget):

    line_edit: QtWidgets.QLineEdit

    def __init__(
        self,
        *args,
        info: Dict[str, Any],
        name: Optional[str] = None,
        **kwargs
    ) -> None:
        super().__init__(*args, name=name, info=info, **kwargs)
        self.line_edit = QtWidgets.QLineEdit()
        self.hlayout.addWidget(self.line_edit)

    def value(self) -> str:
        return self.line_edit.text()


class DeviceChoiceWidget(ArgumentEntryWidget):
    """ For arguments that require devices """
    _search_widget: Optional[HappiDeviceComponentWidget] = None
    combo_box: Optional[QtWidgets.QComboBox] = None
    signal_button: Optional[QtWidgets.QPushButton] = None

    def __init__(self, *args, info: Dict[str, Any], name: Optional[str] = None, **kwargs) -> None:
        super().__init__(*args, name=name, info=info, **kwargs)
        self._device = None
        self._signal_attr = None

        if 'devices' in info:
            # set up a combo box with given values
            self.combo_box = QtWidgets.QComboBox()
            for _, value in info['devices'].items():
                for device_name in value:
                    self.combo_box.addItem(device_name)

            # TODO: Connect signal to arg_changed ?
            self.hlayout.addWidget(self.combo_box)

        else:
            # No pre-determined values, pick from general devices
            self.signal_button = QtWidgets.QPushButton()
            self.signal_button.clicked.connect(self.pick_signal)
            self.hlayout.addWidget(self.signal_button)

    def pick_signal(self) -> None:
        """
        Slot for signal_button.  Opens the HappiDeviceComponentWidget and
        configures it to send the signal selection to this widget
        """
        if self._search_widget is None:
            widget = HappiDeviceComponentWidget(
                client=util.get_happi_client()
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
        logger.debug(f'found attr: {attr}')
        self._signal_attr = attr.attr
        self.signal_button.setText(f'{self._device}.{self._signal_attr}')

    def value(self) -> str:
        if self.combo_box:
            return self.combo_box.currentText()
        elif self.signal_button:
            return self.signal_button.text()
        else:
            raise RuntimeError('no entry widgets available')
