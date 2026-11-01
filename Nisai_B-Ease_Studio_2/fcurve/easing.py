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

from bisect import bisect_left
import math
import struct

import bpy

from ..core import curve as curve_model
from .compat import get_fcurves_for_anim_data


_TIME_EPSILON = 1e-10

_KEYFRAME_INSERT_TOLERANCE = 0.01
_ALIGN_REL_TOLERANCE = 1e-6


class ApplyRollbackError(RuntimeError):
    pass


MESSAGES = {
    "no_selected_keyframes": {
        "ja_JP": "No selected keyframes",
        "en_US": "No selected keyframes",
    },
    "select_consecutive_keyframes": {
        "ja_JP": "Please select two consecutive keyframes",
        "en_US": "Please select two consecutive keyframes",
    },
    "no_value_change": {
        "ja_JP": "Cannot copy because there is no value change",
        "en_US": "Cannot copy because there is no value change",
    },
    "time_diff_too_small": {
        "ja_JP": "Time difference between keyframes is too small",
        "en_US": "Time difference between keyframes is too small",
    },
    "no_valid_selection": {
        "ja_JP": "No valid selection found",
        "en_US": "No valid selection found",
    },
    "invalid_easing": {
        "ja_JP": "Invalid easing",
        "en_US": "Invalid easing",
    },
    "apply_error": {
        "ja_JP": "Error while applying",
        "en_US": "Error while applying",
    },
    "frame_collision": {
        "ja_JP": "Skipped a range with a frame precision collision",
        "en_US": "Skipped a range with a frame precision collision",
    },
}


def get_message(key):

    try:
        language = bpy.context.preferences.view.language
        if language == "ja_JP":
            return MESSAGES[key]["ja_JP"]
        return MESSAGES[key]["en_US"]
    except:
        return MESSAGES[key]["en_US"]


def _show_message(callback, key):

    if callable(callback):
        callback(get_message(key))


def _finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _finite_values(*values):
    return all(_finite_number(value) for value in values)


def _is_consecutive_run(indices):
    return len(indices) >= 2 and all(
        current == previous + 1
        for previous, current in zip(indices, indices[1:])
    )


def _consecutive_runs(indices):

    runs = []
    for index in indices:
        if not runs or index != runs[-1][-1] + 1:
            runs.append([index])
        else:
            runs[-1].append(index)
    return runs


def _rna_float(value):

    try:
        quantized = struct.unpack("=f", struct.pack("=f", float(value)))[0]
    except (OverflowError, struct.error, ValueError):
        return None
    return quantized if math.isfinite(quantized) else None


def _infer_anchor_mode(keyframe):

    co_x, co_y = (float(value) for value in keyframe.co)
    left_x = float(keyframe.handle_left[0]) - co_x
    left_y = float(keyframe.handle_left[1]) - co_y
    right_x = float(keyframe.handle_right[0]) - co_x
    right_y = float(keyframe.handle_right[1]) - co_y
    left_length = math.hypot(left_x, left_y)
    right_length = math.hypot(right_x, right_y)
    if left_length <= _TIME_EPSILON or right_length <= _TIME_EPSILON:
        return "FREE"
    cross = left_x * right_y - left_y * right_x
    dot = left_x * right_x + left_y * right_y
    if (
        dot < 0.0
        and abs(cross)
        <= _ALIGN_REL_TOLERANCE * left_length * right_length
    ):
        return "ALIGNED"
    return "FREE"


