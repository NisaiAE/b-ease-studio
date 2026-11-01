# B-Ease Studio
# Copyright 2025 Nisai
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import copy
import math
from decimal import Decimal, localcontext

from ..constants import (
    CURVE_COORD_MAX,
    CURVE_COORD_MIN,
    DEFAULT_BEZIER_POINTS,
    SNAP_GRID_DIVISIONS,
)
from . import bezier


ANCHOR_MIN_SPACING = 0.01
ANCHOR_MIN_Y_DELTA = 0.001
ANCHOR_MODES = frozenset(("ALIGNED", "FREE"))
_EPSILON = 1e-12
_ALIGN_REL_TOLERANCE = 1e-6
_LOCAL_STORAGE_DIGITS = 12


def _is_finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _clamp01(value):
    return min(max(value, 0.0), 1.0)


def _clamp_coordinate(value):
    return min(max(float(value), CURVE_COORD_MIN), CURVE_COORD_MAX)


def snap(value):
    return round(value * SNAP_GRID_DIVISIONS) / SNAP_GRID_DIVISIONS


_snap = snap


def _anchor_ys_too_close(first_y, second_y):

    return (
        abs(float(first_y) - float(second_y))
        < ANCHOR_MIN_Y_DELTA - _EPSILON
    )


def _separate_anchor_ys(anchors):

    if not isinstance(anchors, (list, tuple)) or len(anchors) < 3:
        return anchors

    source_ys = [float(anchor["y"]) for anchor in anchors]
    last_index = len(anchors) - 1
    chain_direction = None

    for index in range(1, last_index):
        previous_y = float(anchors[index - 1]["y"])
        current_y = float(anchors[index]["y"])
        source_collision = _anchor_ys_too_close(
            source_ys[index - 1],
            source_ys[index],
        )
        current_collision = _anchor_ys_too_close(
            previous_y,
            current_y,
        )
        if not current_collision and not (
            chain_direction is not None and source_collision
        ):
            chain_direction = None
            continue

        if chain_direction is None:
            chain_direction = 1.0 if current_y >= previous_y else -1.0
        separated_y = previous_y + chain_direction * ANCHOR_MIN_Y_DELTA
        if separated_y > CURVE_COORD_MAX or separated_y < CURVE_COORD_MIN:
            chain_direction *= -1.0
            separated_y = previous_y + chain_direction * ANCHOR_MIN_Y_DELTA
        anchors[index]["y"] = separated_y

    endpoint_y = float(anchors[last_index]["y"])
    previous_y = float(anchors[last_index - 1]["y"])
    if not _anchor_ys_too_close(previous_y, endpoint_y):
        return anchors


    chain_direction = 1.0 if previous_y > endpoint_y else -1.0
    anchors[last_index - 1]["y"] = (
        endpoint_y + chain_direction * ANCHOR_MIN_Y_DELTA
    )
    for index in range(last_index - 2, 0, -1):
        current_y = float(anchors[index]["y"])
        following_y = float(anchors[index + 1]["y"])
        source_collision = _anchor_ys_too_close(
            source_ys[index],
            source_ys[index + 1],
        )
        if (
            not _anchor_ys_too_close(current_y, following_y)
            and not source_collision
        ):
            break
        anchors[index]["y"] = (
            following_y + chain_direction * ANCHOR_MIN_Y_DELTA
        )
    return anchors


def _rigid_separate_anchor_ys(
    curve,
    absolute_segments=None,
    old_ys=None,
    force_rebuild=(),
):

    anchors = curve.get("anchors")
    segments = curve.get("segments")
    if (
        not isinstance(anchors, list)
        or not isinstance(segments, list)
        or len(anchors) < 2
        or len(segments) != len(anchors) - 1
    ):
        if isinstance(anchors, (list, tuple)):
            _separate_anchor_ys(anchors)
        return curve

    if absolute_segments is None:
        absolute_segments = [
            _absolute_segment(curve, index)
            for index in range(len(segments))
        ]
    if old_ys is None:
        old_ys = [float(anchor["y"]) for anchor in anchors]
    _separate_anchor_ys(anchors)
    deltas = [
        float(anchor["y"]) - old_y
        for anchor, old_y in zip(anchors, old_ys)
    ]
    for segment_index in range(len(segments)):
        start_delta = deltas[segment_index]
        end_delta = deltas[segment_index + 1]
        if (
            segment_index not in force_rebuild
            and abs(start_delta) <= _EPSILON
            and abs(end_delta) <= _EPSILON
        ):
            continue
        controls = absolute_segments[segment_index]
        segments[segment_index] = _segment_from_absolute(
            anchors[segment_index],
            anchors[segment_index + 1],
            (controls[1][0], controls[1][1] + start_delta),
            (controls[2][0], controls[2][1] + end_delta),
        )
    return curve


def _default_segment():
    x1, y1, x2, y2 = DEFAULT_BEZIER_POINTS
    return {
        "out": [float(x1), float(y1)],
        "in": [float(x2), float(y2)],
    }


def new_default_curve():

    return {
        "anchors": [
            {"x": 0.0, "y": 0.0},
            {"x": 1.0, "y": 1.0},
        ],
        "segments": [_default_segment()],
    }


def from_legacy(points4):

    if not isinstance(points4, (list, tuple)) or len(points4) != 4:
        return None
    if not all(_is_finite_number(value) for value in points4):
        return None

    curve = {
        "anchors": [
            {"x": 0.0, "y": 0.0},
            {"x": 1.0, "y": 1.0},
        ],
        "segments": [
            {
                "out": [float(points4[0]), float(points4[1])],
                "in": [float(points4[2]), float(points4[3])],
            }
        ],
    }
    return validate(curve)


