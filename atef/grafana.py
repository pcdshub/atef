from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Union

import apischema


@dataclass
class DashboardTimePicker:
    refresh_intervals: List[str] = field(
        default_factory=lambda: [
            "5s",
            "10s",
            "30s",
            "1m",
            "5m",
            "15m",
            "30m",
            "1h",
            "2h",
            "1d",
        ]
    )
    timeOptions: List[str] = field(
        default_factory=lambda: [
            "5m",
            "15m",
            "1h",
            "6h",
            "12h",
            "24h",
            "2d",
            "7d",
            "30d",
        ]
    )


@dataclass
class DashboardInput:
    ...


@dataclass
class DashboardLink:
    targetBlank: bool = True
    title: str = ""
    url: str = "http://example.com"


@dataclass
class ThresholdStep:
    color: str
    index: Optional[int] = None
    value: Optional[float] = None
    line: bool = True
    op: str = "gt"
    yaxis: str = "left"


@dataclass
class FieldThreshold:
    steps: List[ThresholdStep] = field(default_factory=list)


@dataclass
class GridPosition:
    h: int = 0
    w: int = 0
    x: int = 0
    y: int = 0


@dataclass
class Panel:
    collapsed: bool = False
    gridPos: GridPosition = field(default_factory=GridPosition)
    id: int = 0
    pluginVersion: str = ""
    title: str = ""
    description: str = ""
    targets: List[AnyPanelTarget] = field(default_factory=list)
    links: List[DashboardLink] = field(default_factory=list)


@dataclass
class RowPanel(Panel):
    type: Literal["row"] = "row"

    panels: List[AnyPanel] = field(default_factory=list)


@dataclass
class ReduceOptions:
    calcs: List[str] = field(default_factory=list)
    fields: str = ""
    values: bool = False


@dataclass
class BarGaugeOptions:
    displayMode: str = "basic"
    orientation: str = "auto"
    reduceOptions: ReduceOptions = field(default_factory=ReduceOptions)
    showUnfilled: bool = False
    text: dict = field(default_factory=dict)


@dataclass
class PanelTarget:
    ...


@dataclass
class EpicsArchiverFunction:
    ...


@dataclass
class EpicsArchiverPanelTarget(PanelTarget):
    alias: str = ""
    aliasPattern: str = ""
    functions: List[EpicsArchiverFunction] = field(default_factory=list)
    hide: bool = False
    operator: str = ""
    refId: str = "A"
    regex: bool = False
    stream: bool = True
    strmCap: str = ""
    strmInt: str = "1m"
    target: str = ""


AnyPanelTarget = Union[EpicsArchiverPanelTarget]


@dataclass
class BarGaugePanel(Panel):
    type: Literal["bargauge"] = "bargauge"

    fieldConfig: Dict = field(default_factory=dict)
    options: BarGaugeOptions = field(default_factory=BarGaugeOptions)


@dataclass
class GaugePanel(Panel):
    type: Literal["gauge"] = "gauge"

    fieldConfig: Dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)


@dataclass
class StatPanelOptions:
    colorMode: str = "value"
    graphMode: str = "none"
    justifyMode: str = "center"
    orientation: str = "auto"
    reduceOptions: ReduceOptions = field(default_factory=ReduceOptions)
    calcs: List[str] = field(default_factory=list)
    fields: str = ""
    values: bool = False
    text: Dict = field(default_factory=dict)  # TODO
    textMode: str = "auto"


@dataclass
class StatPanel(Panel):
    type: Literal["stat"] = "stat"

    fieldConfig: Dict = field(default_factory=dict)
    options: StatPanelOptions = field(default_factory=StatPanelOptions)


@dataclass
class GraphTooltip:
    shared: bool = True
    sort: int = 0
    value_type: str = "individual"


@dataclass
class GraphPanelOptions:
    alertThreshold: bool = True


@dataclass
class GraphAxis:
    hashKey: str = field(default="", metadata=apischema.alias("$$hashKey"))
    format: str = ""
    logBase: int = 1
    show: bool = True
    mode: str = "time"
    values: list = field(default_factory=list)


