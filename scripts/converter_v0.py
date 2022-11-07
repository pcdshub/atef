"""
This script will convert a prototype atef configuration file to the latest
supported (and numbered) version.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union, cast

import apischema
import yaml

import atef
import atef.config
from atef import serialization, tools
from atef.check import Comparison
from atef.type_hints import AnyPath

logger = logging.getLogger(__name__)
DESCRIPTION = __doc__


@dataclass
class IdentifierAndComparison:
    """
    Set of identifiers (IDs) and comparisons to perform on those identifiers.
    """

    #: An optional identifier for this set.
    name: Optional[str] = None
    #: PV name, attribute name, or test-specific identifier.
    ids: List[str] = field(default_factory=list)
    #: The comparisons to perform for *each* of the ids.
    comparisons: List[Comparison] = field(default_factory=list)


@dataclass
@serialization.as_tagged_union
class Configuration:
    """
    Configuration base class for shared settings between all configurations.

    Subclasses of Comparison will be serialized as a tagged union.  This means
    that the subclass name will be used as an identifier for the generated
    serialized dictionary (and JSON object).
    """

    #: Name tied to this configuration.
    name: Optional[str] = None
    #: Description tied to this configuration.
    description: Optional[str] = None
    #: Tags tied to this configuration.
    tags: Optional[List[str]] = None
    #: Comparison checklist for this configuration.
    checklist: List[IdentifierAndComparison] = field(default_factory=list)


@dataclass
class DeviceConfiguration(Configuration):
    """
    A configuration that is built to check one or more devices.

    Identifiers are by default assumed to be attribute (component) names of the
    devices.  Identifiers may refer to components on the device
    (``"component"`` would mean to access each device's ``.component``) or may
    refer to any level of sub-device components (``"sub_device.component"``
    would mean to access each device's ``.sub_device`` and that sub-device's
    ``.a`` component).
    """

    #: Happi device names which give meaning to self.checklist[].ids.
    devices: List[str] = field(default_factory=list)


@dataclass
class PVConfiguration(Configuration):
    """
    A configuration that is built to check live EPICS PVs.

    Identifiers are by default assumed to be PV names.
    """

    ...


@dataclass
class ToolConfiguration(Configuration):
    """
    A configuration unrelated to PVs or Devices which verifies status via some
    tool.

    Comparisons can optionally be run on the tool's results.
    """

    tool: tools.Tool = field(default_factory=tools.Ping)


AnyConfiguration = Union[
    PVConfiguration,
    DeviceConfiguration,
    ToolConfiguration,
]
PathItem = Union[
    AnyConfiguration,
    IdentifierAndComparison,
    Comparison,
    str,
]


@dataclass
class PrototypeConfigurationFile:
    #: configs: PVConfiguration, DeviceConfiguration, or ToolConfiguration.
    configs: List[Configuration]

    @classmethod
    def from_file(cls, filename: AnyPath) -> PrototypeConfigurationFile:
        """Load a configuration file from JSON or yaml."""
        filename = pathlib.Path(filename)
        if filename.suffix.lower() in (".yml", ".yaml"):
            return cls.from_yaml(filename)
        return cls.from_json(filename)

    @classmethod
    def from_json(cls, filename: AnyPath) -> PrototypeConfigurationFile:
        """Load a configuration file from JSON."""
        with open(filename) as fp:
            serialized_config = json.load(fp)
        return apischema.deserialize(cls, serialized_config)

    @classmethod
    def from_yaml(cls, filename: AnyPath) -> PrototypeConfigurationFile:
        """Load a configuration file from yaml."""
        with open(filename) as fp:
            serialized_config = yaml.safe_load(fp)
        return apischema.deserialize(cls, serialized_config)


def _split_shared_checklist(
    checklist: List[IdentifierAndComparison],
) -> Tuple[List[Comparison], Dict[str, List[Comparison]]]:
    """
    Split a prototype "checklist", consisting of pairs of identifiers and
    comparisons into the new format of "shared" and "per-identifier" (i.e.,
    pv/attr) comparisons.

    Parameters
    ----------
    checklist : List[IdentifierAndComparison]
        The prototype checklist.

    Returns
    -------
    List[Comparison]
        Shared comparisons.
    Dict[str, List[Comparison]]
        Per-identifier comparisons, with the identifier as the key.
    """
    shared = []
    by_identifier = {}
    if len(checklist) == 1:
        # If there is only one checklist, the comparisons can be considered
        # "shared".
        for check in checklist:
            for comparison in check.comparisons:
                shared.append(comparison)
                for identifier in check.ids:
                    by_identifier.setdefault(identifier, [])
    else:
        # Otherwise, comparisons from every checklist will become
        # per-identifier.
        for check in checklist:
            for comparison in check.comparisons:
                for identifier in check.ids:
                    by_identifier.setdefault(identifier, []).append(comparison)
    return shared, by_identifier


def convert_configuration(config: AnyConfiguration) -> atef.config.AnyConfiguration:
    """
    Convert a prototype Configuration to a supported one.

    Parameters
    ----------
    config : AnyConfiguration
        The old prototype configuration.

    Returns
    -------
    atef.config.AnyConfiguration
        The new and supported configuration.
    """
    if not isinstance(config, (DeviceConfiguration, PVConfiguration, ToolConfiguration)):
        raise ValueError(f"Unexpected and unsupported config type: {type(config)}")

    shared, by_identifier = _split_shared_checklist(config.checklist)
    if isinstance(config, DeviceConfiguration):
        return atef.config.DeviceConfiguration(
            name=config.name,
            description=config.description,
            tags=config.tags,
            devices=config.devices,
            by_attr=by_identifier,
            shared=shared,
        )

    if isinstance(config, PVConfiguration):
        return atef.config.PVConfiguration(
            name=config.name,
            description=config.description,
            tags=config.tags,
            by_pv=by_identifier,
            shared=shared,
        )

    if isinstance(config, ToolConfiguration):
        return atef.config.ToolConfiguration(
            name=config.name,
            description=config.description,
            tags=config.tags,
            tool=config.tool,
            shared=shared,
            by_attr=by_identifier,
        )


def load(filename: AnyPath) -> atef.config.ConfigurationFile:
    """
    Load the provided prototype atef configuration file to the latest
    supported (and numbered) version.

    Parameters
    ----------
    filename : AnyPath
        The filename to open.

    Returns
    -------
    atef.config.ConfigurationFile
        The converted configuration file.
    """
    old = PrototypeConfigurationFile.from_file(filename)
    new = atef.config.ConfigurationFile()
    for config in old.configs:
        config = cast(AnyConfiguration, config)
        new.root.configs.append(convert_configuration(config))
    return new


def convert(fn: AnyPath) -> str:
    """
    Convert the provided prototype atef configuration file, returning JSON
    to be saved.

    Parameters
    ----------
    filename : AnyPath
        The filename to open.

    Returns
    -------
    str
        The new file contents.
    """
    return json.dumps(load(fn).to_json(), indent=2)


def _create_arg_parser() -> argparse.ArgumentParser:
    """Create the argparser."""
    parser = argparse.ArgumentParser(
        description=DESCRIPTION, formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "--log",
        "-l",
        dest="log_level",
        default="INFO",
        type=str,
        help="Python logging level (e.g. DEBUG, INFO, WARNING)",
    )

    parser.add_argument(
        "filename",
        type=str,
        nargs="+",
        help="File(s) to convert",
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help="Convert and overwrite the files in-place",
    )
    return parser


def main(args=None) -> None:
    """Run the conversion tool."""
    args = _create_arg_parser().parse_args(args=args)
    log_level = args.log_level
    logger.setLevel(log_level)
    logging.basicConfig()

    for filename in args.filename:
        converted = convert(filename)

        if args.write:
            logger.warning("Overwriting converted file: %s", filename)
            with open(filename, "wt") as fp:
                print(converted, file=fp)
        else:
            print(f"-- {filename} --")
            print(converted)
            print()


if __name__ == "__main__":
    main()
