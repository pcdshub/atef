"""
`atef check` runs passive checkouts of devices given a configuration file.
"""

import argparse
from typing import Optional, Sequence

import apischema
import yaml

from ..check import ConfigurationFile, check_device, pv_config_to_device_config

DESCRIPTION = __doc__


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = DESCRIPTION
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument(
        "filename",
        type=str,
        help="Configuration filename",
    )

    argparser.add_argument(
        "--device",
        type=str,
        nargs="*",
        dest="devices",
        help="Limit checkout to the named device",
    )

    return argparser


def main(filename: str, devices: Optional[Sequence[str]] = None):
    serialized_config = yaml.safe_load(open(filename))
    config = apischema.deserialize(ConfigurationFile, serialized_config)
    print(config)
    for pv_config in config.pvs:
        dev_cls, dev_config = pv_config_to_device_config(pv_config)
        dev = dev_cls(name=pv_config.name or "PVConfig")
        check_device(dev, dev_config.checks)
