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

import time
import traceback

import bpy
from bpy.app.handlers import persistent
from bpy.types import Panel

from ..constants import (
    FADE_SPEED,
    FADE_UPDATE_INTERVAL,
    MESSAGE_DISPLAY_DURATION,
)
from ..core import curve as curve_model
from .. import preferences as addon_preferences
from ..properties import (
    BEaseGlobals,
    consume_settings_load_notice,
    consume_settings_reset_notice,
)
from . import draw
from .theme import build_palette, get_canvas_colors
from .widgets import ContextMenu
from .window import PanelWindow


_instances = {}
_draw_handler = None
_dopesheet_draw_handler = None
_message_timer_registered = False
_prune_timer_registered = False
_message_timer_error_count = 0
_prune_timer_error_count = 0
_registered_panels = []
_panel_serials = {}
_consumed_panel_serials = {}
_visible_draw_count = 0

_SIDEBAR_CATEGORY = "B-Ease"
_VIEW2D_CLIPPED = (12000, 12000)
_PANEL_CATEGORY_WIDTH = 20.0
_CONTENT_PADDING = 2.0
_MIN_REGION_SIZE = 2
_MIN_TIMER_INTERVAL = 0.001
_PRUNE_TIMER_INTERVAL = 0.25
_IDLE_TIMER_INTERVAL = 0.5
_TIMER_ERROR_BACKOFF_MAX = 5.0
_PANEL_SEPARATOR_ROUNDING_BIAS = 2999.0
_PANEL_SEPARATOR_HEIGHT_LIMIT = 3000.0
_PANEL_SEPARATOR_FACTOR_LIMIT = 500.0
_PANEL_SEPARATOR_PIXEL_HEIGHT = 6.0

HOST_GRAPH = "GRAPH"
HOST_DOPESHEET = "DOPESHEET"
HOST_TIMELINE = "TIMELINE"
SUPPORTED_HOST_ERROR = (
    "B-Ease requires a Graph Editor, Dope Sheet, or Timeline"
)


def _active_space(area, space=None):
    if space is not None:
        return space
    spaces = getattr(area, "spaces", None)
    return getattr(spaces, "active", None)


def host_from_area_space(area, space=None):

    area_type = getattr(area, "type", None)
    if area_type == "GRAPH_EDITOR":
        if getattr(_active_space(area, space), "mode", None) == "DRIVERS":
            return None
        return HOST_GRAPH
    if area_type != "DOPESHEET_EDITOR":
        return None
    active_space = _active_space(area, space)
    if getattr(active_space, "mode", None) == "TIMELINE":
        return HOST_TIMELINE
    return HOST_DOPESHEET


def host_is_enabled(area, space=None, context=None):

    host = host_from_area_space(area, space)
    if host == HOST_GRAPH:
        return True
    show_in_dope_sheet, show_in_timeline = (
        addon_preferences.get_sidebar_visibility(context)
    )
    if host == HOST_DOPESHEET:
        return show_in_dope_sheet
    if host == HOST_TIMELINE:
        return show_in_timeline
    return False


def _button_name(button):
    if isinstance(button, str):
        value = button.upper()
        aliases = {
            "LEFTMOUSE": "LEFT",
            "MIDDLEMOUSE": "MIDDLE",
            "RIGHTMOUSE": "RIGHT",
        }
        return aliases.get(value, value)
    return {1: "LEFT", 2: "MIDDLE", 3: "RIGHT"}.get(button, str(button))


def _reaction(
    consumed=False,
    start_modal=None,
    cursor=None,
    field_id=None,
):
    return {
        "consumed": bool(consumed),
        "start_modal": start_modal,
        "cursor": cursor,
        "field_id": field_id,
    }


def _ui_region(area, preferred=None):
    if preferred is not None and getattr(preferred, "type", None) == "UI":
        return preferred
    return next(
        (region for region in area.regions if region.type == "UI"),
        None,
    )


def _area_pointer(context):
    area = getattr(context, "area", None)
    return area.as_pointer() if area is not None else None


def _region_key(area, region):
    return int(area.as_pointer()), int(region.as_pointer())


def _region_is_open(area, region):
    space = getattr(area, "spaces", None)
    active = getattr(space, "active", None)
    return bool(
        active is not None
        and getattr(active, "show_region_ui", False)
        and region is not None
        and region.width >= _MIN_REGION_SIZE
        and region.height >= _MIN_REGION_SIZE
    )


def _view2d_mask_rect(region):

    width = max(0, int(region.width))
    height = max(0, int(region.height))
    if width == 0 or height == 0:
        return 0.0, 0.0, 0.0, 0.0

    view2d = region.view2d
    center_x = width // 2
    center_y = height // 2

    def visible(x, y):
        view_x, view_y = view2d.region_to_view(float(x), float(y))
        return tuple(
            view2d.view_to_region(view_x, view_y, clip=True)
        ) != _VIEW2D_CLIPPED

    try:
        xmin = next(x for x in range(width) if visible(x, center_y))
        xmax = next(
            x for x in range(width - 1, xmin - 1, -1)
            if visible(x, center_y)
        )
        ymin = next(y for y in range(height) if visible(center_x, y))
        ymax = next(
            y for y in range(height - 1, ymin - 1, -1)
            if visible(center_x, y)
        )
    except StopIteration:

        return 0.0, 0.0, float(width), float(height)
    return (
        float(xmin),
        float(ymin),
        float(xmax - xmin + 1),
        float(ymax - ymin + 1),
    )


def _category_tabs_on_left(area, region, scale=None):

    if scale is None:
        scale = float(bpy.context.preferences.system.ui_scale)
    try:
        _view_x, anchor_view_y = region.view2d.region_to_view(
            float(region.width) * 0.5,
            float(region.height) * 0.5,
        )
        origin_x, _origin_y = region.view2d.view_to_region(
            0.0,
            float(anchor_view_y),
            clip=False,
        )
        threshold = max(1.0, _PANEL_CATEGORY_WIDTH * scale * 0.5)
        return float(origin_x) > threshold
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass

    region_center = float(region.x) + float(region.width) * 0.5
    area_center = float(area.x) + float(area.width) * 0.5
    return region_center < area_center


def _content_rect(area, region, scale):

    mask_x, mask_y, mask_w, mask_h = _view2d_mask_rect(region)
    padding = max(1.0, round(_CONTENT_PADDING * scale))
    category_width = max(1.0, round(_PANEL_CATEGORY_WIDTH * scale))
    left = mask_x + padding
    right = mask_x + mask_w - padding
    if _category_tabs_on_left(area, region, scale):
        left += category_width
    else:
        right -= category_width
    return (
        left,
        mask_y,
        max(0.0, right - left),
        mask_h,
    )


