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

import math
from typing import NamedTuple

from ..constants import CURVE_COORD_MAX, CURVE_COORD_MIN
from . import curve as curve_model


class DraftEvaluation(NamedTuple):


    curve: object
    applied: bool
    reset: bool
    synced_fields: frozenset


def format_points(points, snap_enabled, editing):

    if editing:
        return ", ".join(_format_edit_value(point) for point in points)
    return ", ".join("{:.2f}".format(point) for point in points)


def parse_points(text):

    try:
        new_points = list(map(float, text.replace("…", "").split(",")))
        if len(new_points) != 4:
            return None
        if not all(math.isfinite(point) for point in new_points):
            return None
        new_points[0] = min(max(new_points[0], 0), 1)
        new_points[2] = min(max(new_points[2], 0), 1)
        new_points[1] = min(
            max(new_points[1], CURVE_COORD_MIN),
            CURVE_COORD_MAX,
        )
        new_points[3] = min(
            max(new_points[3], CURVE_COORD_MIN),
            CURVE_COORD_MAX,
        )
        return [round(point * 100) / 100 for point in new_points]
    except (OverflowError, ValueError):
        return None


def _format_edit_value(value):

    return format(value, ".12g")


def _format_values(values, snap, editing):
    if editing:
        return ", ".join(_format_edit_value(value) for value in values)
    return ", ".join("{:.2f}".format(value) for value in values)


def _parse_values(text, allow_empty=False):
    if not isinstance(text, str):
        return None
    cleaned = text.replace("…", "").replace("...", "").strip()
    if not cleaned:
        return [] if allow_empty else None
    parts = cleaned.split(",")
    if any(not part.strip() for part in parts):
        return None
    try:
        values = [float(part) for part in parts]
    except ValueError:
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return values


def _realign_and_validate(curve, in_master_indices=frozenset()):

    if not isinstance(curve, dict) or not curve_model.anchor_geometry_is_valid(
        curve.get("anchors")
    ):
        return None
    return curve_model.validate(
        curve_model.realign_aligned_handles(
            curve,
            in_master_indices=in_master_indices,
        )
    )


def format_handles(curve, snap, editing):

    normalized = curve_model.validate(curve)
    if normalized is None:
        return ""
    values = []
    for segment in normalized["segments"]:
        values.extend(segment["out"])
        values.extend(segment["in"])
    return _format_values(values, snap, editing)


def parse_handles(text, curve):

    normalized = curve_model.validate(curve)
    values = _parse_values(text)
    if normalized is None or values is None:
        return None
    if len(values) != len(normalized["segments"]) * 4:
        return None

    result = curve_model.deep_copy(normalized)
    for segment_index in range(len(result["segments"])):
        value_index = segment_index * 4
        result["segments"][segment_index] = {
            "out": values[value_index : value_index + 2],
            "in": values[value_index + 2 : value_index + 4],
        }

    return _realign_and_validate(result)


def format_anchors(curve, snap, editing):

    normalized = curve_model.validate(curve)
    if normalized is None:
        return ""
    values = []
    for anchor in normalized["anchors"][1:-1]:
        values.extend((anchor["x"], anchor["y"]))
    return _format_values(values, snap, editing)


def parse_anchors(text, curve):

    normalized = curve_model.validate(curve)
    values = _parse_values(text, allow_empty=True)
    if normalized is None or values is None or len(values) % 2:
        return None

    coordinates = [
        (values[index], values[index + 1])
        for index in range(0, len(values), 2)
    ]
    if any(
        current[0] <= previous[0]
        for previous, current in zip(coordinates, coordinates[1:])
    ):
        return None
    modes = [
        anchor["mode"]
        for anchor in normalized["anchors"][1:-1]
    ]

    anchors = [{"x": 0.0, "y": 0.0}]
    for index, (x_value, y_value) in enumerate(coordinates):
        if not 0.0 < x_value < 1.0:
            return None
        anchors.append(
            {
                "x": x_value,
                "y": y_value,
                "mode": modes[index] if index < len(modes) else "ALIGNED",
            }
        )
    anchors.append({"x": 1.0, "y": 1.0})

    segment_count = len(anchors) - 1
    segments = curve_model.deep_copy(
        normalized["segments"][:segment_count]
    )
    default_segment = curve_model.new_default_curve()["segments"][0]
    while len(segments) < segment_count:
        segments.append(curve_model.deep_copy(default_segment))


    new_anchor_indices = frozenset(
        range(len(modes) + 1, len(anchors) - 1)
    )
    return _realign_and_validate(
        {
            "anchors": anchors,
            "segments": segments,
        },
        in_master_indices=new_anchor_indices,
    )


def _selected_anchor_indices(curve, indices):
    normalized = curve_model.validate(curve)
    if normalized is None or isinstance(indices, (str, bytes)):
        return normalized, []
    try:
        candidates = set(indices)
    except TypeError:
        return normalized, []
    last_index = len(normalized["anchors"]) - 1
    selected = sorted(
        index
        for index in candidates
        if (
            isinstance(index, int)
            and not isinstance(index, bool)
            and 0 <= index <= last_index
        )
    )
    return normalized, selected


