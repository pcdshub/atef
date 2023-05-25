import random
from typing import Any, Callable

import pytest
from pytestqt.qtbot import QtBot
from qtpy import QtCore, QtWidgets

from atef.type_hints import AnyDataclass
from atef.widgets.config.page import ComparisonPage, ConfigurationGroupPage


def gather_comparisons(cfg: AnyDataclass):
    """ Returns a list of comparisons in any of the possible fields """
    comps = []
    if hasattr(cfg, 'shared'):
        for comp in cfg.shared:
            comps.append(('shared', comp))

    if hasattr(cfg, 'by_pv'):
        for key, comp_list in cfg.by_pv.items():
            for comp in comp_list:
                comps.append((f'by_pv: {key}', comp))

    if hasattr(cfg, 'by_attr'):
        for key, comp_list in cfg.by_attr.items():
            for comp in comp_list:
                comps.append((f'by_attr: {key}', comp))
    return comps


def pick_different_combo_option(combo_box: QtWidgets.QComboBox) -> int:
    idx = combo_box.currentIndex()
    count = combo_box.count()
    new_idxs = list(range(count))
    new_idxs.remove(idx)
    try:
        new_idx = random.choice(new_idxs)
    except IndexError:
        return

    return new_idx


def test_add_delete_config(
    qtbot: QtBot,
    monkeypatch: Any,
    configuration_group: ConfigurationGroupPage,
    make_page: Callable
):
    configuration_group_page = make_page(configuration_group)
    original_row_number = len(configuration_group_page.data.configs)
    qtbot.addWidget(configuration_group_page)
    qtbot.mouseClick(configuration_group_page.add_row_button,
                     QtCore.Qt.LeftButton)
    assert len(configuration_group_page.data.configs) == original_row_number + 1

    first_config = configuration_group_page.data.configs[0]

    configuration_group_page.move_config_row(0, 2)
    assert configuration_group_page.data.configs[2] is first_config

    widget = configuration_group_page.config_table.cellWidget(2, 0)

    # mock to auto-confirm deletion
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        lambda *args, **kwargs: QtWidgets.QMessageBox.Yes)
    qtbot.mouseClick(widget.delete_button, QtCore.Qt.LeftButton)

    assert first_config not in configuration_group_page.data.configs


@pytest.mark.parametrize(
    'group',
    ['pv_configuration', 'device_configuration', 'tool_configuration']
)
def test_add_delete_comparison(
    request: Any,
    monkeypatch: Any,
    qtbot: QtBot,
    group: AnyDataclass,
    make_page: Callable,
):
    cfg = request.getfixturevalue(group)
    group_page = make_page(cfg)
    orig_comp_list = gather_comparisons(cfg)

    qtbot.addWidget(group_page)
    qtbot.mouseClick(group_page.add_comparison_button, QtCore.Qt.LeftButton)
    new_comp_list = gather_comparisons(cfg)
    assert len(new_comp_list) == len(orig_comp_list) + 1

    widget = group_page.comparisons_table.cellWidget(0, 0)
    deleted_comparison = widget.data

    # mock to auto-confirm deletion
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        lambda *args, **kwargs: QtWidgets.QMessageBox.Yes)
    qtbot.mouseClick(widget.delete_button, QtCore.Qt.LeftButton)

    final_comp_list = gather_comparisons(cfg)
    assert deleted_comparison not in final_comp_list


@pytest.mark.parametrize(
    'group',
    ['pv_configuration', 'device_configuration', 'tool_configuration']
)
def test_change_attr(
    request: Any,
    qtbot: QtBot,
    group: AnyDataclass,
    make_page: Callable,
):
    cfg = request.getfixturevalue(group)
    orig_comps = gather_comparisons(cfg)
    group_page = make_page(cfg)
    qtbot.addWidget(group_page)

    row_widget = group_page.comparisons_table.cellWidget(0, 0)
    new_idx = pick_different_combo_option(row_widget.attr_combo)
    if not new_idx:
        return

    row_widget.attr_combo.setCurrentIndex(new_idx)
    row_widget.attr_combo.activated.emit(new_idx)
    new_comps = gather_comparisons(cfg)
    assert len(new_comps) == len(orig_comps)
    assert new_comps != orig_comps


@pytest.mark.parametrize(
    'group',
    ['pv_configuration', 'device_configuration', 'tool_configuration']
)
def test_change_comparison(
    request: Any,
    monkeypatch: Any,
    qtbot: QtBot,
    group: AnyDataclass,
    make_page: Callable
):
    cfg = request.getfixturevalue(group)
    group_page = make_page(cfg)
    qtbot.addWidget(group_page)

    # get comparison page
    row_widget = group_page.comparisons_table.cellWidget(0, 0)
    row_widget.child_button.clicked.emit()
    comp_page = group_page.full_tree.currentItem().widget
    old_comp = comp_page.data

    assert isinstance(comp_page, ComparisonPage)
    new_idx = pick_different_combo_option(comp_page.specific_combo)

    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        lambda *args, **kwargs: QtWidgets.QMessageBox.Yes)
    comp_page.specific_combo.setCurrentIndex(new_idx)
    comp_page.specific_combo.activated.emit(new_idx)

    new_comp = group_page.comparisons_table.cellWidget(0, 0).data
    assert new_comp != old_comp