def copy_fcurve_easing(show_message=None, fcurve_provider=None):

    fcurves_with_keys = (
        get_selected_fcurves_and_keyframes()
        if fcurve_provider is None
        else get_selected_fcurves_and_keyframes(
            fcurve_provider=fcurve_provider,
        )
    )

    if not fcurves_with_keys:
        _show_message(show_message, "no_selected_keyframes")
        return None

    for curve, selected_keys in fcurves_with_keys:
        if not _is_consecutive_run(selected_keys):
            continue

        keyframes = [
            curve.keyframe_points[index] for index in selected_keys
        ]
        scalar_values = []
        for keyframe in keyframes:
            scalar_values.extend(
                (
                    keyframe.co[0],
                    keyframe.co[1],
                    keyframe.handle_left[0],
                    keyframe.handle_left[1],
                    keyframe.handle_right[0],
                    keyframe.handle_right[1],
                )
            )
        if not _finite_values(*scalar_values):
            _show_message(show_message, "invalid_easing")
            return None

        first_frame = float(keyframes[0].co[0])
        first_value = float(keyframes[0].co[1])
        last_frame = float(keyframes[-1].co[0])
        last_value = float(keyframes[-1].co[1])
        time_diff = last_frame - first_frame
        value_diff = last_value - first_value

        if value_diff == 0:
            _show_message(show_message, "no_value_change")
            return None
        if abs(time_diff) < _TIME_EPSILON:
            _show_message(show_message, "time_diff_too_small")
            return None

        anchors = []
        for index, keyframe in enumerate(keyframes):
            if index == 0:
                anchor = {"x": 0.0, "y": 0.0}
            elif index == len(keyframes) - 1:
                anchor = {"x": 1.0, "y": 1.0}
            else:
                anchor = {
                    "x": (float(keyframe.co[0]) - first_frame)
                    / time_diff,
                    "y": (float(keyframe.co[1]) - first_value)
                    / value_diff,
                    "mode": _infer_anchor_mode(keyframe),
                }
            anchors.append(anchor)

        segments = []
        for keyframe1, keyframe2 in zip(keyframes, keyframes[1:]):
            frame1 = float(keyframe1.co[0])
            value1 = float(keyframe1.co[1])
            segment_time = float(keyframe2.co[0]) - frame1
            segment_value = float(keyframe2.co[1]) - value1
            if abs(segment_time) < _TIME_EPSILON:
                _show_message(show_message, "time_diff_too_small")
                return None


            out_x = (
                float(keyframe1.handle_right[0]) - frame1
            ) / segment_time
            in_x = (
                float(keyframe2.handle_left[0]) - frame1
            ) / segment_time
            if segment_value == 0.0:


                out_y = 0.0
                in_y = 1.0
            else:
                out_y = (
                    float(keyframe1.handle_right[1]) - value1
                ) / segment_value
                in_y = (
                    float(keyframe2.handle_left[1]) - value1
                ) / segment_value
            segments.append(
                {
                    "out": [out_x, out_y],
                    "in": [in_x, in_y],
                }
            )

        copied = curve_model.validate(
            {"anchors": anchors, "segments": segments}
        )
        if copied is None:
            _show_message(show_message, "invalid_easing")
            return None
        return copied

    _show_message(show_message, "select_consecutive_keyframes")
    return None


def _new_curve_plan(curve):
    return {
        "curve": curve,
        "existing_frames": sorted(
            float(keyframe.co[0]) for keyframe in curve.keyframe_points
        ),
        "insertions": [],
        "assignments": {},
        "applied_count": 0,
        "frame_collision": False,
    }


def _assignment(plan, frame):
    return plan["assignments"].setdefault(
        float(frame),
        {
            "interpolation": False,
            "handle_left": None,
            "handle_right": None,
            "handle_left_type": None,
            "handle_right_type": None,
            "select": False,
            "inserted": False,
        },
    )


def _set_segment_assignment(
    plan,
    left,
    right,
    segment,
    left_type,
    right_type,
    select,
):
    delta_frame = right[0] - left[0]
    delta_value = right[1] - left[1]
    handle_right = (
        left[0] + float(segment["out"][0]) * delta_frame,
        left[1] + float(segment["out"][1]) * delta_value,
    )
    handle_left = (
        left[0] + float(segment["in"][0]) * delta_frame,
        left[1] + float(segment["in"][1]) * delta_value,
    )
    if any(
        _rna_float(value) is None
        for value in (*handle_right, *handle_left)
    ):
        return False

    left_assignment = _assignment(plan, left[0])
    right_assignment = _assignment(plan, right[0])
    left_assignment["interpolation"] = True
    left_assignment["handle_right"] = handle_right
    left_assignment["handle_right_type"] = left_type
    right_assignment["handle_left"] = handle_left
    right_assignment["handle_left_type"] = right_type
    if select:
        left_assignment["select"] = True
        right_assignment["select"] = True
    return True


