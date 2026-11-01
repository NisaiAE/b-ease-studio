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

import re

import bpy

from ..constants import DEFAULT_COLORS, DEFAULT_ICON_COLOR, DEFAULT_QT_COLORS


_COLOR_PATTERN = re.compile(r"rgba?\(([^)]+)\)")


_HOST_EDGE_DARKEN = 24


def _rgba8(color):

    if hasattr(color, "r") and hasattr(color, "g") and hasattr(color, "b"):
        return (
            int(color.r * 255),
            int(color.g * 255),
            int(color.b * 255),
            255,
        )
    if len(color) >= 3:
        return (
            int(color[0] * 255),
            int(color[1] * 255),
            int(color[2] * 255),
            int(color[3] * 255) if len(color) >= 4 else 255,
        )
    raise TypeError("Unsupported color format")


def _rgba_float(color):

    return tuple(channel / 255.0 for channel in _rgba8(color))


def _parse_default(color_string):

    match = _COLOR_PATTERN.fullmatch(color_string.replace(" ", ""))
    if match is None:
        raise ValueError(f"Invalid default color: {color_string}")

    values = match.group(1).split(",")
    red, green, blue = (int(value) / 255.0 for value in values[:3])
    alpha = float(values[3]) if len(values) == 4 else 1.0
    return red, green, blue, alpha


def brighten(color, factor=1.2):

    channels = [int(channel * 255.0) for channel in color[:3]]
    brightened = [min(255, int(channel * factor)) for channel in channels]
    return (
        brightened[0] / 255.0,
        brightened[1] / 255.0,
        brightened[2] / 255.0,
        color[3],
    )


_brighten = brighten


def _theme_palette():

    theme = bpy.context.preferences.themes[0]
    ui = theme.user_interface

    return {
        "bg": _rgba_float(theme.graph_editor.space.back),
        "text": _rgba_float(ui.wcol_regular.text),
        "btn_bg": _rgba_float(ui.wcol_regular.inner),
        "btn_bg_sel": _rgba_float(ui.wcol_regular.inner_sel),
        "btn_outline": _rgba_float(ui.wcol_regular.outline),
        "btn_text": _rgba_float(ui.wcol_regular.text),
        "btn_text_sel": _rgba_float(ui.wcol_regular.text_sel),
        "btn_radius": int(ui.wcol_regular.roundness * 10),
        "input_bg": _rgba_float(ui.wcol_text.inner),
        "input_bg_focus": _rgba_float(ui.wcol_text.inner_sel),
        "input_outline": _rgba_float(ui.wcol_text.outline),
        "input_text": _rgba_float(ui.wcol_text.text),
        "input_text_focus": _rgba_float(ui.wcol_text.text_sel),
        "input_sel_text": _rgba_float(ui.wcol_text.text_sel),
        "input_sel_bg": _rgba_float(ui.wcol_text.item),
        "input_radius": int(ui.wcol_text.roundness * 10),
        "list_bg": _rgba_float(ui.wcol_box.inner),
        "list_text": _rgba_float(ui.wcol_list_item.text),
        "list_outline": _rgba_float(ui.wcol_box.outline),
        "list_radius": int(ui.wcol_box.roundness * 10),
        "list_item_bg": _rgba_float(ui.wcol_list_item.inner),
        "list_item_bg_sel": _rgba_float(ui.wcol_list_item.inner_sel),
        "list_item_radius": int(ui.wcol_list_item.roundness * 10),
        "list_item_text": _rgba_float(ui.wcol_list_item.text),
        "list_item_text_sel": _rgba_float(ui.wcol_list_item.text_sel),
        "scroll_handle": _rgba_float(ui.wcol_scroll.item),
        "splitter_color": _rgba_float(ui.editor_outline),
        "slider_groove": _rgba_float(ui.wcol_numslider.inner),
        "slider_handle": _rgba_float(ui.wcol_numslider.item),
    }


