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

from dataclasses import dataclass
import sys

import gpu
from gpu_extras.batch import batch_for_shader

from . import draw, icons


_alpha_texture_shader = None
_alpha_texture_batch = None
SPLITTER_MIN_PANE_WIDTH = 60.0


def _texture_alpha_shader():

    global _alpha_texture_shader
    if _alpha_texture_shader is not None:
        return _alpha_texture_shader
    interface = gpu.types.GPUStageInterfaceInfo(
        "bease2_widget_alpha_texture_interface"
    )
    interface.no_perspective("VEC2", "uv")
    info = gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0, "VEC2", "pos")
    info.vertex_in(1, "VEC2", "texCoord")
    info.vertex_out(interface)
    info.push_constant("VEC2", "viewportSize")
    info.push_constant("VEC4", "rectData")
    info.push_constant("FLOAT", "opacity")
    info.sampler(0, "FLOAT_2D", "image")
    info.fragment_out(0, "VEC4", "fragColor")
    info.vertex_source(
        """
        void main()
        {
            uv = texCoord;
            vec2 pixel = rectData.xy + pos * rectData.zw;
            vec2 ndc = pixel / viewportSize * 2.0 - 1.0;
            gl_Position = vec4(ndc, 0.0, 1.0);
        }
        """
    )
    info.fragment_source(
        """
        void main()
        {
            vec4 sampled = texture(image, uv);
            fragColor = vec4(sampled.rgb, sampled.a * opacity);
        }
        """
    )
    _alpha_texture_shader = gpu.shader.create_from_info(info)
    return _alpha_texture_shader


def _draw_texture_alpha(x, y, width, height, texture, opacity):
    global _alpha_texture_batch
    shader = _texture_alpha_shader()
    if _alpha_texture_batch is None:
        coordinates = (
            (0.0, 0.0),
            (1.0, 0.0),
            (1.0, 1.0),
            (0.0, 1.0),
        )
        _alpha_texture_batch = batch_for_shader(
            shader,
            "TRIS",
            {"pos": coordinates, "texCoord": coordinates},
            indices=((0, 1, 2), (2, 3, 0)),
        )
    viewport = gpu.state.viewport_get()
    previous_blend = gpu.state.blend_get()
    try:
        gpu.state.blend_set("ALPHA")
        shader.bind()
        shader.uniform_float(
            "viewportSize",
            (float(viewport[2]), float(viewport[3])),
        )
        shader.uniform_float(
            "rectData",
            (float(x), float(y), float(width), float(height)),
        )
        shader.uniform_float("opacity", float(opacity))
        shader.uniform_sampler("image", texture)
        _alpha_texture_batch.draw(shader)
    finally:
        gpu.state.blend_set(previous_blend)


def free_resources():

    global _alpha_texture_shader, _alpha_texture_batch
    _alpha_texture_batch = None
    _alpha_texture_shader = None


def _contains(rect, x, y):
    rx, ry, rw, rh = rect
    return rx <= x <= rx + rw and ry <= y <= ry + rh


def _with_alpha(color, alpha):
    return color[0], color[1], color[2], color[3] * alpha


def _baseline(y, height, font_size):

    return y + (height - font_size) * 0.5 + font_size * 0.18


@dataclass
class Rect:


    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    def tuple(self):
        return self.x, self.y, self.w, self.h

    def contains(self, x, y):
        return _contains(self.tuple(), x, y)


class Widget:


    def __init__(self, rect=(0, 0, 0, 0), palette=None, scale=1.0):
        self.rect = Rect(*map(float, rect))
        self.palette = palette or {}
        self.scale = float(scale)
        self.hovered = False
        self.pressed = False
        self.visible = True
        self.enabled = True

    def set_rect(self, x, y, w, h):
        self.rect = Rect(float(x), float(y), float(w), float(h))

    def clamp_to_region(self, width, height):
        width = max(0.0, float(width))
        height = max(0.0, float(height))
        self.rect.w = min(self.rect.w, width)
        self.rect.h = min(self.rect.h, height)
        self.rect.x = min(max(0.0, self.rect.x), max(0.0, width - self.rect.w))
        self.rect.y = min(max(0.0, self.rect.y), max(0.0, height - self.rect.h))

    def hit(self, x, y):
        return self.visible and self.enabled and self.rect.contains(x, y)

    def set_hover(self, x, y):
        hovered = self.hit(x, y)
        changed = hovered != self.hovered
        self.hovered = hovered
        return changed

    def draw(self):
        raise NotImplementedError


