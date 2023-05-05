import pathlib
from typing import Dict, Protocol, Union

AnyPath = Union[str, pathlib.Path]
Number = Union[int, float]
PrimitiveType = Union[str, int, bool, float]


class AnyDataclass(Protocol):
    """
    Protocol stub shamelessly lifted from stackoverflow to hint at dataclass
    """
    __dataclass_fields__: Dict
