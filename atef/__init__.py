from typing import Union

from apischema import ValidationError

from atef.config import ConfigurationFile
from atef.procedure import ProcedureFile
from atef.type_hints import AnyPath
from atef.version import __version__  # noqa: F401


def load_file(filepath: AnyPath) -> Union[ConfigurationFile, ProcedureFile]:
    try:
        data = ConfigurationFile.from_filename(filepath)
    except ValidationError:
        try:
            data = ProcedureFile.from_filename(filepath)
        except ValidationError:
            raise ValueError(f'failed to open file ({filepath}) as either active '
                             'or passive checkout')

    return data


__all__ = ["load_file"]
