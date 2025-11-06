import logging
from uuid import UUID

from atef.status_logging import configure_and_get_status_logger
from atef.type_hints import AnyDataclass


def get_parent_file(item: AnyDataclass) -> AnyDataclass:
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


def get_status_logger(item: AnyDataclass) -> logging.Logger:
    """
    Get the status logger for this step, using the uuid of the ultimate ancestor
    of `item`
    """
    top_file = get_parent_file(item)
    file_id = getattr(top_file, "uuid", "status_logger")
    if isinstance(file_id, UUID):
        return configure_and_get_status_logger(file_id)
    else:
        return logging.getLogger(file_id)