def format_selection_prefix(indices):

    try:
        selected = sorted(
            index
            for index in set(indices)
            if isinstance(index, int) and not isinstance(index, bool) and index >= 0
        )
    except TypeError:
        selected = []
    if not selected:
        return ""
    if len(selected) >= 4:
        return f"{len(selected)} pts:"
    return ",".join(f"P{index + 1}" for index in selected) + ":"


def format_selected_handles(curve, indices, snap, editing):

    normalized, selected = _selected_anchor_indices(curve, indices)
    if normalized is None or not selected:
        return ""
    values = []
    last_index = len(normalized["anchors"]) - 1
    for anchor_index in selected:
        if anchor_index > 0:
            values.extend(normalized["segments"][anchor_index - 1]["in"])
        if anchor_index < last_index:
            values.extend(normalized["segments"][anchor_index]["out"])
    return _format_values(values, snap, editing)


def parse_selected_handles(text, curve, indices):

    normalized, selected = _selected_anchor_indices(curve, indices)
    values = _parse_values(text)
    last_index = (
        -1 if normalized is None else len(normalized["anchors"]) - 1
    )
    expected_count = sum(
        (2 if anchor_index > 0 else 0)
        + (2 if anchor_index < last_index else 0)
        for anchor_index in selected
    )
    if (
        normalized is None
        or not selected
        or values is None
        or len(values) != expected_count
    ):
        return None

    result = curve_model.deep_copy(normalized)
    value_index = 0
    for anchor_index in selected:
        if anchor_index > 0:
            result["segments"][anchor_index - 1]["in"] = values[
                value_index : value_index + 2
            ]
            value_index += 2
        if anchor_index < last_index:
            result["segments"][anchor_index]["out"] = values[
                value_index : value_index + 2
            ]
            value_index += 2
    return _realign_and_validate(result)


def format_selected_anchors(curve, indices, snap, editing):

    normalized, selected = _selected_anchor_indices(curve, indices)
    if normalized is None or not selected:
        return ""
    values = []
    for anchor_index in selected:
        anchor = normalized["anchors"][anchor_index]
        values.extend((anchor["x"], anchor["y"]))
    return _format_values(values, snap, editing)


def parse_selected_anchors(text, curve, indices):

    normalized, selected = _selected_anchor_indices(curve, indices)
    values = _parse_values(text)
    if (
        normalized is None
        or not selected
        or values is None
        or len(values) != len(selected) * 2
    ):
        return None

    result = curve_model.deep_copy(normalized)
    last_index = len(result["anchors"]) - 1
    for selection_index, anchor_index in enumerate(selected):
        value_index = selection_index * 2
        if anchor_index in (0, last_index):
            anchor = result["anchors"][anchor_index]
            if (
                values[value_index] != anchor["x"]
                or values[value_index + 1] != anchor["y"]
            ):
                return None
            continue
        result["anchors"][anchor_index]["x"] = values[value_index]
        result["anchors"][anchor_index]["y"] = values[value_index + 1]
    return _realign_and_validate(result)


def evaluate_curve_drafts(
    handle_text,
    anchor_text,
    curve,
    changed_field,
    indices=None,
):

    normalized, selected = _selected_anchor_indices(curve, indices or ())
    if normalized is None:
        return DraftEvaluation(None, False, False, frozenset())
    if handle_text == "" and anchor_text == "":
        return DraftEvaluation(
            normalized,
            False,
            True,
            frozenset(("handles", "anchors")),
        )

    if selected:
        parse_handle = lambda text, base: parse_selected_handles(
            text,
            base,
            selected,
        )
        parse_anchor = lambda text, base: parse_selected_anchors(
            text,
            base,
            selected,
        )
    else:
        parse_handle = parse_handles
        parse_anchor = parse_anchors
    parsers = {
        "handles": parse_handle,
        "anchors": parse_anchor,
    }
    texts = {
        "handles": handle_text,
        "anchors": anchor_text,
    }

    def parse_pair(base):
        anchored = parse_anchor(anchor_text, base)
        if anchored is None:
            return None
        return parse_handle(handle_text, anchored)

    def matching_fields(result):
        signature = curve_model.canonical_signature(result)
        matching = set()
        for field_name, parser in parsers.items():
            parsed = parser(texts[field_name], result)
            if (
                parsed is not None
                and curve_model.canonical_signature(parsed) == signature
            ):
                matching.add(field_name)
        return frozenset(matching)

    paired = parse_pair(normalized)
    if paired is not None:
        return DraftEvaluation(
            paired,
            True,
            False,
            matching_fields(paired),
        )

    changed_field = str(changed_field)
    parser = parsers.get(changed_field)
    if parser is None:
        return DraftEvaluation(
            normalized,
            False,
            False,
            frozenset(),
        )
    result = parser(texts[changed_field], normalized)
    if result is None:
        return DraftEvaluation(
            normalized,
            False,
            False,
            frozenset(),
        )

    other_field = "anchors" if changed_field == "handles" else "handles"
    other_result = parsers[other_field](texts[other_field], result)
    if other_result is not None:
        result = other_result
    return DraftEvaluation(
        result,
        True,
        False,
        matching_fields(result),
    )
