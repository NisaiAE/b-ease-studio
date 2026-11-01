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

from collections import OrderedDict

import gpu
from mathutils import Matrix

from ..constants import (
    PREVIEW_PADDING,
    PREVIEW_SIZE,
    THUMBNAIL_CACHE_LIMIT,
)
from ..core import curve as curve_model
from . import draw
from .theme import get_canvas_colors


_thumbnail_cache = OrderedDict()
_cache_limit = THUMBNAIL_CACHE_LIMIT
_active_color_signature = None
_CURVE_SAMPLE_BUDGET = 129
MAX_GENERATIONS_PER_DRAW = 4
_draw_generation_budget = None
_draw_generation_deferred = False


def _color_signature(colors):

    return tuple(
        component
        for key in ("bg", "curve", "handle_align", "handle_vertex")
        for component in colors[key]
    )


def _projection_matrix(width, height):

    return Matrix(
        (
            (2.0 / width, 0.0, 0.0, -1.0),
            (0.0, 2.0 / height, 0.0, -1.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        )
    )


def _image_to_gpu(x_value, y_value):

    return float(x_value), float(PREVIEW_SIZE - y_value)


def _fit_point(point, scale, offset_x, offset_y):
    return _image_to_gpu(
        offset_x + point[0] * scale,
        offset_y + (1.0 - point[1]) * scale,
    )


def _curve_geometry(curve):

    normalized = curve_model.validate(curve)
    if normalized is None:
        raise ValueError("A valid curve is required for a thumbnail")
    fit = curve_model.curve_bbox_fit(
        normalized,
        PREVIEW_SIZE,
        PREVIEW_PADDING,
    )
    if fit is None:
        raise ValueError("Could not fit thumbnail curve")
    scale, offset_x, offset_y = fit
    sampled = tuple(
        _fit_point(point, scale, offset_x, offset_y)
        for point in curve_model.evaluate(
            normalized,
            _CURVE_SAMPLE_BUDGET,
        )
    )
    anchors = tuple(
        _fit_point(
            (anchor["x"], anchor["y"]),
            scale,
            offset_x,
            offset_y,
        )
        for anchor in normalized["anchors"]
    )
    guides = []
    points = [anchors[0]]
    for segment_index in range(len(normalized["segments"])):
        controls = curve_model.absolute_segment(normalized, segment_index)
        out_handle = _fit_point(
            controls[1],
            scale,
            offset_x,
            offset_y,
        )
        in_handle = _fit_point(
            controls[2],
            scale,
            offset_x,
            offset_y,
        )
        guides.extend(
            (
                (anchors[segment_index], out_handle),
                (anchors[segment_index + 1], in_handle),
            )
        )
        points.extend((out_handle, in_handle, anchors[segment_index + 1]))
    return sampled, tuple(guides), tuple(points)


def _render_texture(curve, colors):

    offscreen = gpu.types.GPUOffScreen(PREVIEW_SIZE, PREVIEW_SIZE)
    try:


        draw.scissor_suspend()
        try:
            with offscreen.bind():
                draw.scissor_suspend()
                framebuffer = gpu.state.active_framebuffer_get()
                framebuffer.clear(color=colors["bg"])

                with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                    gpu.matrix.load_matrix(Matrix.Identity(4))
                    gpu.matrix.load_projection_matrix(
                        _projection_matrix(PREVIEW_SIZE, PREVIEW_SIZE)
                    )

                    sampled, guides, points = _curve_geometry(curve)
                    draw.polyline(
                        sampled,
                        6.0,
                        colors["curve"],
                    )
                    for guide in guides:
                        draw.polyline(
                            guide,
                            2.0,
                            colors["handle_align"],
                        )
                    for point in points:
                        draw.circle(
                            point[0],
                            point[1],
                            6.0,
                            colors["handle_vertex"],
                        )

                pixels = framebuffer.read_color(
                    0,
                    0,
                    PREVIEW_SIZE,
                    PREVIEW_SIZE,
                    4,
                    0,
                    "FLOAT",
                )
                pixels.dimensions = PREVIEW_SIZE * PREVIEW_SIZE * 4
                return gpu.types.GPUTexture(
                    (PREVIEW_SIZE, PREVIEW_SIZE),
                    format="RGBA8",
                    data=pixels,
                )
        finally:
            draw.scissor_restore()
    finally:
        offscreen.free()


def begin_draw(generation_limit=MAX_GENERATIONS_PER_DRAW):

    global _draw_generation_budget, _draw_generation_deferred
    _draw_generation_budget = max(0, int(generation_limit))
    _draw_generation_deferred = False


def end_draw():

    global _draw_generation_budget, _draw_generation_deferred
    deferred = _draw_generation_deferred
    _draw_generation_budget = None
    _draw_generation_deferred = False
    return deferred


def get_thumbnail(curve, colors=None):

    global _draw_generation_budget, _draw_generation_deferred
    signature = curve_model.canonical_signature(curve)
    if signature is None:
        raise ValueError("A valid curve is required for a thumbnail")

    colors = get_canvas_colors() if colors is None else colors
    cache_key = (signature, _color_signature(colors))
    texture = _thumbnail_cache.get(cache_key)
    if texture is None:
        if _draw_generation_budget is not None:
            if _draw_generation_budget <= 0:
                _draw_generation_deferred = True
                return None
            _draw_generation_budget -= 1
        texture = _render_texture(curve, colors)
        _thumbnail_cache[cache_key] = texture
        while len(_thumbnail_cache) > max(1, int(_cache_limit)):
            _thumbnail_cache.popitem(last=False)
    else:
        _thumbnail_cache.move_to_end(cache_key)
    return texture


def invalidate_all():

    _thumbnail_cache.clear()


def refresh_theme_colors(colors):

    global _active_color_signature
    signature = _color_signature(colors)
    if signature == _active_color_signature:
        return False
    _active_color_signature = signature
    invalidate_all()
    return True


def free_all():

    global _active_color_signature
    global _draw_generation_budget, _draw_generation_deferred

    invalidate_all()
    _active_color_signature = None
    _draw_generation_budget = None
    _draw_generation_deferred = False