def to_legacy(curve):

    normalized = validate(curve)
    if normalized is None or len(normalized["segments"]) != 1:
        return None
    segment = normalized["segments"][0]
    return [
        segment["out"][0],
        segment["out"][1],
        segment["in"][0],
        segment["in"][1],
    ]


def _normalize_anchors(anchors):

    if not isinstance(anchors, (list, tuple)) or len(anchors) < 2:
        return None

    normalized_anchors = []
    last_index = len(anchors) - 1
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, dict):
            return None
        x_value = anchor.get("x")
        y_value = anchor.get("y")
        if not _is_finite_number(x_value) or not _is_finite_number(y_value):
            return None

        if index == 0:
            normalized_anchor = {"x": 0.0, "y": 0.0}
        elif index == last_index:
            normalized_anchor = {"x": 1.0, "y": 1.0}
        else:
            mode = anchor.get("mode", "ALIGNED")
            if not isinstance(mode, str):
                return None
            mode = mode.strip().upper()
            if mode not in ANCHOR_MODES:
                return None
            normalized_anchor = {
                "x": float(x_value),
                "y": _clamp_coordinate(y_value),
                "mode": mode,
            }
        normalized_anchors.append(normalized_anchor)

    for index in range(1, len(normalized_anchors)):
        previous_x = normalized_anchors[index - 1]["x"]
        current_x = normalized_anchors[index]["x"]
        if current_x <= previous_x:
            return None
        if current_x - previous_x < ANCHOR_MIN_SPACING - _EPSILON:
            return None
    return normalized_anchors


def anchor_geometry_is_valid(anchors):

    if not isinstance(anchors, (list, tuple)) or len(anchors) < 2:
        return False
    coordinates = []
    for anchor in anchors:
        if not isinstance(anchor, dict):
            return False
        x_value = anchor.get("x")
        y_value = anchor.get("y")
        if not _is_finite_number(x_value) or not _is_finite_number(y_value):
            return False
        coordinates.append((float(x_value), float(y_value)))
    if (
        abs(coordinates[0][0]) > _EPSILON
        or abs(coordinates[0][1]) > _EPSILON
        or abs(coordinates[-1][0] - 1.0) > _EPSILON
        or abs(coordinates[-1][1] - 1.0) > _EPSILON
    ):
        return False
    for previous, current in zip(coordinates, coordinates[1:]):
        if current[0] - previous[0] < ANCHOR_MIN_SPACING - _EPSILON:
            return False
    return True


def _normalize_segments(segments, expected_count):

    if not isinstance(segments, (list, tuple)):
        return None
    if len(segments) != expected_count:
        return None
    normalized_segments = []
    for segment in segments:
        if not isinstance(segment, dict):
            return None
        out_handle = segment.get("out")
        in_handle = segment.get("in")
        if not isinstance(out_handle, (list, tuple)) or len(out_handle) != 2:
            return None
        if not isinstance(in_handle, (list, tuple)) or len(in_handle) != 2:
            return None
        values = tuple(out_handle) + tuple(in_handle)
        if not all(_is_finite_number(value) for value in values):
            return None

        normalized_segments.append(
            {
                "out": [
                    _clamp_coordinate(out_handle[0]),
                    _clamp_coordinate(out_handle[1]),
                ],
                "in": [
                    _clamp_coordinate(in_handle[0]),
                    _clamp_coordinate(in_handle[1]),
                ],
            }
        )
    return normalized_segments


def _downgrade_noncollinear_aligned_modes(normalized):

    normalized_anchors = normalized["anchors"]
    normalized_segments = normalized["segments"]
    for index in range(1, len(normalized_anchors) - 1):
        anchor = normalized_anchors[index]
        if anchor["mode"] != "ALIGNED":
            continue
        previous = normalized_anchors[index - 1]
        following = normalized_anchors[index + 1]
        incoming_local = normalized_segments[index - 1]["in"]
        outgoing_local = normalized_segments[index]["out"]
        incoming = (
            previous["x"]
            + incoming_local[0] * (anchor["x"] - previous["x"]),
            previous["y"]
            + incoming_local[1] * (anchor["y"] - previous["y"]),
        )
        outgoing = (
            anchor["x"]
            + outgoing_local[0] * (following["x"] - anchor["x"]),
            anchor["y"]
            + outgoing_local[1] * (following["y"] - anchor["y"]),
        )
        incoming_vector = (
            incoming[0] - anchor["x"],
            incoming[1] - anchor["y"],
        )
        outgoing_vector = (
            outgoing[0] - anchor["x"],
            outgoing[1] - anchor["y"],
        )
        incoming_length = math.hypot(*incoming_vector)
        outgoing_length = math.hypot(*outgoing_vector)
        if incoming_length <= _EPSILON or outgoing_length <= _EPSILON:


            continue
        cross = (
            incoming_vector[0] * outgoing_vector[1]
            - incoming_vector[1] * outgoing_vector[0]
        )
        dot = (
            incoming_vector[0] * outgoing_vector[0]
            + incoming_vector[1] * outgoing_vector[1]
        )
        if (
            dot >= 0.0
            or abs(cross)
            > _ALIGN_REL_TOLERANCE * incoming_length * outgoing_length
        ):


            anchor["mode"] = "FREE"
    return normalized