def _default_palette():

    color_keys = {
        "bg": "bg_color",
        "text": "text_color",
        "btn_bg": "btn_bg",
        "btn_bg_sel": "btn_bg_sel",
        "btn_outline": "btn_outline",
        "btn_text": "btn_text",
        "btn_text_sel": "btn_text_sel",
        "input_bg": "input_bg",
        "input_bg_focus": "input_bg_focus",
        "input_outline": "input_outline",
        "input_text": "input_text",
        "input_text_focus": "input_text_focus",
        "input_sel_text": "input_text_focus",
        "input_sel_bg": "input_text_sel",
        "list_bg": "list_bg",
        "list_text": "list_item_text",
        "list_outline": "list_outline",
        "list_item_bg": "list_item_bg",
        "list_item_bg_sel": "list_item_bg_sel",
        "list_item_text": "list_item_text",
        "list_item_text_sel": "list_item_text_sel",
        "scroll_handle": "scroll_handle",
        "splitter_color": "splitter_color",
        "slider_groove": "slider_groove",
        "slider_handle": "slider_handle",
    }
    palette = {
        key: _parse_default(DEFAULT_COLORS[source_key])
        for key, source_key in color_keys.items()
    }
    palette.update(
        {
            "btn_radius": int(DEFAULT_COLORS["btn_radius"]),
            "input_radius": int(DEFAULT_COLORS["input_radius"]),
            "list_radius": int(DEFAULT_COLORS["list_radius"]),
            "list_item_radius": int(DEFAULT_COLORS["list_item_radius"]),
        }
    )
    return palette


def build_palette():

    try:
        palette = _theme_palette()
    except (AttributeError, IndexError) as error:
        print(
            "B-Ease Studio: Failed to get Blender theme. "
            f"Using default colors: {error}"
        )
        palette = _default_palette()

    palette.update(
        {
            "btn_bg_hover": brighten(palette["btn_bg"]),
            "btn_bg_sel_hover": brighten(palette["btn_bg_sel"]),
            "btn_text_hover": brighten(palette["btn_text"]),
            "btn_text_sel_hover": brighten(palette["btn_text_sel"]),
            "btn_outline_hover": brighten(palette["btn_outline"]),
            "input_bg_hover": brighten(palette["input_bg"]),
            "input_text_hover": brighten(palette["input_text"]),
            "input_outline_hover": brighten(palette["input_outline"]),


            "list_item_hover_bg": (
                palette["list_item_bg_sel"][0],
                palette["list_item_bg_sel"][1],
                palette["list_item_bg_sel"][2],
                0.3,
            ),
            "list_item_text_hover": brighten(palette["list_item_text"]),
            "slider_handle_hover": brighten(palette["slider_handle"]),
            "host_edge": _darken_8bit(
                palette["bg"],
                _HOST_EDGE_DARKEN,
            ),
        }
    )
    return palette


def _default_canvas_colors():

    def convert(key):
        return tuple(channel / 255.0 for channel in DEFAULT_QT_COLORS[key]) + (
            1.0,
        )

    return {
        "bg": convert("graph_editor_bgcolor"),
        "grid": convert("graph_editor_gridcolor"),
        "curve": convert("graph_color"),
        "handle_align": convert("graph_handle_align_color"),
        "handle_vertex": convert("graph_handle_vertex_color"),
    }


def _darken_8bit(color, amount):

    channels = [max(0, int(channel * 255.0) - amount) for channel in color[:3]]
    return (
        channels[0] / 255.0,
        channels[1] / 255.0,
        channels[2] / 255.0,
        color[3],
    )


def get_canvas_colors():

    try:
        theme = bpy.context.preferences.themes[0]
        common = getattr(theme, "common", None)
        curve_theme = (
            common.curves if common is not None else theme.graph_editor
        )
        colors = {
            "bg": _rgba_float(theme.graph_editor.space.back),
            "grid": _rgba_float(theme.graph_editor.grid),
            "curve": _rgba_float(theme.user_interface.axis_x),
            "handle_align": _rgba_float(curve_theme.handle_sel_align),
            "handle_vertex": _rgba_float(
                curve_theme.handle_vertex_select
            ),
        }
    except (AttributeError, IndexError):
        colors = _default_canvas_colors()

    colors["outer_bg"] = _darken_8bit(colors["bg"], 15)
    colors["border"] = _darken_8bit(colors["outer_bg"], 60)
    return colors


def get_icon_color():

    try:
        theme = bpy.context.preferences.themes[0]
        return _rgba_float(theme.user_interface.wcol_regular.text)
    except (AttributeError, IndexError):
        return _parse_default(DEFAULT_ICON_COLOR)
