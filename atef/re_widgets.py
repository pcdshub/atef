# First-pass attempt for RE and plan-related widgets; some code borrowed from
# bluesky-widgets-demo

import os

from bluesky_widgets.models.auto_plot_builders import AutoImages, AutoLines
# from bluesky_widgets.models.plot_builders import Lines
# from bluesky_widgets.models.plot_specs import Axes, Figure
from bluesky_widgets.models.run_engine_client import RunEngineClient
from bluesky_widgets.models.search import Search
# from bluesky_widgets.qt.figures import QtFigures
from bluesky_widgets.qt.run_engine_client import (QtReConsoleMonitor,
                                                  QtReEnvironmentControls,
                                                  QtReExecutionControls,
                                                  QtReManagerConnection,
                                                  QtRePlanEditor,
                                                  QtRePlanHistory,
                                                  QtRePlanQueue,
                                                  QtReQueueControls,
                                                  QtReRunningPlan,
                                                  QtReStatusMonitor)
# from bluesky_widgets.qt.search import QtSearch
from bluesky_widgets.utils.event import Event
from qtpy.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget


class QtRunEngineManager(QWidget):
    def __init__(self, model, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = model
        vbox = QVBoxLayout()
        hbox = QHBoxLayout()
        hbox.addWidget(QtReManagerConnection(model))
        hbox.addWidget(QtReEnvironmentControls(model))
        hbox.addWidget(QtReQueueControls(model))
        hbox.addWidget(QtReExecutionControls(model))
        hbox.addWidget(QtReStatusMonitor(model))

        hbox.addStretch()
        vbox.addLayout(hbox)

        hbox = QHBoxLayout()
        vbox1 = QVBoxLayout()

        # Register plan editor; opening plans in the editor by double-clicking
        # the plan in the table
        pe = QtRePlanEditor(model)
        pq = QtRePlanQueue(model)
        pq.registered_item_editors.append(pe.edit_queue_item)

        vbox1.addWidget(pe, stretch=1)
        vbox1.addWidget(pq, stretch=1)
        hbox.addLayout(vbox1)
        vbox2 = QVBoxLayout()
        vbox2.addWidget(QtReRunningPlan(model), stretch=1)
        vbox2.addWidget(QtRePlanHistory(model), stretch=2)
        vbox2.addWidget(QtReConsoleMonitor(model), stretch=1)
        hbox.addLayout(vbox2)
        vbox.addLayout(hbox)
        self.setLayout(vbox)


headings = (
    "Scan ID",
    "Plan Name",
    "Scanning",
    "Start Time",
    "Duration",
    "Unique ID",
)


def get_columns_for_run(run):
    """
    Given a BlueskyRun, format a row for the table of search results.
    """
    from datetime import datetime

    metadata = run.describe()["metadata"]
    start = metadata["start"]
    stop = metadata["stop"]
    start_time = datetime.fromtimestamp(start["time"])
    motors = start.get("motors", "-")
    if stop is None:
        str_duration = "-"
    else:
        duration = datetime.fromtimestamp(stop["time"]) - start_time
        str_duration = str(duration)
        str_duration = str_duration[: str_duration.index(".")]
    return (
        start.get("scan_id", "-"),
        start.get("plan_name", "-"),
        ", ".join(motors),
        start_time.strftime("%Y-%m-%d %H:%M:%S"),
        str_duration,
        start["uid"][:8],
    )


class SearchWithButton(Search):
    """
    A Search model with a method to handle a click event.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events.add(view=Event)


class Model:
    def __init__(
        self,
        zmq_server_address=os.environ.get("QSERVER_ZMQ_ADDRESS", None),
        zmq_subscribe_address=os.environ.get("QSERVER_ZMQ_CONSOLE_ADDRESS", None),
    ):
        self.search = SearchWithButton(None, columns=get_columns_for_run)
        self.auto_plot_builders = [AutoLines(max_runs=3), AutoImages(max_runs=1)]
        self.run_engine = RunEngineClient(
            zmq_server_address=zmq_server_address,
            zmq_subscribe_address=zmq_subscribe_address,
        )
