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

import traceback

from ..constants import (
    APPLY_BUTTON_HEIGHT,
    BUTTON_HEIGHT,
    MAX_SLIDER_VALUE,
    MIN_SLIDER_VALUE,
)
from ..core import curve as curve_model
from ..core import textfmt
from ..properties import BEaseGlobals, mark_preset_model_changed
from . import draw, thumbnails
from .canvas import DrawingCanvas
from .theme import get_canvas_colors
from .widgets import (
    Button,
    ConfirmDialog,
    ContextMenu,
    LineEdit,
    ListView,
    Rect,
    Slider,
    Splitter,
    SPLITTER_MIN_PANE_WIDTH,
)


class PanelWindow:


    PREFERRED_HEIGHT = 300.0
    LEFT_BOTTOM_HEIGHT = 105.0
    CANVAS_GAP = 8.0

    def __init__(
        self,
        x,
        y,
        width,
        height,
        *,
        palette,
        scale=1.0,
        actions=None,
        dirty_callback=None,
        region_size=(0, 0),
    ):
        self.scale = float(scale)
        self.palette = palette
        self.actions = dict(actions or {})
        self.dirty_callback = dirty_callback
        self.rect = Rect(float(x), float(y), float(width), float(height))
        self.region_size = tuple(map(float, region_size))
        self.context_menu = None
        self.dialog = None
        self._delete_index = None
        self._delete_record = None
        self._pending_rename_record = None
        self._preset_version = -1
        self._preset_signature = None
        self._syncing_curve_text = False
        self.preset_header_row_rect = Rect(0.0, 0.0, 0.0, 0.0)
        self.preset_slider_row_rect = Rect(0.0, 0.0, 0.0, 0.0)
        self.right_pane_rect = Rect(0.0, 0.0, 0.0, 0.0)
        self.right_occlusion_rects = ()
        self.left_controls_occlusion_rect = Rect(0.0, 0.0, 0.0, 0.0)

        self._thumbnail_colors = get_canvas_colors()
        thumbnails.refresh_theme_colors(self._thumbnail_colors)

        ratios = BEaseGlobals.splitter_ratio or [0.8, 0.2]
        ratio_sum = float(ratios[0]) + float(ratios[1])
        ratio = float(ratios[0]) / ratio_sum if ratio_sum > 0.0 else 0.8

        self.canvas = DrawingCanvas(
            palette=palette,
            scale=self.scale,
            on_points_changed=self._on_canvas_points_changed,
            on_selection_changed=self._on_canvas_selection_changed,
        )
        self.copy_button = Button(
            "Copy",
            icon="COPY",
            palette=palette,
            scale=scale,
            on_click=lambda _button: self._copy(),
        )
        self.reverse_button = Button(
            "Reverse",
            icon="REVERSE",
            palette=palette,
            scale=scale,
            on_click=None,
        )
        self.snap_button = Button(
            "Snap",
            icon="SNAP",
            checkable=True,
            checked=BEaseGlobals.snap_enabled,
            palette=palette,
            scale=scale,
            on_click=lambda button: self._toggle_snap(button.checked),
        )
        for toolbar_button in (
            self.copy_button,
            self.reverse_button,
            self.snap_button,
        ):
            toolbar_button.configure_toolbar_label_group(self.snap_button)
        self.handle_input = LineEdit(
            textfmt.format_handles(
                BEaseGlobals.curve,
                BEaseGlobals.snap_enabled,
                False,
            ),
            align="center",
            palette=palette,
            scale=scale,
            on_text_changed=self._on_handle_text_changed,
            on_focus_in=lambda: self._on_curve_text_focus_in(
                self.handle_input
            ),
            tracks_draft=True,
        )

        self.point_input = self.handle_input
        self.anchor_input = LineEdit(
            textfmt.format_anchors(
                BEaseGlobals.curve,
                BEaseGlobals.snap_enabled,
                False,
            ),
            align="center",
            palette=palette,
            scale=scale,
            on_text_changed=self._on_anchor_text_changed,
            on_focus_in=lambda: self._on_curve_text_focus_in(
                self.anchor_input
            ),
            tracks_draft=True,
        )
        self.apply_button = Button(
            "APPLY",
            variant="apply",
            palette=palette,
            scale=scale,
            on_click=lambda _button: self._apply(),
        )
        self.preset_name = LineEdit(
            "",
            placeholder="Preset Name",
            align="left",
            palette=palette,
            scale=scale,
        )
        self.save_button = Button(
            "",
            icon="SAVE",
            palette=palette,
            scale=scale,
            on_click=lambda _button: self._save_preset(),
        )
        self.view_button = Button(
            "",
            icon="GRID" if BEaseGlobals.is_grid_mode else "LIST",
            palette=palette,
            scale=scale,
            on_click=lambda _button: self._toggle_view(),
        )
        self.list_view = ListView(
            self._preset_items(),
            mode=ListView.GRID if BEaseGlobals.is_grid_mode else ListView.LIST,
            icon_size=BEaseGlobals.list_item_size,
            palette=palette,
            scale=scale,
            on_item_clicked=None,
            on_context_menu=self._on_context_menu,
            on_reorder=self._on_reorder,
            texture_provider=self._texture_for_item,
            model_generation=self._preset_version,
        )
        self.slider = Slider(
            MIN_SLIDER_VALUE,
            MAX_SLIDER_VALUE,
            BEaseGlobals.list_item_size,
            palette=palette,
            scale=scale,
            on_value_changed=self._on_slider_changed,
        )
        self.splitter = Splitter(
            ratio,
            min_left=SPLITTER_MIN_PANE_WIDTH,
            min_right=SPLITTER_MIN_PANE_WIDTH,
            palette=palette,
            scale=scale,
            on_ratio_changed=self._on_splitter_changed,
        )
        self.layout()

    @property
    def widgets(self):
        return (
            self.copy_button,
            self.reverse_button,
            self.snap_button,
            self.handle_input,
            self.anchor_input,
            self.apply_button,
            self.preset_name,
            self.save_button,
            self.view_button,
            self.list_view,
            self.slider,
            self.splitter,
        )

    @property
    def text_fields(self):
        return self.handle_input, self.anchor_input, self.preset_name

    def set_callbacks(self, actions=None, dirty_callback=None):
        if actions is not None:
            self.actions = dict(actions)
        if dirty_callback is not None:
            self.dirty_callback = dirty_callback

    def _action(self, name, *args):
        callback = self.actions.get(name)
        return callback(*args) if callback is not None else None

    def _dirty(self, reason):
        if self.dirty_callback is None:
            return
        try:
            self.dirty_callback(reason)
        except TypeError:
            self.dirty_callback()

    def _preset_items(self):
        if not isinstance(BEaseGlobals.bezier_preset_list, list):
            BEaseGlobals.bezier_preset_list = []
            mark_preset_model_changed()
        model = BEaseGlobals.bezier_preset_list
        self._preset_version = BEaseGlobals.preset_model_version
        self._preset_signature = self._model_signature(model)
        return model

    @staticmethod
    def _model_signature(model):
        return id(model), tuple(id(record) for record in model)

    def sync_preset_model(self, force=False):

        model = BEaseGlobals.bezier_preset_list
        if not isinstance(model, list):
            model = []
            BEaseGlobals.bezier_preset_list = model
            mark_preset_model_changed()
        signature = self._model_signature(model)
        if (
            force
            or self._preset_version != BEaseGlobals.preset_model_version
            or self._preset_signature != signature
            or self.list_view.items is not model
        ):
            self.list_view.set_items(
                model,
                generation=BEaseGlobals.preset_model_version,
            )
            self._preset_version = BEaseGlobals.preset_model_version
            self._preset_signature = signature
            return True
        return False

    def _notify_preset_model_changed(self):
        mark_preset_model_changed()
        self.sync_preset_model(force=True)

        from . import runtime

        runtime.sync_preset_models()

    def _texture_for_item(self, _index, item):
        return thumbnails.get_thumbnail(
            item.get("curve"),
            colors=self._thumbnail_colors,
        )

    def _copy(self):
        result = self._action("copy")
        normalized = curve_model.validate(result)
        if normalized is None and isinstance(result, (list, tuple)):
            normalized = curve_model.from_legacy(result)
        if normalized is not None:
            BEaseGlobals.curve = curve_model.deep_copy(normalized)
            self._on_curve_changed()

    def _toggle_snap(self, _checked):

        BEaseGlobals.snap_enabled = not bool(BEaseGlobals.snap_enabled)
        self.snap_button.checked = BEaseGlobals.snap_enabled

        from . import runtime

        runtime.sync_snap_buttons()
        curve_changed = False
        if BEaseGlobals.snap_enabled:
            quantized = curve_model.quantize_curve(BEaseGlobals.curve)
            if (
                quantized is not None
                and curve_model.canonical_signature(quantized)
                != curve_model.canonical_signature(BEaseGlobals.curve)
            ):
                BEaseGlobals.curve = curve_model.deep_copy(quantized)
                curve_changed = True
                self.canvas.clear_selection()

                runtime.reset_canvas_selections()
                self._on_curve_changed()
        if not curve_changed:
            runtime.refresh_points()
            if not any(
                instance.window is self
                for instance in runtime.instances().values()
            ):
                self.update_curve_texts()
        self._dirty("snap_enabled")
        self._action("snap_toggle")

    def _curve_field_text(self, field, *, editing=None):
        selected = self.canvas._valid_selected_anchors()
        editing = field.focused if editing is None else bool(editing)
        if field is self.handle_input:
            return (
                textfmt.format_selected_handles(
                    BEaseGlobals.curve,
                    selected,
                    BEaseGlobals.snap_enabled,
                    editing,
                )
                if selected
                else textfmt.format_handles(
                    BEaseGlobals.curve,
                    BEaseGlobals.snap_enabled,
                    editing,
                )
            )
        return (
            textfmt.format_selected_anchors(
                BEaseGlobals.curve,
                selected,
                BEaseGlobals.snap_enabled,
                editing,
            )
            if selected
            else textfmt.format_anchors(
                BEaseGlobals.curve,
                BEaseGlobals.snap_enabled,
                editing,
            )
        )

    def _on_curve_text_focus_in(self, field):

        if self._syncing_curve_text or not field.is_synced:
            return
        self._syncing_curve_text = True
        try:
            field.set_text(
                self._curve_field_text(field, editing=True),
                notify=False,
                synced=True,
            )
        finally:
            self._syncing_curve_text = False

    def _evaluate_curve_text_drafts(self, changed_field):

        if self._syncing_curve_text:
            return False
        before = curve_model.deep_copy(BEaseGlobals.curve)
        before_signature = curve_model.canonical_signature(before)
        fields = {
            "handles": self.handle_input,
            "anchors": self.anchor_input,
        }
        drafts = {
            name: field.draft
            for name, field in fields.items()
        }
        sync_states = {
            name: field.is_synced
            for name, field in fields.items()
        }
        try:
            return self._evaluate_curve_text_drafts_unchecked(changed_field)
        except Exception:
            traceback.print_exc()
            if (
                curve_model.canonical_signature(BEaseGlobals.curve)
                != before_signature
            ):
                BEaseGlobals.curve = before
            for name, field in fields.items():
                field.set_text(
                    drafts[name],
                    notify=False,
                    synced=sync_states[name],
                )
            field = fields.get(str(changed_field))
            if field is not None:
                field.set_synced(False)
            return False

    def _evaluate_curve_text_drafts_unchecked(self, changed_field):
        evaluation = textfmt.evaluate_curve_drafts(
            self.handle_input.draft,
            self.anchor_input.draft,
            BEaseGlobals.curve,
            changed_field,
            self.canvas._valid_selected_anchors(),
        )
        if evaluation.reset:
            self.update_curve_texts()
            return True
        if not evaluation.applied or evaluation.curve is None:
            return False

        parsed = evaluation.curve
        if BEaseGlobals.snap_enabled:
            parsed = curve_model.quantize_curve(parsed)
        if parsed is None:
            return False

        field_map = {
            "handles": self.handle_input,
            "anchors": self.anchor_input,
        }
        for field_name, field in field_map.items():
            field.set_synced(field_name in evaluation.synced_fields)

        previous = BEaseGlobals.curve
        curve_changed = (
            curve_model.canonical_signature(parsed)
            != curve_model.canonical_signature(previous)
        )
        if not curve_changed:
            return True

        previous_count = len(previous["anchors"])
        BEaseGlobals.curve = curve_model.deep_copy(parsed)

        from . import runtime

        runtime.reset_other_canvas_selections(self)
        if len(parsed["anchors"]) != previous_count:
            self.canvas.clear_selection()
        self._on_curve_changed(preserve_drafts=True)
        return True

    def _on_handle_text_changed(self, _text):
        self._evaluate_curve_text_drafts("handles")

    def _on_anchor_text_changed(self, _text):
        self._evaluate_curve_text_drafts("anchors")

    def _on_canvas_points_changed(self):
        self._on_curve_changed()

    def _on_canvas_selection_changed(self):

        self.handle_input.blur()
        self.anchor_input.blur()
        self.update_curve_texts()

    def _on_curve_changed(self, preserve_drafts=False):


        from . import runtime

        runtime.refresh_points(
            preserve_window=self if preserve_drafts else None,
        )
        if not any(
            instance.window is self
            for instance in runtime.instances().values()
        ) and not preserve_drafts:
            self.update_curve_texts()
        self._dirty("curve")

    def update_curve_texts(self, preserve_field=None):

        if self._syncing_curve_text:
            return False
        self._syncing_curve_text = True
        try:
            changed = False
            selected = self.canvas._valid_selected_anchors()
            prefix = textfmt.format_selection_prefix(selected)
            changed = self.handle_input.set_prefix(prefix) or changed
            changed = self.anchor_input.set_prefix(prefix) or changed
            if preserve_field is not self.handle_input:
                changed = self.handle_input.set_text(
                    self._curve_field_text(self.handle_input),
                    notify=False,
                    synced=True,
                ) or changed
            if preserve_field is not self.anchor_input:
                changed = self.anchor_input.set_text(
                    self._curve_field_text(self.anchor_input),
                    notify=False,
                    synced=True,
                ) or changed
            return changed
        finally:
            self._syncing_curve_text = False

    def update_points_text(self):

        return self.update_curve_texts()

    def _apply(self):

        self.handle_input.blur()
        self.anchor_input.blur()
        self.update_curve_texts()
        return self._action("apply")

    def _save_preset(self):
        preset = {
            "name": self.preset_name.text.strip(),
            "curve": curve_model.deep_copy(BEaseGlobals.curve),
        }
        if BEaseGlobals.bezier_preset_list is None:
            BEaseGlobals.bezier_preset_list = []
        BEaseGlobals.bezier_preset_list.append(preset)
        self._notify_preset_model_changed()
        self.preset_name.set_text("")
        self._dirty("preset_saved")
        self._action("save_preset")

    def _toggle_view(self):
        BEaseGlobals.is_grid_mode = not bool(BEaseGlobals.is_grid_mode)
        self.list_view.set_mode(
            ListView.GRID if BEaseGlobals.is_grid_mode else ListView.LIST
        )
        self.view_button.icon = "GRID" if BEaseGlobals.is_grid_mode else "LIST"
        self._dirty("is_grid_mode")
        self._action("toggle_view")

    def _on_slider_changed(self, value):
        BEaseGlobals.list_item_size = int(value)
        self.list_view.set_icon_size(value)
        self._dirty("list_item_size")

    def _on_splitter_changed(self, _ratio):
        self.layout()
        self._update_global_splitter()

    def _on_reorder(self, new_order):

        if new_order is not BEaseGlobals.bezier_preset_list:
            BEaseGlobals.bezier_preset_list[:] = new_order
        self._notify_preset_model_changed()
        self._dirty("preset_reordered")

    def _on_context_menu(self, index, x, y):
        self.sync_preset_model()
        if not (0 <= index < len(BEaseGlobals.bezier_preset_list)):
            return
        self._delete_index = index
        record = BEaseGlobals.bezier_preset_list[index]
        self._delete_record = record
        self._pending_rename_record = None

        def selected(menu_index):
            self.context_menu = None
            if menu_index == 0:
                self._delete_index = None
                self._delete_record = None
                self._pending_rename_record = record
            elif menu_index == 1:
                self.open_delete_dialog(record)

        self.context_menu = ContextMenu(
            ["Rename...", "Delete..."],
            x,
            y,
            self.region_size,
            palette=self.palette,
            scale=self.scale,
            on_select=selected,
        )
        self._action("context_menu")

    def open_context_menu(self, index, x=None, y=None):

        self.sync_preset_model()
        if not (0 <= index < len(self.list_view.items)):
            return False
        item_rect = self.list_view._item_rect(index)
        self._on_context_menu(
            index,
            item_rect.x if x is None else x,
            item_rect.y + item_rect.h if y is None else y,
        )
        return True

    def open_delete_dialog(self, target):
        self.sync_preset_model()
        if isinstance(target, int):
            if not (0 <= target < len(BEaseGlobals.bezier_preset_list)):
                return False
            record = BEaseGlobals.bezier_preset_list[target]
            self._delete_index = target
        else:
            record = target
            if not any(
                candidate is record
                for candidate in BEaseGlobals.bezier_preset_list
            ):
                return False
            self._delete_index = next(
                index
                for index, candidate in enumerate(BEaseGlobals.bezier_preset_list)
                if candidate is record
            )
        self._delete_record = record
        texture = self._texture_for_item(self._delete_index, record)

        def result(accepted):
            self.dialog = None
            if accepted:
                resolved_index = next(
                    (
                        index
                        for index, candidate in enumerate(
                            BEaseGlobals.bezier_preset_list
                        )
                        if candidate is record
                    ),
                    None,
                )
                if resolved_index is None:
                    return
                BEaseGlobals.bezier_preset_list.pop(resolved_index)
                self._notify_preset_model_changed()
                self._dirty("preset_deleted")
                self._action("delete_preset")

        self.dialog = ConfirmDialog(
            record.get("name", ""),
            texture,
            self.rect.tuple(),
            palette=self.palette,
            scale=self.scale,
            on_result=result,
        )
        self.dialog.clamp_to_region(*self.region_size)
        return True

    def _update_global_splitter(self):
        ratio = min(max(self.splitter.ratio, 0.0), 1.0)
        BEaseGlobals.splitter_ratio = [ratio, 1.0 - ratio]

    def logical_size(self):
        return [
            float(BEaseGlobals.window_size[0]),
            self.rect.h / self.scale,
        ]

    def apply_palette(self, new_palette, canvas_colors):

        self.palette.update(new_palette)
        self._thumbnail_colors = canvas_colors
        thumbnails.refresh_theme_colors(canvas_colors)
        if self.dialog is not None and self._delete_record is not None:
            self.dialog.texture = self._texture_for_item(
                self._delete_index,
                self._delete_record,
            )

    def set_region_size(self, width, height):
        self.region_size = float(width), float(height)
        if self.context_menu is not None:
            self.context_menu.clamp_to_region(*self.region_size)
        if self.dialog is not None:
            self.dialog.clamp_to_region(*self.region_size)

    def set_scale(self, scale):

        scale = float(scale)
        if scale <= 0.0 or abs(scale - self.scale) <= 1e-9:
            return False
        factor = scale / self.scale
        self.scale = scale
        self.rect.w *= factor
        self.rect.h *= factor
        self.canvas.scale = scale

        self.canvas.pan_x *= factor
        self.canvas.pan_y *= factor
        for widget in self.widgets:
            widget.scale = scale
        self.list_view.set_icon_size(BEaseGlobals.list_item_size)
        self.splitter.min_left = SPLITTER_MIN_PANE_WIDTH * scale
        self.splitter.min_right = SPLITTER_MIN_PANE_WIDTH * scale
        if self.context_menu is not None:
            self.context_menu.rescale(scale, self.region_size)
        if self.dialog is not None:
            self.dialog.rescale(scale, self.region_size)
        self.layout()
        return True

    def set_geometry(self, x, y, width, height):
        self.rect = Rect(
            float(x),
            float(y),
            max(0.0, float(width)),
            max(0.0, float(height)),
        )
        self.layout()

    @staticmethod
    def _hide_widget(widget):

        if hasattr(widget, "blur"):
            widget.blur()
        if hasattr(widget, "cancel_interaction"):
            widget.cancel_interaction()
        if hasattr(widget, "scrollbar_mouse_release"):
            widget.scrollbar_mouse_release()
        widget.visible = False
        widget.hovered = False
        widget.pressed = False
        if hasattr(widget, "dragging"):
            widget.dragging = False
        if hasattr(widget, "dragging_selection"):
            widget.dragging_selection = False
        if hasattr(widget, "last_draw_scissor_rect"):
            widget.last_draw_scissor_rect = None
        widget.set_rect(0.0, 0.0, 0.0, 0.0)

    @staticmethod
    def _show_widget(widget):
        widget.visible = True

    def layout(self):
        scale = self.scale
        body_x = self.rect.x
        body_y = self.rect.y
        body_width = self.rect.w
        body_height = self.rect.h

        splitter_width = 7.0 * scale
        available_width = max(0.0, body_width - splitter_width)
        min_pane = SPLITTER_MIN_PANE_WIDTH * scale
        left_width = available_width * self.splitter.ratio
        if available_width >= 2.0 * min_pane:
            left_width = min(
                max(left_width, min_pane),
                available_width - min_pane,
            )
        else:
            left_width = available_width * 0.5
        right_width = max(0.0, available_width - left_width)
        self.splitter.ratio = (
            left_width / available_width if available_width > 0.0 else 0.5
        )

        left_x = body_x
        right_x = body_x + left_width + splitter_width
        self.splitter.set_rect(
            body_x + left_width,
            body_y,
            splitter_width,
            body_height,
        )
        self.splitter.configure(body_x, body_width)

        margin = 5.0 * scale
        spacing = 2.0 * scale
        content_x = left_x + margin
        content_width = max(0.0, left_width - 2.0 * margin)
        apply_height = APPLY_BUTTON_HEIGHT * scale
        input_height = 17.0 * scale
        button_height = BUTTON_HEIGHT * scale


        show_anchor = True
        show_handle = True
        show_toolbar = True
        self.left_layout_stage = "FULL"

        self._show_widget(self.apply_button)
        self.apply_button.set_rect(
            content_x,
            body_y + margin,
            content_width,
            apply_height,
        )
        cursor_y = body_y + margin + apply_height
        if show_anchor:
            cursor_y += spacing
            self._show_widget(self.anchor_input)
            self.anchor_input.set_rect(
                content_x,
                cursor_y,
                content_width,
                input_height,
            )
            cursor_y += input_height
        else:
            self._hide_widget(self.anchor_input)
        if show_handle:
            cursor_y += spacing
            self._show_widget(self.handle_input)
            self.handle_input.set_rect(
                content_x,
                cursor_y,
                content_width,
                input_height,
            )
            cursor_y += input_height
        else:
            self._hide_widget(self.handle_input)

        button_width = max(0.0, (content_width - 2.0 * spacing) / 3.0)
        toolbar_buttons = (
            self.copy_button,
            self.reverse_button,
            self.snap_button,
        )
        if show_toolbar:
            cursor_y += spacing
            for index, button in enumerate(toolbar_buttons):
                self._show_widget(button)
                button.set_rect(
                    content_x + index * (button_width + spacing),
                    cursor_y,
                    button_width,
                    button_height,
                )
            cursor_y += button_height
        else:
            for button in toolbar_buttons:
                self._hide_widget(button)

        canvas_y = cursor_y + self.CANVAS_GAP * scale
        body_top = body_y + body_height
        minimum_canvas_height = max(1.0, 1.0 * scale)
        if body_top - canvas_y < minimum_canvas_height:
            canvas_y = max(body_y, body_top - minimum_canvas_height)
        self.canvas.visible = True
        self.canvas.set_rect(
            left_x,
            canvas_y,
            left_width,
            max(minimum_canvas_height, body_top - canvas_y),
        )
        self.left_controls_occlusion_rect = Rect(
            left_x,
            body_y,
            left_width,
            max(0.0, canvas_y - body_y),
        )

        right_margin = 5.0 * scale
        right_spacing = 5.0 * scale
        right_content_x = right_x + right_margin
        right_content_width = max(0.0, right_width - 2.0 * right_margin)
        top_height = 22.0 * scale
        icon_button_width = 26.0 * scale
        top_y = body_y + body_height - right_margin - top_height
        view_x = right_content_x + right_content_width - icon_button_width
        save_x = view_x - right_spacing - icon_button_width
        name_width = max(0.0, save_x - right_spacing - right_content_x)
        slider_height = 24.0 * scale
        slider_y = body_y + right_margin
        slider_top = slider_y + slider_height

        list_y = slider_top + right_spacing
        list_top = top_y - right_spacing
        list_height = list_top - list_y


        show_header = True
        show_slider = True
        show_list = list_height > 0.0
        self.right_layout_stage = "FULL" if show_list else "NO_LIST"

        if show_header:
            self.preset_header_row_rect = Rect(
                right_x,
                top_y,
                right_width,
                top_height,
            )
            self._show_widget(self.preset_name)
            self._show_widget(self.save_button)
            self._show_widget(self.view_button)
            self.preset_name.set_rect(
                right_content_x,
                top_y,
                name_width,
                top_height,
            )
            self.save_button.set_rect(
                save_x,
                top_y,
                icon_button_width,
                top_height,
            )
            self.view_button.set_rect(
                view_x,
                top_y,
                icon_button_width,
                top_height,
            )
        else:
            self.preset_header_row_rect = Rect(0.0, 0.0, 0.0, 0.0)
            self._hide_widget(self.preset_name)
            self._hide_widget(self.save_button)
            self._hide_widget(self.view_button)

        if show_slider:
            self.preset_slider_row_rect = Rect(
                right_x,
                slider_y,
                right_width,
                slider_height,
            )
            self._show_widget(self.slider)
            self.slider.set_rect(
                right_content_x,
                slider_y,
                right_content_width,
                slider_height,
            )
        else:
            self.preset_slider_row_rect = Rect(0.0, 0.0, 0.0, 0.0)
            self._hide_widget(self.slider)

        if show_list:
            self._show_widget(self.list_view)
            self.list_view.set_rect(
                right_content_x,
                list_y,
                right_content_width,
                list_height,
            )
        else:
            self._hide_widget(self.list_view)

        self.right_pane_rect = Rect(
            right_x,
            body_y,
            right_width,
            body_height,
        )
        if show_list:
            list_rect = self.list_view.rect
            pane_right = right_x + right_width
            pane_top = body_y + body_height
            list_right = list_rect.x + list_rect.w
            list_rect_top = list_rect.y + list_rect.h
            candidates = (
                Rect(
                    right_x,
                    body_y,
                    right_width,
                    max(0.0, list_rect.y - body_y),
                ),
                Rect(
                    right_x,
                    list_rect_top,
                    right_width,
                    max(0.0, pane_top - list_rect_top),
                ),
                Rect(
                    right_x,
                    list_rect.y,
                    max(0.0, list_rect.x - right_x),
                    list_rect.h,
                ),
                Rect(
                    list_right,
                    list_rect.y,
                    max(0.0, pane_right - list_right),
                    list_rect.h,
                ),
            )
            self.right_occlusion_rects = tuple(
                rect for rect in candidates if rect.w > 0.0 and rect.h > 0.0
            )
        elif right_width > 0.0 and body_height > 0.0:
            self.right_occlusion_rects = (self.right_pane_rect,)
        else:
            self.right_occlusion_rects = ()

    def update_hover(self, x, y):
        changed = self.canvas.set_hover(x, y)
        for widget in self.widgets:
            changed = widget.set_hover(x, y) or changed
        if self.context_menu is not None:
            changed = self.context_menu.set_hover(x, y) or changed
        if self.dialog is not None:
            changed = self.dialog.set_hover(x, y) or changed
        return changed

    def draw(self):

        self.layout()
        thumbnails.begin_draw()
        try:
            self.sync_preset_model()
            draw.scissor_push(*self.rect.tuple())
            try:
                draw.rect(
                    self.rect.x,
                    self.rect.y,
                    self.rect.w,
                    self.rect.h,
                    self.palette["bg"],
                )

                self.canvas.draw()
                if (
                    self.left_controls_occlusion_rect.w > 0.0
                    and self.left_controls_occlusion_rect.h > 0.0
                ):
                    draw.rect(
                        *self.left_controls_occlusion_rect.tuple(),
                        self.palette["bg"],
                    )
                self.copy_button.draw()
                self.reverse_button.draw()
                self.snap_button.draw()
                self.handle_input.draw()
                self.anchor_input.draw()
                self.apply_button.draw()
                self.list_view.draw()

                for cover_rect in self.right_occlusion_rects:
                    draw.rect(*cover_rect.tuple(), self.palette["bg"])
                self.slider.draw()
                self.preset_name.draw()
                self.save_button.draw()
                self.view_button.draw()
                self.splitter.draw()
            finally:
                draw.scissor_pop()


            if self.context_menu is not None:
                self.context_menu.draw()
            if self.dialog is not None:
                self.dialog.draw()
        finally:
            deferred = thumbnails.end_draw()
        return deferred


EditorWindow = PanelWindow