def _plan_matching_key_count(plan, normalized, key_coordinates):
    last_index = len(key_coordinates) - 1
    for index, (left, right) in enumerate(
        zip(key_coordinates, key_coordinates[1:])
    ):
        delta_frame = right[0] - left[0]
        delta_value = right[1] - left[1]
        if abs(delta_frame) < _TIME_EPSILON or delta_value == 0.0:
            continue
        left_type = (
            "FREE"
            if index == 0
            else normalized["anchors"][index]["mode"]
        )
        right_type = (
            "FREE"
            if index + 1 == last_index
            else normalized["anchors"][index + 1]["mode"]
        )
        if not _set_segment_assignment(
            plan,
            left,
            right,
            normalized["segments"][index],
            left_type,
            right_type,
            False,
        ):
            return False
        plan["applied_count"] += 1
    return True


def _plan_insert_pair(plan, normalized, left, right):
    delta_frame = right[0] - left[0]
    delta_value = right[1] - left[1]
    if abs(delta_frame) < _TIME_EPSILON or delta_value == 0.0:
        return True

    mapped_anchors = []
    for anchor in normalized["anchors"]:
        mapped_frame = _rna_float(
            left[0] + float(anchor["x"]) * delta_frame
        )
        mapped = (
            mapped_frame,
            left[1] + float(anchor["y"]) * delta_value,
        )
        if mapped_frame is None or _rna_float(mapped[1]) is None:
            return False
        mapped_anchors.append(mapped)

    mapped_frames = [mapped[0] for mapped in mapped_anchors]
    inserted_frames = mapped_frames[1:-1]
    if inserted_frames and any(
        right_frame - left_frame <= _KEYFRAME_INSERT_TOLERANCE
        for left_frame, right_frame in zip(mapped_frames, mapped_frames[1:])
    ):
        plan["frame_collision"] = True
        return True
    existing_frames = plan["existing_frames"]
    for frame in inserted_frames:
        index = bisect_left(existing_frames, frame)
        neighbors = existing_frames[max(0, index - 1):index + 1]
        if any(
            abs(existing_frame - frame) <= _KEYFRAME_INSERT_TOLERANCE
            for existing_frame in neighbors
        ):
            plan["frame_collision"] = True
            return True

    for index, mapped in enumerate(mapped_anchors):
        if 0 < index < len(normalized["anchors"]) - 1:
            plan["insertions"].append(mapped)
            inserted_assignment = _assignment(plan, mapped[0])
            inserted_assignment["inserted"] = True

    last_index = len(mapped_anchors) - 1
    for index, (mapped_left, mapped_right) in enumerate(
        zip(mapped_anchors, mapped_anchors[1:])
    ):
        left_type = (
            "FREE"
            if index == 0
            else normalized["anchors"][index]["mode"]
        )
        right_type = (
            "FREE"
            if index + 1 == last_index
            else normalized["anchors"][index + 1]["mode"]
        )
        if not _set_segment_assignment(
            plan,
            mapped_left,
            mapped_right,
            normalized["segments"][index],
            left_type,
            right_type,
            True,
        ):
            return False
    plan["applied_count"] += 1
    return True


def _resolve_keyframes(curve, expected_frames):


    expected_frames = set(expected_frames)
    resolved = {}
    for keyframe in curve.keyframe_points:
        frame = float(keyframe.co[0])
        if frame not in expected_frames:
            continue
        if frame in resolved:
            return None
        resolved[frame] = keyframe
    return resolved if len(resolved) == len(expected_frames) else None