@dataclass
class GraphYAxisSettings:
    align: bool = False


@dataclass
class GraphLegend:
    avg: bool = False
    current: bool = False
    max: bool = False
    min: bool = False
    show: bool = True
    total: bool = False
    values: bool = False


@dataclass
class GraphPanel(Panel):
    type: Literal["graph"] = "graph"

    fieldConfig: Dict = field(default_factory=dict)
    options: GraphPanelOptions = field(default_factory=GraphPanelOptions)
    aliasColors: dict = field(default_factory=dict)
    bars: bool = False
    dashLength: int = 10
    dashes: bool = False
    fill: int = 1
    fillGradient: int = 0
    hiddenSeries: bool = False
    legend: GraphLegend = field(default_factory=GraphLegend)
    lines: bool = True
    linewidth: int = 1
    nullPointMode: str = "null"
    percentage: bool = False
    pluginVersion: str = "8.3.3"
    pointradius: int = 2
    points: bool = False
    renderer: str = "flot"
    seriesOverrides: list = field(default_factory=list)
    spaceLength: int = 10
    stack: bool = False
    steppedLine: bool = False
    thresholds: list = field(default_factory=list)
    timeRegions: list = field(default_factory=list)
    title: str = "MR1L0 Pitch"
    tooltip: GraphTooltip = field(default_factory=GraphTooltip)
    xaxis: GraphAxis = field(default_factory=GraphAxis)
    yaxes: List[GraphAxis] = field(default_factory=list)
    yaxis: GraphYAxisSettings = field(default_factory=GraphYAxisSettings)


@dataclass
class TimeSeriesPanel(Panel):
    type: Literal["timeseries"] = "timeseries"

    fieldConfig: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)


AnyPanel = Union[
    BarGaugePanel,
    GaugePanel,
    GraphPanel,
    RowPanel,
    StatPanel,
    TimeSeriesPanel,
]


@dataclass
class DashboardAnnotationTarget:
    limit: int = 100
    matchAny: bool = False
    tags: List[str] = field(default_factory=list)
    type: str = "dashboard"


@dataclass
class DashboardAnnotation:
    builtIn: int = 1
    datasource: str = "-- Grafana --"
    enable: bool = True
    hide: bool = True
    iconColor: str = "rgba(0, 0, 0, 1)"
    name: str = ""
    target: DashboardAnnotationTarget = field(default_factory=DashboardAnnotationTarget)
    type: str = "dashboard"


@dataclass
class DashboardRow:
    ...


@dataclass
class DashboardAnnotations:
    list: List[DashboardAnnotation] = field(default_factory=list)


@dataclass
class DashboardTemplating:
    ...


@dataclass
class DashboardTemplatings:
    list: List[DashboardTemplating] = field(default_factory=list)


@dataclass
class DashboardTime:
    from_: str = field(default="now-1h", metadata=apischema.alias("from"))
    to: str = "now"


@dataclass
class Dashboard:
    annotations: DashboardAnnotations = field(default_factory=DashboardAnnotations)
    description: Optional[str] = ""
    editable: Optional[bool] = True
    fiscalYearStartMonth: int = 0
    graphTooltip: int = 0
    hideControls: Optional[bool] = False
    id: int = 0
    inputs: List[DashboardInput] = field(default_factory=list)
    links: List[DashboardLink] = field(default_factory=list)
    liveNow: bool = False
    panels: List[AnyPanel] = field(default_factory=list)
    refresh: str = "10s"
    rows: List[DashboardRow] = field(default_factory=list)
    schemaVersion: int = 34
    sharedCrosshair: bool = False
    style: str = "dark"
    tags: List[str] = field(default_factory=list)
    templating: DashboardTemplatings = field(default_factory=DashboardTemplatings)
    time: DashboardTime = field(default_factory=DashboardTime)
    timepicker: DashboardTimePicker = field(default_factory=DashboardTimePicker)
    timezone: str = "utc"
    title: str = ""
    uid: Optional[str] = None
    version: int = 0
    weekStart: str = ""
