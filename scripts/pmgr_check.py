"""
This script creates an atef check from a pmgr configuration.  The configuration will
be converted into a PVConfiguration.  Note that default tolerances will be used for
checks.

An example invocation might be:
"python scripts/pmgr_check.py cxi "KB1 DS SLIT LEF" CXI:KB1:MMS:13 test_pmgr_checkout.json"
"""
import argparse
import json
import logging
from typing import Any, Dict

import apischema
from pmgr import pmgrAPI

from atef.check import Equals
from atef.config import ConfigurationFile, ConfigurationGroup, PVConfiguration

DESCRIPTION = __doc__
logger = logging.getLogger()


def get_pv(prefix: str, key: str):
    """
    Parse key from pmgr configuration data dictionary.  Keys are of the form:
    'FLD_ACCL' or 'FLD_BDST', denoting the suffixes to append to `prefix`.

    Ignores unrecognized keys (keys without expected prefixes)

    Parameters
    ----------
    prefix : str
        the EPICS PV prefix
    key : str
        the key from a pmgr configuration data dictionary

    Returns
    -------
    str
        a fully qualified EPICS PV
    """
    if 'FLD_' in key:
        suffix = key.removeprefix('FLD')
    elif 'PV_' in key:
        suffix = key.removeprefix('PV')
    else:
        logger.debug(f'Unrecognized key provided: {key}')
        return

    # general string fixing... ew
    suffix_parts = suffix.split("__")
    new_suffix_list = [":".join(substr.split('_')) for substr in suffix_parts]
    suffix = '_'.join(new_suffix_list)
    suffix = ".".join(suffix.rsplit(":", 1))
    pv = prefix + suffix
    return pv


def get_cfg_data(hutch: str, config_name: str) -> Dict[str, Any]:
    """
    Get pmgr config data corresponding to ``config_name`` and ``hutch``

    Parameters
    ----------
    hutch : str
        the hutch name, e.g. 'cxi'
    config_name : str
        the pmgr config name, e.g. 'KB1 DS SLIT LEF'

    Returns
    -------
    Dict[str, Any]
        The configuration values dictionary
    """
    pm = pmgrAPI.pmgrAPI('ims_motor', hutch.lower())
    cfg_data = pm.get_config_values(config_name)

    return cfg_data


def create_atef_check(config_name: str, cfg_data: Dict[str, Any], prefix: str) -> PVConfiguration:
    """
    Construct the full atef checkout.  Simply creates an Equals comparison for each
    value in the pmgr configuration, and groups it in a PVConfiguration

    Parameters
    ----------
    config_name : str
        the pmgr config name, e.g. 'KB1 DS SLIT LEF'
    cfg_data : Dict[str, Any]
        the configuration values dictionary, as returned from `get_cfg_data`
    prefix : str
        the EPICS Prefix

    Returns
    -------
    PVConfiguration
        The completed atef checkout
    """
    pv_config = PVConfiguration(name=f'check motor config: {config_name}',
                                description='Configuration pulled from pmgr')

    for key, value in cfg_data.items():
        pv = get_pv(prefix, key)
        if pv is None:
            continue

        comp = Equals(name=f'check for {pv}', description=f'Checking {pv} == {value}',
                      value=value or 0)

        # would need to handle first-time additions
        pv_config.by_pv[pv] = [comp]

    return pv_config


def _create_arg_parser() -> argparse.ArgumentParser:
    """Create the argparser."""
    parser = argparse.ArgumentParser(
        description=DESCRIPTION, formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "hutch",
        type=str,
        help="name of hutch, e.g. 'cxi'",
    )

    parser.add_argument(
        "pmgr_name",
        type=str,
        help="name of stored pmgr configuration, case and whitespace sensitive."
             "e.g. 'KB1 DS SLIT LEF'",
    )

    parser.add_argument(
        "prefix",
        type=str,
        help="EPICS PV Prefix, e.g. 'CXI:KB1:MMS:13'",
    )

    parser.add_argument(
        "filename",
        type=str,
        help="Output filepath",
    )

    parser.add_argument(
        "--log",
        "-l",
        dest="log_level",
        default="INFO",
        type=str,
        help="Python logging level (e.g. DEBUG, INFO, WARNING), by default INFO",
    )

    parser.add_argument(
        "--table",
        "-t",
        dest="table_name",
        default="ims_motor",
        type=str,
        help="Table type, by default 'ims_motor'",
    )

    return parser


def main(args=None) -> None:
    """Get pmgr data and contruct checkout."""
    argp = _create_arg_parser().parse_args(args=args)
    log_level = argp.log_level
    logger.setLevel(log_level)
    logging.basicConfig()

    cfg_data = get_cfg_data(argp.hutch, argp.pmgr_name)
    pv_config = create_atef_check(argp.pmgr_name, cfg_data, argp.prefix)
    # try looking at the whole thing
    file = ConfigurationFile(root=ConfigurationGroup(name='base group', configs=[pv_config]))

    ser = apischema.serialize(ConfigurationFile, file)

    with open(argp.filename, 'w') as fd:
        json.dump(ser, fd, indent=2)


if __name__ == "__main__":
    main()
