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

from __future__ import annotations

import math
import time

from ..constants import (
    GRID_MAIN_DIVISIONS,
    GRID_PADDING,
    GRID_SUB_DIVISIONS,
)
from ..core import curve as curve_model
from ..properties import BEaseGlobals
from . import draw, theme
from .widgets import Rect


class DrawingCanvas:


    CURVE_SAMPLE_BUDGET = 512
    HIT_RADIUS = 14.0
    BAND_DRAG_THRESHOLD = 4.0
    POINT_DIAMETER = 8.0
    ZOOM_STEP = 1.15
    MIN_ZOOM = 1.0
    MAX_ZOOM = 100.0
    MAX_WHEEL_NOTCHES = 100.0


    PAN_SNAP_RADIUS = 15.0
    TRACKPAD_SESSION_GAP = 0.3
    TARGET_ANCHOR = "ANCHOR"
    TARGET_HANDLE = "HANDLE"

    def __init__(
        self,
        rect=(0, 0, 0, 0),
        *,
        palette=None,
        scale=1.0,
        on_points_changed=None,
        on_selection_changed=None,
    ):
        self.rect = Rect(*map(float, rect))
        self.visible = True
        self._palette = palette
        self.scale = float(scale)
        self.on_points_changed = on_points_changed
        self.on_selection_changed = on_selection_changed
        self.hovered = False
        self.hover_target = None
        self.selected_anchor_indices = set()
        self.active_target = None

        self.selected_handle = None
        self.dragging_handle = False
        self.dragging_anchor = False


        self.handle_sync_indices = None
        self.drag_moved = False
        self.drag_start_pos = None
        self.band_pending = False
        self.band_selecting = False
        self.band_current_pos = None

        self.band_mode = "SET"
        self.panning = False

        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0

        self._pan_raw = None
        self._last_trackpad_time = 0.0
        self.last_mouse_pos = None
        self.message = ""
        self.message_opacity = 1.0

    def set_rect(self, x, y, w, h):
        self.rect = Rect(float(x), float(y), float(w), float(h))

    def hit(self, x, y):
        return (
            self.visible
            and self.rect.w > 0.0
            and self.rect.h > 0.0
            and self.rect.contains(x, y)
        )

    def set_hover(self, x, y):
        hovered = self.hit(x, y)
        target = self.hit_target(x, y, include_endpoints=True) if hovered else None
        changed = hovered != self.hovered or target != self.hover_target
        self.hovered = hovered
        self.hover_target = target
        return changed

    @property
    def hovered_target(self):

        return self.hover_target

    @property
    def dragging(self):
        return self.dragging_handle or self.dragging_anchor

    @property
    def selected_anchor_index(self):

        selected = self._valid_selected_anchors()
        return next(iter(selected)) if len(selected) == 1 else None

    @selected_anchor_index.setter
    def selected_anchor_index(self, index):
        self.select_anchor(index)

    @property
    def band_interaction(self):

        return self.band_pending

    @property
    def scroll_offset_y(self):

        return self.pan_y

    @scroll_offset_y.setter
    def scroll_offset_y(self, value):
        self.pan_y = float(value)

    def _grid_geometry(self):


        size = max(
            1.0,
            min(self.rect.w, self.rect.h) - GRID_PADDING * self.scale,
        ) * self.zoom
        return (
            self.rect.x + (self.rect.w - size) * 0.5 + self.pan_x,
            self.rect.y + (self.rect.h - size) * 0.5 + self.pan_y,
            size,
        )

    def _data_to_region(self, x_value, y_value):
        grid_x, grid_y, size = self._grid_geometry()
        return grid_x + size * x_value, grid_y + size * y_value

    def _region_to_data(self, x, y):
        grid_x, grid_y, size = self._grid_geometry()
        return (x - grid_x) / size, (y - grid_y) / size

    def _notify_points(self):
        if self.on_points_changed is not None:
            self.on_points_changed()

    def _notify_selection(self):
        if self.on_selection_changed is not None:
            self.on_selection_changed()

    @staticmethod
    def _anchor_target(index):
        return (DrawingCanvas.TARGET_ANCHOR, int(index))

    @staticmethod
    def _handle_target(segment_index, side):
        return (
            DrawingCanvas.TARGET_HANDLE,
            int(segment_index),
            str(side),
        )

    @staticmethod
    def _distance(point, x, y):
        return math.hypot(point[0] - x, point[1] - y)

    def _curve_geometry(self):

        curve = BEaseGlobals.curve
        anchors = [
            self._data_to_region(anchor["x"], anchor["y"])
            for anchor in curve["anchors"]
        ]
        handles = {}
        for segment_index in range(len(curve["segments"])):
            controls = curve_model.absolute_segment(curve, segment_index)
            handles[(segment_index, "out")] = self._data_to_region(
                *controls[1]
            )
            handles[(segment_index, "in")] = self._data_to_region(
                *controls[2]
            )
        return curve, anchors, handles

    def target_region_position(self, target):

        if target is None:
            return None
        curve, anchors, handles = self._curve_geometry()
        if target[0] == self.TARGET_ANCHOR:
            index = target[1]
            if 0 <= index < len(anchors):
                return anchors[index]
            return None
        if target[0] == self.TARGET_HANDLE:
            segment_index, side = target[1], target[2]
            if 0 <= segment_index < len(curve["segments"]):
                return handles.get((segment_index, side))
        return None

    def hit_target(self, x, y, *, include_endpoints=False):

        if not self.hit(x, y):
            return None
        curve, anchors, handles = self._curve_geometry()
        radius = self.HIT_RADIUS * self.scale

        handle_candidates = []
        for (segment_index, side), point in handles.items():
            distance = self._distance(point, x, y)
            if distance <= radius:
                handle_candidates.append(
                    (distance, self._handle_target(segment_index, side))
                )
        if handle_candidates:
            return min(handle_candidates, key=lambda item: item[0])[1]

        middle_candidates = []
        for index in range(1, len(anchors) - 1):
            distance = self._distance(anchors[index], x, y)
            if distance <= radius:
                middle_candidates.append(
                    (distance, self._anchor_target(index))
                )
        if middle_candidates:
            return min(middle_candidates, key=lambda item: item[0])[1]

        if include_endpoints:
            endpoint_candidates = []
            for index in (0, len(anchors) - 1):
                distance = self._distance(anchors[index], x, y)
                if distance <= radius:
                    endpoint_candidates.append(
                        (distance, self._anchor_target(index))
                    )
            if endpoint_candidates:
                return min(endpoint_candidates, key=lambda item: item[0])[1]
        return None

    def hit_middle_anchor(self, x, y):

        if not self.hit(x, y):
            return None
        _curve, anchors, _handles = self._curve_geometry()
        radius = self.HIT_RADIUS * self.scale
        candidates = [
            (self._distance(anchors[index], x, y), index)
            for index in range(1, len(anchors) - 1)
        ]
        candidates = [item for item in candidates if item[0] <= radius]
        return min(candidates, default=(None, None), key=lambda item: item[0])[1]

    def _valid_selected_anchors(self):
        anchors = BEaseGlobals.curve["anchors"]
        valid = {
            index
            for index in self.selected_anchor_indices
            if (
                isinstance(index, int)
                and not isinstance(index, bool)
                and 0 <= index < len(anchors)
            )
        }
        if valid != self.selected_anchor_indices:
            self.selected_anchor_indices = valid
            self._notify_selection()
        return set(valid)

    def select_anchors(self, indices):
        anchors = BEaseGlobals.curve["anchors"]
        try:
            values = set(indices)
        except TypeError:
            values = set()
        normalized = {
            index
            for index in values
            if (
                isinstance(index, int)
                and not isinstance(index, bool)
                and 0 <= index < len(anchors)
            )
        }
        changed = normalized != self.selected_anchor_indices
        self.selected_anchor_indices = normalized
        if changed:
            self._notify_selection()
        return changed

    def select_anchor(self, index):
        return self.select_anchors(
            {index}
            if isinstance(index, int) and not isinstance(index, bool)
            else set()
        )

    def clear_selection(self):
        return self.select_anchors(set())

    def _set_curve(self, updated):
        if updated is None:
            return False
        before = curve_model.canonical_signature(BEaseGlobals.curve)
        after = curve_model.canonical_signature(updated)
        if after is None:
            return False
        if before == after:
            return False
        BEaseGlobals.curve = updated
        self._notify_points()
        return True

    def add_anchor_at(self, x, y):
        if not self.hit(x, y):
            return False
        x_value, y_value = self._region_to_data(x, y)
        if BEaseGlobals.snap_enabled:
            x_value = curve_model.snap(x_value)
            y_value = curve_model.snap(y_value)
        original = BEaseGlobals.curve
        updated = curve_model.add_anchor_at_point(
            original,
            x_value,
            y_value,
        )
        if (
            updated is None
            or len(updated["anchors"]) == len(original["anchors"])
        ):
            return False
        new_anchor_index = next(
            index
            for index, anchor in enumerate(updated["anchors"])
            if abs(anchor["x"] - x_value) <= 1e-9
        )
        BEaseGlobals.curve = updated
        self.select_anchor(new_anchor_index)
        self._notify_points()
        return True

    def double_click(self, x, y):

        if not self.hit(x, y):
            return False
        if self.hit_target(x, y, include_endpoints=True) is not None:
            return False
        return self.add_anchor_at(x, y)

    def delete_selected_anchor(self):
        last_index = len(BEaseGlobals.curve["anchors"]) - 1
        indices = sorted(
            (
                index
                for index in self._valid_selected_anchors()
                if 0 < index < last_index
            ),
            reverse=True,
        )
        if not indices:
            return False
        updated = BEaseGlobals.curve
        for index in indices:
            updated = curve_model.delete_anchor(updated, index)
            if updated is None:
                return False
        BEaseGlobals.curve = updated
        self.clear_selection()
        self.hover_target = None
        self._notify_points()
        return True

    delete_selected_anchors = delete_selected_anchor

    def set_selected_anchor_mode(self, mode):
        last_index = len(BEaseGlobals.curve["anchors"]) - 1
        indices = sorted(
            index
            for index in self._valid_selected_anchors()
            if 0 < index < last_index
        )
        if not indices:
            return False
        updated = BEaseGlobals.curve
        for index in indices:
            updated = curve_model.set_anchor_mode(updated, index, mode)
            if updated is None:
                return False
        return self._set_curve(updated)

    def set_anchor_mode(self, index, mode):
        updated = curve_model.set_anchor_mode(
            BEaseGlobals.curve,
            index,
            mode,
        )
        return self._set_curve(updated)

    @staticmethod
    def _handle_owner_anchor(target):

        segment_index, side = target[1], target[2]
        return segment_index if side == "out" else segment_index + 1

    def _handle_sync_selection(self, target):

        selected = self._valid_selected_anchors()
        if (
            len(selected) < 2
            or self._handle_owner_anchor(target) not in selected
        ):
            return None

        side = target[2]
        last_index = len(BEaseGlobals.curve["anchors"]) - 1
        applicable = {
            index
            for index in selected
            if (
                0 < index < last_index
                or (index == 0 and side == "out")
                or (index == last_index and side == "in")
            )
        }
        return sorted(applicable) if len(applicable) >= 2 else None

    def _move_active_target(self, x, y):
        target = self.active_target
        if target is None:
            return False
        x_value, y_value = self._region_to_data(x, y)
        curve = BEaseGlobals.curve
        if target[0] == self.TARGET_ANCHOR:
            index = target[1]
            if not 0 < index < len(curve["anchors"]) - 1:
                return False
            selected = self._valid_selected_anchors()
            if index not in selected:
                selected = {index}
                self.select_anchor(index)
            ordered_indices = [index] + sorted(
                selected - {index, 0, len(curve["anchors"]) - 1}
            )
            anchor = curve["anchors"][index]
            updated = curve_model.move_anchors(
                curve,
                ordered_indices,
                x_value - anchor["x"],
                y_value - anchor["y"],
                BEaseGlobals.snap_enabled,
            )
        else:
            segment_index, side = target[1], target[2]
            if not 0 <= segment_index < len(curve["segments"]):
                return False
            start = curve["anchors"][segment_index]
            end = curve["anchors"][segment_index + 1]
            local = curve_model.absolute_to_local(
                start,
                end,
                (x_value, y_value),
            )
            if self.handle_sync_indices:
                updated = curve_model.move_handles_sync(
                    curve,
                    segment_index,
                    side,
                    local[0],
                    local[1],
                    BEaseGlobals.snap_enabled,
                    self.handle_sync_indices,
                )
            else:
                updated = curve_model.move_handle(
                    curve,
                    segment_index,
                    side,
                    local[0],
                    local[1],
                    BEaseGlobals.snap_enabled,
                )
        return self._set_curve(updated)

    def mouse_press(self, x, y, button="LEFT", *, shift=False, ctrl=False):
        if not self.hit(x, y):
            return False
        if button == "LEFT":
            target = self.hit_target(x, y, include_endpoints=True)
            if target is None:
                self.active_target = None
                self.selected_handle = None
                self.dragging_handle = False
                self.dragging_anchor = False
                self.handle_sync_indices = None
                self.drag_start_pos = (float(x), float(y))
                self.drag_moved = False
                self.band_pending = True
                self.band_selecting = False
                self.band_current_pos = self.drag_start_pos

                self.band_mode = (
                    "ADD" if shift else ("SUB" if ctrl else "SET")
                )
                return True
            if (
                shift
                and target[0] == self.TARGET_ANCHOR
                and target[1] in self._valid_selected_anchors()
            ):


                self.select_anchors(
                    self._valid_selected_anchors() - {target[1]}
                )
                return False
            self.active_target = target
            self.drag_start_pos = (float(x), float(y))
            self.drag_moved = False
            self.band_pending = False
            self.band_selecting = False
            self.band_current_pos = None
            if target[0] == self.TARGET_HANDLE:
                self.selected_handle = target
                self.dragging_handle = True
                self.dragging_anchor = False


                self.handle_sync_indices = self._handle_sync_selection(
                    target
                )

                self._move_active_target(x, y)
            else:
                self.selected_handle = None
                self.dragging_handle = False
                self.dragging_anchor = True
                self.handle_sync_indices = None
                if target[1] not in self._valid_selected_anchors():
                    if shift:


                        self.select_anchors(
                            self._valid_selected_anchors() | {target[1]}
                        )
                    else:
                        self.select_anchor(target[1])
            return True
        if button == "MIDDLE":
            self.panning = True
            self.last_mouse_pos = (x, y)
            self._pan_raw = None
            return True
        return False

    def _apply_pan_delta(self, delta_x, delta_y):

        if self._pan_raw is None:
            self._pan_raw = (self.pan_x, self.pan_y)
        raw_x = self._pan_raw[0] + float(delta_x)
        raw_y = self._pan_raw[1] + float(delta_y)
        self._pan_raw = (raw_x, raw_y)
        if (
            self.zoom <= self.MIN_ZOOM
            and math.hypot(raw_x, raw_y)
            < self.PAN_SNAP_RADIUS * self.scale
        ):
            new_x, new_y = 0.0, 0.0
        else:
            new_x, new_y = raw_x, raw_y
        changed = new_x != self.pan_x or new_y != self.pan_y
        self.pan_x = new_x
        self.pan_y = new_y
        return changed

    def trackpad_pan(self, delta_x, delta_y):

        now = time.monotonic()
        if now - self._last_trackpad_time > self.TRACKPAD_SESSION_GAP:
            self._pan_raw = None
        self._last_trackpad_time = now
        return self._apply_pan_delta(delta_x, delta_y)

    def mouse_move(self, x, y):
        if self.band_pending and self.drag_start_pos is not None:
            clamped = self._clamp_to_rect(x, y)
            changed = clamped != self.band_current_pos
            self.band_current_pos = clamped
            movement = self._distance(self.drag_start_pos, x, y)
            if movement >= self.BAND_DRAG_THRESHOLD * self.scale:
                changed = not self.band_selecting or changed
                self.band_selecting = True
                self.drag_moved = True
            return changed
        if self.dragging:
            if self.drag_start_pos is not None:
                movement = self._distance(self.drag_start_pos, x, y)
                if movement > 0.5 * self.scale:
                    self.drag_moved = True
            return self._move_active_target(x, y)
        if self.panning and self.last_mouse_pos is not None:
            delta_x = x - self.last_mouse_pos[0]
            delta_y = y - self.last_mouse_pos[1]
            changed = self._apply_pan_delta(delta_x, delta_y)
            self.last_mouse_pos = (x, y)
            return changed
        return False

    def mouse_release(self, button="LEFT", x=None, y=None):
        if button == "LEFT":
            if self.band_pending:
                if x is not None and y is not None:
                    self.band_current_pos = self._clamp_to_rect(x, y)
                    if (
                        self.drag_start_pos is not None
                        and self._distance(self.drag_start_pos, x, y)
                        >= self.BAND_DRAG_THRESHOLD * self.scale
                    ):
                        self.band_selecting = True
                if self.band_selecting:


                    band = self._anchors_in_band()
                    current = self._valid_selected_anchors()
                    if self.band_mode == "ADD":
                        self.select_anchors(current | band)
                    elif self.band_mode == "SUB":
                        self.select_anchors(current - band)
                    else:
                        self.select_anchors(band)
                elif self.band_mode == "SET":

                    self.clear_selection()
                self.cancel_band_selection()
                return True
            was_dragging = self.dragging
            self.dragging_handle = False
            self.dragging_anchor = False
            self.handle_sync_indices = None
            self.active_target = None
            self.selected_handle = None
            self.drag_start_pos = None
            return was_dragging
        if button == "MIDDLE":
            was_panning = self.panning
            self.panning = False
            self.last_mouse_pos = None
            self._pan_raw = None
            return was_panning
        return False

    def cancel_band_selection(self):
        was_pending = self.band_pending
        self.band_pending = False
        self.band_selecting = False
        self.band_current_pos = None
        self.drag_start_pos = None
        self.drag_moved = False
        self.band_mode = "SET"
        return was_pending

    def _clamp_to_rect(self, x, y):
        return (
            min(max(float(x), self.rect.x), self.rect.x + self.rect.w),
            min(max(float(y), self.rect.y), self.rect.y + self.rect.h),
        )

    def band_rect(self):
        if (
            not self.band_selecting
            or self.drag_start_pos is None
            or self.band_current_pos is None
        ):
            return None
        start_x, start_y = self._clamp_to_rect(*self.drag_start_pos)
        current_x, current_y = self.band_current_pos
        left = min(start_x, current_x)
        bottom = min(start_y, current_y)
        return (
            left,
            bottom,
            abs(current_x - start_x),
            abs(current_y - start_y),
        )

    def _anchors_in_band(self):
        rectangle = self.band_rect()
        if rectangle is None:
            return set()
        left, bottom, width, height = rectangle
        right = left + width
        top = bottom + height
        _curve, anchors, _handles = self._curve_geometry()
        return {
            index
            for index in range(len(anchors))
            if (
                left <= anchors[index][0] <= right
                and bottom <= anchors[index][1] <= top
            )
        }

    def on_wheel(self, delta, x=None, y=None):

        notches = float(delta)
        if not math.isfinite(notches) or notches == 0.0:
            return False
        notches = min(
            self.MAX_WHEEL_NOTCHES,
            max(-self.MAX_WHEEL_NOTCHES, notches),
        )
        if notches > 0.0:
            new_zoom = self.zoom * (self.ZOOM_STEP ** notches)
        else:
            new_zoom = self.zoom / (self.ZOOM_STEP ** -notches)
        new_zoom = min(self.MAX_ZOOM, max(self.MIN_ZOOM, new_zoom))
        if new_zoom == self.zoom:
            return False
        if x is None or y is None:
            x = self.rect.x + self.rect.w * 0.5
            y = self.rect.y + self.rect.h * 0.5
        anchor_x, anchor_y = self._region_to_data(x, y)
        self.zoom = new_zoom
        moved_x, moved_y = self._data_to_region(anchor_x, anchor_y)
        self.pan_x += x - moved_x
        self.pan_y += y - moved_y

        self._pan_raw = None
        return True

    def reset_view(self):

        changed = (
            self.zoom != self.MIN_ZOOM
            or self.pan_x != 0.0
            or self.pan_y != 0.0
        )
        self.zoom = self.MIN_ZOOM
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._pan_raw = None
        return changed

    def select_all_anchors(self):

        anchors = BEaseGlobals.curve["anchors"]
        return self.select_anchors(range(len(anchors)))

    def toggle_selected_anchor_mode(self):

        last_index = len(BEaseGlobals.curve["anchors"]) - 1
        indices = sorted(
            index
            for index in self._valid_selected_anchors()
            if 0 < index < last_index
        )
        if not indices:
            return False
        anchors = BEaseGlobals.curve["anchors"]
        all_aligned = all(
            anchors[index].get("mode") == "ALIGNED" for index in indices
        )
        next_mode = "FREE" if all_aligned else "ALIGNED"
        return self.set_selected_anchor_mode(next_mode)

    def set_message(self, message, opacity=1.0):
        self.message = str(message)
        self.message_opacity = min(max(float(opacity), 0.0), 1.0)

    @staticmethod
    def _centered_square(point, diameter, color):
        draw.rect(
            point[0] - diameter * 0.5,
            point[1] - diameter * 0.5,
            diameter,
            diameter,
            color,
        )

    def _point_style(self, target, colors, selection_color):
        selected = (
            target[0] == self.TARGET_ANCHOR
            and target[1] in self._valid_selected_anchors()
        )
        dragging = target == self.active_target and self.dragging
        hovered = target == self.hover_target and not dragging
        factor = 1.0
        color = colors["handle_vertex"]
        if selected:
            factor = 1.2
            color = selection_color
        if hovered:
            factor = 1.4
            color = theme.brighten(color, 1.2)
        if dragging:
            color = selection_color
        return self.POINT_DIAMETER * self.scale * factor, color

    def draw(self):
        if not self.visible:
            return
        colors = theme.get_canvas_colors()
        selection_color = (
            self._palette["list_item_bg_sel"]
            if self._palette is not None
            else theme.build_palette()["list_item_bg_sel"]
        )
        draw.scissor_push(*self.rect.tuple())
        try:
            draw.rect(*self.rect.tuple(), colors["outer_bg"])
            grid_x, grid_y, size = self._grid_geometry()
            draw.rect(grid_x, grid_y, size, size, colors["bg"])
            draw.rounded_rect(
                grid_x,
                grid_y,
                size,
                size,
                0.0,
                None,
                colors["border"],
                2.0 * self.scale,
            )


            overlap_step = GRID_SUB_DIVISIONS // GRID_MAIN_DIVISIONS
            for index in range(1, GRID_SUB_DIVISIONS):
                if index % overlap_step == 0:
                    continue
                fraction = index / GRID_SUB_DIVISIONS
                gx = grid_x + size * fraction
                gy = grid_y + size * fraction
                draw.polyline(
                    ((gx, grid_y), (gx, grid_y + size)),
                    0.5 * self.scale,
                    colors["grid"],
                )
                draw.polyline(
                    ((grid_x, gy), (grid_x + size, gy)),
                    0.5 * self.scale,
                    colors["grid"],
                )
            for index in range(1, GRID_MAIN_DIVISIONS):
                fraction = index / GRID_MAIN_DIVISIONS
                gx = grid_x + size * fraction
                gy = grid_y + size * fraction
                draw.polyline(
                    ((gx, grid_y), (gx, grid_y + size)),
                    1.0 * self.scale,
                    colors["grid"],
                )
                draw.polyline(
                    ((grid_x, gy), (grid_x + size, gy)),
                    1.0 * self.scale,
                    colors["grid"],
                )

            curve, anchors, handles = self._curve_geometry()
            sampled = curve_model.evaluate(
                curve,
                self.CURVE_SAMPLE_BUDGET,
            )
            draw.polyline(
                tuple(self._data_to_region(*point) for point in sampled),
                3.0 * self.scale,
                colors["curve"],
            )

            for segment_index in range(len(curve["segments"])):
                draw.polyline(
                    (
                        anchors[segment_index],
                        handles[(segment_index, "out")],
                    ),
                    1.0 * self.scale,
                    colors["handle_align"],
                )
                draw.polyline(
                    (
                        anchors[segment_index + 1],
                        handles[(segment_index, "in")],
                    ),
                    1.0 * self.scale,
                    colors["handle_align"],
                )

            for (segment_index, side), point in handles.items():
                target = self._handle_target(segment_index, side)
                diameter, color = self._point_style(
                    target,
                    colors,
                    selection_color,
                )
                draw.circle(point[0], point[1], diameter, color)

            for index, point in enumerate(anchors):
                target = self._anchor_target(index)
                diameter, color = self._point_style(
                    target,
                    colors,
                    selection_color,
                )


                self._centered_square(point, diameter, color)

            band = self.band_rect()
            if band is not None:
                band_fill = (
                    selection_color[0],
                    selection_color[1],
                    selection_color[2],
                    0.15,
                )
                draw.rounded_rect(
                    *band,
                    0.0,
                    band_fill,
                    selection_color,
                    1.0 * self.scale,
                )

            if self.message:
                message_color = (
                    colors["curve"][0],
                    colors["curve"][1],
                    colors["curve"][2],
                    colors["curve"][3] * self.message_opacity,
                )

                draw.text(
                    self.rect.x + 5.0 * self.scale,
                    self.rect.y + 5.0 * self.scale,
                    self.message,
                    12.0 * self.scale,
                    message_color,
                )
        finally:
            draw.scissor_pop()


DrawingWidget = DrawingCanvas
