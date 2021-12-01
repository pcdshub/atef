from collections import defaultdict
from collections.abc import AsyncIterable, Callable, Iterator
from dataclasses import dataclass, field
from types import new_class
from typing import Any, Dict, List, TypeVar, get_type_hints

from apischema import deserializer, schema, serializer, type_name
from apischema.conversions import Conversion
from apischema.metadata import conversion
from apischema.objects import object_deserialization
from apischema.tagged_unions import Tagged, TaggedUnion, get_tagged
from apischema.utils import to_pascal_case

_alternative_constructors: Dict[type, List[Callable]] = defaultdict(list)
Func = TypeVar("Func", bound=Callable)


def alternative_constructor(func: Func) -> Func:
    _alternative_constructors[get_type_hints(func)["return"]].append(func)
    return func


def rec_subclasses(cls: type) -> Iterator:
    """Recursive implementation of type.__subclasses__"""
    for sub_cls in cls.__subclasses__():
        yield sub_cls
        yield from rec_subclasses(sub_cls)


Cls = TypeVar("Cls", bound=type)


def as_tagged_union(cls: Cls) -> Cls:
    def serialization() -> Conversion:
        annotations = {sub.__name__: Tagged[sub] for sub in rec_subclasses(cls)}
        namespace = {"__annotations__": annotations}
        tagged_union = new_class(
            cls.__name__, (TaggedUnion,), exec_body=lambda ns: ns.update(namespace)
        )
        return Conversion(
            lambda obj: tagged_union(**{obj.__class__.__name__: obj}),
            source=cls,
            target=tagged_union,
            # Conversion must not be inherited because it would lead to
            # infinite recursion otherwise
            inherited=False,
        )

    def deserialization() -> Conversion:
        annotations: dict[str, Any] = {}
        namespace: dict[str, Any] = {"__annotations__": annotations}
        for sub in rec_subclasses(cls):
            annotations[sub.__name__] = Tagged[sub]
            # Add tagged fields for all its alternative constructors
            for constructor in _alternative_constructors.get(sub, ()):
                # Build the alias of the field
                alias = to_pascal_case(constructor.__name__)
                # object_deserialization uses get_type_hints, but the constructor
                # return type is stringified and the class not defined yet,
                # so it must be assigned manually
                constructor.__annotations__["return"] = sub
                # Use object_deserialization to wrap constructor as deserializer
                deserialization = object_deserialization(constructor, type_name(alias))
                # Add constructor tagged field with its conversion
                annotations[alias] = Tagged[sub]
                namespace[alias] = Tagged(conversion(deserialization=deserialization))
        # Create the deserialization tagged union class
        tagged_union = new_class(
            cls.__name__, (TaggedUnion,), exec_body=lambda ns: ns.update(namespace)
        )
        return Conversion(
            lambda obj: get_tagged(obj)[1], source=tagged_union, target=cls
        )

    deserializer(lazy=deserialization, target=cls)
    serializer(lazy=serialization, source=cls)
    return cls
