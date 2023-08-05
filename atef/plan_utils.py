import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

import databroker
from bluesky import RunEngine
from bluesky_queueserver.manager.profile_ops import (
    ScriptLoadingError, existing_plans_and_devices_from_nspace,
    load_allowed_plans_and_devices, load_startup_module, prepare_plan)

from atef.enums import PlanDestination
from atef.util import get_happi_client

logger = logging.getLogger(__name__)


DEFAULT_PERMISSIONS_PATH = (Path(__file__).parent / "tests" / "profiles" /
                            "user_group_permissions.yaml")

PERMISSIONS_PATH = os.environ.get('ATEF_PERMISSIONS_PATH') or DEFAULT_PERMISSIONS_PATH

_PLAN_MODULES = ['atef.annotated_plans']  # , 'nabs.plans']


def get_default_namespace() -> Dict[str, Any]:
    """ Look in the basic places to get the default namespace"""
    nspace = {}
    # Load plans: bluesky.plans, nabs
    for module_name in _PLAN_MODULES:
        try:
            load_startup_module(module_name, nspace=nspace)
        except ScriptLoadingError as ex:
            logger.warning(f"unable to load namespace from module '{module_name}'"
                           f": {ex}")

    # TODO: think about caching this, don't want to re-get all the happi devices each run
    # load devices: happi
    client = get_happi_client()
    results = client.search()
    for res in results:
        nspace[res.metadata['name']] = res.get()

    return nspace


class BlueskyState:
    # think about making this a singleton thread.
    # Tryto include:
    # - self.env_state, self.running_plan_exec_state
    # - self.re_state (property)
    # - self._execute_plan_or_task() -> self._execte_plan() -> self._generate_new_plan() [runs in RE]
    # - self._generate_continued_plan()
    # - self.run(), self.start()  --> Used by multiprocessing.Process, may have different API
    # - shutdown code?

    def __init__(self):
        self.run_map = {}
        # Set up plans / devices to stay here for future access?
        self.plans_md = {}
        self.devices_md = {}
        self.plans_in_ns = {}
        self.devices_in_ns = {}

        self.allowed_plans = {}
        self.allowed_devices = {}

    def get_allowed_plans_and_devices(
        self,
        destination: PlanDestination,
        hutch: Optional[str] = None,
    ) -> Tuple[Dict, Dict]:
        """
        Gather the allowed plans and devices for a given hutch and destination

        Plans taken from a standard list

        TODO: set up yaml file for each hutch? specify an env var for these?
        - epad(path_to_yaml)

        TODO: machinery around yaml

        TODO: if the destination is a queueserver, we should query it for its
        permitted namespace
        """
        if self.allowed_devices and self.allowed_plans:
            return self.allowed_plans, self.allowed_devices

        nspace = get_default_namespace()
        epd = existing_plans_and_devices_from_nspace(nspace=nspace)
        self.plans_md, self.devices_md, self.plans_in_ns, self.devices_in_ns = epd

        self.allowed_plans, self.allowed_devices = load_allowed_plans_and_devices(
            existing_plans=self.plans_md, existing_devices=self.devices_md,
            path_user_group_permissions=PERMISSIONS_PATH
        )

        return self.allowed_plans, self.allowed_devices

    def register_identifier(self, identifier: str) -> None:
        if identifier in self.run_map:
            raise ValueError('identifier already registered')
        self.run_map[identifier] = None


class GlobalRunEngine:
    RE: RunEngine
    db: databroker.Broker

    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(GlobalRunEngine, cls).__new__(cls)
            cls.db = databroker.Broker.named('temp')
            cls.RE = RunEngine({})
            cls.RE.subscribe(cls.db.insert)

        return cls.instance

    def run_plan(self, state: BlueskyState, item: Dict[str, Any], identifier: str) -> Tuple[UUID, ...]:
        parsed_plan = prepare_plan(
            item,
            plans_in_nspace=state.plans_in_ns,
            devices_in_nspace=state.devices_in_ns,
            allowed_plans=state.allowed_plans,
            allowed_devices=state.allowed_devices
        )

        # actually run plan
        run_uuids = self.RE(parsed_plan["callable"](
            *parsed_plan["args"], **parsed_plan["kwargs"]
        ))
        state.run_map[identifier] = run_uuids
        return run_uuids


def register_run_identifier(state: BlueskyState, name: str) -> str:
    """
    generate and return a unique identifer and register it to the BlueskyState.

    Attempts to register the given name,
    """
    new_name = name
    attempt_ct = 1
    while (new_name in state.run_map) and (attempt_ct < 100):
        new_name = new_name + f'_{attempt_ct}'
        attempt_ct += 1

    if attempt_ct >= 100:
        raise RuntimeError(f'{attempt_ct} runs with the identifier ({name}) '
                           'found.  Please pick a more unique name.')

    state.register_identifier(new_name)

    return new_name


def run_in_local_RE(item: Dict[str, Any], identifier: str, state: BlueskyState):
    """
    Run a plan item in a local RunEngine.  Should:
    - once again verify the plan...
    - get current RE instance
    - Run plan
    - return uuid for databroker access
    """
    # TODO: Dispatch to worker thread with stop/pause methods available
    # put in QThread or other thread?...

    # Can we just use REWorker from bsqs?  is a multiprocessing.Process, to re_worker.start()
    state.get_allowed_plans_and_devices(destination=PlanDestination.local_)
    gre = GlobalRunEngine()
    gre.run_plan(state, item, identifier)
