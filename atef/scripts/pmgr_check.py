"""
This script creates an atef check from a pmgr configuration.  The configuration will
be converted into a PVConfiguration.  Note that default tolerances will be used for
checks.

An example invocation might be:
python scripts/pmgr_check.py cxi test_pmgr_checkout.json --names "KB1 DS SLIT LEF" --prefix CXI:KB1:MMS:13
"""
import argparse
import json
import logging
from typing import Any, Dict, List

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
    if 'FLD_' in key:
        suffix = ".".join(suffix.rsplit(":", 1))
    pv = prefix + suffix
    return pv


def get_cfg_data(
    hutch: str,
    config_name: str,
    table_name: str = 'ims_motor'
) -> Dict[str, Any]:
    """
    Get pmgr config data corresponding to ``config_name`` and ``hutch``

    Parameters
    ----------
    hutch : str
        the hutch name, e.g. 'cxi'
    config_name : str
        the pmgr config name, e.g. 'KB1 DS SLIT LEF'
    table_name : str
        the name of the pmgr table to examine, by default 'ims_motor'

    Returns
    -------
    Dict[str, Any]
        The configuration values dictionary
    """
    pm = pmgrAPI.pmgrAPI(table_name, hutch.lower())
    cfg_data = pm.get_config_values(config_name)

    return cfg_data


def create_atef_check(
    config_name: str,
    cfg_data: Dict[str, Any],
    prefix: str
) -> PVConfiguration:
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


def build_arg_parser(argparser=None) -> argparse.ArgumentParser:
    """Create the argparser."""
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = DESCRIPTION
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument(
        "--names",
        "-n",
        dest="pmgr_names",
        type=str,
        nargs="+",
        help="a list of stored pmgr configuration names, case and whitespace sensitive. "
             "e.g. 'KB1 DS SLIT LEF'.  Length must match --prefixes",
    )

    argparser.add_argument(
        "--prefixes",
        "-p",
        dest="prefixes",
        type=str,
        nargs="+",
        help="a list of EPICS PV prefixes, e.g. 'CXI:KB1:MMS:13'.  Length must match --names",
    )

    argparser.add_argument(
        "--table",
        "-t",
        dest="table_name",
        default="ims_motor",
        type=str,
        help="Table type, by default 'ims_motor'",
    )

    argparser.add_argument(
        dest="hutch",
        type=str,
        help="name of hutch, e.g. 'cxi'",
    )

    argparser.add_argument(
        "filename",
        type=str,
        help="Output filepath",
    )

    return argparser


def main(
    hutch: str,
    filename: str,
    pmgr_names: List[str],
    prefixes: List[str],
    table_name: str = 'ims_motor'
) -> None:
    if len(prefixes) != len(pmgr_names):
        raise ValueError('Must provide the same number of configuration names '
                         f'{len(pmgr_names)} and prefixes {len(prefixes)}')

    file = ConfigurationFile(root=ConfigurationGroup(name='base group', configs=[]))
    for prefix, name in zip(prefixes, pmgr_names):
        cfg_data = get_cfg_data(hutch, name, table_name=table_name)
        pv_config = create_atef_check(name, cfg_data, prefix)

        file.root.configs.append(pv_config)

    ser = apischema.serialize(ConfigurationFile, file)

    with open(filename, 'w') as fd:
        json.dump(ser, fd, indent=2)


def main_script(args=None) -> None:
    """Get pmgr data and contruct checkout."""
    parser = build_arg_parser()
    # Add log_level if running file alone
    parser.add_argument(
        "--log",
        "-l",
        dest="log_level",
        default="INFO",
        type=str,
        help="Python logging level (e.g. DEBUG, INFO, WARNING), by default INFO",
    )
    args = parser.parse_args()
    kwargs = vars(args)
    logger.setLevel(args.log_level)
    kwargs.pop('log_level')
    logging.basicConfig()

    main(**kwargs)


if __name__ == "__main__":
    main_script()
