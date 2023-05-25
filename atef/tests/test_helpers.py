from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

import pytest

from atef.qt_helpers import QDataclassBridge, QDataclassList, QDataclassValue
from atef.type_hints import AnyDataclass


@pytest.fixture
def sample_dataclass() -> AnyDataclass:
    @dataclass
    class SampleDataclass:
        str_field: str = 'a'
        int_field: int = 1
        float_field: float = 0.5
        bool_field: bool = False
        other_field: Any = False
        complex_field: Union[str, int, float] = '2/3'

        sequence_field: Sequence[bool] = field(default_factory=list)
        list_str_field: List[str] = field(default_factory=lambda: ['a', 'b'])
        list_int_field: List[int] = field(default_factory=lambda: [1, 2])
        list_many_field: List[Union[str, int]] = field(
            default_factory=lambda: ['a', 1]
        )

        dict_field: Dict[str, int] = field(default_factory=lambda: {'a', 1})

        optional_str: Optional[str] = None
        optional_list: Optional[List[Any]] = None

    return SampleDataclass()


@pytest.fixture
def sample_bridge(sample_dataclass) -> QDataclassBridge:
    return QDataclassBridge(sample_dataclass)


@pytest.mark.parametrize('field, cls, changed_value_type', [
    ['str_field', QDataclassValue, 'str'],
    ['int_field', QDataclassValue, 'int'],
    ['float_field', QDataclassValue, 'double'],
    ['bool_field', QDataclassValue, 'bool'],
    ['complex_field', QDataclassValue, 'object'],
    ['dict_field', QDataclassValue, 'object'],
    ['sequence_field', QDataclassList, 'bool'],
    ['list_int_field', QDataclassList, 'int'],
    ['list_str_field', QDataclassList, 'str'],
    ['optional_list', QDataclassList, 'object'],
    ['optional_str', QDataclassValue, 'object'],
])
def test_qt_bridge_types(
    sample_bridge: QDataclassBridge,
    field: str,
    cls: Union[QDataclassValue, QDataclassList],
    changed_value_type: str
):
    assert hasattr(sample_bridge, field)
    bridge_field = getattr(sample_bridge, field)
    assert isinstance(bridge_field, cls)

    # weird way of checking the expected type of the signal: 2changed_value(QString)
    assert changed_value_type in bridge_field.changed_value.signal.lower()
