"""
Plans with additional annotations to facilitate gui auto-generation
In the future this should probably be pulled out into a separate repository
as it will probably see considerable use
"""
from typing import (Any, Callable, Dict, Generator, Iterable, List, Optional,
                    Tuple, Union)

import bluesky.plans as bp
from bluesky_queueserver import parameter_annotation_decorator


def add_docstring_from(func):
    def wrapper(orig_func):
        if not orig_func.__doc__:
            orig_func.__doc__ = ""
        orig_func.__doc__ += (func.__doc__ or "")
        return orig_func

    return wrapper


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "num": {"annotation": "int"},
        "delay": {"annotation": "typing.Union[typing.Iterable[float], float]"},
    }
})
@add_docstring_from(bp.count)
def count(
    detectors: List[Any],
    num: int = 1,
    delay: Union[Iterable[Union[float, int]], float, int] = None,
    *,
    per_shot: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.count(detectors, num, delay, per_shot=per_shot, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motors": {"annotation": "typing.List[__DEVICE__]"},
        "point_lists": {"annotation": "typing.List[typing.List[float]]"},
    }
})
@add_docstring_from(bp.list_scan)
def list_scan(
    detectors: List[Any],
    motors: List[Any],
    point_lists: List[List[float]],
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    motor_args = [val for pair in zip(motors, point_lists) for val in pair]
    yield from bp.list_scan(detectors, *motor_args, per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motors": {"annotation": "typing.List[__DEVICE__]"},
        "point_lists": {"annotation": "typing.List[typing.List[float]]"},
    }
})
@add_docstring_from(bp.rel_list_scan)
def rel_list_scan(
    detectors: List[Any],
    motors: List[Any],
    point_lists: List[List[float]],
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    motor_args = [val for pair in zip(motors, point_lists) for val in pair]
    yield from bp.rel_list_scan(detectors, *motor_args, per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motors": {"annotation": "typing.List[__DEVICE__]"},
        "point_lists": {"annotation": "typing.List[typing.List[float]]"},
    }
})
@add_docstring_from(bp.list_grid_scan)
def list_grid_scan(
    detectors: List[Any],
    motors: List[Any],
    point_lists: List[List[float]],
    snake_axes: bool = False,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    motor_args = [val for pair in zip(motors, point_lists) for val in pair]
    yield from bp.list_grid_scan(detectors, *motor_args, snake_axes,
                                 per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motors": {"annotation": "typing.List[__DEVICE__]"},
        "point_lists": {"annotation": "typing.List[typing.List[float]]"},
    }
})
@add_docstring_from(bp.rel_list_grid_scan)
def rel_list_grid_scan(
    detectors: List[Any],
    motors: List[Any],
    point_lists: List[List[float]],
    snake_axes: bool = False,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    motor_args = [val for pair in zip(motors, point_lists) for val in pair]
    yield from bp.rel_list_grid_scan(detectors, *motor_args, snake_axes,
                                     per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.log_scan)
def log_scan(
    detectors: List[Any],
    motor: Any,
    start: float,
    stop: float,
    num: int,
    *,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.log_scan(detectors, motor, start, stop, num,
                           per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.rel_log_scan)
def rel_log_scan(
    detectors: List[Any],
    motor: Any,
    start: float,
    stop: float,
    num: int,
    *,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.rel_log_scan(detectors, motor, start, stop, num,
                               per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.adaptive_scan)
def adaptive_scan(
    detectors: List[Any],
    target_field: str,
    motor: Any,
    start: float,
    stop: float,
    min_step: float,
    max_step: float,
    target_delta: float,
    backstep: bool,
    threshold: float = 0.8,
    *,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.adaptive_scan(detectors, target_field, motor, start, stop,
                                min_step, max_step, target_delta, backstep,
                                threshold, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.rel_adaptive_scan)
def rel_adaptive_scan(
    detectors: List[Any],
    target_field: str,
    motor: Any,
    start: float,
    stop: float,
    min_step: float,
    max_step: float,
    target_delta: float,
    backstep: bool,
    threshold: float = 0.8,
    *,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.rel_adaptive_scan(detectors, target_field, motor, start, stop,
                                    min_step, max_step, target_delta, backstep,
                                    threshold, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.tune_centroid)
def tune_centroid(
    detectors: List[Any],
    signal: str,
    motor: Any,
    start: float,
    stop: float,
    min_step: float,
    num: int = 10,
    step_factor: float = 3.0,
    snake: bool = False,
    *,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.tune_centroid(detectors, signal, motor, start, stop,
                                min_step, num, step_factor, snake, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
    }
})
@add_docstring_from(bp.scan_nd)
def scan_nd(
    detectors: List[Any],
    cycler: Any,
    *,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.scan_nd(detectors, cycler, per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motors": {"annotation": "typing.List[__DEVICE__]"},
        "limits": {"annotation": "typing.List[typing.Tuple[float, float]]"},
    }
})
@add_docstring_from(bp.scan)
def scan(
    detectors: List[Any],
    motors: List[Any],
    limits: List[Tuple[float, float]],
    num: Optional[int] = None,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    motor_args = [item for tup in zip(motors, *zip(*limits)) for item in tup]
    yield from bp.scan(detectors, *motor_args, num=num, per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motors": {"annotation": "typing.List[__DEVICE__]"},
        "limits": {"annotation": "typing.List[typing.Tuple[float, float]]"},
    }
})
@add_docstring_from(bp.inner_product_scan)
def inner_product_scan(
    detectors: List[Any],
    num: int,
    motors: List[Any],
    limits: List[Tuple[float, float]],
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    motor_args = [item for tup in zip(motors, *zip(*limits)) for item in tup]
    yield from bp.inner_product_scan(detectors, num, *motor_args,
                                     per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motors": {"annotation": "typing.List[__DEVICE__]"},
        "limits": {"annotation": "typing.List[typing.Tuple[float, float, int]]"},
    }
})
@add_docstring_from(bp.grid_scan)
def grid_scan(
    detectors: List[Any],
    motors: List[Any],
    limits: List[Tuple[float, float, int]],
    snake_axes: Optional[Union[bool, Iterable[Any]]] = None,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    motor_args = [item for tup in zip(motors, *zip(*limits)) for item in tup]
    yield from bp.grid_scan(detectors, *motor_args, snake_axes=snake_axes,
                            per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motors": {"annotation": "typing.List[__DEVICE__]"},
        "limits": {"annotation": "typing.List[typing.Tuple[float, float, int]]"},
    }
})
@add_docstring_from(bp.rel_grid_scan)
def rel_grid_scan(
    detectors: List[Any],
    motors: List[Any],
    limits: List[Tuple[float, float, int]],
    snake_axes: Optional[Union[bool, Iterable[Any]]] = None,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    motor_args = [item for tup in zip(motors, *zip(*limits)) for item in tup]
    yield from bp.rel_grid_scan(detectors, *motor_args, snake_axes=snake_axes,
                                per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motors": {"annotation": "typing.List[__DEVICE__]"},
        "limits": {"annotation": "typing.List[typing.Tuple[float, float]]"},
    }
})
@add_docstring_from(bp.relative_inner_product_scan)
def relative_inner_product_scan(
    detectors: List[Any],
    num: int,
    motors: List[Any],
    limits: List[Tuple[float, float]],
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    motor_args = [item for tup in zip(motors, *zip(*limits)) for item in tup]
    yield from bp.relative_inner_product_scan(detectors, num, *motor_args,
                                              per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motors": {"annotation": "typing.List[__DEVICE__]"},
        "limits": {"annotation": "typing.List[typing.Tuple[float, float]]"},
    }
})
@add_docstring_from(bp.rel_scan)
def rel_scan(
    detectors: List[Any],
    motors: List[Any],
    limits: List[Tuple[float, float]],
    num: Optional[int] = None,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    motor_args = [item for tup in zip(motors, *zip(*limits)) for item in tup]
    yield from bp.rel_scan(detectors, *motor_args, num=num, per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detector": {"annotation": "__DEVICE__"},
        "motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.tweak)
def tweak(
    detector: Any,
    target_field: str,
    motor: Any,
    step: float,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.tweak(detector, target_field, motor, step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "x_motor": {"annotation": "__DEVICE__"},
        "y_motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.spiral_fermat)
def spiral_fermat(
    detectors: List[Any],
    x_motor: Any,
    y_motor: Any,
    x_start: float,
    y_start: float,
    x_range: float,
    y_range: float,
    dr: float,
    factor: float,
    *,
    dr_y: Optional[float] = None,
    tilt: Optional[float] = 0.0,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.spiral_fermat(detectors, x_motor, y_motor, x_start, y_start,
                                x_range, y_range, dr, factor, dr_y=dr_y, tilt=tilt,
                                per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "x_motor": {"annotation": "__DEVICE__"},
        "y_motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.rel_spiral_fermat)
def rel_spiral_fermat(
    detectors: List[Any],
    x_motor: Any,
    y_motor: Any,
    x_range: float,
    y_range: float,
    dr: float,
    factor: float,
    *,
    dr_y: Optional[float] = None,
    tilt: Optional[float] = 0.0,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.rel_spiral_fermat(detectors, x_motor, y_motor, x_range, y_range,
                                    dr, factor, dr_y=dr_y, tilt=tilt,
                                    per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "x_motor": {"annotation": "__DEVICE__"},
        "y_motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.spiral)
def spiral(
    detectors: List[Any],
    x_motor: Any,
    y_motor: Any,
    x_start: float,
    y_start: float,
    x_range: float,
    y_range: float,
    dr: float,
    nth: float,
    *,
    dr_y: Optional[float] = None,
    tilt: Optional[float] = 0.0,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.spiral(detectors, x_motor, y_motor, x_start, y_start,
                         x_range, y_range, dr, nth, dr_y=dr_y, tilt=tilt,
                         per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "x_motor": {"annotation": "__DEVICE__"},
        "y_motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.rel_spiral)
def rel_spiral(
    detectors: List[Any],
    x_motor: Any,
    y_motor: Any,
    x_range: float,
    y_range: float,
    dr: float,
    nth: float,
    *,
    dr_y: Optional[float] = None,
    tilt: Optional[float] = 0.0,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.rel_spiral(detectors, x_motor, y_motor, x_range, y_range,
                             dr, nth, dr_y=dr_y, tilt=tilt, per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "x_motor": {"annotation": "__DEVICE__"},
        "y_motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.spiral_square)
def spiral_square(
    detectors: List[Any],
    x_motor: Any,
    y_motor: Any,
    x_center: float,
    y_center: float,
    x_range: float,
    y_range: float,
    x_num: float,
    y_num: float,
    *,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.spiral_square(detectors, x_motor, y_motor, x_center, y_center,
                                x_range, y_range, x_num, y_num,
                                per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "x_motor": {"annotation": "__DEVICE__"},
        "y_motor": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.rel_spiral_square)
def rel_spiral_square(
    detectors: List[Any],
    x_motor: Any,
    y_motor: Any,
    x_range: float,
    y_range: float,
    x_num: float,
    y_num: float,
    *,
    per_step: Optional[Callable] = None,
    md: Optional[Dict[str, Any]] = None
):
    yield from bp.rel_spiral_square(detectors, x_motor, y_motor,
                                    x_range, y_range, x_num, y_num,
                                    per_step=per_step, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "go_plan": {"annotation": "__PLAN__"},
        "monitor_sig": {"annotation": "__DEVICE__"},
        "inner_plan_func": {"annotation": "__PLAN__"},
    }
})
@add_docstring_from(bp.ramp_plan)
def ramp_plan(
    go_plan: Generator,
    monitor_sig: Any,
    inner_plan_func: Callable,
    take_pre_data: bool = True,
    timeout: Optional[float] = None,
    period: Optional[float] = None,
    md: Dict[str, Any] = None
):
    yield from bp.ramp_plan(go_plan, monitor_sig, inner_plan_func, take_pre_data,
                            timeout=timeout, period=period, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "flyers": {"annotation": "typing.List[__DEVICE__]"},
    }
})
@add_docstring_from(bp.fly)
def fly(
    flyers: List[Any],
    *,
    md: Dict[str, Any] = None
):
    yield from bp.fly(flyers, md=md)


@parameter_annotation_decorator({
    "parameters": {
        "detectors": {"annotation": "typing.List[__DEVICE__]"},
        "motor1": {"annotation": "__DEVICE__"},
        "motor2": {"annotation": "__DEVICE__"},
    }
})
@add_docstring_from(bp.x2x_scan)
def x2x_scan(
    detectors: List[Any],
    motor1: Any,
    motor2: Any,
    start: float,
    stop: float,
    num: int,
    *,
    per_step: Optional[Callable] = None,
    md: Optional[Callable] = None
):
    yield from bp.x2x_scan(detectors, motor1, motor2, start, stop, num,
                           per_step=per_step, md=md)
