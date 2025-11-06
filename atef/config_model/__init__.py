from typing import Union

from apischema import ValidationError

from atef.config_model.active import ProcedureFile
from atef.config_model.passive import ConfigurationFile
from atef.type_hints import AnyPath


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