def _host_edge_rect(region, scale, area=None):

    width = max(0.0, float(region.width))
    height = max(0.0, float(region.height))
    if width <= 0.0 or height <= 0.0:
        return (0.0, 0.0, 0.0, 0.0)


    line_w = min(width, max(1.0, float(round(scale))))
    alignment = getattr(region, "alignment", None)
    if alignment == "RIGHT":
        x = 0.0
    elif alignment == "LEFT":
        x = width - line_w
    elif area is not None:

        region_center = float(region.x) + width * 0.5
        area_center = float(area.x) + float(area.width) * 0.5
        x = width - line_w if region_center < area_center else 0.0
    else:
        x = 0.0
    return (x, 0.0, line_w, height)


class SidebarInstance:


    def __init__(
        self,
        area_ptr,
        area,
        region,
        panel,
        *,
        window=None,
        dirty_callback=None,
    ):
        self.area_ptr = int(area_ptr)
        self.area = area
        self.region = region
        self.region_ptr = region.as_pointer() if region is not None else None
        self.host_window = window
        self.window = panel
        self.dirty_callback = dirty_callback
        self.hover_target = None
        self.active_drag = None
        self.text_focus = None
        self.text_focus_curve_version = None
        self.message = ""
        self.message_opacity = 0.0
        self.message_started_at = None
        self.message_deadline = None
        self.message_fading = False
        self.last_draw_error = None
        self.tab_visible = False
        self.host_clip_rect = (0.0, 0.0, 0.0, 0.0)
        self.last_draw_geometry = None
        self.window.canvas.on_points_changed = self._on_canvas_points_changed
        self.window.canvas.on_selection_changed = (
            self._on_canvas_selection_changed
        )
        self.window.reverse_button.on_click = self._reverse_curve
        self.window.list_view.on_item_clicked = self._on_preset_clicked

    @property
    def menu(self):
        return self.window.context_menu

    @property
    def dialog(self):
        return self.window.dialog

    def set_callbacks(self, actions=None, dirty_callback=None):
        if dirty_callback is not None:
            self.dirty_callback = dirty_callback
        if actions is None:
            self.window.set_callbacks(None, dirty_callback)
            return
        callbacks = dict(actions or {})
        self.window.set_callbacks(callbacks, dirty_callback)

    def _refresh_region(self, context=None, *, detect_scale=False):
        area = getattr(context, "area", None) if context is not None else None
        if area is not None and area.as_pointer() == self.area_ptr:
            self.area = area
            region = _ui_region(area, getattr(context, "region", None))
            if region is not None:
                self.region = region
                self.region_ptr = region.as_pointer()
        if self.region is not None:
            self.window.set_region_size(self.region.width, self.region.height)
        self.window.sync_preset_model()
        if detect_scale:
            preferences = (
                getattr(context, "preferences", None)
                if context is not None
                else None
            )
            if preferences is None:
                preferences = getattr(bpy.context, "preferences", None)
            system = getattr(preferences, "system", None)
            if system is not None:
                scale = float(system.ui_scale)
                self.window.set_scale(scale)
        self._sync_host_geometry()

    def _sync_host_geometry(self):
        region = self.region
        if region is None:
            return False
        scale = self.window.scale
        clip_x, clip_y, clip_w, clip_h = _content_rect(
            self.area,
            region,
            scale,
        )
        self.host_clip_rect = (clip_x, clip_y, clip_w, clip_h)
        geometry = (clip_x, clip_y, clip_w, clip_h)
        changed = self.window.rect.tuple() != geometry
        if changed:
            self.window.set_geometry(*geometry)
        if (
            self.text_focus is not None
            and not self.text_focus.visible
        ):
            self._blur_text()
        self.last_draw_geometry = geometry
        return True

    def content_hit(self, x, y):
        clip_x, clip_y, clip_w, clip_h = self.host_clip_rect
        return (
            clip_w > 0.0
            and clip_h > 0.0
            and clip_x <= x <= clip_x + clip_w
            and clip_y <= y <= clip_y + clip_h
            and self.window.rect.contains(x, y)
        )

    def tag_redraw(self):
        try:
            self.area.tag_redraw()
        except (ReferenceError, AttributeError):
            pass

    def _on_canvas_points_changed(self):
        self.window._on_canvas_points_changed()

    def _on_canvas_selection_changed(self):

        self._blur_text()
        self.window.update_curve_texts()
        self.tag_redraw()

    def _reverse_curve(self, _button=None):
        updated = curve_model.reverse(BEaseGlobals.curve)
        if updated is None:
            return False
        BEaseGlobals.curve = curve_model.deep_copy(updated)
        reset_canvas_selections()
        refresh_points()
        self.window._dirty("curve")
        self.window._action("reverse")
        self.tag_redraw()
        return True

    def _on_preset_clicked(self, index):
        self.window.sync_preset_model()
        if not 0 <= index < len(self.window.list_view.items):
            return False
        selected = self.window.list_view.items[index].get("curve")
        if curve_model.validate(selected) is None:
            return False
        BEaseGlobals.curve = curve_model.deep_copy(selected)
        reset_canvas_selections()
        refresh_points()
        self.window._dirty("curve")
        self.window._action("preset_clicked")
        self.tag_redraw()
        return True

    def delete_selected_anchor(self):
        if self.text_focus is not None:
            return False
        had_selection = bool(
            self.window.canvas._valid_selected_anchors()
        )
        if not self.window.canvas.delete_selected_anchor():

            return had_selection
        reset_canvas_selections()
        self.tag_redraw()
        return True

    def canvas_key(self, event_type, x, y, alt=False):

        if self.text_focus is not None:
            return False
        x = float(x)
        y = float(y)
        if not self.content_hit(x, y) or not self.window.canvas.hit(x, y):
            return False
        canvas = self.window.canvas
        event_type = str(event_type).upper()
        if event_type == "A":
            changed = (
                canvas.clear_selection()
                if alt
                else canvas.select_all_anchors()
            )
        elif event_type == "V":
            changed = canvas.toggle_selected_anchor_mode()
        elif event_type == "HOME":
            changed = canvas.reset_view()
            if changed:
                canvas.set_hover(x, y)
        else:
            return False
        if changed:
            self.tag_redraw()
        return True

    def _open_canvas_context_menu(self, x, y):
        canvas = self.window.canvas
        anchor_index = canvas.hit_middle_anchor(x, y)
        if anchor_index is not None:
            selected_indices = canvas._valid_selected_anchors()
            if anchor_index not in selected_indices:
                canvas.select_anchor(anchor_index)
                selected_indices = {anchor_index}
            selected_indices = tuple(sorted(selected_indices))

            def selected(menu_index):
                self.window.context_menu = None
                canvas.select_anchors(selected_indices)
                if menu_index == 0:
                    if canvas.delete_selected_anchor():
                        reset_canvas_selections()
                elif menu_index == 1:
                    canvas.set_selected_anchor_mode("FREE")
                elif menu_index == 2:
                    canvas.set_selected_anchor_mode("ALIGNED")
                self.tag_redraw()

            last_index = len(BEaseGlobals.curve["anchors"]) - 1
            actionable_count = sum(
                1
                for index in selected_indices
                if 0 < index < last_index
            )
            delete_label = (
                f"Delete {actionable_count} Points"
                if actionable_count > 1
                else "Delete Point"
            )
            items = [delete_label, "Free", "Aligned"]
            item_icons = [None, "HANDLE_FREE", "HANDLE_ALIGNED"]
        else:

            if canvas.hit_target(x, y, include_endpoints=True) is not None:
                return False

            def selected(menu_index):
                self.window.context_menu = None
                if menu_index == 0 and canvas.add_anchor_at(x, y):
                    reset_canvas_selections(except_area_ptr=self.area_ptr)
                self.tag_redraw()

            items = ["Add Point"]
            item_icons = None

        self.window.context_menu = ContextMenu(
            items,
            x,
            y,
            self.window.region_size,
            palette=self.window.palette,
            scale=self.window.scale,
            icons=item_icons,
            on_select=selected,
        )
        self.tag_redraw()
        return True

    def draw(self, context):
        area = getattr(context, "area", None)
        region = getattr(context, "region", None)
        if area is not None and not host_is_enabled(
            area,
            getattr(context, "space_data", None),
            context,
        ):
            return False
        if area is not None and area.as_pointer() != self.area_ptr:
            return False
        if region is None or region.type != "UI":
            return False
        if area is None and region.as_pointer() != self.region_ptr:
            return False
        self._refresh_region(context, detect_scale=True)
        if not self.tab_visible or not self._sync_host_geometry():
            return False
        new_palette = build_palette()
        new_canvas_colors = get_canvas_colors()
        if (
            new_palette != self.window.palette
            or new_canvas_colors != self.window._thumbnail_colors
        ):
            self.window.apply_palette(new_palette, new_canvas_colors)
        self.window.canvas.set_message(self.message, self.message_opacity)
        edge_x, edge_y, edge_w, edge_h = _host_edge_rect(
            self.region,
            self.window.scale,
            self.area,
        )
        if edge_w > 0.0 and edge_h > 0.0:

            draw.rect(
                edge_x,
                edge_y,
                edge_w,
                edge_h,
                self.window.palette["host_edge"],
            )
        draw.scissor_push(*self.host_clip_rect)
        try:
            thumbnails_deferred = self.window.draw()
        finally:
            draw.scissor_pop()
        if thumbnails_deferred:
            self.tag_redraw()
        return True

    def _blur_text(self, except_field=None):
        changed = False
        for field in self.window.text_fields:
            if field is except_field:
                continue
            changed = field.blur() or changed
        if except_field is None:
            self.text_focus = None
            self.text_focus_curve_version = None
        return changed

    def _focus_text(self, field):
        if not field.visible or not field.enabled:
            return False
        self._blur_text(except_field=field)
        self.text_focus = field
        self.text_focus_curve_version = BEaseGlobals.curve_version
        field.focus()
        return True

    def cancel_stale_curve_interaction(self):

        active = self.active_drag
        active_is_stale = (
            active is not None
            and active.get("kind") in {"canvas_handle", "canvas_band"}
            and active.get("curve_version") != BEaseGlobals.curve_version
        )
        text_is_stale = (
            self.text_focus is not None
            and self.text_focus_curve_version != BEaseGlobals.curve_version
        )
        if not active_is_stale and not text_is_stale:
            return False
        if active is not None:
            self.cancel_active_drag(restore_handle=False)
        if self.text_focus is not None:
            self._blur_text()
        self.tag_redraw()
        return True

    def focus_text(self, field_name):

        field = self._text_field_mapping().get(str(field_name))
        if field is None or not self._focus_text(field):
            return False
        self.tag_redraw()
        return True

    def _text_field_mapping(self):
        return {
            "points": self.window.handle_input,
            "point_input": self.window.handle_input,
            "handles": self.window.handle_input,
            "handle_input": self.window.handle_input,
            "anchors": self.window.anchor_input,
            "anchor_input": self.window.anchor_input,
            "preset": self.window.preset_name,
            "preset_name": self.window.preset_name,
        }

    def field_id(self, field=None):
        field = self.text_focus if field is None else field
        if field is self.window.handle_input:
            return "point_input"
        if field is self.window.anchor_input:
            return "anchor_input"
        if field is self.window.preset_name:
            return "preset_name"
        return None

    def text_value(self, field_name):
        field = self._text_field_mapping().get(str(field_name))
        return None if field is None else field.text

    def set_text_value(self, field_name, value):
        field = self._text_field_mapping().get(str(field_name))
        if field is None or not self._focus_text(field):
            return False
        changed = field.set_text(str(value))
        self.text_focus_curve_version = BEaseGlobals.curve_version
        field.caret = len(field.text)
        field.anchor = field.caret
        field._ensure_caret_visible()
        if field is self.window.preset_name:
            self._blur_text()
        self.tag_redraw()
        return changed

    def blur_text(self):
        changed = self._blur_text()
        if changed:
            self.tag_redraw()
        return changed

    def _popup_press(self, x, y, button):
        dialog = self.window.dialog
        if dialog is not None:
            if button == "LEFT":
                target = dialog.mouse_press(x, y)
                self.active_drag = {
                    "kind": "dialog",
                    "target": dialog,
                    "button": button,
                }
            return _reaction(True, "drag" if button == "LEFT" else None)

        menu = self.window.context_menu
        if menu is not None:
            if button == "LEFT" and menu.hit(x, y):
                menu.mouse_press(x, y)
                self.active_drag = {
                    "kind": "menu",
                    "target": menu,
                    "button": button,
                }
                return _reaction(True, "drag")
            self.window.context_menu = None
            self.tag_redraw()
            return _reaction(True)
        return None

    def on_mouse_press(
        self,
        x,
        y,
        button,
        context,
        *,
        double_click=False,
        shift=False,
        ctrl=False,
    ):
        self._refresh_region(context)
        x = float(x)
        y = float(y)
        button = _button_name(button)

        popup_reaction = self._popup_press(x, y, button)
        if popup_reaction is not None:
            return popup_reaction

        if not self.content_hit(x, y):
            if self._blur_text():
                self.tag_redraw()
            return _reaction(False)

        if button == "RIGHT":
            blurred = self._blur_text()
            if self.window.canvas.hit(x, y):
                opened = self._open_canvas_context_menu(x, y)
                if blurred or opened:
                    self.tag_redraw()
                return _reaction(
                    True,
                    "popup" if opened else None,
                )
            consumed = self.window.list_view.mouse_press(x, y, "RIGHT")
            if consumed or blurred:
                self.tag_redraw()
            return _reaction(
                True,
                "popup" if self.window.context_menu is not None else None,
            )

        if button == "MIDDLE":
            blurred = self._blur_text()
            if self.window.canvas.mouse_press(x, y, "MIDDLE"):
                self.active_drag = {
                    "kind": "canvas_pan",
                    "target": self.window.canvas,
                    "button": button,
                }
                self.tag_redraw()
                return _reaction(True, "drag", "SCROLL_XY")
            if blurred:
                self.tag_redraw()
            return _reaction(False)

        if button != "LEFT":
            return _reaction(True)

        for field in self.window.text_fields:
            if field.hit(x, y):
                if field is self.window.preset_name:

                    self.active_drag = None
                    self._blur_text()
                    self.tag_redraw()
                    return _reaction(
                        True,
                        "ime",
                        "TEXT",
                        self.field_id(field),
                    )
                self._focus_text(field)
                if double_click:


                    field.mouse_release()
                    field.select_all()
                    self.active_drag = None
                    self.tag_redraw()
                    return _reaction(True, "text", "TEXT")
                field.mouse_press(x, y)
                self.active_drag = {
                    "kind": "text_select",
                    "target": field,
                    "button": button,
                }
                self.tag_redraw()
                return _reaction(True, "text", "TEXT")

        self._blur_text()
        if self.window.canvas.hit(x, y) and double_click:
            added = self.window.canvas.double_click(x, y)
            if added:
                reset_canvas_selections(except_area_ptr=self.area_ptr)
            self.tag_redraw()
            return _reaction(True)

        start_curve = curve_model.deep_copy(BEaseGlobals.curve)

        if self.window.canvas.mouse_press(
            x, y, "LEFT", shift=shift, ctrl=ctrl
        ):
            drag_kind = (
                "canvas_band"
                if self.window.canvas.band_interaction
                else "canvas_handle"
            )
            self.active_drag = {
                "kind": drag_kind,
                "target": self.window.canvas,
                "button": button,
                "start_curve": start_curve,
                "curve_version": BEaseGlobals.curve_version,
            }
            self.tag_redraw()

            return _reaction(True, "drag", None)
        if self.window.canvas.hit(x, y):

            self.tag_redraw()
            return _reaction(True)

        if self.window.splitter.mouse_press(x, y):
            self.active_drag = {
                "kind": "splitter",
                "target": self.window.splitter,
                "button": button,
            }
            return _reaction(True, "drag", "MOVE_X")

        if self.window.slider.mouse_press(x, y):
            self.active_drag = {
                "kind": "slider",
                "target": self.window.slider,
                "button": button,
            }
            self.tag_redraw()
            return _reaction(True, "drag", "MOVE_X")

        if self.window.list_view.scrollbar_mouse_press(x, y):
            self.active_drag = {
                "kind": "list_scrollbar",
                "target": self.window.list_view,
                "button": button,
            }
            self.tag_redraw()
            return _reaction(True, "drag")

        if self.window.list_view.mouse_press(x, y, "LEFT"):
            self.active_drag = {
                "kind": "list",
                "target": self.window.list_view,
                "button": button,
            }
            self.tag_redraw()
            return _reaction(True, "drag")

        for target in (
            self.window.copy_button,
            self.window.reverse_button,
            self.window.snap_button,
            self.window.apply_button,
            self.window.save_button,
            self.window.view_button,
        ):
            if target.mouse_press(x, y):
                self.active_drag = {
                    "kind": "button",
                    "target": target,
                    "button": button,
                }
                self.tag_redraw()
                return _reaction(True, "drag")

        return _reaction(True)

    def _hover_cursor(self, x, y):
        if self.window.splitter.hit(x, y):
            return "MOVE_X"
        if any(field.hit(x, y) for field in self.window.text_fields):
            return "TEXT"
        return None

    def on_mouse_move(self, x, y, context=None):
        self._refresh_region(context)
        if self.cancel_stale_curve_interaction():
            return _reaction(True)
        x = float(x)
        y = float(y)
        active = self.active_drag
        cursor = None
        changed = False
        if active is not None:
            kind = active["kind"]
            if kind in {"canvas_handle", "canvas_band", "canvas_pan"}:
                changed = active["target"].mouse_move(x, y)
                if kind == "canvas_handle":
                    active["curve_version"] = BEaseGlobals.curve_version
                cursor = "SCROLL_XY" if kind == "canvas_pan" else None
            elif kind == "list_scrollbar":
                changed = active["target"].scrollbar_mouse_move(x, y)
            elif kind in {"splitter", "slider", "list", "text_select"}:
                changed = active["target"].mouse_move(x, y)
                if kind in {"splitter", "slider"}:
                    cursor = "MOVE_X"
                elif kind == "text_select":
                    cursor = "TEXT"
            elif kind == "button":
                changed = active["target"].set_hover(x, y)
            elif kind == "menu":
                changed = active["target"].set_hover(x, y)
            elif kind == "dialog":
                changed = active["target"].set_hover(x, y)
        else:
            changed = self.window.update_hover(x, y)
            cursor = self._hover_cursor(x, y)

        if changed:
            self.tag_redraw()
        return _reaction(
            active is not None
            or self.window.dialog is not None
            or self.window.context_menu is not None
            or self.content_hit(x, y),
            None,
            cursor,
        )

    def on_mouse_release(self, x, y, button, context=None):
        self._refresh_region(context)
        if self.cancel_stale_curve_interaction():
            return _reaction(True)
        x = float(x)
        y = float(y)
        button = _button_name(button)
        active = self.active_drag
        if active is None:
            return _reaction(self.content_hit(x, y))
        if active.get("button") != button:
            return _reaction(True)

        kind = active["kind"]
        target = active.get("target")
        rename_record = None
        if kind == "canvas_handle":
            target.mouse_release("LEFT", x, y)
        elif kind == "canvas_band":
            target.mouse_release("LEFT", x, y)
        elif kind == "canvas_pan":
            target.mouse_release("MIDDLE")
        elif kind in {"splitter", "slider"}:
            target.mouse_release()
            if kind == "splitter":
                self.window._update_global_splitter()
                self.window._dirty("splitter_released")
        elif kind == "list_scrollbar":
            target.scrollbar_mouse_release()
        elif kind == "list":
            target.mouse_release(x, y)
        elif kind == "text_select":
            target.mouse_release()
        elif kind == "button":
            target.mouse_release(x, y)
        elif kind == "menu":
            target.mouse_release(x, y)
            rename_record = self.window._pending_rename_record
            self.window._pending_rename_record = None
        elif kind == "dialog":
            target.mouse_release(x, y)
        self.active_drag = None
        self.window.update_hover(x, y)
        self.tag_redraw()
        if rename_record is not None:
            return _reaction(True, "rename", None, rename_record)
        return _reaction(True, None, self._hover_cursor(x, y))

    def cancel_active_drag(self, restore_handle=False):

        active = self.active_drag
        if active is None:
            return False
        kind = active["kind"]
        target = active.get("target")
        if kind == "canvas_handle":
            target.mouse_release("LEFT")
            if restore_handle:
                restore_handle = (
                    active.get("curve_version")
                    == BEaseGlobals.curve_version
                )
            if restore_handle:
                BEaseGlobals.curve = curve_model.deep_copy(
                    active["start_curve"]
                )
                refresh_points()
        elif kind == "canvas_band":
            target.cancel_band_selection()
        elif kind == "canvas_pan":
            target.mouse_release("MIDDLE")
        elif kind in {"splitter", "slider"}:
            target.mouse_release()
        elif kind == "list_scrollbar":
            target.scrollbar_mouse_release()
        elif kind == "list":
            target.cancel_interaction()
        elif kind == "text_select":
            target.mouse_release()
        elif kind == "button":
            target.cancel_press()
        elif kind in {"menu", "dialog"}:
            target.pressed_index = None
        self.active_drag = None
        self.tag_redraw()
        return True

    def on_wheel(self, delta, context=None, x=None, y=None, route=None):

        self._refresh_region(context)
        if self.window.dialog is not None or self.window.context_menu is not None:
            return _reaction(True)
        if route == "list":
            changed = self.window.list_view.on_wheel(delta)
            if changed:
                self.tag_redraw()
            return _reaction(True)
        if x is not None and y is not None:
            x = float(x)
            y = float(y)
            if not self.content_hit(x, y):
                return _reaction(route == "list_only")
            canvas_hit = self.window.canvas.hit(x, y)
            list_hit = self.window.list_view.hit(x, y)
        else:
            canvas_hit = self.window.canvas.hovered
            list_hit = self.window.list_view.hovered
        if route == "list_only":
            if list_hit:
                changed = self.window.list_view.on_wheel(delta)
                if changed:
                    self.tag_redraw()
            return _reaction(True)
        if canvas_hit:

            changed = self.window.canvas.on_wheel(delta, x, y)
            if changed:

                if x is not None and y is not None:
                    self.window.canvas.set_hover(x, y)
                self.tag_redraw()
            return _reaction(True)
        if not list_hit:
            return _reaction(False)
        changed = self.window.list_view.on_wheel(delta)
        if changed:
            self.tag_redraw()
        return _reaction(True)

    def on_trackpad_pan(self, delta_x, delta_y, context=None, x=None, y=None):

        self._refresh_region(context)
        if self.window.dialog is not None or self.window.context_menu is not None:
            return _reaction(True)
        if x is None or y is None:
            return _reaction(False)
        x = float(x)
        y = float(y)
        if not self.content_hit(x, y) or not self.window.canvas.hit(x, y):
            return _reaction(False)
        delta_x = float(delta_x)
        delta_y = float(delta_y)
        if delta_x != 0.0 or delta_y != 0.0:
            canvas = self.window.canvas

            changed = canvas.trackpad_pan(delta_x, delta_y)
            if changed:

                canvas.set_hover(x, y)
                self.tag_redraw()
        return _reaction(True)

    def popup_key(self, event_type):

        if self.window.dialog is None and self.window.context_menu is None:
            return False
        if str(event_type).upper() == "ESC":
            self.cancel_active_drag()
            if self.window.dialog is not None:
                self.window.dialog._result(False)
            else:
                self.window.context_menu = None
            self.tag_redraw()
        return True

    def insert_text(self, string):
        if self.text_focus is None:
            return False
        handled = self.text_focus.insert_text(string)
        if handled:
            self.text_focus_curve_version = BEaseGlobals.curve_version
            self.tag_redraw()
        return handled

    def key(self, event_type, shift=False, ctrl_or_cmd=False):
        field = self.text_focus
        if field is None:
            return False
        event_type = str(event_type).upper()
        handled = False
        if ctrl_or_cmd and event_type == "A":
            handled = field.select_all()
        elif ctrl_or_cmd and event_type == "C":
            if field.has_selection():
                bpy.context.window_manager.clipboard = field.selected_text()
            handled = True
        elif ctrl_or_cmd and event_type == "X":
            if field.has_selection():
                bpy.context.window_manager.clipboard = field.selected_text()
                field.delete()
            handled = True
        elif ctrl_or_cmd and event_type == "V":
            handled = field.insert_text(bpy.context.window_manager.clipboard)
        elif event_type in {"BACK_SPACE", "BACKSPACE"}:
            handled = field.backspace()
        elif event_type in {"DEL", "DELETE"}:
            handled = field.delete()
        elif event_type in {"LEFT_ARROW", "LEFT"}:
            handled = field.left(select=shift)
        elif event_type in {"RIGHT_ARROW", "RIGHT"}:
            handled = field.right(select=shift)
        elif event_type == "HOME":
            handled = field.home(select=shift)
        elif event_type == "END":
            handled = field.end(select=shift)
        if handled:
            self.text_focus_curve_version = BEaseGlobals.curve_version
            self.tag_redraw()
        return handled

    def show_message(self, text):
        self.message = str(text)
        self.message_opacity = 1.0
        self.message_started_at = time.monotonic()
        self.message_deadline = (
            self.message_started_at + MESSAGE_DISPLAY_DURATION / 1000.0
        )


        self.window.canvas.set_message(self.message, self.message_opacity)
        self.tag_redraw()
        _ensure_message_timer()

    def tick(self, now=None):
        if not self.message or self.message_deadline is None:
            return False
        now = time.monotonic() if now is None else float(now)
        old_opacity = self.message_opacity
        old_message = self.message
        if self.message_fading:
            self.message_opacity -= FADE_SPEED
            if self.message_opacity <= 0.0:
                self.message_opacity = 0.0
                self.message = ""
                self.message_started_at = None
                self.message_deadline = None
                self.message_fading = False
        elif now >= self.message_deadline:
            self.message_fading = True
        self.window.canvas.set_message(self.message, self.message_opacity)
        changed = (
            self.message != old_message
            or abs(self.message_opacity - old_opacity) > 1e-9
        )
        if changed:
            self.tag_redraw()
        return changed

    def next_message_delay(self, now=None):
        if not self.message or self.message_deadline is None:
            return None
        if self.message_fading:
            return FADE_UPDATE_INTERVAL / 1000.0
        now = time.monotonic() if now is None else float(now)
        return max(0.0, self.message_deadline - now)