def _preserve_cyclic_endpoint_handles(plan):

    fcurve = plan["curve"]
    if len(fcurve.keyframe_points) < 2 or not fcurve.modifiers:
        return
    modifier = fcurve.modifiers[0]

    if (
        modifier.type != "CYCLES"
        or modifier.mute
        or not modifier.is_valid
        or modifier.use_restricted_range
        or modifier.use_influence
        or modifier.cycles_before != 0
        or modifier.cycles_after != 0
        or modifier.mode_before not in {"REPEAT", "REPEAT_OFFSET"}
        or modifier.mode_after not in {"REPEAT", "REPEAT_OFFSET"}
    ):
        return
    endpoints = (
        (fcurve.keyframe_points[0], "right"),
        (fcurve.keyframe_points[-1], "left"),
    )
    automatic = {"AUTO", "AUTO_CLAMPED"}
    if not all(
        keyframe.handle_left_type in automatic
        and keyframe.handle_right_type in automatic
        for keyframe, _side in endpoints
    ):
        return
    if not any(
        plan["assignments"].get(float(keyframe.co[0]), {}).get(f"handle_{side}")
        is not None
        for keyframe, side in endpoints
    ):
        return


    for keyframe, side in endpoints:
        assignment = plan["assignments"].get(float(keyframe.co[0]), {})
        if assignment.get(f"handle_{side}") is None:
            coordinate = tuple(getattr(keyframe, f"handle_{side}"))
            setattr(keyframe, f"handle_{side}_type", "FREE")
            setattr(keyframe, f"handle_{side}", coordinate)


def _apply_curve_plan(plan):
    fcurve = plan["curve"]
    _preserve_cyclic_endpoint_handles(plan)


    existing_frames = plan["existing_frames"]
    for keyframe in fcurve.keyframe_points:
        frame = float(keyframe.co[0])
        assignment = plan["assignments"].get(frame)
        if assignment is None:
            continue
        for side, other_side in (("left", "right"), ("right", "left")):
            if (
                assignment[f"handle_{side}"] is not None
                or assignment[f"handle_{other_side}"] is None
            ):
                continue
            handle_type = getattr(keyframe, f"handle_{side}_type")
            endpoint_vector = handle_type == "VECTOR" and len(existing_frames) > 1 and (
                (side == "left" and frame == existing_frames[0] and any(
                    frame < inserted_frame < existing_frames[1]
                    for inserted_frame, _value in plan["insertions"]
                ))
                or (side == "right" and frame == existing_frames[-1] and any(
                    existing_frames[-2] < inserted_frame < frame
                    for inserted_frame, _value in plan["insertions"]
                ))
            )
            if handle_type in {"AUTO", "AUTO_CLAMPED"} or endpoint_vector:
                coordinate = tuple(getattr(keyframe, f"handle_{side}"))
                setattr(keyframe, f"handle_{side}_type", "FREE")
                setattr(keyframe, f"handle_{side}", coordinate)

    for frame, value in plan["insertions"]:
        fcurve.keyframe_points.insert(
            frame,
            value,
            options={"FAST"},
            keyframe_type="KEYFRAME",
        )

    resolved = _resolve_keyframes(fcurve, plan["assignments"])
    if resolved is None:
        raise RuntimeError("Could not resolve inserted keyframes")

    for frame, assignment in plan["assignments"].items():
        keyframe = resolved[frame]
        if assignment["interpolation"]:
            keyframe.interpolation = "BEZIER"
        if assignment["inserted"]:
            keyframe.type = "KEYFRAME"
        if assignment["handle_left"] is not None:
            keyframe.handle_left_type = "FREE"
        if assignment["handle_right"] is not None:
            keyframe.handle_right_type = "FREE"

    for frame, assignment in plan["assignments"].items():
        keyframe = resolved[frame]
        if assignment["handle_left"] is not None:
            keyframe.handle_left = assignment["handle_left"]
        if assignment["handle_right"] is not None:
            keyframe.handle_right = assignment["handle_right"]

    for frame, assignment in plan["assignments"].items():
        keyframe = resolved[frame]
        left_type = assignment["handle_left_type"] or keyframe.handle_left_type
        right_type = assignment["handle_right_type"] or keyframe.handle_right_type


        if (
            left_type == right_type == "ALIGNED"
            and math.dist(keyframe.co, keyframe.handle_left) > _TIME_EPSILON
            and math.dist(keyframe.co, keyframe.handle_right) > _TIME_EPSILON
            and _infer_anchor_mode(keyframe) != "ALIGNED"
        ):
            for side in ("left", "right"):
                if assignment[f"handle_{side}_type"] == "ALIGNED":
                    assignment[f"handle_{side}_type"] = "FREE"
        if assignment["handle_left_type"] is not None:
            keyframe.handle_left_type = assignment["handle_left_type"]
        if assignment["handle_right_type"] is not None:
            keyframe.handle_right_type = assignment["handle_right_type"]
        if assignment["select"]:
            keyframe.select_control_point = True
            keyframe.select_left_handle = True
            keyframe.select_right_handle = True


    fcurve.keyframe_points.sort()
    fcurve.keyframe_points.handles_recalc()


