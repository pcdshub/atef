import json
import logging
import os
import re
from dataclasses import fields, is_dataclass
from enum import Enum
from functools import partial
from typing import (Any, Callable, Generator, Iterable, List, Optional, Tuple,
                    Union, get_args)

from apischema import ValidationError, deserialize
from qtpy import QtCore, QtWidgets

from atef.config import ConfigurationFile, PreparedFile
from atef.procedure import PreparedProcedureFile, ProcedureFile
from atef.type_hints import PrimitiveType
from atef.widgets.core import DesignerDisplay

logger = logging.getLogger(__name__)


def walk_find_match(
    item: Any,
    match: Callable,
    parent: List[Tuple[Union[str, int], Any]] = []
) -> Generator:
    """
    Walk the dataclass and find every key / field where ``match`` evaluates to True.

    Yields a list of 'paths' to the matching key / field. A path is a list of
    (field, object) tuples that lead from the top level ``item`` to the matching
    key / field.
    - If the object is a dataclass, `field` will be a field in that dataclass
    - If the object is a list, `field` will be the index in that list
    - If the object is a dict, `field` will be a key in that dictionary

    ``match`` should be a Callable taking a single argument and returning a boolean,
    specifying whether that argument matched a search term or not.  This is
    commonly a simple lambda wrapping an equality or regex search.

    Ex:
    paths = walk_find_match(ConfigFile, 'All Fields Demo')

    Parameters
    ----------
    item : Any
        the item to search in.  A dataclass at the top level, but can also be a
        list or dict
    match : Callable
        a function that takes a single argument and returns a boolean
    parent : List[Tuple[Union[str, int], Any]], optional
        the 'path' traveled to arive at ``item`` at this point, by default []
        (used internally)

    Yields
    ------
    Generator
        paths leading to keys or fields where ``match`` is True
    """
    if is_dataclass(item):
        # get fields, recurse through fields
        for field in fields(item):
            yield from walk_find_match(getattr(item, field.name), match,
                                       parent=parent + [(item, field.name)])
    elif isinstance(item, list):
        for idx, l_item in enumerate(item):
            # TODO: py3.10 allows isinstance with Unions
            if isinstance(l_item, get_args(PrimitiveType)) and match(l_item):
                yield parent + [('__list__', idx)]
            else:
                yield from walk_find_match(l_item, match,
                                           parent=parent + [('__list__', idx)])
    elif isinstance(item, dict):
        for d_key, d_value in item.items():
            # don't halt at first key match, values could also have matches
            if isinstance(d_value, get_args(PrimitiveType)) and match(d_value):
                yield parent + [('__dictvalue__', d_key)]
            else:
                yield from walk_find_match(d_value, match,
                                           parent=parent + [('__dictvalue__', d_key)])
            if match(d_key):
                yield parent + [('__dictkey__', d_key)]

    elif isinstance(item, Enum):
        if match(item.name):
            yield parent + [('__enum__', item)]

    elif match(item):
        yield parent


def get_deepest_dataclass_in_path(path) -> Tuple[Any, str]:
    rev_idx = -1
    while rev_idx > (-len(path) - 1):
        if is_dataclass(path[rev_idx][0]):
            break
        else:
            rev_idx -= 1
    return path[rev_idx]


def get_item_from_path(path, item: Optional[Any] = None) -> Any:
    # providing item shouldn't be necessary if item was used to trace the path before.
    # in that case the objects are stashed in the path
    # item is expected to be top-level, if provided.
    if not item:
        item = path[0][0]
    for seg in path:
        if seg[0] == '__dictkey__':
            item = seg[1]
        elif seg[0] == '__dictvalue__':
            item = item[seg[1]]
        elif seg[0] == '__list__':
            item = item[seg[1]]
        elif seg[0] == '__enum__':
            item = item.value
        else:
            # general dataclass case
            item = getattr(item, seg[1])
    return item