def _timer_is_registered():
    try:
        return bpy.app.timers.is_registered(_message_timer_callback)
    except Exception:
        return _message_timer_registered


def _message_timer_callback():
    global _message_timer_error_count, _message_timer_registered
    try:
        prune_invalid_instances()
        now = time.monotonic()
        delays = []
        for instance in tuple(_instances.values()):
            instance.tick(now)
            delay = instance.next_message_delay(now)
            if delay is not None:
                delays.append(delay)
        _message_timer_error_count = 0
        if not delays:
            _message_timer_registered = False
            return None
        return max(_MIN_TIMER_INTERVAL, min(delays))
    except Exception:
        traceback.print_exc()
        _message_timer_error_count += 1
        return min(
            _TIMER_ERROR_BACKOFF_MAX,
            _IDLE_TIMER_INTERVAL
            * (2 ** min(_message_timer_error_count - 1, 8)),
        )


def _ensure_message_timer():
    global _message_timer_registered
    if _message_timer_registered and _timer_is_registered():
        return
    now = time.monotonic()
    delays = [
        delay
        for instance in _instances.values()
        for delay in (instance.next_message_delay(now),)
        if delay is not None
    ]
    if not delays:
        _message_timer_registered = False
        return
    bpy.app.timers.register(
        _message_timer_callback,
        first_interval=max(_MIN_TIMER_INTERVAL, min(delays)),
    )
    _message_timer_registered = True


