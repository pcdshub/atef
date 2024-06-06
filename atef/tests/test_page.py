from typing import Any, Callable

import pytest
from pytestqt.qtbot import QtBot
from qtpy import QtCore, QtWidgets

from atef.config import TemplateConfiguration
from atef.type_hints import AnyDataclass
from atef.widgets.config.page import ComparisonPage, ConfigurationGroupPage


def gather_comparisons(cfg: AnyDataclass):
    """Returns a list of comparisons in any of the possible fields"""
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


def get_different_combo_options(combo_box: QtWidgets.QComboBox) -> list[int]:
    idx = combo_box.currentIndex()
    count = combo_box.count()
    new_idxs = list(range(count))
    new_idxs.remove(idx)
    print(f'curr: {idx}, {new_idxs}')
    return new_idxs


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
    qtbot.wait_until(
        lambda: first_config not in configuration_group_page.data.configs
    )


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

    table = group_page.comparisons_table
    table.update_table()
    index = table.proxy_model.index(0, 0)
    widget = table.table_view.indexWidget(index)

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

    table = group_page.comparisons_table
    table.update_table()
    index = table.proxy_model.index(0, 0)
    row_widget = table.table_view.indexWidget(index)

    new_idxs = get_different_combo_options(row_widget.attr_combo)
    if not new_idxs:
        return

    for idx in new_idxs:
        row_widget.attr_combo.setCurrentIndex(idx)
        row_widget.attr_combo.activated.emit(idx)
        qtbot.waitUntil(lambda: gather_comparisons(cfg) != orig_comps, timeout=10000)
        assert len(gather_comparisons(cfg)) == len(orig_comps)


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
    group_data = group_page.data
    full_tree = group_page.full_tree

    # get comparison page
    table = group_page.comparisons_table
    table.update_table()
    index = table.proxy_model.index(0, 0)
    row_widget = table.table_view.indexWidget(index)

    row_widget.child_button.clicked.emit()
    qtbot.wait_until(lambda: isinstance(group_page.full_tree.current_widget,
                                        ComparisonPage))
    comp_page = group_page.full_tree.current_widget
    old_comp = comp_page.data

    new_idxs = get_different_combo_options(comp_page.specific_combo)
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        lambda *args, **kwargs: QtWidgets.QMessageBox.Yes)
    for idx in new_idxs:
        qtbot.addWidget(group_page)
        qtbot.addWidget(comp_page)
        comp_page.specific_combo.setCurrentIndex(idx)
        comp_page.specific_combo.activated.emit(idx)

        def condition():
            assert full_tree.current_widget.data != old_comp

        qtbot.waitUntil(condition, timeout=10000)
        new_data = full_tree.current_widget.data
        # ensure group_page still exists even if it falls out of cache
        full_tree.select_by_data(group_data)
        full_tree.select_by_data(new_data)
        comp_page = full_tree.current_widget


def test_template_page(
    qtbot: QtBot,
    template_configuration: TemplateConfiguration,
    make_page: Callable,
):
    group_page = make_page(template_configuration)

    # Does the configuration initialize properly?
    qtbot.wait_until(
        lambda: group_page.template_page_widget.staged_list.count() == 1
    )

    # test preparation
    group_page.full_tree.mode = 'run'
    group_page.full_tree.switch_mode('run')

    qtbot.wait_signal(group_page.full_tree.mode_switch_finished)
    qtbot.wait_until(
        lambda: group_page.template_page_widget.staged_list.count() == 1
    )
    qtbot.addWidget(group_page)