def replace_item_from_path(
    item: Any,
    path: List[Tuple[Any, Any]],
    replace_fn: Optional[Callable] = None,
) -> None:
    # walk forward until step -2
    # use step -1 to assign the new value
    final_step = path[-1]
    parent_item = get_item_from_path(path[:-1], item=item)

    if final_step[0] == "__dictkey__":
        parent_item[replace_fn(final_step[1])] = parent_item.pop(final_step[1])
    elif final_step[0] in ("__dictvalue__", "__list__"):
        # replace value
        old_value = parent_item[final_step[1]]
        parent_item[final_step[1]] = replace_fn(old_value)
    elif final_step[0] == "__enum__":
        parent_item = get_item_from_path(path[:-2], item=item)
        old_enum: Enum = getattr(parent_item, path[-2][1])
        new_enum = getattr(final_step[1], replace_fn(old_enum.name))
        setattr(parent_item, path[-2][1], new_enum)
    else:
        # simple field paths don't have a final (__sth__, ?) segement
        old_value = getattr(parent_item, path[-1][1])
        setattr(parent_item, path[-1][1], replace_fn(old_value))


class FindReplaceWidget(DesignerDisplay, QtWidgets.QWidget):

    search_edit: QtWidgets.QLineEdit
    replace_edit: QtWidgets.QLineEdit

    case_button: QtWidgets.QToolButton
    regex_button: QtWidgets.QToolButton

    preview_button: QtWidgets.QPushButton
    verify_button: QtWidgets.QPushButton
    open_file_button: QtWidgets.QPushButton
    open_converted_button: QtWidgets.QPushButton

    change_list: QtWidgets.QListWidget

    filename = 'find_replace_widget.ui'

    def __init__(
        self,
        *args,
        filepath: Optional[str] = None,
        window: Optional[Any] = None,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.fp = filepath
        self.window = window
        self.match_paths: Iterable[List[Any]] = []
        self.orig_file = None

        if not filepath:
            self.open_converted_button.hide()
        else:
            self.open_file(filename=filepath)

        if window:
            self.open_converted_button.clicked.connect(self.open_converted)
        else:
            self.open_converted_button.hide()

        self.setup_open_file_button()
        self.preview_button.clicked.connect(self.preview_changes)
        self.verify_button.clicked.connect(self.verify_changes)

        self.replace_edit.editingFinished.connect(self.update_replace_fn)
        self.search_edit.editingFinished.connect(self.update_match_fn)
        # placeholder no-op functions
        self._match_fn = lambda x: False
        self._replace_fn = lambda x: x

    def setup_open_file_button(self):
        self.open_file_button.clicked.connect(self.open_file)

    def open_file(self, *args, filename: Optional[str] = None, **kwargs):
        if filename is None:
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                parent=self,
                caption='Select a config',
                filter='Json Files (*.json)',
            )
        if not filename:
            return

        self.orig_file = self.load_file(filename)
        self.setWindowTitle(f'find and replace: ({os.path.basename(filename)})')

    def load_file(self, filepath) -> Union[ConfigurationFile, ProcedureFile]:
        with open(filepath, 'r') as fp:
            self._original_json = json.load(fp)
        try:
            data = deserialize(ConfigurationFile, self._original_json)
        except ValidationError:
            logger.debug('failed to open as passive checkout')
            try:
                data = deserialize(ProcedureFile, self._original_json)
            except ValidationError:
                logger.error('failed to open file as either active '
                             'or passive checkout')

        return data

    def update_replace_fn(self, *args, **kwargs):
        replace_text = self.replace_edit.text()

        def replace_fn(value):
            if isinstance(value, str):
                return self._search_regex.sub(replace_text, value)
            elif isinstance(value, int):
                # cast to float first
                return int(float(value))
            else:  # try to cast as original type
                return type(value)(replace_text)

        self._replace_fn = replace_fn

    def update_match_fn(self, *args, **kwargs):
        search_text = self.search_edit.text()

        flags = re.IGNORECASE if not self.case_button.isChecked() else 0
        use_regex = self.regex_button.isChecked()

        if use_regex:
            self._search_regex = re.compile(f'{search_text}', flags=flags)
        else:
            # exact match
            self._search_regex = re.compile(f'{re.escape(search_text)}', flags=flags)

        def match_fn(match):
            return self._search_regex.search(str(match)) is not None

        self._match_fn = match_fn

    def preview_changes(self, *args, **kwargs):
        # update everything to be safe (finishedEditing can be uncertain)
        self.update_match_fn()
        self.update_replace_fn()

        self.change_list.clear()
        self.match_paths = walk_find_match(self.orig_file, self._match_fn)
        replace_text = self.replace_edit.text()
        search_text = self.search_edit.text()

        def remove_item(list_item):
            self.change_list.takeItem(self.change_list.row(list_item))

        def accept_change(list_item):
            try:
                replace_item_from_path(self.orig_file, path,
                                       replace_fn=self._replace_fn)
            except KeyError:
                logger.warning(f'Unable to replace ({search_text}) with '
                               f'({replace_text}) in file.  File may have '
                               f'already been edited')
            except Exception as ex:
                logger.warning(f'Unable to apply change. {ex}')

            remove_item(list_item)

        # generator can be unstable if dataclass changes during walk
        # this is only ok because we consume generator entirely
        for path in self.match_paths:
            # Modify a preview file to create preview
            preview_file = self.load_file(self.fp)
            if replace_text:
                try:
                    replace_item_from_path(preview_file, path,
                                           replace_fn=self._replace_fn)
                    post_text = str(get_item_from_path(path[:-1], item=preview_file))
                except Exception as ex:
                    logger.warning('Unable to generate preview, provided replacement '
                                   f'text is invalid: {ex}')
                    post_text = '[INVALID]'
            else:
                post_text = ''

            pre_text = str(get_item_from_path(path[:-1], item=self.orig_file))
            row_widget = FindReplaceRow(pre_text=pre_text,
                                        post_text=post_text,
                                        path=path)

            l_item = QtWidgets.QListWidgetItem()
            l_item.setSizeHint(QtCore.QSize(row_widget.width(), row_widget.height()))
            self.change_list.addItem(l_item)
            self.change_list.setItemWidget(l_item, row_widget)

            row_widget.button_box.accepted.connect(partial(accept_change, l_item))
            row_widget.button_box.rejected.connect(partial(remove_item, l_item))

    def verify_changes(self, *args, **kwargs):
        # check to make sure changes are valid

        try:
            if self.config_type is ConfigurationFile:
                self.prepared_file = PreparedFile.from_config(self.orig_file)
            if self.config_type is ProcedureFile:
                # clear all results when making a new run tree
                self.prepared_file = PreparedProcedureFile.from_origin(self.orig_file)
        except Exception as ex:
            print(f'prepare fail: {ex}')
            return

        print('should work')

    def open_converted(self, *args, **kwargs):
        # open new file in new tab
        self.window._new_tab(data=self.orig_file, filename=self.fp)