def _stop_message_timer_if_idle(force=False):
    global _message_timer_registered
    if not force:
        return
    if _timer_is_registered():
        bpy.app.timers.unregister(_message_timer_callback)
        _message_timer_registered = False
    else:
        _message_timer_registered = False


def _live_areas():
    live = {}
    window_manager = getattr(bpy.context, "window_manager", None)
    for host_window in tuple(getattr(window_manager, "windows", ()) or ()):
        screen = getattr(host_window, "screen", None)
        for area in tuple(getattr(screen, "areas", ()) or ()):
            try:
                live[int(area.as_pointer())] = (host_window, area)
            except (ReferenceError, AttributeError):
                continue
    return live


def tag_redraw_all_areas():

    tagged = 0
    seen = set()
    for screen in tuple(getattr(bpy.data, "screens", ()) or ()):
        for area in tuple(getattr(screen, "areas", ()) or ()):
            try:
                pointer = int(area.as_pointer())
                if pointer in seen:
                    continue
                seen.add(pointer)
                area.tag_redraw()
                tagged += 1
            except (AttributeError, ReferenceError, RuntimeError):
                continue
    return tagged


def on_sidebar_preferences_changed():

    removed = prune_invalid_instances()
    tag_redraw_all_areas()
    return removed


def prune_invalid_instances():

    live = _live_areas()
    removed = []
    for area_ptr, instance in tuple(_instances.items()):
        entry = live.get(area_ptr)
        if entry is None:
            if close_sidebar(None, area_ptr):
                removed.append(area_ptr)
            continue
        host_window, area = entry
        if not host_is_enabled(area, _active_space(area)):
            if close_sidebar(None, area_ptr):
                removed.append(area_ptr)
            continue
        region = _ui_region(area)
        if region is None or not _region_is_open(area, region):
            if close_sidebar(None, area_ptr):
                removed.append(area_ptr)
            continue
        instance.host_window = host_window
        instance.area = area
        instance.region = region
        instance.region_ptr = region.as_pointer()
    return removed