def normalize_curve(curve):

    if not isinstance(curve, dict):
        return None
    anchors = curve.get("anchors")
    segments = curve.get("segments")
    normalized_anchors = _normalize_anchors(anchors)
    if normalized_anchors is None:
        return None
    normalized_segments = _normalize_segments(
        segments,
        len(normalized_anchors) - 1,
    )
    if normalized_segments is None:
        return None

    normalized = {
        "anchors": normalized_anchors,
        "segments": normalized_segments,
    }


    _rigid_separate_anchor_ys(normalized)
    normalized["segments"] = _normalize_segments(
        normalized["segments"],
        len(normalized["anchors"]) - 1,
    )
    return _downgrade_noncollinear_aligned_modes(normalized)


def validate(curve):

    return normalize_curve(curve)


def deep_copy(curve):

    return copy.deepcopy(curve)


def quantize_curve(curve):

    normalized = validate(curve)
    if normalized is None:
        return None

    last_index = len(normalized["anchors"]) - 1
    minimum_steps = max(
        1,
        int(math.ceil(
            ANCHOR_MIN_SPACING * SNAP_GRID_DIVISIONS - _EPSILON
        )),
    )
    rounded_anchors = deep_copy(normalized["anchors"])
    previous_step = 0
    for index in range(1, last_index):
        anchor = rounded_anchors[index]
        rounded_step = round(anchor["x"] * SNAP_GRID_DIVISIONS)
        remaining_intervals = last_index - index
        minimum_step = previous_step + minimum_steps
        maximum_step = (
            SNAP_GRID_DIVISIONS - remaining_intervals * minimum_steps
        )
        grid_step = min(max(rounded_step, minimum_step), maximum_step)
        anchor["x"] = grid_step / SNAP_GRID_DIVISIONS
        anchor["y"] = _snap(anchor["y"])
        previous_step = grid_step

    target_anchors = deep_copy(rounded_anchors)
    _separate_anchor_ys(target_anchors)
    separated_anchor_indices = set()
    for index in range(1, last_index):
        if (
            abs(target_anchors[index]["y"] - rounded_anchors[index]["y"])
            <= _EPSILON
        ):
            continue
        separated_anchor_indices.add(index)


    anchors_are_quantized = all(
        abs(anchor["x"] - target["x"]) <= _EPSILON
        and abs(anchor["y"] - target["y"]) <= _EPSILON
        for anchor, target in zip(normalized["anchors"], target_anchors)
    )
    handles_are_quantized = True
    if anchors_are_quantized:
        for segment_index in range(len(normalized["segments"])):
            controls = _absolute_segment(normalized, segment_index)
            if any(
                abs(value - _snap(value)) > _EPSILON
                for value in controls[1]
            ) and segment_index not in separated_anchor_indices:
                handles_are_quantized = False
                break
            end_anchor = normalized["anchors"][segment_index + 1]
            if end_anchor.get("mode") == "ALIGNED":
                continue
            if any(
                abs(value - _snap(value)) > _EPSILON
                for value in controls[2]
            ) and segment_index + 1 not in separated_anchor_indices:
                handles_are_quantized = False
                break
    local_storage_is_stable = all(
        value == round(value, _LOCAL_STORAGE_DIGITS)
        for segment in normalized["segments"]
        for side in ("out", "in")
        for value in segment[side]
    )
    if (
        anchors_are_quantized
        and handles_are_quantized
        and local_storage_is_stable
    ):
        return deep_copy(normalized)

    absolute_segments = [
        _absolute_segment(normalized, segment_index)
        for segment_index in range(len(normalized["segments"]))
    ]
    result = deep_copy(normalized)
    for index in range(1, last_index):
        result["anchors"][index]["x"] = target_anchors[index]["x"]
        result["anchors"][index]["y"] = target_anchors[index]["y"]

    for segment_index, controls in enumerate(absolute_segments):
        start_y_delta = (
            target_anchors[segment_index]["y"]
            - rounded_anchors[segment_index]["y"]
        )
        end_y_delta = (
            target_anchors[segment_index + 1]["y"]
            - rounded_anchors[segment_index + 1]["y"]
        )
        out_point = (
            _snap(controls[1][0]),
            _snap(controls[1][1]) + start_y_delta,
        )
        end_anchor = result["anchors"][segment_index + 1]
        base_in_point = (
            tuple(controls[2])
            if end_anchor.get("mode") == "ALIGNED"
            else tuple(_snap(value) for value in controls[2])
        )
        in_point = (
            base_in_point[0],
            base_in_point[1] + end_y_delta,
        )
        result["segments"][segment_index] = _segment_from_absolute(
            result["anchors"][segment_index],
            result["anchors"][segment_index + 1],
            out_point,
            in_point,
            stable_storage=True,
        )


    result = realign_aligned_handles(result)
    for segment in result["segments"]:
        for side in ("out", "in"):
            segment[side] = [
                round(value, _LOCAL_STORAGE_DIGITS)
                for value in segment[side]
            ]
    return validate(result)


def canonical_signature(curve):

    normalized = validate(curve)
    if normalized is None:
        return None
    anchors = tuple(
        (
            anchor["x"],
            anchor["y"],
            anchor.get("mode"),
        )
        for anchor in normalized["anchors"]
    )
    segments = tuple(
        (
            tuple(segment["out"]),
            tuple(segment["in"]),
        )
        for segment in normalized["segments"]
    )
    return anchors, segments


def _local_to_absolute(start, end, local):
    return (
        start["x"] + local[0] * (end["x"] - start["x"]),
        start["y"] + local[1] * (end["y"] - start["y"]),
    )