class FindReplaceRow(DesignerDisplay, QtWidgets.QWidget):

    button_box: QtWidgets.QDialogButtonBox
    dclass_label: QtWidgets.QLabel
    pre_label: QtWidgets.QLabel
    post_label: QtWidgets.QLabel
    details_button: QtWidgets.QToolButton

    filename = 'find_replace_row_widget.ui'

    def __init__(
        self,
        *args,
        pre_text: str = 'pre',
        post_text: str = 'post',
        path: List[Any] = [],
        **kwargs
    ) -> None:
        super().__init__(*args, *kwargs)
        last_dclass, attr = get_deepest_dataclass_in_path(path)
        dclass_type = type(last_dclass).__name__

        self.dclass_label.setText(f'{dclass_type}.{attr}')
        self.pre_label.setText(pre_text)
        self.post_label.setText(post_text)

        self.button_box.button(QtWidgets.QDialogButtonBox.Ok).setText('')
        self.button_box.button(QtWidgets.QDialogButtonBox.Cancel).setText('')

        path_list = []
        for segment in path:
            if isinstance(segment[1], str):
                name = segment[1]
            else:
                name = type(segment[1]).__name__
            path_list.append(f'({name}, {segment[0]})')

        path_str = '>'.join(path_list)
        detail_scroll = QtWidgets.QScrollArea()
        detail_widget = QtWidgets.QLabel(path_str + '\n')
        detail_widget.setWordWrap(True)
        detail_scroll.setWidget(detail_widget)

        widget_action = QtWidgets.QWidgetAction(self.details_button)
        widget_action.setDefaultWidget(detail_widget)

        widget_menu = QtWidgets.QMenu(self.details_button)
        widget_menu.addAction(widget_action)
        self.details_button.setMenu(widget_menu)