def _snapshot_keyframe(keyframe):
    state = {
        "co": tuple(float(value) for value in keyframe.co),
        "handle_left": tuple(float(value) for value in keyframe.handle_left),
        "handle_right": tuple(float(value) for value in keyframe.handle_right),
        "interpolation": keyframe.interpolation,
        "handle_left_type": keyframe.handle_left_type,
        "handle_right_type": keyframe.handle_right_type,
        "type": keyframe.type,
        "select_control_point": bool(keyframe.select_control_point),
        "select_left_handle": bool(keyframe.select_left_handle),
        "select_right_handle": bool(keyframe.select_right_handle),
    }
    for attribute in ("easing", "amplitude", "back", "period"):
        try:
            state[attribute] = getattr(keyframe, attribute)
        except (AttributeError, TypeError):
            pass
    return state


def _snapshot_fcurve(fcurve):
    return [_snapshot_keyframe(keyframe) for keyframe in fcurve.keyframe_points]


def _restore_fcurve_snapshot(fcurve, snapshot):
    keyframe_points = fcurve.keyframe_points


    keyframe_points.clear()
    keyframe_points.add(len(snapshot))

    restored = list(keyframe_points)
    if len(restored) != len(snapshot):
        raise RuntimeError("Could not restore all keyframes")
    attribute_failures = []
    for keyframe, state in zip(restored, snapshot):
        keyframe.handle_left_type = "FREE"
        keyframe.handle_right_type = "FREE"
        keyframe.co = state["co"]
        keyframe.handle_left = state["handle_left"]
        keyframe.handle_right = state["handle_right"]
        keyframe.interpolation = state["interpolation"]
        keyframe.type = state["type"]
        for attribute in ("easing", "amplitude", "back", "period"):
            if attribute in state:
                try:
                    setattr(keyframe, attribute, state[attribute])
                except (AttributeError, TypeError, ValueError) as error:
                    attribute_failures.append((attribute, error))
        keyframe.handle_left_type = state["handle_left_type"]
        keyframe.handle_right_type = state["handle_right_type"]
        keyframe.select_control_point = state["select_control_point"]
        keyframe.select_left_handle = state["select_left_handle"]
        keyframe.select_right_handle = state["select_right_handle"]
    if attribute_failures:
        details = "; ".join(
            f"{attribute}: {type(error).__name__}: {error}"
            for attribute, error in attribute_failures
        )
        raise RuntimeError(f"Could not restore keyframe properties: {details}")
    if not _snapshots_equivalent(_snapshot_fcurve(fcurve), snapshot):
        raise RuntimeError("Restored FCurve does not match its snapshot")


