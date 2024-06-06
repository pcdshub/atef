"""Find-and-Replace functionality, for use in templating checkouts as well"""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import (TYPE_CHECKING, Any, Callable, Generator, List, Optional,
                    Tuple, Union, get_args)

import happi

from atef.cache import DataCache

if TYPE_CHECKING:
    from atef.config import ConfigurationFile
    from atef.procedure import ProcedureFile

from atef.type_hints import PrimitiveType

logger = logging.getLogger(__name__)

MatchFunction = Callable[[Any], bool]
ReplaceFunction = Callable[[Any], Any]


@contextmanager
def patch_client_cache():
    old_happi_cache = happi.loader.cache
    try:
        happi.loader.cache = {}
        dcache = DataCache()
        # Clear the global signal cache to prevent previous signals from leaking
        dcache.signals.clear()
        yield
    finally:
        happi.loader.cache = old_happi_cache


def walk_find_match(
    item: Any,
    match: Callable,
    parent: List[Tuple[Any, Any]] = []
) -> Generator[List[Tuple[Any, Any]], None, None]:
    """
    Walk the dataclass and find every key / field where ``match`` evaluates to True.

    Yields a list of 'paths' to the matching key / field. A path is a list of
    (object, field) tuples that lead from the top level ``item`` to the matching
    key / field.
    - If the object is a dataclass, ``field`` will be a field in that dataclass
    - If the object is a list, ``field`` will be the index in that list
    - If the object is a dict, ``field`` will be a key in that dictionary

    ``match`` should be a Callable taking a single argument and returning a boolean,
    specifying whether that argument matched a search term or not.  This is
    commonly a simple lambda wrapping an equality or regex search.

    Ex:
    paths = walk_find_match(ConfigFile, lambda x: x == 5)
    paths = walk_find_match(ConfigFile, lambda x: re.compile('^warning$').search(x) is not None)

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
    List[Tuple[Any, Any]]
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


def simplify_path(path: List[Tuple[Any, Any]]) -> List[Tuple[str, Any]]:
    """
    Simplify ``path`` by replacing any objects with their type.
    Useful for creating a path that can be easily serialized.

    Simplified paths can be used in ``get_item_from_path`` and
    ``replace_item_from_path``, as long as ``item`` is provided.

    Parameters
    ----------
    path : List[Tuple[Any, Any]]
        the path to be simplified

    Returns
    -------
    List[Tuple[str, Any]]
        the simplified path
    """
    simplified_path = []
    for seg in path:
        if not isinstance(seg[0], str):
            item = str(type(seg[0]))
        else:
            item = seg[0]
        simplified_path.append((item, seg[1]))

    return simplified_path


def expand_path(path: List[Tuple[str, Any]], target: Any) -> List[Tuple[Any, Any]]:
    """
    Expands ``path`` using ``target`` as the object to traverse.
    Replaces all string type references with the object in question.

    The inverse of ``simplify_path``

    Parameters
    ----------
    path : List[Tuple[str, Any]]
        the simplified path to expand
    target : Any
        the object that ``path`` is referring to

    Returns
    -------
    List[Tuple[Any, Any]]
        the expanded path
    """
    new_path = []
    new_path.append((target, path[0][1]))
    # Look at all path segments after index 0, replace the next object in line.
    for idx in range(len(path) - 1):
        if path[idx+1][0].startswith("__") and path[idx+1][0].endswith("__"):
            seg_object = path[idx+1][0]
        else:
            seg_object = get_item_from_path(path[:idx+1], item=target)
        new_path.append((seg_object, path[idx + 1][1]))

    return new_path


def get_item_from_path(
    path: List[Tuple[Any, Any]],
    item: Optional[Any] = None
) -> Any:
    """
    Get the item the path points to.  This can work for any subpath

    If ``item`` is not provided, use the stashed objects in ``path``.
    Item is expected to be top-level object, if provided.
    (i.e. analagous to path[0][0]).

    Parameters
    ----------
    path : List[Tuple[Any, Any]]
        A "path" to a search match, as returned by walk_find_match
    item : Optional[Any], optional
        the item of interest to explore, by default None

    Returns
    -------
    Any
        the object at the end of ``path``, starting from ``item``
    """
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
            item = item.name
        else:
            # general dataclass case
            item = getattr(item, seg[1])
    return item


def get_deepest_dataclass_in_path(
    path: List[Tuple[Any, Any]],
    item: Optional[Any] = None
) -> Tuple[Any, str]:
    """
    Grab the deepest dataclass in the path, and return its segment

    Parameters
    ----------
    path : List[Tuple[Any, Any]]
        A "path" to a search match, as returned by walk_find_match
    item : Any
        An object to start the path from

    Returns
    -------
    Tuple[AnyDataclass, str]
        The deepest dataclass, and field name for the next step
    """
    rev_idx = -1
    while rev_idx > (-len(path) - 1):
        if is_dataclass(path[rev_idx][0]):
            break
        else:
            rev_idx -= 1
    if item:
        return get_item_from_path(path[:rev_idx], item), path[rev_idx][1]

    return path[rev_idx]


def replace_item_from_path(
    item: Any,
    path: List[Tuple[Any, Any]],
    replace_fn: ReplaceFunction
) -> None:
    """
    replace some object in ``item`` located at the end of ``path``, according
    to ``replace_fn``.

    ``replace_fn`` should take the original value, and return the new value
    for insertion into ``item``.  This function frequently involves string
    substitution, and possibly type conversions

    Parameters
    ----------
    item : Any
        The object to replace information in
    path : List[Tuple[Any, Any]]
        A "path" to a search match, as returned by walk_find_match
    replace_fn : ReplaceFunction
        A function that returns the replacement object
    """
    # need the final step to specify what is being replaced
    final_step = path[-1]
    # need the item one step before the last to perform the assignment on
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
        # simple field paths don't have a final (__sth__, ?) segment
        old_value = getattr(parent_item, path[-1][1])
        setattr(parent_item, path[-1][1], replace_fn(old_value))


def get_default_match_fn(search_regex: re.Pattern) -> MatchFunction:
    """
    Returns a standard match function using the provided regex pattern

    Parameters
    ----------
    search_regex : re.Pattern
        compiled regex pattern to match items against

    Returns
    -------
    MatchFunction
        a match function to be used in ``walk_find_match``
    """
    def match_fn(match):
        return search_regex.search(str(match)) is not None

    return match_fn


def get_default_replace_fn(
    replace_text: str,
    search_regex: re.Pattern
) -> ReplaceFunction:
    """
    Returns a standard replace function, which attempts to match the type of the
    item being replaced

    Parameters
    ----------
    replace_text : str
        text to replace
    search_regex : re.Pattern
        the compiled regex search pattern, for use in string replacements

    Returns
    -------
    ReplaceFunction
        a replacement function for use in ``replace_item_from_path``
    """
    def replace_fn(value):
        if isinstance(value, str):
            return search_regex.sub(replace_text, value)
        elif isinstance(value, int):
            # cast to float first
            return int(float(value))
        else:  # try to cast as original type
            return type(value)(replace_text)

    return replace_fn


@dataclass
class RegexFindReplace:
    """
    A specialized FindReplaceAction
    Limited to default regex search and replace function
    """
    # attribute access chain leading to the item of interest.
    # A simplified path please
    path: List[Tuple[Any, Any]]
    # search regex, used to generate path and replace elements
    search_regex: str
    # parameters used to re-construct replace function
    replace_text: str
    # case-sensitive
    case_sensitive: bool = True

    def to_action(self, target: Optional[Any] = None) -> FindReplaceAction:
        """Create FindReplaceAction from a SerializableFindReplaceAction"""
        flags = re.IGNORECASE if not self.case_sensitive else 0
        try:
            search_regex = re.compile(self.search_regex, flags=flags)
        except re.error:
            raise ValueError(f'regex is not valid: {self.search_regex}, '
                             'could not construct FindReplaceAction')
        replace_fn = get_default_replace_fn(
            self.replace_text, search_regex
        )
        if target:
            path = expand_path(self.path, target=target)
        else:
            path = self.path

        return FindReplaceAction(
            path=path,
            replace_fn=replace_fn,
            target=target,
            origin=self,
        )


@dataclass
class FindReplaceAction:
    path: List[Tuple[Any, Any]]
    replace_fn: ReplaceFunction
    # Union[ConfigurationFile, ProcedureFile], but circular imports
    target: Optional[Any] = None

    origin: Optional[RegexFindReplace] = None

    def apply(
        self,
        target: Optional[Union[ConfigurationFile, ProcedureFile]] = None,
        path: Optional[List[Tuple[Any, Any]]] = None,
        replace_fn: Optional[ReplaceFunction] = None
    ) -> bool:
        """
        Apply the find-replace action, return True if action was applied
        successfully.

        Can specify any of ``target``, ``path``, or ``replace_fn`` in order
        to use that object instead of the stored object

        Parameters
        ----------
        target : Optional[Union[ConfigurationFile, ProcedureFile]], optional
            The file to apply the find-replace action to, by default this applies
            to the current target of the action, by default None
        path : Optional[List[Tuple[Any, Any]]], optional
            A "path" to a search match, as returned by walk_find_match,
            by default None
        replace_fn : Optional[ReplaceFunction], optional
            A function that takes the value and returns the replaced value,
            by default None

        Returns
        -------
        bool
            the success of the apply action
        """

        target = target or self.target
        path = path or self.path
        replace_fn = replace_fn or self.replace_fn
        try:
            replace_item_from_path(target, path, replace_fn)
        except KeyError as ex:
            logger.warning(f'Unable to find key ({ex}) in file. '
                           'File may have already been edited')
            return False
        except Exception as ex:
            logger.warning(f'Unable to apply change. {ex}')
            return False

        return True

    def same_path(self, path: List[Tuple[Any, Any]]) -> bool:
        """Checks if this FindReplaceAction's path matches ``path``, ignoring objects"""
        return all([own_step[1] == other_step[1]
                    for own_step, other_step in zip(self.path, path)])
