import json
import logging
import re
from dataclasses import fields, is_dataclass
from typing import (Any, Callable, Generator, Iterable, List, Tuple, Union,
                    get_args)

from apischema import deserialize
from qtpy import QtWidgets

from atef.config import ConfigurationFile, PreparedFile
from atef.procedure import PreparedProcedureFile, ProcedureFile
from atef.type_hints import PrimitiveType
from atef.widgets.core import DesignerDisplay
from atef.widgets.utils import ExpandableFrame, insert_widget

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
                                       parent=parent + [(field.name, item)])
    elif isinstance(item, list):
        for idx, l_item in enumerate(item):
            # TODO: py3.10 allows isinstance with Unions
            if not isinstance(l_item, get_args(PrimitiveType)) and match(l_item):
                yield parent + [(idx, '__list__')]
            else:
                yield from walk_find_match(l_item,
                                           match, parent=parent + [(idx, '__list__')])
    elif isinstance(item, dict):
        for d_key, d_value in item.items():
            if match(d_key):
                yield parent + [('__key__', '__dict__')]
            # don't halt at first key match, values could also have matches
            if not isinstance(d_value, get_args(PrimitiveType)) and match(d_value):
                yield parent + [(d_key, '__dict__')]
            else:
                yield from walk_find_match(d_value,
                                           match, parent=parent + [(d_key, '__dict__')])

    elif match(item):
        yield parent


def get_deepest_dataclass_in_path(path) -> Any:
    rev_idx = -1
    while rev_idx > (-len(path) - 1):
        if is_dataclass(path[rev_idx][1]):
            break
        else:
            rev_idx -= 1
    return path[rev_idx][1]


def get_item_from_path(item, path) -> Any:
    for seg in path:
        # allow starting from an item that is partway down path
        if seg[1] != item:
            continue
        if seg[1] == '__dict__':
            if seg[0] == '__key__':
                return item
            else:
                item = item[seg[0]]
        elif seg[1] == '__list__':
            item = item[seg[0]]
        else:
            # dataclass case
            item = getattr(seg[1], seg[0])
    return item


def replace_item_from_path(item: Any, search: Any, replace: Any, path: List[Tuple[Any, Any]]) -> Any:
    # traverse backward until we get a dataclass (necessary?)
    # walk forward until step -2
    # use step -1 to assign the new value
    # TODO: consider enums.   How do we match those
    final_step = path[-1]
    parent_item = get_item_from_path(item, path[:-1])

    if final_step[1] == "__dict__":
        if final_step[0] == '__key__':
            # replace the key in this dictionary
            parent_item[replace] = parent_item.pop(search)
        else:
            # replace value
            parent_item[final_step[0]] = replace
    elif final_step[1] == "__list__":
        # replace item in list
        parent_item[final_step[0]] = replace
    else:
        # replace value.  If is an enum, try to access the enum.
        setattr(parent_item, final_step, replace)


class FindReplaceWidget(DesignerDisplay, QtWidgets.QWidget):

    search_edit: QtWidgets.QLineEdit
    replace_edit: QtWidgets.QLineEdit

    case_button: QtWidgets.QToolButton
    regex_button: QtWidgets.QToolButton

    preview_button: QtWidgets.QPushButton
    verify_button: QtWidgets.QPushButton
    open_button: QtWidgets.QPushButton

    change_list: QtWidgets.QListWidget

    filename = 'find_replace_widget.ui'

    def __init__(self, *args, filepath: str, config_type: Any, **kwargs):
        super().__init__(*args, **kwargs)
        self.fp = filepath
        self.config_type = config_type
        self.match_paths: Iterable[List[Any]] = []

        self._refresh_original()

        self.preview_button.clicked.connect(self.preview_changes)
        self.verify_button.clicked.connect(self.verify_changes)
        self.open_button.clicked.connect(self.open_converted)

    def _refresh_original(self):
        with open(self.fp, 'r') as fp:
            self._original_json = json.load(fp)

        self.orig_file = deserialize(self.config_type, self._original_json)

    def run_search_replace(self):
        search_text = self.search_edit.text()
        replace_text = self.replace_edit.text()

        match_case = self.case_button.isChecked()
        use_regex = self.regex_button.isChecked()

        logger.debug(f'Running search/replace with {replace_text, match_case}')

        if use_regex:
            regex = re.compile(search_text)
        else:
            regex = re.compile(search_text)

        def match_fn(match):
            return regex.search(str(match)) is not None

        self.match_paths = walk_find_match(self.orig_file, match_fn)

    def preview_changes(self, *args, **kwargs):
        self.run_search_replace()
        # generate the previews in
        self.change_list.clear()
        for path in self.match_paths:
            # find deepest dataclass
            rev_idx = -1
            while rev_idx > (-len(path) - 1):
                if is_dataclass(path[rev_idx][1]):
                    break
                else:
                    rev_idx -= 1

            last_type = path[rev_idx][1]
            dclass_type = type(last_type).__name__
            # # add item to list
            # preview_text = ''.join(region).strip('\n')
            row_widget = FindReplaceRow(pre_text='', post_text='', dclass_text=dclass_type, path=path)
            l_item = QtWidgets.QListWidgetItem()
            l_item.setSizeHint(row_widget.sizeHint())
            self.change_list.addItem(l_item)
            self.change_list.setItemWidget(l_item, row_widget)

    def verify_changes(self, *args, **kwargs):
        # check to make sure changes are valid
        # Create a new File
        # Create prepared version of file
        # new_json = json.loads(''.join(self._new))
        # try:
        #     deser = deserialize(self.config_type, new_json)
        # except Exception as ex:
        #     print(f'deserialize fail: {ex}')
        #     return

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
        return


class FindReplaceRow(DesignerDisplay, QtWidgets.QWidget):

    details_frame: QtWidgets.QFrame
    button_box: QtWidgets.QDialogButtonBox
    dclass_label: QtWidgets.QLabel
    pre_label: QtWidgets.QLabel
    post_label: QtWidgets.QLabel

    filename = 'find_replace_row_widget.ui'

    def __init__(
        self,
        *args,
        dclass_text: str = 'dclass',
        pre_text: str = 'pre',
        post_text: str = 'post',
        path: List[Any] = [],
        **kwargs
    ) -> None:
        super().__init__(*args, *kwargs)
        self.post_label.setText(post_text)
        self.pre_label.setText(pre_text)
        self.dclass_label.setText(dclass_text)
        self.button_box.button(QtWidgets.QDialogButtonBox.Ok).setText('')
        self.button_box.button(QtWidgets.QDialogButtonBox.Cancel).setText('')

        new_frame = ExpandableFrame(text='details...')
        path_list = []
        for segment in path:
            if isinstance(segment[1], str):
                name = segment[1]
            else:
                name = type(segment[1]).__name__
            path_list.append(f'({name}, {segment[0]})')

        path_str = '>'.join(path_list)
        detail_widget = QtWidgets.QLabel(path_str + '\n')
        detail_widget.setWordWrap(True)
        new_frame.add_widget(detail_widget)
        insert_widget(new_frame, self.details_frame)
