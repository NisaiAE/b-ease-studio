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

from functools import wraps
import time
import traceback

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..core import curve as curve_model
from ..fcurve.easing import (
    ApplyRollbackError,
    copy_fcurve_easing,
    get_message,
    get_visible_fcurves_from_context,
    paste_fcurve_easing,
)
from ..properties import BEaseGlobals, save_data
from ..ui import runtime


_SAVE_REASONS = {
    "preset_saved",
    "preset_deleted",
    "preset_renamed",
    "preset_reordered",
    "splitter_released",
}
_registered_classes = []
_DOUBLE_CLICK_DISTANCE = 5.0
_DOUBLE_CLICK_INTERVAL = 0.45


def _area_pointer(context):
    area = getattr(context, "area", None)
    return None if area is None else area.as_pointer()


def _log_exception(error, *, chain=False):
    try:
        traceback.print_exception(type(error), error, error.__traceback__, chain=chain)
    except Exception:

        pass


def _show_message_callback(context):
    try:
        area_ptr = _area_pointer(context)
    except Exception as error:
        _log_exception(error)
        area_ptr = None

    def show(text):
        shown = set()
        if area_ptr is not None:
            shown.add(int(area_ptr))
            try:
                runtime.show_message(area_ptr, text)
            except Exception as error:
                _log_exception(error)
        try:
            candidates = tuple(runtime.instances().items())
        except Exception as error:
            _log_exception(error)
            candidates = ()
        for candidate_ptr, instance in candidates:
            try:
                if candidate_ptr not in shown and instance.tab_visible:
                    runtime.show_message(candidate_ptr, text)
            except Exception as error:
                _log_exception(error)

    return show


def _operation_message_callback(context):
    messages = []
    canvas_message = _show_message_callback(context)

    def show(text):
        messages.append(str(text))
        canvas_message(text)

    return messages, show


def _save_failure_message():
    return "Failed to save settings"


def _has_supported_editor(context):

    return runtime.host_from_area_space(
        getattr(context, "area", None),
        getattr(context, "space_data", None),
    ) is not None


def _editable_visible_fcurves(context, visible):

    try:
        editable = context.editable_fcurves
    except (AttributeError, ReferenceError, TypeError):
        return None
    if editable is None:
        return None
    editable_ids = {int(fcurve.as_pointer()) for fcurve in editable}
    return tuple(fcurve for fcurve in visible if int(fcurve.as_pointer()) in editable_ids)


def _report_exception(context, action, error):


    _log_exception(error, chain=True)
    try:
        detail = str(error).strip()
    except Exception:
        detail = ""
    description = type(error).__name__
    if detail:
        description = f"{description}: {detail}"
    try:
        _show_message_callback(context)(f"{action} failed: {description}")
    except Exception as notification_error:
        _log_exception(notification_error)


def _guard_ui_operator(action):

    def decorate(callback):
        def call(self, context, *args):
            try:
                return callback(self, context, *args)
            except Exception as error:
                cleanup = getattr(self, "_cancel_after_error", None)
                if cleanup is not None:
                    try:
                        cleanup(context)
                    except Exception as cleanup_error:
                        _log_exception(cleanup_error)
                _report_exception(context, action, error)
                return {"CANCELLED"}

        if callback.__name__ in {"invoke", "modal"}:
            def guarded(self, context, event):
                return call(self, context, event)
        else:
            def guarded(self, context):
                return call(self, context)
        return wraps(callback)(guarded)
    return decorate


def _runtime_dirty(reason=None, area_ptr=None):
    if reason in _SAVE_REASONS:
        success = save_data()
        if not success and area_ptr is not None:
            runtime.show_message(area_ptr, _save_failure_message())
        return bool(success)
    return True


def _runtime_dirty_callback(context):
    area_ptr = _area_pointer(context)

    def dirty(reason=None):
        try:
            return _runtime_dirty(reason, area_ptr)
        except Exception as error:


            _report_exception(context, "Save settings", error)
            return False

    return dirty