def _prune_timer_callback():
    global _prune_timer_error_count, _prune_timer_registered
    try:
        prune_invalid_instances()
        _prune_timer_error_count = 0
        if not _instances:
            _prune_timer_registered = False
            return None
        return _PRUNE_TIMER_INTERVAL
    except Exception:
        traceback.print_exc()
        _prune_timer_error_count += 1
        return min(
            _TIMER_ERROR_BACKOFF_MAX,
            _PRUNE_TIMER_INTERVAL
            * (2 ** min(_prune_timer_error_count - 1, 8)),
        )


def _ensure_prune_timer():
    global _prune_timer_registered
    if _prune_timer_registered:
        try:
            if bpy.app.timers.is_registered(_prune_timer_callback):
                return
        except Exception:
            pass
    bpy.app.timers.register(
        _prune_timer_callback,
        first_interval=_PRUNE_TIMER_INTERVAL,
    )
    _prune_timer_registered = True


def _stop_prune_timer():
    global _prune_timer_registered
    if bpy.app.timers.is_registered(_prune_timer_callback):
        bpy.app.timers.unregister(_prune_timer_callback)
        _prune_timer_registered = False
    else:
        _prune_timer_registered = False


@persistent
def _load_pre_close_sidebars(_unused):
    close_all(None)