def absolute_to_local(start, end, point, stable_storage=False):


    def local_axis(start_value, end_value, point_value):
        try:
            start_value = float(start_value)
            end_value = float(end_value)
            point_value = float(point_value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if not all(
            math.isfinite(value)
            for value in (start_value, end_value, point_value)
        ):
            return 0.0
        delta = end_value - start_value
        if not math.isfinite(delta) or abs(delta) < _EPSILON:
            return 0.0
        local = (point_value - start_value) / delta
        return local if math.isfinite(local) else 0.0

    local_x = local_axis(start.get("x"), end.get("x"), point[0])
    local_y = local_axis(start.get("y"), end.get("y"), point[1])
    if stable_storage:
        local_x = round(local_x, _LOCAL_STORAGE_DIGITS)
        local_y = round(local_y, _LOCAL_STORAGE_DIGITS)
    return [local_x, local_y]


_absolute_to_local = absolute_to_local


def absolute_segment(curve, segment_index):
    start = curve["anchors"][segment_index]
    end = curve["anchors"][segment_index + 1]
    segment = curve["segments"][segment_index]
    return (
        (start["x"], start["y"]),
        _local_to_absolute(start, end, segment["out"]),
        _local_to_absolute(start, end, segment["in"]),
        (end["x"], end["y"]),
    )


_absolute_segment = absolute_segment


def _segment_from_absolute(
    start,
    end,
    out_point,
    in_point,
    stable_storage=False,
):
    return {
        "out": _absolute_to_local(
            start,
            end,
            out_point,
            stable_storage,
        ),
        "in": _absolute_to_local(
            start,
            end,
            in_point,
            stable_storage,
        ),
    }


def _correct_bezpart(start, out_point, in_point, end):

    out_x, out_y = out_point
    in_x, in_y = in_point
    if out_x < start[0]:
        out_x = start[0]
    if in_x > end[0]:
        in_x = end[0]

    width = end[0] - start[0]
    delta_out_x = out_x - start[0]
    delta_in_x = in_x - end[0]
    length_out = abs(delta_out_x)
    length_in = abs(delta_in_x)
    if length_out + length_in == 0.0:
        return (out_x, out_y), (in_x, in_y)
    if length_out > width:
        factor = width / length_out
        out_x = start[0] + factor * delta_out_x
        out_y = start[1] + factor * (out_y - start[1])
    if length_in > width:
        factor = width / length_in
        in_x = end[0] + factor * delta_in_x
        in_y = end[1] + factor * (in_y - end[1])
    return (out_x, out_y), (in_x, in_y)


def corrected_segment_controls(curve, seg_index):

    start, out_point, in_point, end = _absolute_segment(curve, seg_index)
    out_point, in_point = _correct_bezpart(start, out_point, in_point, end)
    return start, out_point, in_point, end


def _cubic_point(control_points, t):

    return bezier.cubic_point_generic(control_points, t)


def _sample_intervals(curve, n_total):
    segment_count = len(curve["segments"])
    remaining = int(n_total) - 1 - segment_count
    widths = [
        curve["anchors"][index + 1]["x"]
        - curve["anchors"][index]["x"]
        for index in range(segment_count)
    ]
    exact_extras = [remaining * width for width in widths]
    extras = [math.floor(value) for value in exact_extras]
    unassigned = remaining - sum(extras)
    by_remainder = sorted(
        range(segment_count),
        key=lambda index: (-(exact_extras[index] - extras[index]), index),
    )
    for index in by_remainder[:unassigned]:
        extras[index] += 1
    return [1 + extra for extra in extras]


def _sample_anchor_polyline(curve, n_total):

    anchors = curve["anchors"]
    sampled = []
    segment_index = 0
    for step in range(n_total):
        x_value = step / (n_total - 1)
        while (
            segment_index < len(anchors) - 2
            and x_value > anchors[segment_index + 1]["x"]
        ):
            segment_index += 1
        left = anchors[segment_index]
        right = anchors[segment_index + 1]
        width = right["x"] - left["x"]
        ratio = (x_value - left["x"]) / width
        sampled.append(
            (
                x_value,
                left["y"] + ratio * (right["y"] - left["y"]),
            )
        )
    return sampled


def evaluate(curve, n_total):

    normalized = validate(curve)
    if (
        normalized is None
        or isinstance(n_total, bool)
        or not isinstance(n_total, int)
        or n_total < 2
    ):
        return []

    if len(normalized["segments"]) > n_total - 1:
        return _sample_anchor_polyline(normalized, n_total)

    intervals = _sample_intervals(normalized, n_total)
    sampled = []
    for segment_index, interval_count in enumerate(intervals):

        controls = corrected_segment_controls(normalized, segment_index)
        first_step = 0 if segment_index == 0 else 1
        for step in range(first_step, interval_count + 1):
            sampled.append(_cubic_point(controls, step / interval_count))
    return sampled


def curve_bbox_fit(curve, size, padding=0.1):

    sampled = evaluate(curve, 51)
    if not sampled:
        return None

    preview_points = [(point[0], 1.0 - point[1]) for point in sampled]
    return bezier.fit_points_to_square(preview_points, size, padding)


def _lerp(point1, point2, t):
    return (
        point1[0] + (point2[0] - point1[0]) * t,
        point1[1] + (point2[1] - point1[1]) * t,
    )


_ROOT_ACCEPT_LOWER = -1.0e-10
_ROOT_ACCEPT_UPPER = 1.000001


def _cube_root(value):

    if value == 0.0:
        return 0.0
    if value < 0.0:
        return -math.exp(math.log(-value) / 3.0)
    return math.exp(math.log(value) / 3.0)


def _cubic_roots_for_x(control_points, target_x):

    q0 = control_points[0][0]
    q1 = control_points[1][0]
    q2 = control_points[2][0]
    q3 = control_points[3][0]
    c0 = q0 - target_x
    c1 = 3.0 * (q1 - q0)
    c2 = 3.0 * (q0 - 2.0 * q1 + q2)
    c3 = q3 - q0 + 3.0 * (q1 - q2)
    roots = []

    def _accept(candidate):
        if _ROOT_ACCEPT_LOWER <= candidate <= _ROOT_ACCEPT_UPPER:
            roots.append(candidate)

    if c3 != 0.0:
        c0 /= c3
        c1 /= c3
        c2 /= c3
        p = c1 / 3.0 - c2 * c2 / 9.0
        q = (2.0 * c2 * c2 * c2 - 9.0 * c2 * c1 + 27.0 * c0) / 54.0
        d = q * q + p * p * p
        if d > 0.0:
            t = math.sqrt(d)
            _accept(_cube_root(-q + t) + _cube_root(-q - t) - c2 / 3.0)
            return roots
        if d == 0.0:
            t = _cube_root(-q)
            _accept(2.0 * t - c2 / 3.0)
            _accept(-t - c2 / 3.0)
            return roots
        phi = math.acos(-q / math.sqrt(-(p * p * p)))
        t = math.sqrt(-p)
        p = math.cos(phi / 3.0)
        q = math.sqrt(3.0 - 3.0 * p * p)
        _accept(2.0 * t * p - c2 / 3.0)
        _accept(-t * (p + q) - c2 / 3.0)
        _accept(-t * (p - q) - c2 / 3.0)
        return roots
    if c2 != 0.0:
        p = c1 * c1 - 4.0 * c2 * c0
        if p > 0.0:
            p = math.sqrt(p)
            _accept((-c1 - p) / (2.0 * c2))
            _accept((-c1 + p) / (2.0 * c2))
            return roots
        if p == 0.0:
            _accept(-c1 / (2.0 * c2))
        return roots
    if c1 != 0.0:
        _accept(-c0 / c1)
        return roots
    if c0 == 0.0:
        roots.append(0.0)
    return roots


def _cubic_x_derivative(control_points, t):

    return bezier.cubic_derivative_generic(control_points, t)[0]


def _solve_t_for_x(control_points, target_x):

    roots = _cubic_roots_for_x(control_points, target_x)
    if roots:
        t = _clamp01(roots[0])
        for _iteration in range(3):
            error = _cubic_point(control_points, t)[0] - target_x
            if abs(error) <= 1e-14:
                break
            derivative = _cubic_x_derivative(control_points, t)
            if abs(derivative) <= _EPSILON:
                break
            candidate = t - error / derivative

            if not 0.0 <= candidate <= 1.0 or abs(candidate - t) > 0.05:
                break
            t = candidate
        return t
    return _solve_t_for_x_monotonic(control_points, target_x)


def _solve_t_for_x_monotonic(control_points, target_x):

    lower = 0.0
    upper = 1.0
    start_x = control_points[0][0]
    end_x = control_points[3][0]
    t = (target_x - start_x) / (end_x - start_x)

    for _iteration in range(80):
        point_x = _cubic_point(control_points, t)[0]
        error = point_x - target_x
        if abs(error) <= 1e-14:
            return _clamp01(t)
        if error < 0.0:
            lower = t
        else:
            upper = t

        derivative = _cubic_x_derivative(control_points, t)
        if abs(derivative) > _EPSILON:
            candidate = t - error / derivative
        else:
            candidate = (lower + upper) * 0.5
        if not lower < candidate < upper:
            candidate = (lower + upper) * 0.5
        t = candidate
    return (lower + upper) * 0.5


def subdivide_at_x(curve, x):

    normalized = validate(curve)
    if normalized is None:
        return None
    if not _is_finite_number(x):
        return deep_copy(normalized)
    target_x = float(x)

    segment_index = None
    for index in range(len(normalized["segments"])):
        left_x = normalized["anchors"][index]["x"]
        right_x = normalized["anchors"][index + 1]["x"]
        if (
            target_x >= left_x + ANCHOR_MIN_SPACING - _EPSILON
            and target_x <= right_x - ANCHOR_MIN_SPACING + _EPSILON
        ):
            segment_index = index
            break
    if segment_index is None:
        return deep_copy(normalized)

    controls = corrected_segment_controls(normalized, segment_index)
    t = _solve_t_for_x(controls, target_x)
    point01 = _lerp(controls[0], controls[1], t)
    point12 = _lerp(controls[1], controls[2], t)
    point23 = _lerp(controls[2], controls[3], t)
    point012 = _lerp(point01, point12, t)
    point123 = _lerp(point12, point23, t)
    split_point = _lerp(point012, point123, t)
    split_point = (target_x, split_point[1])


    left_out_point, left_in_point = _correct_bezpart(
        controls[0],
        point01,
        point012,
        split_point,
    )
    right_out_point, right_in_point = _correct_bezpart(
        split_point,
        point123,
        point23,
        controls[3],
    )

    result = deep_copy(normalized)
    absolute_before = [
        _absolute_segment(result, index)
        for index in range(len(result["segments"]))
    ]
    result["anchors"].insert(
        segment_index + 1,
        {
            "x": split_point[0],
            "y": split_point[1],
            "mode": "ALIGNED",
        },
    )
    result["segments"][segment_index : segment_index + 1] = [
        {"out": [0.0, 0.0], "in": [0.0, 0.0]},
        {"out": [0.0, 0.0], "in": [0.0, 0.0]},
    ]
    absolute_after = (
        absolute_before[:segment_index]
        + [
            (controls[0], left_out_point, left_in_point, split_point),
            (split_point, right_out_point, right_in_point, controls[3]),
        ]
        + absolute_before[segment_index + 1 :]
    )
    _rigid_separate_anchor_ys(
        result,
        absolute_segments=absolute_after,
        force_rebuild={segment_index, segment_index + 1},
    )
    return validate(result)


add_anchor_on_curve = subdivide_at_x


def add_anchor_at_point(curve, x, y):

    normalized = validate(curve)
    if normalized is None:
        return None
    if not _is_finite_number(x) or not _is_finite_number(y):
        return deep_copy(normalized)

    target_x = float(x)
    target_y = float(y)
    result = subdivide_at_x(normalized, target_x)
    if (
        result is None
        or len(result["anchors"]) == len(normalized["anchors"])
    ):
        return result

    anchor_index = next(
        index
        for index, anchor in enumerate(result["anchors"])
        if abs(anchor["x"] - target_x) <= _EPSILON
    )
    absolute_before = [
        _absolute_segment(result, index)
        for index in range(len(result["segments"]))
    ]
    old_ys = [float(anchor["y"]) for anchor in result["anchors"]]

    result["anchors"][anchor_index]["y"] = target_y
    _rigid_separate_anchor_ys(
        result,
        absolute_segments=absolute_before,
        old_ys=old_ys,
    )
    return validate(result)


def delete_anchor(curve, index):

    normalized = validate(curve)
    if normalized is None:
        return None
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or index <= 0
        or index >= len(normalized["anchors"]) - 1
    ):
        return deep_copy(normalized)

    left_controls = _absolute_segment(normalized, index - 1)
    right_controls = _absolute_segment(normalized, index)
    result = deep_copy(normalized)
    del result["anchors"][index]
    del result["segments"][index]
    merged_slot = index - 1
    absolute_after = []
    for segment_index in range(len(result["segments"])):
        if segment_index == merged_slot:
            absolute_after.append(
                (
                    left_controls[0],
                    left_controls[1],
                    right_controls[2],
                    right_controls[3],
                )
            )
        else:
            absolute_after.append(
                _absolute_segment(result, segment_index)
            )
    _rigid_separate_anchor_ys(
        result,
        absolute_segments=absolute_after,
        force_rebuild={merged_slot},
    )
    return validate(result)