def _editor_context_override(context):
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    window = getattr(context, "window", None)
    screen = getattr(context, "screen", None)
    space_data = getattr(context, "space_data", None)
    if (
        area is None
        or region is None
        or window is None
        or screen is None
        or space_data is None
        or runtime.host_from_area_space(area, space_data) is None
    ):
        return None
    return {
        "window": window,
        "screen": screen,
        "area": area,
        "region": region,
        "space_data": space_data,
    }


def _run_ui_operator(operator, context_override, *, undo=False, action="Action"):
    try:
        if context_override is None:
            return operator("EXEC_DEFAULT", undo)
        with bpy.context.temp_override(**context_override):
            return operator("EXEC_DEFAULT", undo)
    except Exception as error:
        _report_exception(bpy.context, action, error)
        return {"CANCELLED"}


def _copy_from_ui(context_override=None):
    _run_ui_operator(
        bpy.ops.blenderease2.copy_easing,
        context_override,
        action="Copy",
    )


    return None


def _apply_from_ui(context_override=None):


    return _run_ui_operator(
        bpy.ops.blenderease2.apply_easing,
        context_override,
        undo=True,
        action="Apply",
    )


def _runtime_actions(context=None):
    context_override = (
        None if context is None else _editor_context_override(context)
    )
    return {
        "copy": lambda: _copy_from_ui(context_override),
        "apply": lambda: _apply_from_ui(context_override),
    }


class BEASE2_OT_open_editor(Operator):


    bl_idname = "blenderease2.open_editor"
    bl_label = "B-Ease"
    bl_description = "Edit selected F-Curves with B-Ease"
    bl_options = {"REGISTER"}

    def _valid_context(self, context):
        area = getattr(context, "area", None)
        return area is not None and runtime.host_is_enabled(
            area,
            getattr(context, "space_data", None),
            context,
        )

    @_guard_ui_operator("B-Ease")
    def invoke(self, context, event):
        if not self._valid_context(context):
            self.report({"WARNING"}, runtime.SUPPORTED_HOST_ERROR)
            return {"CANCELLED"}
        runtime.toggle_sidebar(
            context,
            actions=_runtime_actions(context),
            dirty_callback=_runtime_dirty_callback(context),
        )
        return {"FINISHED"}

    @_guard_ui_operator("B-Ease")
    def execute(self, context):
        if not self._valid_context(context):
            self.report({"WARNING"}, runtime.SUPPORTED_HOST_ERROR)
            return {"CANCELLED"}
        runtime.toggle_sidebar(
            context,
            actions=_runtime_actions(context),
            dirty_callback=_runtime_dirty_callback(context),
        )
        return {"FINISHED"}


class BEASE2_OT_apply_easing(Operator):
    bl_idname = "blenderease2.apply_easing"
    bl_label = "Apply B-Ease Easing"
    bl_description = "Apply the current easing to selected intervals on unlocked visible channels"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _has_supported_editor(context)

    @_guard_ui_operator("Apply")
    def execute(self, context):
        if not _has_supported_editor(context):
            return {"CANCELLED"}
        messages, show_message = _operation_message_callback(context)
        try:
            fcurves = get_visible_fcurves_from_context(context)
            if fcurves is None:
                show_message("Cannot determine visible animation channels")
                return {"CANCELLED"}
            fcurves = _editable_visible_fcurves(context, fcurves)
            if fcurves is None:
                show_message("Cannot determine editable animation channels")
                return {"CANCELLED"}
            applied = paste_fcurve_easing(
                curve_model.deep_copy(BEaseGlobals.curve),
                show_message=show_message,
                fcurve_provider=fcurves,
            )
        except ApplyRollbackError as error:

            _log_exception(error, chain=True)
            message = "Use Undo. Apply was not fully restored."
            try:
                show_message(message)
            except Exception as notification_error:
                _log_exception(notification_error)
            try:
                self.report({"WARNING"}, message)
            except Exception as notification_error:
                _log_exception(notification_error)
            return {"FINISHED"}
        except Exception as error:
            _report_exception(context, "Apply", error)
            return {"CANCELLED"}
        if not applied:
            if not messages:
                show_message(get_message("no_valid_selection"))
            return {"CANCELLED"}

        try:

            runtime.refresh_points()
        except Exception as error:
            _report_exception(context, "Apply display update", error)
        return {"FINISHED"}


