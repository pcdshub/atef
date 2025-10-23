"""Utility functions for main configuration dataclasses (both active anc pasive)"""

from atef.type_hints import AnyDataclass


def get_parent_file(item) -> AnyDataclass:
    """
    Walk up the tree and return the top-most parent
    """
    curr_item = item
    while hasattr(curr_item, "parent"):
        next_item = getattr(curr_item, "parent", None)
        if next_item is None:
            return curr_item
        curr_item = next_item
    return curr_item