def _snapshots_equivalent(actual, expected):
    if len(actual) != len(expected):
        return False
    vector_fields = ("co", "handle_left", "handle_right")
    exact_fields = (
        "interpolation",
        "handle_left_type",
        "handle_right_type",
        "type",
        "select_control_point",
        "select_left_handle",
        "select_right_handle",
    )
    optional_fields = ("easing", "amplitude", "back", "period")
    for actual_state, expected_state in zip(actual, expected):
        for field in vector_fields:
            if any(
                not math.isclose(
                    actual_value,
                    expected_value,
                    rel_tol=2e-7,
                    abs_tol=1e-5,
                )
                for actual_value, expected_value in zip(
                    actual_state[field],
                    expected_state[field],
                )
            ):
                return False
        if any(
            actual_state[field] != expected_state[field]
            for field in exact_fields
        ):
            return False
        for field in optional_fields:
            if field not in expected_state:
                continue
            actual_value = actual_state.get(field)
            expected_value = expected_state[field]
            if isinstance(expected_value, (int, float)):
                if actual_value is None or not math.isclose(
                    float(actual_value),
                    float(expected_value),
                    rel_tol=2e-7,
                    abs_tol=1e-5,
                ):
                    return False
            elif actual_value != expected_value:
                return False
    return True


def paste_fcurve_easing(curve, show_message=None, fcurve_provider=None):

    normalized = curve_model.validate(curve)
    if normalized is None:
        _show_message(show_message, "invalid_easing")
        return 0

    fcurves_with_keys = (
        get_selected_fcurves_and_keyframes()
        if fcurve_provider is None
        else get_selected_fcurves_and_keyframes(
            fcurve_provider=fcurve_provider,
        )
    )
    if not fcurves_with_keys:
        _show_message(show_message, "no_selected_keyframes")
        return 0

    unique_targets = []
    target_ids = set()
    for fcurve, selected_keys in fcurves_with_keys:
        if not getattr(getattr(fcurve, "id_data", None), "is_editable", True):
            continue
        if fcurve.lock or (fcurve.group and fcurve.group.lock):
            continue
        identity = _fcurve_identity(fcurve)
        if identity in target_ids:
            continue
        target_ids.add(identity)
        unique_targets.append((fcurve, selected_keys))
    fcurves_with_keys = unique_targets

    operation_snapshots = []
    for fcurve, _selected_keys in fcurves_with_keys:
        operation_snapshots.append((fcurve, _snapshot_fcurve(fcurve)))

    mutation_plans = []
    frame_collision = False

    for fcurve, selected_keys in fcurves_with_keys:
        selected_keys = list(selected_keys)
        if not selected_keys:
            continue

        if len(selected_keys) == 2 and not _is_consecutive_run(selected_keys):
            continue

        coordinates = [
            (float(keyframe.co[0]), float(keyframe.co[1]))
            for keyframe in fcurve.keyframe_points
        ]
        if not all(_finite_values(*coordinate) for coordinate in coordinates):
            _show_message(show_message, "invalid_easing")
            return 0

        plan = _new_curve_plan(fcurve)
        for run in _consecutive_runs(selected_keys):
            if len(run) == len(normalized["anchors"]):
                selected_coordinates = [
                    coordinates[index] for index in run
                ]
                if not _plan_matching_key_count(
                    plan,
                    normalized,
                    selected_coordinates,
                ):
                    _show_message(show_message, "invalid_easing")
                    return 0
                continue

            if len(run) == 1:
                target_pairs = (
                    [(run[0], run[0] + 1)]
                    if len(selected_keys) == 1
                    and run[0] < len(fcurve.keyframe_points) - 1
                    else []
                )
            else:
                target_pairs = list(zip(run, run[1:]))
            for left_index, right_index in target_pairs:
                if not _plan_insert_pair(
                    plan,
                    normalized,
                    coordinates[left_index],
                    coordinates[right_index],
                ):
                    _show_message(show_message, "invalid_easing")
                    return 0
        frame_collision = frame_collision or plan["frame_collision"]
        if plan["applied_count"]:
            frames = plan["existing_frames"]
            duplicate_frames = {
                left for left, right in zip(frames, frames[1:])
                if left == right
            }
            if duplicate_frames & plan["assignments"].keys():
                frame_collision = True
                continue
            mutation_plans.append(plan)

    if frame_collision:
        _show_message(show_message, "frame_collision")

    applied_count = 0
    try:
        for plan in mutation_plans:
            _apply_curve_plan(plan)
            applied_count += plan["applied_count"]
    except Exception as apply_error:
        rollback_failures = []
        for fcurve, snapshot in operation_snapshots:
            try:
                _restore_fcurve_snapshot(fcurve, snapshot)
            except Exception as rollback_error:

                rollback_failures.append(rollback_error)
        if rollback_failures:
            details = "; ".join(
                f"{type(error).__name__}: {error}"
                for error in rollback_failures
            )
            raise ApplyRollbackError(
                "Apply failed and FCurve rollback was incomplete: " + details
            ) from apply_error
        _show_message(show_message, "apply_error")
        return 0
    return applied_count