class BEASE2_OT_copy_easing(Operator):
    bl_idname = "blenderease2.copy_easing"
    bl_label = "Copy B-Ease Easing"
    bl_description = "Read easing handles from consecutive selected keyframes"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _has_supported_editor(context)

    @_guard_ui_operator("Copy")
    def execute(self, context):
        if not _has_supported_editor(context):
            return {"CANCELLED"}
        messages, show_message = _operation_message_callback(context)
        try:
            fcurves = get_visible_fcurves_from_context(context)
            if fcurves is None:
                show_message("Cannot determine visible animation channels")
                return {"CANCELLED"}
            copied = copy_fcurve_easing(
                show_message=show_message,
                fcurve_provider=fcurves,
            )
        except Exception as error:
            _report_exception(context, "Copy", error)
            return {"CANCELLED"}
        if copied is None:
            if not messages:
                show_message(get_message("no_valid_selection"))
            return {"CANCELLED"}
        BEaseGlobals.curve = curve_model.deep_copy(copied)
        try:
            runtime.reset_canvas_selections()
            runtime.refresh_points()
        except Exception as error:
            _report_exception(context, "Copy display update", error)
        return {"FINISHED"}


class BEASE2_OT_edit_text_ime(Operator):


    bl_idname = "blenderease2.edit_text_ime"
    bl_label = "Edit Text..."
    bl_description = "Edit the preset name with Blender's text input dialog"

    field_id: StringProperty(options={"HIDDEN"})
    initial_value: StringProperty(options={"HIDDEN"})
    value: StringProperty(name="Text")

    @_guard_ui_operator("Edit text")
    def invoke(self, context, _event):
        area_ptr = _area_pointer(context)
        current = (
            None
            if area_ptr is None
            else runtime.text_value(area_ptr, self.field_id)
        )
        self.value = self.initial_value if current is None else current
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, _context):
        layout = self.layout

        layout.activate_init = True
        layout.prop(self, "value", text="")

    @_guard_ui_operator("Edit text")
    def execute(self, context):
        area_ptr = _area_pointer(context)
        if area_ptr is None:
            return {"CANCELLED"}
        if not runtime.set_text_value(area_ptr, self.field_id, self.value):

            if runtime.text_value(area_ptr, self.field_id) is None:
                return {"CANCELLED"}
        return {"FINISHED"}


class BEASE2_OT_rename_preset(Operator):


    bl_idname = "blenderease2.rename_preset"
    bl_label = "Rename Preset"
    bl_description = "Rename this saved preset"

    area_ptr: StringProperty(options={"HIDDEN"})
    target_id: StringProperty(options={"HIDDEN"})
    value: StringProperty(name="Name")

    @_guard_ui_operator("Rename preset")
    def invoke(self, context, _event):
        target = runtime.preset_record(self.area_ptr, self.target_id)
        if target is None:
            return {"CANCELLED"}
        self._target_record = target
        self.value = str(target.get("name", ""))
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, _context):
        layout = self.layout
        layout.activate_init = True
        layout.prop(self, "value", text="")

    @_guard_ui_operator("Rename preset")
    def execute(self, _context):
        target = getattr(self, "_target_record", None)
        if target is None:
            target = runtime.preset_record(self.area_ptr, self.target_id)
        if target is None:
            return {"CANCELLED"}
        if not runtime.rename_preset(self.area_ptr, target, self.value):
            return {"CANCELLED"}
        return {"FINISHED"}