class BEASE2_PT_sidebar(Panel):
    bl_idname = "BEASE2_PT_sidebar"
    bl_label = "B-Ease Studio"
    bl_space_type = "GRAPH_EDITOR"
    bl_region_type = "UI"
    bl_category = _SIDEBAR_CATEGORY
    bl_options = {"HIDE_HEADER"}

    @classmethod
    def poll(cls, context):
        return host_from_area_space(
            getattr(context, "area", None),
            getattr(context, "space_data", None),
        ) == HOST_GRAPH

    def draw(self, context):
        panel_draw(context, self.layout)


class BEASE2_PT_dopesheet_sidebar(Panel):
    bl_idname = "BEASE2_PT_dopesheet_sidebar"
    bl_label = "B-Ease Studio"
    bl_space_type = "DOPESHEET_EDITOR"
    bl_region_type = "UI"
    bl_category = _SIDEBAR_CATEGORY
    bl_options = {"HIDE_HEADER"}

    @classmethod
    def poll(cls, context):
        return host_is_enabled(
            getattr(context, "area", None),
            getattr(context, "space_data", None),
            context,
        )

    def draw(self, context):
        panel_draw(context, self.layout)


PANEL_CLASSES = (
    BEASE2_PT_sidebar,
    BEASE2_PT_dopesheet_sidebar,
)


def _panel_is_registered(cls):
    try:
        return getattr(bpy.types, cls.__name__, None) is cls
    except (AttributeError, ReferenceError):
        return False


def register_handlers():
    _registered_panels[:] = [
        cls for cls in PANEL_CLASSES if _panel_is_registered(cls)
    ]
    for cls in PANEL_CLASSES:
        if cls in _registered_panels:
            continue
        bpy.utils.register_class(cls)
        _registered_panels.append(cls)
    _ensure_handler()
    if _load_pre_close_sidebars not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_load_pre_close_sidebars)


def unregister_handlers():
    global _message_timer_registered, _prune_timer_registered
    failures = []
    try:
        if _load_pre_close_sidebars in bpy.app.handlers.load_pre:
            bpy.app.handlers.load_pre.remove(_load_pre_close_sidebars)
    except Exception as error:
        if _load_pre_close_sidebars in bpy.app.handlers.load_pre:
            failures.append(("load_pre handler", error))
    try:
        _remove_handler()
    except Exception as error:
        failures.append(("draw handler", error))
    for label, callback in (
        ("message timer", lambda: _stop_message_timer_if_idle(force=True)),
        ("prune timer", _stop_prune_timer),
    ):
        try:
            callback()
        except Exception as error:
            timer_callback = (
                _message_timer_callback
                if label == "message timer"
                else _prune_timer_callback
            )
            try:
                still_registered = bpy.app.timers.is_registered(timer_callback)
            except Exception:
                still_registered = True
            if still_registered:
                failures.append((label, error))
            elif label == "message timer":
                _message_timer_registered = False
            else:
                _prune_timer_registered = False
    _registered_panels[:] = [
        cls for cls in PANEL_CLASSES if _panel_is_registered(cls)
    ]
    failed_panels = []
    for cls in reversed(tuple(_registered_panels)):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as error:
            if _panel_is_registered(cls):
                failures.append((f"sidebar panel {cls.__name__}", error))
                failed_panels.append(cls)
    _registered_panels[:] = reversed(failed_panels)
    if failures:
        details = "; ".join(
            f"{label}: {type(error).__name__}: {error}"
            for label, error in failures
        )
        raise RuntimeError(
            "Runtime handler cleanup failed after all resources were tried: "
            f"{details}"
        )


