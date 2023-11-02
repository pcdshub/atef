"""
Helpers for walking dataclasses.

If relying on Prepared dataclass walk methods (walk_groups, walk_comparisons),
note that if a configuration fails to prepare, they will be skipped
"""
from __future__ import annotations

from typing import Generator, List, Tuple, Union

from atef.check import Comparison
from atef.config import (AnyPreparedConfiguration, Configuration,
                         PreparedComparison, PreparedConfiguration,
                         PreparedFile)
from atef.procedure import (AnyPreparedProcedure, PreparedProcedureFile,
                            PreparedProcedureStep, ProcedureStep)


def walk_config_file(
    config: Union[PreparedFile, PreparedConfiguration, PreparedComparison],
    level: int = 0
) -> Generator[Tuple[Union[AnyPreparedConfiguration, PreparedComparison], int], None, None]:
    """
    Yields each config and comparison and its depth
    Performs a recursive depth-first search

    Parameters
    ----------
    config : Union[PreparedFile, PreparedConfiguration, PreparedComparison]
        the configuration or comparison to walk
    level : int, optional
        the current recursion depth, by default 0

    Yields
    ------
    Generator[Tuple[Any, int], None, None]
    """
    yield config, level
    if isinstance(config, PreparedFile):
        yield from walk_config_file(config.root, level=level+1)
    elif isinstance(config, PreparedConfiguration):
        if hasattr(config, 'configs'):
            for conf in config.configs:
                yield from walk_config_file(conf, level=level+1)
        if hasattr(config, 'comparisons'):
            for comp in config.comparisons:
                yield from walk_config_file(comp, level=level+1)


def walk_procedure_file(
    config: Union[PreparedProcedureFile, PreparedProcedureStep, PreparedComparison],
    level: int = 0
) -> Generator[Tuple[Union[AnyPreparedProcedure, PreparedComparison], int], None, None]:
    """
    Yields each ProcedureStep / Comparison and its depth
    Performs a recursive depth-first search

    Parameters
    ----------
    config : Union[PreparedProcedureFile, PreparedProcedureStep,
                    PreparedComparison]
        the item to yield and walk through
    level : int, optional
        the current recursion depth, by default 0

    Yields
    ------
    Generator[Tuple[Any, int], None, None]
    """
    yield config, level
    if isinstance(config, PreparedProcedureFile):
        yield from walk_procedure_file(config.root, level=level+1)
    elif isinstance(config, PreparedProcedureStep):
        for sub_step in getattr(config, 'steps', []):
            yield from walk_procedure_file(sub_step, level=level+1)
        if hasattr(config, 'walk_comparisons'):
            for sub_comp in config.walk_comparisons():
                yield from walk_procedure_file(sub_comp, level=level+1)


def walk_steps(
    step: Union[ProcedureStep, PreparedProcedureStep]
) -> Generator[Union[ProcedureStep, PreparedProcedureStep], None, None]:
    """
    Yield ProedureSteps in ``step``, depth-first.

    Parameters
    ----------
    step : ProcedureStep
        Step to yield ProcedureSteps from

    Yields
    ------
    Generator[ProcedureStep, None, None]
    """
    yield step
    for sub_step in getattr(step, 'steps', []):
        yield from walk_steps(sub_step)


def get_prepared_step(
    prepared_file: PreparedProcedureFile,
    origin: Union[ProcedureStep, Comparison],
) -> List[Union[PreparedProcedureStep, PreparedComparison]]:
    """
    Gather all PreparedProcedureStep dataclasses the correspond to the original
    ProcedureStep.
    If a PreparedProcedureStep also has comparisions, use the walk_comparisons
    method to check if the "origin" matches any of thoes comparisons

    Only relevant for active checkouts.

    Parameters
    ----------
    prepared_file : PreparedProcedureFile
        the PreparedProcedureFile to search through
    origin : Union[ProcedureStep, Comparison]
        the step / comparison to match

    Returns
    -------
    List[Union[PreparedProcedureStep, PreparedComparison]]
        the PreparedProcedureStep's or PreparedComparison's related to ``origin``
    """
    # As of the writing of this docstring, this helper is only expected to return
    # lists of length 1.  However in order to match the passive checkout workflow,
    # we still return a list of relevant steps or comparisons.
    matched_steps = []
    for pstep in walk_steps(prepared_file.root):
        if getattr(pstep, 'origin', None) is origin:
            matched_steps.append(pstep)
        # check PreparedComparisons, which might be included in some steps
        if hasattr(pstep, 'walk_comparisons'):
            for comp in pstep.walk_comparisons():
                if comp.comparison is origin:
                    matched_steps.append(comp)

    return matched_steps


def get_relevant_configs_comps(
    prepared_file: PreparedFile,
    original_c: Union[Configuration, Comparison]
) -> List[Union[PreparedConfiguration, PreparedComparison]]:
    """
    Gather all the PreparedConfiguration or PreparedComparison dataclasses
    that correspond to the original comparison or config.

    Phrased another way: maps prepared comparisons onto the comparison
    seen in the GUI

    Currently for passive checkout files only

    Parameters
    ----------
    prepared_file : PreparedFile
        the file containing configs or comparisons to be gathered
    original_c : Union[Configuration, Comparison]
        the comparison to match PreparedComparison or PreparedConfigurations to

    Returns
    -------
    List[Union[PreparedConfiguration, PreparedComparison]]:
        the configuration or comparison dataclasses related to ``original_c``
    """
    matched_c = []

    for config in prepared_file.walk_groups():
        if config.config is original_c:
            matched_c.append(config)

    for comp in prepared_file.walk_comparisons():
        if comp.comparison is original_c:
            matched_c.append(comp)

    return matched_c