def _normalized_anchor_indices(indices, anchor_count):

    if isinstance(indices, (str, bytes)):
        return []
    try:
        values = list(indices)
    except TypeError:
        return []

    normalized = []
    seen = set()
    for index in values:
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index <= 0
            or index >= anchor_count - 1
            or index in seen
        ):
            continue
        normalized.append(index)
        seen.add(index)
    return normalized


def _normalized_handle_sync_indices(indices, anchor_count, side):

    if isinstance(indices, (str, bytes)):
        return []
    try:
        values = list(indices)
    except TypeError:
        return []

    normalized = []
    seen = set()
    last_index = anchor_count - 1
    for index in values:
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < anchor_count
            or index in seen
            or (index == 0 and side != "out")
            or (index == last_index and side != "in")
        ):
            continue
        normalized.append(index)
        seen.add(index)
    return normalized


def move_anchors(curve, indices, delta_x, delta_y, snap):

    normalized = validate(curve)
    if normalized is None:
        return None
    moved_indices = _normalized_anchor_indices(
        indices,
        len(normalized["anchors"]),
    )
    if (
        not moved_indices
        or not _is_finite_number(delta_x)
        or not _is_finite_number(delta_y)
    ):
        return deep_copy(normalized)

    dragged_index = moved_indices[0]
    dragged_anchor = normalized["anchors"][dragged_index]
    requested_delta_x = float(delta_x)
    requested_delta_y = float(delta_y)
    if snap:
        requested_delta_x = (
            _snap(dragged_anchor["x"] + requested_delta_x)
            - dragged_anchor["x"]
        )
        requested_delta_y = (
            _snap(dragged_anchor["y"] + requested_delta_y)
            - dragged_anchor["y"]
        )

    selected = set(moved_indices)
    lower_delta = -math.inf
    upper_delta = math.inf
    absolute_segments = [
        _absolute_segment(normalized, segment_index)
        for segment_index in range(len(normalized["segments"]))
    ]


    for segment_index in range(len(normalized["segments"])):
        start_index = segment_index
        end_index = segment_index + 1
        start_selected = start_index in selected
        end_selected = end_index in selected
        if start_selected == end_selected:
            continue

        start_x = normalized["anchors"][start_index]["x"]
        end_x = normalized["anchors"][end_index]["x"]
        if end_selected:
            lower_delta = max(
                lower_delta,
                start_x + ANCHOR_MIN_SPACING - end_x,
            )
        else:
            upper_delta = min(
                upper_delta,
                end_x - ANCHOR_MIN_SPACING - start_x,
            )

    if lower_delta > upper_delta:
        applied_delta_x = 0.0
    else:
        applied_delta_x = min(
            max(requested_delta_x, lower_delta),
            upper_delta,
        )

    result = deep_copy(normalized)
    for index in moved_indices:
        result["anchors"][index]["x"] += applied_delta_x
        result["anchors"][index]["y"] += requested_delta_y
    _separate_anchor_ys(result["anchors"])


    anchor_deltas = {
        index: (
            result["anchors"][index]["x"]
            - normalized["anchors"][index]["x"],
            result["anchors"][index]["y"]
            - normalized["anchors"][index]["y"],
        )
        for index in range(1, len(normalized["anchors"]) - 1)
        if (
            abs(
                result["anchors"][index]["x"]
                - normalized["anchors"][index]["x"]
            )
            > _EPSILON
            or abs(
                result["anchors"][index]["y"]
                - normalized["anchors"][index]["y"]
            )
            > _EPSILON
        )
    }
    for segment_index, controls in enumerate(absolute_segments):
        start_index = segment_index
        end_index = segment_index + 1
        out_point = controls[1]
        in_point = controls[2]
        if start_index in anchor_deltas:
            delta = anchor_deltas[start_index]
            out_point = (
                out_point[0] + delta[0],
                out_point[1] + delta[1],
            )
        if end_index in anchor_deltas:
            delta = anchor_deltas[end_index]
            in_point = (
                in_point[0] + delta[0],
                in_point[1] + delta[1],
            )
        result["segments"][segment_index] = _segment_from_absolute(
            result["anchors"][start_index],
            result["anchors"][end_index],
            out_point,
            in_point,
        )
    return validate(result)