def _default_callbacks(context):
    from ..ops.operators import _runtime_actions, _runtime_dirty_callback

    return _runtime_actions(context), _runtime_dirty_callback(context)


def panel_draw(context, layout):

    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if (
        area is None
        or not host_is_enabled(
            area,
            getattr(context, "space_data", None),
            context,
        )
        or region is None
        or region.type != "UI"
    ):
        return
    key = _region_key(area, region)
    _panel_serials[key] = _panel_serials.get(key, 0) + 1

    actions, dirty_callback = _default_callbacks(context)
    ensure_sidebar(
        context,
        actions=actions,
        dirty_callback=dirty_callback,
    )

    _mask_x, _mask_y, _mask_w, mask_h = _view2d_mask_rect(region)
    physical_height = mask_h
    scale = _ui_scale(context)

    separator_height = max(1.0, physical_height - 16.0 * scale)
    count = max(
        1,
        int(
            (separator_height + _PANEL_SEPARATOR_ROUNDING_BIAS)
            // _PANEL_SEPARATOR_HEIGHT_LIMIT
        ),
    )
    factor = min(
        _PANEL_SEPARATOR_FACTOR_LIMIT,
        max(
            0.1,
            separator_height / (count * _PANEL_SEPARATOR_PIXEL_HEIGHT * scale),
        ),
    )
    column = layout.column(align=False)
    for _index in range(count):
        column.separator(factor=factor)


def _ui_scale(context=None):
    preferences = (
        getattr(context, "preferences", None)
        if context is not None
        else None
    )
    if preferences is None:
        preferences = getattr(bpy.context, "preferences", None)
    system = getattr(preferences, "system", None)
    return max(0.01, float(getattr(system, "ui_scale", 1.0)))


def _draw_callback():
    global _visible_draw_count
    area = getattr(bpy.context, "area", None)
    region = getattr(bpy.context, "region", None)
    if (
        area is None
        or not host_is_enabled(
            area,
            getattr(bpy.context, "space_data", None),
            bpy.context,
        )
        or region is None
        or region.type != "UI"
    ):
        return
    key = _region_key(area, region)
    serial = _panel_serials.get(key, 0)
    previous = _consumed_panel_serials.get(key, 0)
    _consumed_panel_serials[key] = serial
    instance = _instances.get(key[0])


    tab_visible = bool(
        instance is not None
        and _region_is_open(area, region)
        and serial > 0
        and serial != previous
    )
    if instance is not None:
        instance.tab_visible = tab_visible
    if not tab_visible or serial == previous:
        return
    try:
        if instance.draw(bpy.context):
            _visible_draw_count += 1
    except Exception:
        instance.last_draw_error = traceback.format_exc()
        print("B-Ease Studio: sidebar draw failed", flush=True)
        print(instance.last_draw_error, flush=True)


def _ensure_handler():
    global _draw_handler, _dopesheet_draw_handler
    if _draw_handler is None:
        _draw_handler = bpy.types.SpaceGraphEditor.draw_handler_add(
            _draw_callback,
            (),
            "UI",
            "POST_PIXEL",
        )
    if _dopesheet_draw_handler is None:
        _dopesheet_draw_handler = (
            bpy.types.SpaceDopeSheetEditor.draw_handler_add(
                _draw_callback,
                (),
                "UI",
                "POST_PIXEL",
            )
        )


def _remove_handler():
    global _draw_handler, _dopesheet_draw_handler
    failures = []
    if _dopesheet_draw_handler is not None:
        try:
            bpy.types.SpaceDopeSheetEditor.draw_handler_remove(
                _dopesheet_draw_handler,
                "UI",
            )
        except Exception as error:
            failures.append(("SpaceDopeSheetEditor", error))
        else:
            _dopesheet_draw_handler = None
    if _draw_handler is not None:
        try:
            bpy.types.SpaceGraphEditor.draw_handler_remove(
                _draw_handler,
                "UI",
            )
        except Exception as error:
            failures.append(("SpaceGraphEditor", error))
        else:
            _draw_handler = None
    if failures:
        details = "; ".join(
            f"{label}: {type(error).__name__}: {error}"
            for label, error in failures
        )
        raise RuntimeError("Draw handler cleanup failed: " + details)


def ensure_sidebar(
    context,
    actions=None,
    dirty_callback=None,
):

    area = getattr(context, "area", None)
    if area is None:
        raise ValueError("ensure_sidebar requires context.area")
    if not host_is_enabled(
        area,
        getattr(context, "space_data", None),
        context,
    ):
        raise ValueError("B-Ease Studio sidebar host is unavailable or disabled")
    area_ptr = area.as_pointer()
    existing = _instances.get(area_ptr)
    if existing is not None:
        if actions is not None or dirty_callback is not None:
            existing.set_callbacks(actions, dirty_callback)
        _show_pending_settings_reset(existing)
        return existing

    region = _ui_region(area, getattr(context, "region", None))
    if region is None:
        raise ValueError("B-Ease Studio host UI region not found")
    scale = _ui_scale(context)
    clip_x, clip_y, clip_w, clip_h = _content_rect(
        area,
        region,
        scale,
    )
    panel = PanelWindow(
        clip_x,
        clip_y,
        clip_w,
        clip_h,
        palette=build_palette(),
        scale=scale,
        actions=actions,
        dirty_callback=dirty_callback,
        region_size=(region.width, region.height),
    )
    panel.layout()
    instance = SidebarInstance(
        area_ptr,
        area,
        region,
        panel,
        window=getattr(context, "window", None),
        dirty_callback=dirty_callback,
    )
    _instances[area_ptr] = instance
    _ensure_prune_timer()
    _show_pending_settings_reset(instance)
    instance.tag_redraw()
    return instance


def close_sidebar(context, area_ptr):

    area_ptr = int(area_ptr)
    instance = _instances.get(area_ptr)
    if instance is None:
        return False
    instance.cancel_active_drag(restore_handle=True)
    instance._blur_text()
    instance.window.context_menu = None
    dialog = instance.window.dialog
    if dialog is not None:
        dialog._result(False)
    instance.window.dialog = None
    instance.window._delete_index = None
    instance.window._delete_record = None
    instance.window._pending_rename_record = None
    _instances.pop(area_ptr, None)
    instance.tag_redraw()
    instance.message = ""
    instance.message_started_at = None
    instance.message_deadline = None
    instance.message_fading = False
    key = area_ptr, instance.region_ptr
    _panel_serials.pop(key, None)
    _consumed_panel_serials.pop(key, None)
    if not _instances:
        _free_gpu_resources()
    return True