class BEASE2_OT_route_event(Operator):


    bl_idname = "blenderease2.route_event"
    bl_label = "B-Ease Event Router"
    bl_options = {"INTERNAL"}

    _area_ptr = None
    _mode = None
    _button = None
    _cursor_active = False
    _cursor = None
    _last_left_press_at = None
    _last_left_press_pos = None

    _hover_cursor_name = None

    @classmethod
    def _apply_hover_cursor(cls, context, cursor):

        window = getattr(context, "window", None)
        if window is None:
            return
        try:
            if cursor:
                if cls._hover_cursor_name != cursor:
                    window.cursor_set(cursor)
                    cls._hover_cursor_name = cursor
            elif cls._hover_cursor_name is not None:
                window.cursor_set("DEFAULT")
                cls._hover_cursor_name = None
        except (TypeError, RuntimeError):
            traceback.print_exc()

    @staticmethod
    def _coordinates(event):
        return float(event.mouse_region_x), float(event.mouse_region_y)

    @staticmethod
    def _button_name(event_type):
        return {
            "LEFTMOUSE": "LEFT",
            "MIDDLEMOUSE": "MIDDLE",
            "RIGHTMOUSE": "RIGHT",
        }.get(event_type)

    def _set_cursor(self, context, cursor):
        if not cursor or getattr(context, "window", None) is None:
            return
        if self._cursor_active and cursor == self._cursor:
            return
        try:
            context.window.cursor_modal_set(cursor)
            self._cursor_active = True
            self._cursor = cursor
        except (TypeError, RuntimeError):
            traceback.print_exc()

    def _restore_cursor(self, context):
        if not self._cursor_active:
            return
        try:
            if getattr(context, "window", None) is not None:
                context.window.cursor_modal_restore()
        except (TypeError, RuntimeError):
            traceback.print_exc()
        self._cursor_active = False
        self._cursor = None

    def _finish(self, context, *, pass_through=False):
        self._restore_cursor(context)
        self._mode = None
        if pass_through:
            return {"FINISHED", "PASS_THROUGH"}
        return {"FINISHED"}

    def _cancel_after_error(self, context):


        try:
            instance = runtime.get_instance(self._area_ptr)
        except Exception as error:
            _log_exception(error)
            instance = None
        steps = []
        if instance is not None:
            steps.extend((
                lambda: instance.cancel_active_drag(restore_handle=True),
                lambda: instance.blur_text(),
                lambda: instance.popup_key("ESC"),


                lambda: setattr(instance, "active_drag", None),
                lambda: setattr(instance, "text_focus", None),
                lambda: setattr(instance, "text_focus_curve_version", None),
                lambda: setattr(instance.window, "dialog", None),
                lambda: setattr(instance.window, "context_menu", None),
            ))
        steps.extend((
            lambda: self._restore_cursor(context),
            lambda: setattr(self, "_cursor_active", False),
            lambda: setattr(self, "_cursor", None),
            lambda: setattr(self, "_mode", None),
            lambda: setattr(self, "_button", None),
            lambda: self._apply_hover_cursor(context, None),
        ))
        for cleanup in steps:
            try:
                cleanup()
            except Exception as error:
                _log_exception(error)

    def _invoke_ime(self, context, instance, field_id):
        initial = instance.text_value(field_id)
        result = bpy.ops.blenderease2.edit_text_ime(
            "INVOKE_DEFAULT",
            field_id=field_id,
            initial_value="" if initial is None else initial,
        )
        return "RUNNING_MODAL" in result or "FINISHED" in result

    def _invoke_rename(self, context, instance, target):
        result = bpy.ops.blenderease2.rename_preset(
            "INVOKE_DEFAULT",
            area_ptr=str(instance.area_ptr),
            target_id=str(id(target)),
        )
        return "RUNNING_MODAL" in result or "FINISHED" in result

    def _begin_modal(self, context, reaction, button):
        self._mode = reaction["start_modal"]
        self._button = button
        self._set_cursor(context, reaction.get("cursor"))
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _is_double_click(self, event, x, y, instance):
        if getattr(event, "value", None) == "DOUBLE_CLICK":
            type(self)._last_left_press_at = None
            type(self)._last_left_press_pos = None
            return True
        now = time.monotonic()
        operator_type = type(self)
        previous_at = operator_type._last_left_press_at
        previous_pos = operator_type._last_left_press_pos


        operator_type._last_left_press_at = now
        operator_type._last_left_press_pos = (x, y, self._area_ptr)
        if previous_at is None or previous_pos is None:
            return False
        if len(previous_pos) >= 3 and previous_pos[2] != self._area_ptr:
            return False
        threshold = _DOUBLE_CLICK_DISTANCE * instance.window.scale
        return (
            now - previous_at <= _DOUBLE_CLICK_INTERVAL
            and abs(x - previous_pos[0]) + abs(y - previous_pos[1])
            <= threshold
        )

    @_guard_ui_operator("B-Ease interaction")
    def invoke(self, context, event):
        area = getattr(context, "area", None)
        region = getattr(context, "region", None)
        if area is None or region is None or region.type != "UI":
            self._apply_hover_cursor(context, None)
            return {"PASS_THROUGH"}
        if not runtime.host_is_enabled(
            area,
            getattr(context, "space_data", None),
            context,
        ):
            self._apply_hover_cursor(context, None)
            return {"PASS_THROUGH"}
        area_ptr = _area_pointer(context)
        instance = (
            None if area_ptr is None else runtime.get_instance(area_ptr)
        )
        if instance is None or not instance.tab_visible:
            self._apply_hover_cursor(context, None)
            return {"PASS_THROUGH"}

        self._area_ptr = area_ptr
        event_type = event.type
        x, y = self._coordinates(event)
        instance._refresh_region(context)
        if not instance.content_hit(x, y):
            self._apply_hover_cursor(context, None)
            return {"PASS_THROUGH"}


        if event_type in {"DEL", "DELETE", "BACK_SPACE", "BACKSPACE", "X"}:
            if instance.popup_key(event_type):
                return {"FINISHED"}
            if instance.text_focus is not None:
                return {"PASS_THROUGH"}
            if instance.delete_selected_anchor():
                return {"FINISHED"}
            return {"PASS_THROUGH"}


        if event_type in {"A", "V", "HOME"}:
            if instance.popup_key(event_type):
                return {"FINISHED"}
            if instance.text_focus is not None:
                return {"PASS_THROUGH"}
            if instance.canvas_key(event_type, x, y, alt=event.alt):
                return {"FINISHED"}
            return {"PASS_THROUGH"}

        if event_type == "MOUSEMOVE":
            reaction = instance.on_mouse_move(x, y, context)
            self._apply_hover_cursor(context, reaction.get("cursor"))
            return {"PASS_THROUGH"}
        if event_type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            delta = 1 if event_type == "WHEELUPMOUSE" else -1
            reaction = instance.on_wheel(delta, context, x, y)
            return {"FINISHED"} if reaction["consumed"] else {"PASS_THROUGH"}
        if event_type == "TRACKPADPAN":


            delta_x = float(event.mouse_x - event.mouse_prev_x)
            delta_y = float(event.mouse_y - event.mouse_prev_y)
            reaction = instance.on_trackpad_pan(delta_x, delta_y, context, x, y)
            return {"FINISHED"} if reaction["consumed"] else {"PASS_THROUGH"}

        button = self._button_name(event_type)
        if button is None:
            return {"PASS_THROUGH"}


        modified_click = bool(event.shift or event.ctrl or event.oskey)
        if button == "LEFT" and modified_click:
            type(self)._last_left_press_at = None
            type(self)._last_left_press_pos = None
        double_click = (
            button == "LEFT"
            and not modified_click
            and self._is_double_click(event, x, y, instance)
        )
        reaction = instance.on_mouse_press(
            x,
            y,
            button,
            context,
            double_click=double_click,
            shift=bool(event.shift),
            ctrl=bool(event.ctrl or event.oskey),
        )
        if not reaction["consumed"]:
            return {"PASS_THROUGH"}
        if reaction["start_modal"] == "ime":
            self._invoke_ime(context, instance, reaction["field_id"])
            return {"FINISHED"}
        if reaction["start_modal"] == "rename":
            self._invoke_rename(context, instance, reaction["field_id"])
            return {"FINISHED"}
        if reaction["start_modal"] in {"drag", "text"}:
            return self._begin_modal(context, reaction, button)
        if reaction["start_modal"] == "popup":
            return self._begin_modal(context, reaction, button)
        return {"FINISHED"}

    def _modal_drag(self, context, event, instance):
        if event.type == "ESC" and event.value == "PRESS":
            instance.cancel_active_drag(restore_handle=True)
            return self._finish(context)

        if (
            event.type == "RIGHTMOUSE"
            and event.value == "PRESS"
            and self._button != "RIGHT"
        ):
            instance.cancel_active_drag(restore_handle=True)
            return self._finish(context)
        if event.type == "MOUSEMOVE":
            x, y = self._coordinates(event)
            reaction = instance.on_mouse_move(x, y, context)
            self._set_cursor(context, reaction.get("cursor"))
            return {"RUNNING_MODAL"}
        if (
            event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}
            and instance.active_drag is not None
            and instance.active_drag.get("kind") == "list"
            and (
                instance.window.list_view.mode
                == instance.window.list_view.GRID
            )
        ):


            delta = 1 if event.type == "WHEELUPMOUSE" else -1
            instance.on_wheel(delta, context, route="list")
            return {"RUNNING_MODAL"}
        event_button = self._button_name(event.type)
        if event_button == self._button and event.value == "RELEASE":
            x, y = self._coordinates(event)
            reaction = instance.on_mouse_release(
                x,
                y,
                event_button,
                context,
            )
            if reaction["start_modal"] == "rename":
                self._invoke_rename(context, instance, reaction["field_id"])
            elif reaction["start_modal"] == "ime":
                self._invoke_ime(context, instance, reaction["field_id"])
            return self._finish(context)
        return {"RUNNING_MODAL"}

    def _modal_text_mouse_press(self, context, event, instance):
        x, y = self._coordinates(event)
        button = self._button_name(event.type)
        double_click = (
            button == "LEFT"
            and self._is_double_click(event, x, y, instance)
        )
        reaction = instance.on_mouse_press(
            x,
            y,
            button,
            context,
            double_click=double_click,
        )
        if reaction["start_modal"] == "ime":
            self._invoke_ime(context, instance, reaction["field_id"])
            return self._finish(context)
        if reaction["start_modal"] == "rename":
            self._invoke_rename(context, instance, reaction["field_id"])
            return self._finish(context)
        if reaction["start_modal"] == "popup":
            self._mode = "popup"
            self._button = button
            self._set_cursor(context, reaction.get("cursor"))
            return {"RUNNING_MODAL"}
        if reaction["start_modal"] == "drag":
            self._mode = "drag"
            self._button = button
            self._set_cursor(context, reaction.get("cursor"))
            return {"RUNNING_MODAL"}
        if reaction["start_modal"] == "text":
            self._button = button
            self._set_cursor(context, reaction.get("cursor"))
            return {"RUNNING_MODAL"}
        return self._finish(
            context,
            pass_through=not reaction["consumed"],
        )

    def _modal_text(self, context, event, instance):
        event_type = event.type
        if event_type == "ESC" and event.value == "PRESS":
            instance.cancel_active_drag()
            instance.blur_text()
            return self._finish(context)

        if event_type == "MOUSEMOVE":
            x, y = self._coordinates(event)
            reaction = instance.on_mouse_move(x, y, context)
            self._set_cursor(context, reaction.get("cursor") or "TEXT")
            return {"RUNNING_MODAL"}

        button = self._button_name(event_type)
        if button is not None and event.value in {"PRESS", "DOUBLE_CLICK"}:
            return self._modal_text_mouse_press(
                context,
                event,
                instance,
            )
        if button is not None and event.value == "RELEASE":
            x, y = self._coordinates(event)
            reaction = instance.on_mouse_release(x, y, button, context)
            if reaction["start_modal"] == "rename":
                self._invoke_rename(context, instance, reaction["field_id"])
                return self._finish(context)
            if reaction["start_modal"] == "ime":
                self._invoke_ime(context, instance, reaction["field_id"])
                return self._finish(context)
            return {"RUNNING_MODAL"}

        if event_type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:


            x, y = self._coordinates(event)
            delta = 1 if event_type == "WHEELUPMOUSE" else -1
            instance.on_wheel(delta, context, x, y, route="list_only")
            return {"RUNNING_MODAL"}

        ctrl_or_cmd = bool(event.ctrl or event.oskey)
        if (
            event.value == "PRESS"
            and ctrl_or_cmd
            and event_type in {"A", "C", "V", "X"}
        ):
            instance.key(
                event_type,
                shift=event.shift,
                ctrl_or_cmd=True,
            )
            return {"RUNNING_MODAL"}

        navigation_keys = {
            "BACK_SPACE",
            "BACKSPACE",
            "DEL",
            "DELETE",
            "LEFT_ARROW",
            "LEFT",
            "RIGHT_ARROW",
            "RIGHT",
            "HOME",
            "END",
        }
        if event.value == "PRESS" and event_type in navigation_keys:
            instance.key(
                event_type,
                shift=event.shift,
                ctrl_or_cmd=False,
            )
            return {"RUNNING_MODAL"}

        unicode_text = getattr(event, "unicode", "")
        if (
            not ctrl_or_cmd
            and unicode_text
            and event.value in {"PRESS", "NOTHING"}
        ):
            instance.insert_text(unicode_text)


        return {"RUNNING_MODAL"}

    def _modal_popup(self, context, event, instance):

        event_type = event.type
        if event_type == "ESC" and event.value == "PRESS":
            instance.popup_key("ESC")
            return self._finish(context)

        if event_type == "MOUSEMOVE":
            x, y = self._coordinates(event)
            reaction = instance.on_mouse_move(x, y, context)
            self._set_cursor(context, reaction.get("cursor"))
            return {"RUNNING_MODAL"}

        if event_type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            x, y = self._coordinates(event)
            delta = 1 if event_type == "WHEELUPMOUSE" else -1
            instance.on_wheel(delta, context, x, y)
            return {"RUNNING_MODAL"}

        button = self._button_name(event_type)
        if button is not None and event.value in {"PRESS", "DOUBLE_CLICK"}:
            x, y = self._coordinates(event)
            reaction = instance.on_mouse_press(
                x,
                y,
                button,
                context,
                double_click=event.value == "DOUBLE_CLICK",
            )
            if reaction["start_modal"] == "rename":
                self._invoke_rename(context, instance, reaction["field_id"])
                return self._finish(context)
            return {"RUNNING_MODAL"}
        if button is not None and event.value == "RELEASE":
            x, y = self._coordinates(event)
            reaction = instance.on_mouse_release(x, y, button, context)
            if reaction["start_modal"] == "rename":
                self._invoke_rename(context, instance, reaction["field_id"])
                return self._finish(context)
            if instance.dialog is None and instance.menu is None:
                return self._finish(context)
            return {"RUNNING_MODAL"}


        return {"RUNNING_MODAL"}

    @_guard_ui_operator("B-Ease interaction")
    def modal(self, context, event):
        instance = (
            None
            if self._area_ptr is None
            else runtime.get_instance(self._area_ptr)
        )
        if instance is None:
            return self._finish(context, pass_through=True)
        if instance.cancel_stale_curve_interaction():
            return self._finish(context)
        if self._mode == "drag":
            return self._modal_drag(context, event, instance)
        if self._mode == "popup":
            return self._modal_popup(context, event, instance)
        return self._modal_text(context, event, instance)