def move_anchor(curve, index, x, y, snap):

    normalized = validate(curve)
    if normalized is None:
        return None
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or index <= 0
        or index >= len(normalized["anchors"]) - 1
        or not _is_finite_number(x)
        or not _is_finite_number(y)
    ):
        return deep_copy(normalized)

    anchor = normalized["anchors"][index]
    return move_anchors(
        normalized,
        [index],
        float(x) - anchor["x"],
        float(y) - anchor["y"],
        snap,
    )


def _handle_absolute(curve, segment_index, side):
    controls = _absolute_segment(curve, segment_index)
    return controls[1] if side == "out" else controls[2]


def _set_absolute_handle(
    curve,
    segment_index,
    side,
    point,
    stable_storage=False,
):
    start = curve["anchors"][segment_index]
    end = curve["anchors"][segment_index + 1]
    try:
        delta_x = float(end["x"]) - float(start["x"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(delta_x) or abs(delta_x) < _EPSILON:
        return False
    curve["segments"][segment_index][side] = _absolute_to_local(
        start,
        end,
        point,
        stable_storage,
    )
    return True


def realign_aligned_handles(curve, in_master_indices=frozenset()):

    result = deep_copy(curve)
    anchors = result.get("anchors", [])
    segments = result.get("segments", [])

    _rigid_separate_anchor_ys(result)
    for index in range(1, len(anchors) - 1):
        anchor = anchors[index]
        if anchor.get("mode") != "ALIGNED":
            continue
        if index - 1 >= len(segments) or index >= len(segments):
            continue
        try:
            previous_width = float(anchor["x"]) - float(
                anchors[index - 1]["x"]
            )
            following_width = float(anchors[index + 1]["x"]) - float(
                anchor["x"]
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (
            not math.isfinite(previous_width)
            or not math.isfinite(following_width)
            or abs(previous_width) < _EPSILON
            or abs(following_width) < _EPSILON
        ):
            continue
        anchor_point = (anchor["x"], anchor["y"])
        outgoing = _absolute_segment(result, index)[1]
        incoming = _absolute_segment(result, index - 1)[2]
        out_vector = (
            outgoing[0] - anchor_point[0],
            outgoing[1] - anchor_point[1],
        )
        in_vector = (
            incoming[0] - anchor_point[0],
            incoming[1] - anchor_point[1],
        )
        out_length = math.hypot(*out_vector)
        in_length = math.hypot(*in_vector)
        if out_length <= _EPSILON or in_length <= _EPSILON:
            continue
        if index in in_master_indices:

            desired = (
                anchor_point[0] - in_vector[0] / in_length * out_length,
                anchor_point[1] - in_vector[1] / in_length * out_length,
            )
            _set_absolute_handle(result, index, "out", desired)
        else:

            desired = (
                anchor_point[0] - out_vector[0] / out_length * in_length,
                anchor_point[1] - out_vector[1] / out_length * in_length,
            )
            _set_absolute_handle(result, index - 1, "in", desired)
    return result


def _aligned_opposite(
    curve,
    anchor_index,
    moved_point,
    opposite,
    stable_storage=False,
):
    anchor = curve["anchors"][anchor_index]
    anchor_point = (anchor["x"], anchor["y"])
    vector_x = moved_point[0] - anchor_point[0]
    vector_y = moved_point[1] - anchor_point[1]
    moved_length = math.hypot(vector_x, vector_y)
    if moved_length <= _EPSILON:
        return

    opposite_segment, opposite_side, opposite_point = opposite
    opposite_length = math.hypot(
        opposite_point[0] - anchor_point[0],
        opposite_point[1] - anchor_point[1],
    )
    aligned_point = (
        anchor_point[0] - vector_x / moved_length * opposite_length,
        anchor_point[1] - vector_y / moved_length * opposite_length,
    )
    _set_absolute_handle(
        curve,
        opposite_segment,
        opposite_side,
        aligned_point,
        stable_storage,
    )


def move_handle(curve, seg_index, side, x, y, snap):

    normalized = validate(curve)
    if normalized is None:
        return None
    normalized_side = side.lower() if isinstance(side, str) else None
    if (
        isinstance(seg_index, bool)
        or not isinstance(seg_index, int)
        or not 0 <= seg_index < len(normalized["segments"])
        or normalized_side not in ("out", "in")
        or not _is_finite_number(x)
        or not _is_finite_number(y)
    ):
        return deep_copy(normalized)

    x_value = float(x)
    y_value = float(y)
    drag_start = normalized["anchors"][seg_index]
    drag_end = normalized["anchors"][seg_index + 1]
    moved_point = _local_to_absolute(
        drag_start,
        drag_end,
        (x_value, y_value),
    )
    if snap:
        moved_point = tuple(_snap(value) for value in moved_point)
        x_value, y_value = _absolute_to_local(
            drag_start,
            drag_end,
            moved_point,
            stable_storage=True,
        )

    if normalized_side == "out":
        anchor_index = seg_index
        opposite_location = (
            (seg_index - 1, "in") if seg_index > 0 else None
        )
    else:
        anchor_index = seg_index + 1
        opposite_location = (
            (seg_index + 1, "out")
            if seg_index + 1 < len(normalized["segments"])
            else None
        )

    opposite = None
    if (
        opposite_location is not None
        and 0 < anchor_index < len(normalized["anchors"]) - 1
        and normalized["anchors"][anchor_index]["mode"] == "ALIGNED"
    ):
        opposite = (
            opposite_location[0],
            opposite_location[1],
            _handle_absolute(
                normalized,
                opposite_location[0],
                opposite_location[1],
            ),
        )

    result = deep_copy(normalized)
    result["segments"][seg_index][normalized_side] = [x_value, y_value]
    if opposite is not None:


        _aligned_opposite(
            result,
            anchor_index,
            moved_point,
            opposite,
            stable_storage=snap,
        )
    return validate(result)


def move_handles_sync(curve, seg_index, side, x, y, snap, anchor_indices):

    normalized = validate(curve)
    if normalized is None:
        return None
    normalized_side = side.lower() if isinstance(side, str) else None
    if (
        isinstance(seg_index, bool)
        or not isinstance(seg_index, int)
        or not 0 <= seg_index < len(normalized["segments"])
        or normalized_side not in ("out", "in")
        or not _is_finite_number(x)
        or not _is_finite_number(y)
    ):
        return deep_copy(normalized)

    x_value = float(x)
    y_value = float(y)
    new_point = _local_to_absolute(
        normalized["anchors"][seg_index],
        normalized["anchors"][seg_index + 1],
        (x_value, y_value),
    )
    if snap:
        new_point = tuple(_snap(value) for value in new_point)
        x_value, y_value = _absolute_to_local(
            normalized["anchors"][seg_index],
            normalized["anchors"][seg_index + 1],
            new_point,
            stable_storage=True,
        )

    dragged_owner = (
        seg_index if normalized_side == "out" else seg_index + 1
    )
    old_point = _handle_absolute(normalized, seg_index, normalized_side)
    delta = (new_point[0] - old_point[0], new_point[1] - old_point[1])


    moved = [(seg_index, dragged_owner)]
    for anchor_index in _normalized_handle_sync_indices(
        anchor_indices,
        len(normalized["anchors"]),
        normalized_side,
    ):
        if anchor_index == dragged_owner:
            continue
        moved.append(
            (
                anchor_index
                if normalized_side == "out"
                else anchor_index - 1,
                anchor_index,
            )
        )

    result = deep_copy(normalized)
    result["segments"][seg_index][normalized_side] = [x_value, y_value]
    for segment_index, _owner in moved[1:]:
        point = _handle_absolute(
            normalized,
            segment_index,
            normalized_side,
        )
        _set_absolute_handle(
            result,
            segment_index,
            normalized_side,
            (point[0] + delta[0], point[1] + delta[1]),
            stable_storage=snap,
        )


    last_anchor = len(normalized["anchors"]) - 1
    for segment_index, owner in moved:
        if (
            not 0 < owner < last_anchor
            or normalized["anchors"][owner]["mode"] != "ALIGNED"
        ):
            continue
        if normalized_side == "out":
            opposite_location = (owner - 1, "in")
        else:
            opposite_location = (owner, "out")
        moved_point = _handle_absolute(
            result,
            segment_index,
            normalized_side,
        )
        _aligned_opposite(
            result,
            owner,
            moved_point,
            (
                opposite_location[0],
                opposite_location[1],
                _handle_absolute(
                    normalized,
                    opposite_location[0],
                    opposite_location[1],
                ),
            ),
            stable_storage=snap,
        )
    return validate(result)


def set_anchor_mode(curve, index, mode):

    normalized = validate(curve)
    if normalized is None:
        return None
    normalized_mode = mode.strip().upper() if isinstance(mode, str) else None
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or index <= 0
        or index >= len(normalized["anchors"]) - 1
        or normalized_mode not in ANCHOR_MODES
    ):
        return deep_copy(normalized)

    result = deep_copy(normalized)
    result["anchors"][index]["mode"] = normalized_mode
    if normalized_mode == "ALIGNED":
        outgoing = _handle_absolute(result, index, "out")
        incoming = (
            index - 1,
            "in",
            _handle_absolute(result, index - 1, "in"),
        )
        _aligned_opposite(result, index, outgoing, incoming)
    return validate(result)


def _one_minus(value):

    with localcontext() as context:
        context.prec = 40
        return float(Decimal("1") - Decimal(repr(float(value))))


def reverse(curve):

    normalized = validate(curve)
    if normalized is None:
        return None

    reversed_anchors = []
    last_index = len(normalized["anchors"]) - 1
    for index, anchor in enumerate(reversed(normalized["anchors"])):
        if index == 0:
            reversed_anchors.append({"x": 0.0, "y": 0.0})
        elif index == last_index:
            reversed_anchors.append({"x": 1.0, "y": 1.0})
        else:
            reversed_anchors.append(
                {
                    "x": _one_minus(anchor["x"]),
                    "y": _one_minus(anchor["y"]),
                    "mode": anchor["mode"],
                }
            )

    reversed_segments = []
    for segment in reversed(normalized["segments"]):
        reversed_segments.append(
            {
                "out": [
                    _one_minus(segment["in"][0]),
                    _one_minus(segment["in"][1]),
                ],
                "in": [
                    _one_minus(segment["out"][0]),
                    _one_minus(segment["out"][1]),
                ],
            }
        )
    return validate(
        {
            "anchors": reversed_anchors,
            "segments": reversed_segments,
        }
    )
