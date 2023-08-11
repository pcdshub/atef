from typing import Any, List

from bluesky.plans import count
from bluesky_queueserver import parameter_annotation_decorator


def unmarked_plan(dets, num, default_arg=1):
    yield from count(dets, num=num)


def native_hint_plan(dets: List[Any], num: int, default_arg=1):
    yield from count(dets, num=num)


def docstring_plan(dets: List[Any], num: int, default_arg=1):
    """This is the docstring plan

    Parameters
    ----------
    dets : List[__DEVICE__]
        This is a list of devices to read as detectors
    num : int
        Number of times to acquire
    default_arg : int, optional
        not used I guess, by default 1

    Yields
    ------
    Any
        this is a plan
    """
    yield from count(dets, num=num)


@parameter_annotation_decorator({
    "parameters": {
        "dets": {
            "annotation": "typing.List[typing.List[__DEVICE__]]",
            "description": "detector_desc, param_ann_plan"
        },
        "num": {
            "annotation": "str"  # takes prescedence
        }
    }
})
def param_ann_plan(dets: List[List[Any]], num: int, default_arg=1):
    yield from count(dets, num=num)