def _get_editor_context(editor_type):

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == editor_type:
                for region in area.regions:
                    if region.type == "WINDOW":
                        return {
                            "window": window,
                            "area": area,
                            "region": region,
                            "screen": window.screen,
                        }
    return None


def get_graph_editor_context():

    return _get_editor_context("GRAPH_EDITOR")


def get_dopesheet_context():

    return _get_editor_context("DOPESHEET_EDITOR")


def get_visible_fcurves_from_context(context=None):

    context = bpy.context if context is None else context
    area = getattr(context, "area", None)
    if getattr(area, "type", None) not in {
        "GRAPH_EDITOR",
        "DOPESHEET_EDITOR",
    }:
        return None
    space = getattr(context, "space_data", None)
    if space is None:
        space = getattr(getattr(area, "spaces", None), "active", None)
    if getattr(area, "type", None) == "GRAPH_EDITOR" and getattr(space, "mode", None) == "DRIVERS":
        return ()
    for attribute in ("visible_fcurves", "selected_visible_fcurves"):
        try:
            fcurves = getattr(context, attribute)
        except (AttributeError, ReferenceError, TypeError):
            continue
        if fcurves is not None:
            return tuple(fcurves)
    return None


def _provider_fcurves(fcurve_provider):
    if callable(fcurve_provider):
        return fcurve_provider()
    return fcurve_provider


def get_selected_fcurves_and_keyframes(fcurve_provider=None):

    if fcurve_provider is None:
        return collect_selected_keyframes_from_scene()
    fcurves = _provider_fcurves(fcurve_provider)
    if fcurves is None:
        return collect_selected_keyframes_from_scene()
    selected = []
    seen = set()
    for fcurve in fcurves:
        identity = _fcurve_identity(fcurve)
        if identity in seen:
            continue
        seen.add(identity)
        selected_indices = _selected_keyframe_indices(fcurve)
        if selected_indices:
            selected.append((fcurve, selected_indices))
    return selected


def _selected_keyframe_indices(fcurve):
    if getattr(fcurve, "driver", None) is not None:
        return []
    return [
        index
        for index, keyframe in enumerate(fcurve.keyframe_points)
        if keyframe.select_control_point
    ]


def _fcurve_identity(fcurve):
    try:
        return int(fcurve.as_pointer())
    except (AttributeError, ReferenceError, TypeError):
        return id(fcurve)


def _collect_selected_from_animation_data(animation_data, output, seen):
    if animation_data is None or not animation_data.action:
        return
    for fcurve in get_fcurves_for_anim_data(animation_data):
        identity = _fcurve_identity(fcurve)
        if identity in seen:
            continue
        selected_indices = _selected_keyframe_indices(fcurve)
        if selected_indices:
            seen.add(identity)
            output.append((fcurve, selected_indices))


def collect_selected_keyframes_from_scene(only_selected=True):

    fcurves_with_keys = []
    seen = set()

    if only_selected:
        objects = (
            obj for obj in bpy.context.scene.objects if obj.select_get()
        )
    else:
        objects = bpy.context.scene.objects

    for obj in objects:
        _collect_selected_from_animation_data(
            obj.animation_data,
            fcurves_with_keys,
            seen,
        )

        if hasattr(obj, "data") and obj.data:
            _collect_selected_from_animation_data(
                getattr(obj.data, "animation_data", None),
                fcurves_with_keys,
                seen,
            )

    return fcurves_with_keys