CLASSES = (
    BEASE2_OT_open_editor,
    BEASE2_OT_apply_easing,
    BEASE2_OT_copy_easing,
    BEASE2_OT_edit_text_ime,
    BEASE2_OT_rename_preset,
    BEASE2_OT_route_event,
)


def _class_is_registered(cls):
    try:
        namespace, name = cls.bl_idname.split(".", 1)
        identifier = f"{namespace.upper()}_OT_{name}"
        return getattr(bpy.types, identifier, None) is cls
    except (AttributeError, ReferenceError, ValueError):
        return False


def register():
    _registered_classes[:] = [
        cls for cls in _registered_classes if _class_is_registered(cls)
    ]
    if _registered_classes:
        return
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
    except Exception:
        unregister()
        raise


def unregister():
    failures = []
    failed_classes = []
    for cls in reversed(tuple(_registered_classes)):
        if not _class_is_registered(cls):
            continue
        try:
            bpy.utils.unregister_class(cls)
        except Exception as error:
            if _class_is_registered(cls):
                failures.append((cls.__name__, error))
                failed_classes.append(cls)
    _registered_classes[:] = reversed(failed_classes)
    if failures:
        details = "; ".join(
            f"{name}: {type(error).__name__}: {error}"
            for name, error in failures
        )
        raise RuntimeError(
            "Operator class cleanup failed after all classes were tried: "
            f"{details}"
        )
