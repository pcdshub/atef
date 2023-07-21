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

_PLAN_MODULES = ['bluesky.plans', 'nabs.plans']


def get_default_namespace() -> Dict[str, Any]:
    """ Look in the basic places to get the default namespace"""
    nspace = {}
    # Load plans: bluesky.plans, nabs
    for module_name in _PLAN_MODULES:
        try:
            load_startup_module('bluesky.plans', nspace=nspace)
        except ScriptLoadingError as ex:
            logger.warning(f"unable to load namespace from module '{module_name}'"
                           f": {ex}")

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
    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(BlueskyState, cls).__new__(cls)
        return cls.instance

    def __init__(self):
        self.db = databroker.Broker.named('temp')
        self.RE = RunEngine({})
        self.RE.subscribe(self.db.insert)

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

    def run_plan(self, item) -> UUID:
        parsed_plan = prepare_plan(
            item,
            plans_in_nspace=self.plans_in_ns,
            devices_in_nspace=self.devices_in_ns,
            allowed_plans=self.allowed_plans,
            allowed_devices=self.allowed_devices
        )

        # actually run plan
        run_uuid = self.RE(parsed_plan["callable"](
                           *parsed_plan["args"], **parsed_plan["kwargs"]))

        return run_uuid


def run_in_local_RE(item) -> UUID:
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
    BSState = BlueskyState()
    BSState.get_allowed_plans_and_devices(destination=PlanDestination.local_)
    return BSState.run_plan(item)