class Button(Widget):


    def __init__(
        self,
        label="",
        icon=None,
        *,
        variant="normal",
        checkable=False,
        checked=False,
        on_click=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.label = str(label)
        self.icon = icon.upper() if icon else None
        self.variant = variant
        self.checkable = bool(checkable)
        self.checked = bool(checked)
        self.on_click = on_click
        self._label_fit_reference = None
        self._clip_group_content = False

    def configure_toolbar_label_group(self, fit_reference):

        self._label_fit_reference = fit_reference
        self._clip_group_content = True

    def _label_required_width(self):

        if not self.label:
            return None
        if not self.icon:
            return 0.0
        font_size = 13.0 * self.scale
        icon_width = 16.0 * self.scale
        spacing = 4.0 * self.scale
        horizontal_padding = 4.0 * self.scale
        return (
            icon_width
            + spacing
            + draw.text_width(self.label, font_size)
            + 2.0 * horizontal_padding
        )

    def _label_fits_current_width(self):

        reference = self._label_fit_reference or self
        required_width = reference._label_required_width()
        if required_width is None:
            return False
        return required_width <= reference.rect.w

    def _colors(self):
        palette = self.palette
        selected = self.pressed or self.checked or self.variant in {
            "apply",
            "selected",
        }
        if selected:
            fill = palette["btn_bg_sel"]
            text_color = palette["btn_text_sel"]
            outline = palette["btn_outline"]
            if self.hovered:
                if self.variant in {"apply", "selected"}:
                    fill = palette["btn_bg_sel_hover"]
                    text_color = palette["btn_text_sel_hover"]
                outline = palette["btn_outline_hover"]
        elif self.hovered:
            fill = palette["btn_bg_hover"]
            text_color = palette["btn_text_hover"]
            outline = palette["btn_outline_hover"]
        else:
            fill = palette["btn_bg"]
            text_color = palette["btn_text"]
            outline = palette["btn_outline"]
        return fill, text_color, outline

    def draw(self):
        if not self.visible:
            return
        x, y, width, height = self.rect.tuple()
        fill, text_color, outline = self._colors()
        draw.rounded_rect(
            x,
            y,
            width,
            height,
            self.palette["btn_radius"] * self.scale,
            fill,
            outline,
            1.0 * self.scale,
        )

        font_size = 13.0 * self.scale
        icon_size = min(16.0 * self.scale, max(0.0, height - 4.0 * self.scale))
        spacing = 4.0 * self.scale
        show_label = self._label_fits_current_width()
        text_width = (
            draw.text_width(self.label, font_size) if show_label else 0.0
        )
        content_width = text_width
        if self.icon:
            content_width += icon_size
            if show_label:
                content_width += spacing
        cursor_x = x + (width - content_width) * 0.5
        if self._clip_group_content and show_label:
            draw.scissor_push(x, y, width, height)
        try:
            if self.icon and icon_size > 0.0:
                icons.draw_icon(
                    cursor_x,
                    y + (height - icon_size) * 0.5,
                    icon_size,
                    self.icon,
                    text_color,
                )
                cursor_x += icon_size + (spacing if show_label else 0.0)
            if show_label:
                draw.text(
                    cursor_x,
                    _baseline(y, height, font_size),
                    self.label,
                    font_size,
                    text_color,
                )
        finally:
            if self._clip_group_content and show_label:
                draw.scissor_pop()

    def mouse_press(self, x, y):
        if not self.hit(x, y):
            return False
        self.pressed = True
        return True

    def mouse_release(self, x, y):
        was_pressed = self.pressed
        clicked = was_pressed and self.hit(x, y)
        self.pressed = False
        if clicked:
            if self.checkable:
                self.checked = not self.checked
            if self.on_click is not None:
                self.on_click(self)
        return clicked

    def cancel_press(self):
        self.pressed = False


class LineEdit(Widget):


    def __init__(
        self,
        text="",
        *,
        placeholder="",
        prefix="",
        align="left",
        on_text_changed=None,
        on_focus_in=None,
        on_focus_out=None,
        tracks_draft=False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.text = str(text)
        self.placeholder = str(placeholder)
        self.prefix = str(prefix)
        self.align = align
        self.on_text_changed = on_text_changed
        self.on_focus_in = on_focus_in
        self.on_focus_out = on_focus_out
        self.tracks_draft = bool(tracks_draft)
        self.draft = self.text
        self.is_synced = True
        self.focused = False
        self.caret = len(self.text)
        self.anchor = self.caret
        self.scroll_x = 0.0
        self.dragging_selection = False

    @property
    def selection(self):
        return min(self.anchor, self.caret), max(self.anchor, self.caret)

    def has_selection(self):
        start, end = self.selection
        return start != end

    def selected_text(self):
        start, end = self.selection
        return self.text[start:end]

    def set_text(self, text, *, notify=True, synced=None):
        text = str(text)
        changed = text != self.text
        self.text = text
        self.draft = text
        if self.tracks_draft:
            if synced is not None:
                self.is_synced = bool(synced)
            elif notify and changed:
                self.is_synced = False
        self.caret = min(self.caret, len(text))
        self.anchor = min(self.anchor, len(text))
        self._ensure_caret_visible()
        if notify and changed and self.on_text_changed is not None:
            self.on_text_changed(self.text)
        return changed

    def set_synced(self, synced):

        synced = bool(synced)
        changed = synced != self.is_synced
        self.is_synced = synced
        return changed

    def set_prefix(self, prefix):

        prefix = str(prefix)
        changed = prefix != self.prefix
        self.prefix = prefix
        self._ensure_caret_visible()
        return changed

    def focus(self):
        if self.focused:
            return False
        self.focused = True
        if self.on_focus_in is not None:
            self.on_focus_in()
        self._ensure_caret_visible()
        return True

    def blur(self):
        if not self.focused:
            return False
        self.focused = False
        self.dragging_selection = False
        if self.on_focus_out is not None:
            self.on_focus_out()
        return True

    def _font_size(self):
        return 13.0 * self.scale

    def _padding(self):
        return 4.0 * self.scale

    def _prefix_width(self):
        if not self.prefix:
            return 0.0
        return self._text_width(self.prefix) + 3.0 * self.scale

    def _available_width(self):
        return max(
            0.0,
            self.rect.w - 2.0 * self._padding() - self._prefix_width(),
        )

    def _text_width(self, text=None):
        return draw.text_width(
            self.text if text is None else text,
            self._font_size(),
        )

    def _unscrolled_origin(self):
        padding = self._padding()
        available = self._available_width()
        text_width = self._text_width()
        content_x = self.rect.x + padding + self._prefix_width()
        if self.align == "center" and text_width <= available:
            return content_x + (available - text_width) * 0.5
        return content_x

    def _text_origin(self):
        return self._unscrolled_origin() - self.scroll_x

    def _ensure_caret_visible(self):
        available = self._available_width()
        if available <= 0.0:
            self.scroll_x = 0.0
            return
        full_width = self._text_width()
        if full_width <= available:
            self.scroll_x = 0.0
            return
        caret_width = self._text_width(self.text[: self.caret])
        inset = 1.0 * self.scale
        if caret_width - self.scroll_x > available - inset:
            self.scroll_x = caret_width - available + inset
        elif caret_width - self.scroll_x < inset:
            self.scroll_x = max(0.0, caret_width - inset)
        self.scroll_x = min(max(0.0, self.scroll_x), full_width - available)

    def _index_at(self, x):
        local_x = x - self._text_origin()
        if local_x <= 0.0:
            return 0
        previous_width = 0.0
        for index in range(1, len(self.text) + 1):
            width = self._text_width(self.text[:index])
            if local_x < (previous_width + width) * 0.5:
                return index - 1
            previous_width = width
        return len(self.text)

    def mouse_press(self, x, y, *, shift=False):
        if not self.hit(x, y):
            return False
        self.focus()
        index = self._index_at(x)
        if not shift:
            self.anchor = index
        self.caret = index
        self.dragging_selection = True
        self._ensure_caret_visible()
        return True

    def mouse_move(self, x, _y):
        if not self.dragging_selection:
            return False
        new_caret = self._index_at(x)
        changed = new_caret != self.caret
        self.caret = new_caret
        self._ensure_caret_visible()
        return changed

    def mouse_release(self):
        was_dragging = self.dragging_selection
        self.dragging_selection = False
        return was_dragging

    def _replace_selection(self, replacement):
        start, end = self.selection
        new_text = self.text[:start] + replacement + self.text[end:]
        self.caret = start + len(replacement)
        self.anchor = self.caret
        self.set_text(new_text)
        self._ensure_caret_visible()

    def insert_text(self, string):
        if not self.focused or not string:
            return False
        self._replace_selection(str(string))
        return True

    def backspace(self):
        if not self.focused:
            return False
        if self.has_selection():
            self._replace_selection("")
        elif self.caret > 0:
            self.anchor = self.caret - 1
            self._replace_selection("")
        else:
            return False
        return True

    def delete(self):
        if not self.focused:
            return False
        if self.has_selection():
            self._replace_selection("")
        elif self.caret < len(self.text):
            self.anchor = self.caret + 1
            self._replace_selection("")
        else:
            return False
        return True

    def _move_caret(self, destination, select):
        destination = min(max(0, int(destination)), len(self.text))
        changed = destination != self.caret or (not select and self.anchor != destination)
        self.caret = destination
        if not select:
            self.anchor = destination
        self._ensure_caret_visible()
        return changed

    def left(self, select=False):
        if self.has_selection() and not select:
            return self._move_caret(self.selection[0], False)
        return self._move_caret(self.caret - 1, select)

    def right(self, select=False):
        if self.has_selection() and not select:
            return self._move_caret(self.selection[1], False)
        return self._move_caret(self.caret + 1, select)

    def home(self, select=False):
        return self._move_caret(0, select)

    def end(self, select=False):
        return self._move_caret(len(self.text), select)

    def select_all(self):
        if not self.focused:
            return False
        self.anchor = 0
        self.caret = len(self.text)
        self._ensure_caret_visible()
        return True

    def draw(self):
        if not self.visible:
            return
        palette = self.palette
        if self.focused:
            fill = palette["input_bg_focus"]
            text_color = palette["input_text_focus"]
            outline = palette["input_outline"]
        elif self.hovered:
            fill = palette["input_bg_hover"]
            text_color = palette["input_text_hover"]
            outline = palette["input_outline_hover"]
        else:
            fill = palette["input_bg"]
            text_color = palette["input_text"]
            outline = palette["input_outline"]
        draft_text_color = (
            text_color
            if not self.tracks_draft or self.is_synced
            else _with_alpha(text_color, 0.55)
        )

        x, y, width, height = self.rect.tuple()
        draw.rounded_rect(
            x,
            y,
            width,
            height,
            palette["input_radius"] * self.scale,
            fill,
            outline,
            1.0 * self.scale,
        )

        clip_padding = 2.0 * self.scale
        draw.scissor_push(
            x + clip_padding,
            y + 1.0 * self.scale,
            max(0.0, width - 2.0 * clip_padding),
            max(0.0, height - 2.0 * self.scale),
        )
        try:
            font_size = self._font_size()
            baseline = _baseline(y, height, font_size)
            prefix_width = self._prefix_width()
            if self.prefix:
                draw.text(
                    self.rect.x + self._padding(),
                    baseline,
                    self.prefix,
                    font_size,
                    text_color,
                )
            draw.scissor_push(
                x + clip_padding + prefix_width,
                y + 1.0 * self.scale,
                max(0.0, width - 2.0 * clip_padding - prefix_width),
                max(0.0, height - 2.0 * self.scale),
            )
            origin = self._text_origin()
            has_text_space = self._available_width() > 0.0
            try:
                if self.text and has_text_space:
                    if self.focused and self.has_selection():
                        start, end = self.selection
                        selection_x = origin + self._text_width(self.text[:start])
                        selection_width = (
                            self._text_width(self.text[:end])
                            - self._text_width(self.text[:start])
                        )
                        draw.rect(
                            selection_x,
                            y + 2.0 * self.scale,
                            selection_width,
                            max(0.0, height - 4.0 * self.scale),
                            palette["input_sel_bg"],
                        )
                    draw.text(
                        origin,
                        baseline,
                        self.text,
                        font_size,
                        draft_text_color,
                    )
                elif self.placeholder and not self.focused and has_text_space:
                    draw.text(
                        self.rect.x + self._padding() + prefix_width,
                        baseline,
                        self.placeholder,
                        font_size,
                        _with_alpha(palette["input_text"], 0.5),
                    )
                if self.focused and has_text_space:
                    caret_x = origin + self._text_width(self.text[: self.caret])
                    draw.rect(
                        caret_x,
                        y + 3.0 * self.scale,
                        max(1.0, 1.0 * self.scale),
                        max(0.0, height - 6.0 * self.scale),
                        palette["input_text_focus"],
                    )
            finally:
                draw.scissor_pop()
        finally:
            draw.scissor_pop()


class ListView(Widget):


    LIST = "LIST"
    GRID = "GRID"

    def __init__(
        self,
        items=None,
        *,
        mode=LIST,
        icon_size=50,
        on_item_clicked=None,
        on_context_menu=None,
        on_reorder=None,
        texture_provider=None,
        model_generation=0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.items = items if isinstance(items, list) else list(items or [])
        self.mode = self.GRID if str(mode).upper() == self.GRID else self.LIST
        self.icon_size = float(icon_size) * self.scale
        self.scroll_y = 0.0
        self.hover_index = None
        self.pressed_index = None
        self._items_generation = int(model_generation)
        self._pressed_generation = None
        self._pressed_record = None
        self.on_item_clicked = on_item_clicked
        self.on_context_menu = on_context_menu
        self.on_reorder = on_reorder
        self.texture_provider = texture_provider
        self.dragging = False
        self.drag_start = None
        self.drag_pointer = None
        self.drop_index = None
        self.drop_indicator = None
        self.scrollbar_dragging = False
        self._scrollbar_drag_offset = 0.0

        self.last_draw_scissor_rect = None

    def set_items(self, items, generation=None):
        self.items = items if isinstance(items, list) else list(items or [])
        self._items_generation = (
            self._items_generation + 1
            if generation is None
            else int(generation)
        )
        self.scroll_y = min(self.scroll_y, self.max_scroll())

    def set_mode(self, mode):
        self.mode = self.GRID if str(mode).upper() == self.GRID else self.LIST
        self.cancel_interaction()
        self.scroll_y = min(self.scroll_y, self.max_scroll())

    def set_icon_size(self, logical_size):
        self.icon_size = float(logical_size) * self.scale
        self.scroll_y = min(self.scroll_y, self.max_scroll())

    def _font_size(self):
        return 10.0 * self.scale

    def _row_height(self):
        return self.icon_size + 4.0 * self.scale

    def _grid_metrics(self):
        padding = 2.0 * self.scale
        cell_width = self.icon_size + 2.0 * padding
        cell_height = self.icon_size + self._font_size() + 3.0 * padding
        usable_width = max(1.0, self.rect.w - 2.0 * self.scale)
        columns = max(1, int(usable_width // max(1.0, cell_width)))
        return cell_width, cell_height, columns

    def content_height(self):
        if self.mode == self.LIST:
            return len(self.items) * self._row_height()
        _cell_width, cell_height, columns = self._grid_metrics()
        rows = (len(self.items) + columns - 1) // columns
        return rows * cell_height

    def max_scroll(self):
        return max(0.0, self.content_height() - self.rect.h)

    def _item_rect(self, index):
        if index < 0 or index >= len(self.items):
            return None
        x, y, width, height = self.rect.tuple()
        top = y + height + self.scroll_y
        if self.mode == self.LIST:
            row_height = self._row_height()
            return Rect(
                x + 1.0 * self.scale,
                top - (index + 1) * row_height,
                max(0.0, width - 2.0 * self.scale),
                row_height,
            )
        cell_width, cell_height, columns = self._grid_metrics()
        row = index // columns
        column = index % columns
        return Rect(
            x + 1.0 * self.scale + column * cell_width,
            top - (row + 1) * cell_height,
            cell_width,
            cell_height,
        )

    def index_at(self, x, y):
        if not self.hit(x, y):
            return None
        if self.mode == self.LIST:
            distance = self.rect.y + self.rect.h + self.scroll_y - y
            index = int(distance // max(1.0, self._row_height()))
        else:
            cell_width, cell_height, columns = self._grid_metrics()
            column = int((x - self.rect.x - self.scale) // max(1.0, cell_width))
            row = int(
                (self.rect.y + self.rect.h + self.scroll_y - y)
                // max(1.0, cell_height)
            )
            if column < 0 or column >= columns:
                return None
            index = row * columns + column
        return index if 0 <= index < len(self.items) else None

    def set_hover(self, x, y):
        changed = super().set_hover(x, y)
        hover_index = self.index_at(x, y)
        changed = changed or hover_index != self.hover_index
        self.hover_index = hover_index
        return changed

    def on_wheel(self, delta):
        if not self.visible or self.rect.w <= 0.0 or self.rect.h <= 0.0:
            return False
        if self.max_scroll() <= 0.0:
            return False
        unit = (
            self._row_height()
            if self.mode == self.LIST
            else self._grid_metrics()[1]
        )
        old_scroll = self.scroll_y

        self.scroll_y = min(
            self.max_scroll(),
            max(0.0, self.scroll_y - float(delta) * unit * 3.0),
        )
        changed = self.scroll_y != old_scroll
        if changed and self.dragging and self.drag_pointer is not None:
            self._update_drop_target(*self.drag_pointer)
        return changed

    def mouse_press(self, x, y, button="LEFT"):
        if self.scrollbar_dragging:
            return False
        index = self.index_at(x, y)
        if button == "RIGHT":
            if index is not None and self.on_context_menu is not None:
                self.on_context_menu(index, x, y)
                return True
            return False
        if button != "LEFT" or index is None:
            return False
        self.pressed_index = index
        self._pressed_generation = self._items_generation
        self._pressed_record = self.items[index]
        self.drag_start = (x, y)
        self.drag_pointer = (x, y)
        self.drop_index = index
        self.drop_indicator = None
        return True

    def _list_drop_index(self, x, y):
        pointer_index = self.index_at(
            min(max(x, self.rect.x), self.rect.x + self.rect.w),
            min(max(y, self.rect.y), self.rect.y + self.rect.h),
        )
        if pointer_index is None:
            return 0 if y >= self.rect.y + self.rect.h else len(self.items)
        item_rect = self._item_rect(pointer_index)
        return pointer_index + (
            1 if y < item_rect.y + item_rect.h * 0.5 else 0
        )

    def _grid_drop_target(self, x, y):

        if not self.items:
            return 0, None
        cell_width, cell_height, columns = self._grid_metrics()
        grid_left = self.rect.x + 1.0 * self.scale
        top = self.rect.y + self.rect.h + self.scroll_y
        pointer_y = min(
            max(float(y), self.rect.y),
            self.rect.y + self.rect.h,
        )
        row = int((top - pointer_y) // max(1.0, cell_height))
        row_count = (len(self.items) + columns - 1) // columns
        row = min(max(0, row), row_count - 1)
        row_start = row * columns
        row_length = min(columns, len(self.items) - row_start)

        relative_x = min(
            max(float(x) - grid_left, 0.0),
            row_length * cell_width,
        )
        slot = int(
            relative_x / max(1.0, cell_width) + 0.5
        )
        slot = min(max(0, slot), row_length)
        drop_index = min(len(self.items), row_start + slot)

        row_y = top - (row + 1) * cell_height
        line_x = grid_left + slot * cell_width
        indicator = Rect(
            line_x - 1.0 * self.scale,
            row_y,
            2.0 * self.scale,
            cell_height,
        )
        return drop_index, indicator

    def _update_drop_target(self, x, y):
        if self.mode == self.LIST:
            self.drop_index = self._list_drop_index(x, y)
            self.drop_indicator = None
        else:
            self.drop_index, self.drop_indicator = (
                self._grid_drop_target(x, y)
            )

    def mouse_move(self, x, y):
        if self.scrollbar_dragging:
            return False
        if self.pressed_index is None:
            return False
        self.drag_pointer = (x, y)
        if self.drag_start is not None:
            threshold = 4.0 * self.scale
            if (
                abs(x - self.drag_start[0]) + abs(y - self.drag_start[1])
                >= threshold
            ):
                self.dragging = True
            if self.dragging:
                self._update_drop_target(x, y)
        return True

    def mouse_release(self, x, y):
        if self.scrollbar_dragging:
            return False
        if self.pressed_index is None:
            return False
        source = self.pressed_index
        was_dragging = self.dragging
        clicked_index = self.index_at(x, y)
        identity_matches = (
            self._pressed_generation == self._items_generation
            and 0 <= source < len(self.items)
            and self.items[source] is self._pressed_record
        )
        if not identity_matches:
            self.cancel_interaction()
            return True
        if was_dragging:
            destination = self.drop_index
            if destination is not None:
                destination = min(max(0, destination), len(self.items))
                item = self.items.pop(source)
                if destination > source:
                    destination -= 1
                self.items.insert(destination, item)
                if destination != source and self.on_reorder is not None:
                    self.on_reorder(list(self.items))
        elif clicked_index == source and self.on_item_clicked is not None:
            self.on_item_clicked(source)
        self.cancel_interaction()
        return True

    def cancel_interaction(self):
        self.pressed_index = None
        self._pressed_generation = None
        self._pressed_record = None
        self.dragging = False
        self.drag_start = None
        self.drag_pointer = None
        self.drop_index = None
        self.drop_indicator = None

    def _draw_item(self, index, item_rect, *, lifted=False):
        item = self.items[index]
        palette = self.palette
        selected = index == self.pressed_index and not self.dragging
        hovered = index == self.hover_index and not selected
        if selected:
            background = palette["list_item_bg_sel"]
            text_color = palette["list_item_text_sel"]
        elif hovered:
            background = palette["list_item_hover_bg"]
            text_color = palette["list_item_text_hover"]
        else:
            background = palette["list_item_bg"]
            text_color = palette["list_item_text"]
        if lifted:
            background = _with_alpha(palette["list_item_bg_sel"], 0.5)
            text_color = _with_alpha(palette["list_item_text_sel"], 0.5)

        draw.rounded_rect(
            item_rect.x,
            item_rect.y,
            item_rect.w,
            item_rect.h,
            palette["list_item_radius"] * self.scale,
            background,
            None,
            0.0,
        )
        padding = 2.0 * self.scale
        texture = (
            self.texture_provider(index, item)
            if self.texture_provider is not None
            else item.get("texture")
        )

        def draw_thumbnail(icon_x, icon_y):
            if texture is None:
                placeholder_bg = palette["input_bg"]
                placeholder_outline = palette["input_outline"]
                if lifted:
                    placeholder_bg = _with_alpha(placeholder_bg, 0.5)
                    placeholder_outline = _with_alpha(
                        placeholder_outline,
                        0.5,
                    )
                draw.rounded_rect(
                    icon_x,
                    icon_y,
                    self.icon_size,
                    self.icon_size,
                    palette["input_radius"] * self.scale,
                    placeholder_bg,
                    placeholder_outline,
                    1.0 * self.scale,
                )
            elif lifted:
                _draw_texture_alpha(
                    icon_x,
                    icon_y,
                    self.icon_size,
                    self.icon_size,
                    texture,
                    0.5,
                )
            else:
                draw.texture(
                    icon_x,
                    icon_y,
                    self.icon_size,
                    self.icon_size,
                    texture,
                )

        if self.mode == self.LIST:
            icon_x = item_rect.x + padding
            icon_y = item_rect.y + padding
            draw_thumbnail(icon_x, icon_y)
            text_x = icon_x + self.icon_size + 4.0 * self.scale
            draw.text(
                text_x,
                _baseline(item_rect.y, item_rect.h, self._font_size()),
                item.get("name", ""),
                self._font_size(),
                text_color,
            )
        else:
            icon_x = item_rect.x + (item_rect.w - self.icon_size) * 0.5
            icon_y = item_rect.y + item_rect.h - padding - self.icon_size
            draw_thumbnail(icon_x, icon_y)
            label = str(item.get("name", ""))
            label_width = draw.text_width(label, self._font_size())
            draw.text(
                item_rect.x + (item_rect.w - label_width) * 0.5,
                item_rect.y + padding,
                label,
                self._font_size(),
                text_color,
            )

    def _rect_intersects_view(self, rect):

        return (
            rect.x + rect.w > self.rect.x
            and rect.x < self.rect.x + self.rect.w
            and rect.y + rect.h > self.rect.y
            and rect.y < self.rect.y + self.rect.h
        )

    def _scrollbar_geometry(self):
        if not self.visible or self.rect.w <= 0.0 or self.rect.h <= 0.0:
            return None
        content_height = self.content_height()
        if content_height <= self.rect.h or content_height <= 0.0:
            return None
        bar_width = 12.0 * self.scale
        margin = 2.0 * self.scale
        track_height = max(1.0, self.rect.h - 2.0 * margin)
        handle_height = max(
            12.0 * self.scale,
            track_height * self.rect.h / content_height,
        )
        travel = max(0.0, track_height - handle_height)
        fraction = self.scroll_y / max(1.0, self.max_scroll())
        return Rect(
            self.rect.x + self.rect.w - bar_width + margin,
            self.rect.y + self.rect.h - margin - handle_height - travel * fraction,
            max(0.0, bar_width - 2.0 * margin),
            handle_height,
        )

    def scrollbar_mouse_press(self, x, y):
        scrollbar = self._scrollbar_geometry()
        if scrollbar is None:
            return False
        bar_width = 12.0 * self.scale
        strip = Rect(
            self.rect.x + self.rect.w - bar_width,
            self.rect.y,
            bar_width,
            self.rect.h,
        )
        if not strip.contains(x, y):
            return False

        self.cancel_interaction()
        self.hover_index = None
        self.scrollbar_dragging = True
        if scrollbar.contains(x, y):
            self._scrollbar_drag_offset = (
                float(y) - (scrollbar.y + scrollbar.h * 0.5)
            )
        else:
            self._scrollbar_drag_offset = 0.0
            self._set_scroll_from_scrollbar_pointer(y)
        return True

    def _set_scroll_from_scrollbar_pointer(self, y):
        scrollbar = self._scrollbar_geometry()
        if scrollbar is None:
            return False
        margin = 2.0 * self.scale
        track_height = max(1.0, self.rect.h - 2.0 * margin)
        travel = max(0.0, track_height - scrollbar.h)
        if travel <= 0.0:
            return False
        top_center = (
            self.rect.y + self.rect.h - margin - scrollbar.h * 0.5
        )
        handle_center = float(y) - self._scrollbar_drag_offset
        fraction = min(
            1.0,
            max(0.0, (top_center - handle_center) / travel),
        )
        old_scroll = self.scroll_y
        self.scroll_y = fraction * self.max_scroll()
        return self.scroll_y != old_scroll

    def scrollbar_mouse_move(self, x, y):
        if not self.scrollbar_dragging:
            return False
        return self._set_scroll_from_scrollbar_pointer(y)

    def scrollbar_mouse_release(self):
        was_dragging = self.scrollbar_dragging
        self.scrollbar_dragging = False
        self._scrollbar_drag_offset = 0.0
        return was_dragging

    def draw(self):
        self.last_draw_scissor_rect = None
        if not self.visible or self.rect.w <= 0.0 or self.rect.h <= 0.0:
            return
        draw.rounded_rect(
            self.rect.x,
            self.rect.y,
            self.rect.w,
            self.rect.h,
            self.palette["list_radius"] * self.scale,
            self.palette["list_bg"],
            self.palette["list_outline"],
            1.0 * self.scale,
        )
        scissor_rect = Rect(*self.rect.tuple())
        self.last_draw_scissor_rect = scissor_rect.tuple()
        draw.scissor_push(*self.last_draw_scissor_rect)
        try:
            for index in range(len(self.items)):
                item_rect = self._item_rect(index)
                if not self._rect_intersects_view(item_rect):
                    continue
                if self.dragging and index == self.pressed_index:
                    continue
                draw.scissor_push(*item_rect.tuple())
                try:
                    self._draw_item(index, item_rect)
                finally:
                    draw.scissor_pop()

            if self.dragging and self.pressed_index is not None:
                if self.mode == self.LIST:
                    line_y = self.rect.y + self.rect.h
                    if self.drop_index is not None:
                        if self.drop_index >= len(self.items):
                            line_y = self.rect.y + self.rect.h + self.scroll_y
                            line_y -= len(self.items) * self._row_height()
                        else:
                            target_rect = self._item_rect(self.drop_index)
                            line_y = target_rect.y + target_rect.h
                    draw.rect(
                        self.rect.x + 2.0 * self.scale,
                        line_y - 1.0 * self.scale,
                        max(0.0, self.rect.w - 4.0 * self.scale),
                        2.0 * self.scale,
                        self.palette["list_item_bg_sel"],
                    )
                elif self.drop_indicator is not None:
                    draw.rect(
                        *self.drop_indicator.tuple(),
                        self.palette["list_item_bg_sel"],
                    )
                source_rect = self._item_rect(self.pressed_index)
                if self.mode == self.LIST:
                    lifted_rect = Rect(
                        source_rect.x,
                        self.drag_pointer[1] - source_rect.h * 0.5,
                        source_rect.w,
                        source_rect.h,
                    )
                else:
                    lifted_rect = Rect(
                        self.drag_pointer[0] - source_rect.w * 0.5,
                        self.drag_pointer[1] - source_rect.h * 0.5,
                        source_rect.w,
                        source_rect.h,
                    )
                if self._rect_intersects_view(lifted_rect):
                    draw.scissor_push(*lifted_rect.tuple())
                    try:
                        self._draw_item(
                            self.pressed_index,
                            lifted_rect,
                            lifted=True,
                        )
                    finally:
                        draw.scissor_pop()

            scrollbar = self._scrollbar_geometry()
            if scrollbar is not None:
                draw.rounded_rect(
                    scrollbar.x,
                    scrollbar.y,
                    scrollbar.w,
                    scrollbar.h,
                    3.0 * self.scale,
                    self.palette["scroll_handle"],
                    None,
                    0.0,
                )
        finally:
            draw.scissor_pop()


class Slider(Widget):


    def __init__(
        self,
        minimum=0,
        maximum=100,
        value=0,
        *,
        on_value_changed=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.minimum = int(minimum)
        self.maximum = int(maximum)
        self.value = min(max(int(value), self.minimum), self.maximum)
        self.on_value_changed = on_value_changed
        self.dragging = False

    def _track(self):
        margin = 10.0 * self.scale
        return (
            self.rect.x + margin,
            self.rect.x + max(margin, self.rect.w - margin),
        )

    def _handle_x(self):
        left, right = self._track()
        span = max(1, self.maximum - self.minimum)
        return left + (right - left) * (self.value - self.minimum) / span

    def set_value(self, value, *, notify=True):
        value = min(max(int(round(value)), self.minimum), self.maximum)
        changed = value != self.value
        self.value = value
        if changed and notify and self.on_value_changed is not None:
            self.on_value_changed(value)
        return changed

    def _value_at(self, x):
        left, right = self._track()
        fraction = min(max((x - left) / max(1.0, right - left), 0.0), 1.0)
        return self.minimum + fraction * (self.maximum - self.minimum)

    def mouse_press(self, x, y):
        if not self.hit(x, y):
            return False
        self.dragging = True
        self.set_value(self._value_at(x))
        return True

    def mouse_move(self, x, _y):
        return self.dragging and self.set_value(self._value_at(x))

    def mouse_release(self):
        was_dragging = self.dragging
        self.dragging = False
        return was_dragging

    def draw(self):
        if not self.visible:
            return
        groove_height = 4.0 * self.scale
        left, right = self._track()
        draw.rect(
            left,
            self.rect.y + (self.rect.h - groove_height) * 0.5,
            max(0.0, right - left),
            groove_height,
            self.palette["slider_groove"],
        )
        draw.circle(
            self._handle_x(),
            self.rect.y + self.rect.h * 0.5,
            12.0 * self.scale,
            (
                self.palette["slider_handle_hover"]
                if self.hovered or self.dragging
                else self.palette["slider_handle"]
            ),
        )


class Splitter(Widget):


    def __init__(
        self,
        ratio=0.8,
        *,
        min_left=SPLITTER_MIN_PANE_WIDTH,
        min_right=SPLITTER_MIN_PANE_WIDTH,
        on_ratio_changed=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.ratio = float(ratio)
        self.min_left = float(min_left) * self.scale
        self.min_right = float(min_right) * self.scale
        self.on_ratio_changed = on_ratio_changed
        self.dragging = False
        self.container_x = 0.0
        self.container_width = 0.0

    def configure(self, container_x, container_width):
        self.container_x = float(container_x)
        self.container_width = float(container_width)

    def _set_from_x(self, x):
        available = max(1.0, self.container_width - self.rect.w)
        left = min(
            max(float(x) - self.container_x, self.min_left),
            max(self.min_left, available - self.min_right),
        )
        new_ratio = min(max(left / available, 0.0), 1.0)
        changed = new_ratio != self.ratio
        self.ratio = new_ratio
        if changed and self.on_ratio_changed is not None:
            self.on_ratio_changed(new_ratio)
        return changed

    def mouse_press(self, x, y):
        if not self.hit(x, y):
            return False
        self.dragging = True
        return True

    def mouse_move(self, x, _y):
        return self.dragging and self._set_from_x(x)

    def mouse_release(self):
        was_dragging = self.dragging
        self.dragging = False
        return was_dragging

    def draw(self):
        if self.visible:
            draw.rect(
                self.rect.x,
                self.rect.y,
                self.rect.w,
                self.rect.h,
                self.palette["splitter_color"],
            )


class ContextMenu(Widget):


    def __init__(
        self,
        items,
        x,
        y,
        region_size,
        *,
        icons=None,
        on_select=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.items = [str(item) for item in items]
        if icons is None:
            icon_values = []
        elif isinstance(icons, (str, bytes)):
            icon_values = [icons]
        else:
            try:
                icon_values = list(icons)
            except TypeError:
                icon_values = []
        self.item_icons = [
            str(icon).upper() if icon else None
            for icon in icon_values[: len(self.items)]
        ]
        self.item_icons.extend(
            [None] * (len(self.items) - len(self.item_icons))
        )
        self.has_icon_slot = any(self.item_icons)
        self.on_select = on_select
        self.hover_index = None
        self.pressed_index = None
        font_size = 13.0 * self.scale
        padding = 5.0 * self.scale
        item_height = font_size + 2.0 * padding
        width = max(
            80.0 * self.scale,
            max((draw.text_width(item, font_size) for item in self.items), default=0)
            + 2.0 * padding
            + (20.0 * self.scale if self.has_icon_slot else 0.0),
        )
        height = len(self.items) * item_height
        region_width, region_height = region_size
        left = min(max(0.0, float(x)), max(0.0, region_width - width))

        bottom = min(max(0.0, float(y) - height), max(0.0, region_height - height))
        self.set_rect(left, bottom, width, height)
        self.item_height = item_height

    def rescale(self, scale, region_size):
        factor = float(scale) / self.scale
        self.scale = float(scale)
        self.rect.w *= factor
        self.rect.h *= factor
        self.item_height *= factor
        self.clamp_to_region(*region_size)

    def index_at(self, x, y):
        if not self.hit(x, y):
            return None
        index = int((self.rect.y + self.rect.h - y) // self.item_height)
        return index if 0 <= index < len(self.items) else None

    def set_hover(self, x, y):
        changed = super().set_hover(x, y)
        index = self.index_at(x, y)
        changed = changed or index != self.hover_index
        self.hover_index = index
        return changed

    def mouse_press(self, x, y):
        self.pressed_index = self.index_at(x, y)
        return self.pressed_index is not None

    def mouse_release(self, x, y):
        index = self.index_at(x, y)
        selected = index is not None and index == self.pressed_index
        self.pressed_index = None
        if selected and self.on_select is not None:
            self.on_select(index)
        return selected

    def draw(self):
        draw.rounded_rect(
            self.rect.x,
            self.rect.y,
            self.rect.w,
            self.rect.h,
            self.palette["btn_radius"] * self.scale,
            self.palette["bg"],
            self.palette["btn_outline"],
            1.0 * self.scale,
        )
        padding = 5.0 * self.scale
        font_size = 13.0 * self.scale
        for index, label in enumerate(self.items):
            item_y = self.rect.y + self.rect.h - (index + 1) * self.item_height
            hovered = index == self.hover_index
            if hovered:
                draw.rounded_rect(
                    self.rect.x,
                    item_y,
                    self.rect.w,
                    self.item_height,
                    self.palette["btn_radius"] * self.scale,
                    self.palette["btn_bg_hover"],
                    None,
                    0.0,
                )
            text_x = self.rect.x + padding
            text_color = (
                self.palette["btn_text_hover"]
                if hovered
                else self.palette["text"]
            )
            if self.has_icon_slot:
                icon_size = 16.0 * self.scale
                item_icon = self.item_icons[index]
                if item_icon:
                    icons.draw_icon(
                        text_x,
                        item_y + (self.item_height - icon_size) * 0.5,
                        icon_size,
                        item_icon,
                        text_color,
                    )
                text_x += icon_size + 4.0 * self.scale
            draw.text(
                text_x,
                _baseline(item_y, self.item_height, font_size),
                label,
                font_size,
                text_color,
            )


class ConfirmDialog(Widget):


    def __init__(
        self,
        name,
        texture,
        panel_rect,
        *,
        on_result=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.name = str(name)
        self.texture = texture
        self.on_result = on_result
        self.width = 300.0 * self.scale
        self.height = 180.0 * self.scale
        px, py, pw, ph = panel_rect
        self.set_rect(
            px + (pw - self.width) * 0.5,
            py + (ph - self.height) * 0.5,
            self.width,
            self.height,
        )
        self.yes_button = Button(
            "Yes",
            variant="selected",
            palette=self.palette,
            scale=self.scale,
            on_click=lambda _button: self._result(True),
        )
        self.no_button = Button(
            "No",
            palette=self.palette,
            scale=self.scale,
            on_click=lambda _button: self._result(False),
        )
        self._layout_buttons()

    def _layout_buttons(self):
        button_width = min(80.0 * self.scale, self.rect.w * 0.4)
        button_height = min(24.0 * self.scale, self.rect.h * 0.25)
        spacing = min(8.0 * self.scale, self.rect.w * 0.04)
        right = self.rect.x + self.rect.w - min(
            12.0 * self.scale,
            self.rect.w * 0.06,
        )
        button_y = self.rect.y + min(12.0 * self.scale, self.rect.h * 0.08)
        left_button, right_button = (
            (self.yes_button, self.no_button)
            if sys.platform == "win32"
            else (self.no_button, self.yes_button)
        )
        right_button.set_rect(
            right - button_width,
            button_y,
            button_width,
            button_height,
        )
        left_button.set_rect(
            right - 2.0 * button_width - spacing,
            button_y,
            button_width,
            button_height,
        )

    def clamp_to_region(self, width, height):
        super().clamp_to_region(width, height)
        self.width = self.rect.w
        self.height = self.rect.h
        self._layout_buttons()

    def rescale(self, scale, region_size):
        factor = float(scale) / self.scale
        self.scale = float(scale)
        self.rect.w *= factor
        self.rect.h *= factor
        self.yes_button.scale = self.scale
        self.no_button.scale = self.scale
        self.clamp_to_region(*region_size)

    def _result(self, accepted):
        if self.on_result is not None:
            self.on_result(bool(accepted))

    def set_hover(self, x, y):
        changed = super().set_hover(x, y)
        return (
            self.no_button.set_hover(x, y)
            or self.yes_button.set_hover(x, y)
            or changed
        )

    def mouse_press(self, x, y):
        if self.no_button.mouse_press(x, y):
            return "no"
        if self.yes_button.mouse_press(x, y):
            return "yes"
        return "dialog" if self.hit(x, y) else "outside"

    def mouse_release(self, x, y):
        clicked = self.no_button.mouse_release(x, y)
        clicked = self.yes_button.mouse_release(x, y) or clicked
        return clicked

    def draw(self):
        draw.rounded_rect(
            self.rect.x,
            self.rect.y,
            self.rect.w,
            self.rect.h,
            self.palette["btn_radius"] * self.scale,
            self.palette["bg"],
            self.palette["btn_outline"],
            1.0 * self.scale,
        )
        thumbnail_size = 50.0 * self.scale
        thumbnail_x = self.rect.x + (self.rect.w - thumbnail_size) * 0.5
        thumbnail_y = self.rect.y + self.rect.h - 64.0 * self.scale
        if self.texture is not None:
            draw.texture(
                thumbnail_x,
                thumbnail_y,
                thumbnail_size,
                thumbnail_size,
                self.texture,
            )
        font_size = 13.0 * self.scale
        lines = (self.name, "Delete this preset?")
        text_y = thumbnail_y - 20.0 * self.scale
        for index, line in enumerate(lines):
            width = draw.text_width(line, font_size)
            draw.text(
                self.rect.x + (self.rect.w - width) * 0.5,
                text_y - index * 17.0 * self.scale,
                line,
                font_size,
                self.palette["text"],
            )
        self.no_button.draw()
        self.yes_button.draw()