def active_category(region):

    try:
        return str(region.active_panel_category)
    except (AttributeError, ReferenceError, TypeError):
        return None


def open_sidebar(context, actions=None, dirty_callback=None):

    area = getattr(context, "area", None)
    if area is None or not host_is_enabled(
        area,
        getattr(context, "space_data", None),
        context,
    ):
        raise ValueError("B-Ease Studio sidebar host is unavailable or disabled")
    region = _ui_region(area)
    if region is None:
        raise ValueError("B-Ease Studio host UI region not found")
    area.spaces.active.show_region_ui = True
    try:
        region.active_panel_category = _SIDEBAR_CATEGORY
    except (AttributeError, ReferenceError, TypeError):
        pass
    instance = ensure_sidebar(
        context,
        actions=actions,
        dirty_callback=dirty_callback,
    )
    instance.tag_redraw()
    return instance


def toggle_sidebar(context, actions=None, dirty_callback=None):

    area = getattr(context, "area", None)
    if area is None or not host_is_enabled(
        area,
        getattr(context, "space_data", None),
        context,
    ):
        raise ValueError("B-Ease Studio sidebar host is unavailable or disabled")
    region = _ui_region(area)
    if region is None:
        raise ValueError("B-Ease Studio host UI region not found")

    instance = _instances.get(area.as_pointer())
    if (
        getattr(area.spaces.active, "show_region_ui", False)
        and instance is not None
        and instance.tab_visible
    ):
        area.spaces.active.show_region_ui = False
        close_sidebar(context, area.as_pointer())
        area.tag_redraw()
        return None
    return open_sidebar(
        context,
        actions=actions,
        dirty_callback=dirty_callback,
    )


def get_instance(area_ptr):
    prune_invalid_instances()
    return _instances.get(int(area_ptr))


def instances():
    prune_invalid_instances()
    return dict(_instances)


def sync_preset_models():

    prune_invalid_instances()
    changed = False
    for instance in tuple(_instances.values()):
        if instance.window.sync_preset_model():
            instance.tag_redraw()
            changed = True
    return changed


def preset_record(area_ptr, target_id):

    try:
        instance = get_instance(int(area_ptr))
        identity = int(target_id)
    except (TypeError, ValueError):
        return None
    if instance is None:
        return None
    instance.window.sync_preset_model()
    return next(
        (
            record
            for record in BEaseGlobals.bezier_preset_list
            if id(record) == identity
        ),
        None,
    )


def rename_preset(area_ptr, target, value):

    try:
        instance = get_instance(int(area_ptr))
    except (TypeError, ValueError):
        return False
    if instance is None:
        return False
    instance.window.sync_preset_model()
    record = next(
        (
            candidate
            for candidate in BEaseGlobals.bezier_preset_list
            if candidate is target
        ),
        None,
    )
    if record is None:
        return False
    record["name"] = str(value).strip()
    instance.active_drag = None
    instance._blur_text()
    instance.window._notify_preset_model_changed()
    instance.window._dirty("preset_renamed")
    instance.window._action("rename_preset")
    instance.tag_redraw()
    return True


def reset_canvas_selections(except_area_ptr=None):

    changed = False
    except_pointer = (
        None if except_area_ptr is None else int(except_area_ptr)
    )
    for area_ptr, instance in tuple(_instances.items()):
        if except_pointer is not None and int(area_ptr) == except_pointer:
            continue
        if instance.window.canvas.clear_selection():
            instance.tag_redraw()
            changed = True
    return changed


def reset_other_canvas_selections(window):

    for area_ptr, instance in tuple(_instances.items()):
        if instance.window is window:
            return reset_canvas_selections(except_area_ptr=area_ptr)
    return False


def sync_snap_buttons():

    checked = bool(BEaseGlobals.snap_enabled)
    changed = False
    for instance in tuple(_instances.values()):
        button = instance.window.snap_button
        if button.checked != checked:
            button.checked = checked
            changed = True
        instance.tag_redraw()
    return changed


def tag_redraw(area_ptr):
    instance = get_instance(area_ptr)
    if instance is not None:
        instance.tag_redraw()
        return True
    return False


def refresh_points(area_ptr=None, preserve_field=None, preserve_window=None):

    targets = (
        [get_instance(area_ptr)]
        if area_ptr is not None
        else list(_instances.values())
    )
    changed = False
    for instance in targets:
        if instance is None:
            continue
        if instance.window is not preserve_window:
            if instance.text_focus in (
                instance.window.handle_input,
                instance.window.anchor_input,
            ):
                instance._blur_text()
            instance.window.update_curve_texts(
                preserve_field=preserve_field,
            )
        instance.tag_redraw()
        changed = True
    return changed


def show_message(area_ptr, text):
    instance = get_instance(area_ptr)
    if instance is None:
        return False
    instance.show_message(text)
    return True


def _show_pending_settings_reset(instance):
    load_notice = consume_settings_load_notice()
    if load_notice:
        instance.show_message(load_notice)
        return True
    if not consume_settings_reset_notice():
        return False
    instance.show_message("Settings could not be recovered; original files kept")
    return True


def text_value(area_ptr, field_id):
    instance = get_instance(area_ptr)
    return None if instance is None else instance.text_value(field_id)


def set_text_value(area_ptr, field_id, value):
    instance = get_instance(area_ptr)
    if instance is None:
        return False
    return instance.set_text_value(field_id, value)


def draw_handler_count():
    return int(_draw_handler is not None) + int(
        _dopesheet_draw_handler is not None
    )


def message_timer_count():
    return int(_message_timer_registered and _timer_is_registered())


def visible_draw_count():
    return _visible_draw_count


def close_all(context=None):

    failures = []
    for area_ptr in list(_instances):
        try:
            close_sidebar(context, area_ptr)
        except Exception as error:
            failures.append((f"sidebar {area_ptr}", error))
    for label, callback in (("GPU resources", _free_gpu_resources),):
        try:
            callback()
        except Exception as error:
            failures.append((label, error))
    if failures:
        details = "; ".join(
            f"{label}: {type(error).__name__}: {error}"
            for label, error in failures
        )
        raise RuntimeError(
            "Runtime cleanup failed after all resources were tried: "
            f"{details}"
        )


def _free_gpu_resources():

    from . import icons, thumbnails, widgets

    failures = []
    for label, callback in (
        ("thumbnails", thumbnails.free_all),
        ("icons", icons.free_icons),
        ("widgets", widgets.free_resources),
        ("draw", draw.free_resources),
    ):
        try:
            callback()
        except Exception as error:
            failures.append((label, error))
    if failures:
        details = "; ".join(
            f"{label}: {type(error).__name__}: {error}"
            for label, error in failures
        )
        raise RuntimeError(
            "GPU cleanup failed after all resources were tried: " + details
        )
