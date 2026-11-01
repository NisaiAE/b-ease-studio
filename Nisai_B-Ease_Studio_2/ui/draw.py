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

import blf
import gpu
from gpu_extras.batch import batch_for_shader

from ..core import bezier


_shader_cache = {}
_batch_cache = {}
_scissor_stack = []


def _viewport_size():

    viewport = gpu.state.viewport_get()
    return float(viewport[2]), float(viewport[3])


def _create_sdf_shader():

    interface = gpu.types.GPUStageInterfaceInfo("bease2_sdf_interface")
    interface.no_perspective("VEC2", "localCoord")

    info = gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0, "VEC2", "pos")
    info.vertex_out(interface)
    info.push_constant("VEC2", "viewportSize")
    info.push_constant("VEC4", "rectData")
    info.push_constant("FLOAT", "radius")
    info.push_constant("FLOAT", "borderWidth")
    info.push_constant("VEC4", "fillColor")
    info.push_constant("VEC4", "borderColor")
    info.fragment_out(0, "VEC4", "fragColor")
    info.vertex_source(
        """
        void main()
        {
            localCoord = pos * rectData.zw;
            vec2 pixel = rectData.xy + localCoord;
            vec2 ndc = pixel / viewportSize * 2.0 - 1.0;
            gl_Position = vec4(ndc, 0.0, 1.0);
        }
        """
    )
    info.fragment_source(
        """
        void main()
        {
            vec2 halfSize = rectData.zw * 0.5;
            vec2 q = abs(localCoord - halfSize)
                     - (halfSize - vec2(radius));
            float distance = length(max(q, vec2(0.0)))
                             + min(max(q.x, q.y), 0.0) - radius;
            float aa = max(fwidth(distance), 0.0001);
            float outerCoverage = 1.0 - smoothstep(-aa, aa, distance);
            float innerCoverage = 1.0 - smoothstep(
                -aa, aa, distance + borderWidth
            );
            vec4 color = mix(borderColor, fillColor, innerCoverage);
            color.a *= outerCoverage;
            fragColor = color;
        }
        """
    )
    return gpu.shader.create_from_info(info)


def _create_tint_shader():

    interface = gpu.types.GPUStageInterfaceInfo("bease2_tint_interface")
    interface.no_perspective("VEC2", "uv")

    info = gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0, "VEC2", "pos")
    info.vertex_in(1, "VEC2", "texCoord")
    info.vertex_out(interface)
    info.push_constant("VEC2", "viewportSize")
    info.push_constant("VEC4", "rectData")
    info.push_constant("VEC4", "tintColor")
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
            float alpha = texture(image, uv).r;
            fragColor = vec4(
                tintColor.rgb,
                tintColor.a * alpha
            );
        }
        """
    )
    return gpu.shader.create_from_info(info)


def _shader(name):

    shader = _shader_cache.get(name)
    if shader is not None:
        return shader

    if name == "SDF":
        shader = _create_sdf_shader()
    elif name == "TINT":
        shader = _create_tint_shader()
    elif name == "IMAGE":
        shader = gpu.shader.from_builtin("IMAGE")
    elif name == "POLYLINE":
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    else:
        raise KeyError(name)

    _shader_cache[name] = shader
    return shader


def _unit_quad(shader, with_uv=False):

    key = "UNIT_QUAD_UV" if with_uv else "UNIT_QUAD"
    batch = _batch_cache.get(key)
    if batch is not None:
        return batch

    coordinates = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    attributes = {"pos": coordinates}
    if with_uv:
        attributes["texCoord"] = coordinates
    batch = batch_for_shader(
        shader,
        "TRIS",
        attributes,
        indices=((0, 1, 2), (2, 3, 0)),
    )
    _batch_cache[key] = batch
    return batch


def _draw_sdf(
    x,
    y,
    width,
    height,
    radius,
    fill_color,
    border_color,
    border_width,
):

    width = float(width)
    height = float(height)
    if width <= 0.0 or height <= 0.0:
        return
    if fill_color is None and border_color is None:
        return

    radius = max(0.0, min(float(radius), width * 0.5, height * 0.5))
    border_width = max(
        0.0,
        min(float(border_width), width * 0.5, height * 0.5),
    )

    if border_color is None:
        border_color = fill_color
        border_width = 0.0
    if fill_color is None:
        fill_color = (0.0, 0.0, 0.0, 0.0)

    shader = _shader("SDF")
    batch = _unit_quad(shader)
    previous_blend = gpu.state.blend_get()
    try:
        gpu.state.blend_set("ALPHA")
        shader.bind()
        shader.uniform_float("viewportSize", _viewport_size())
        shader.uniform_float(
            "rectData",
            (float(x), float(y), width, height),
        )
        shader.uniform_float("radius", radius)
        shader.uniform_float("borderWidth", border_width)
        shader.uniform_float("fillColor", fill_color)
        shader.uniform_float("borderColor", border_color)
        batch.draw(shader)
    finally:
        gpu.state.blend_set(previous_blend)


def rect(x, y, w, h, color):

    _draw_sdf(x, y, w, h, 0.0, color, None, 0.0)


def rounded_rect(
    x,
    y,
    w,
    h,
    radius,
    fill_color,
    border_color=None,
    border_width=1.0,
):

    _draw_sdf(
        x,
        y,
        w,
        h,
        radius,
        fill_color,
        border_color,
        border_width,
    )


def circle(cx, cy, diameter, color):

    diameter = float(diameter)
    if diameter <= 0.0:
        return
    _draw_sdf(
        float(cx) - diameter * 0.5,
        float(cy) - diameter * 0.5,
        diameter,
        diameter,
        diameter * 0.5,
        color,
        None,
        0.0,
    )


def polyline(points, width, color):

    coordinates = [tuple(map(float, point[:2])) for point in points]
    if len(coordinates) < 2 or width <= 0:
        return

    shader = _shader("POLYLINE")
    batch = batch_for_shader(
        shader,
        "LINE_STRIP",
        {"pos": coordinates},
    )
    previous_blend = gpu.state.blend_get()
    try:
        gpu.state.blend_set("ALPHA")
        shader.bind()
        shader.uniform_float("viewportSize", _viewport_size())
        shader.uniform_float("lineWidth", float(width))
        shader.uniform_float("color", color)
        batch.draw(shader)
    finally:
        gpu.state.blend_set(previous_blend)


def _sample_generic_cubic(p0, c1, c2, p1, segments):

    control_points = tuple(
        tuple(map(float, point[:2])) for point in (p0, c1, c2, p1)
    )
    return [
        bezier.cubic_point_generic(control_points, index / segments)
        for index in range(segments + 1)
    ]


def cubic_path(p0, c1, c2, p1, width, color, segments=128):

    segment_count = max(1, int(segments))
    polyline(
        _sample_generic_cubic(p0, c1, c2, p1, segment_count),
        width,
        color,
    )


def text(x, y, string, size_px, color, font_id=0):

    blf.size(font_id, int(round(size_px)))
    blf.position(font_id, float(x), float(y), 0.0)
    blf.color(font_id, *color)
    blf.draw(font_id, str(string))


def text_width(string, size_px, font_id=0):

    blf.size(font_id, int(round(size_px)))
    return blf.dimensions(font_id, str(string))[0]


def texture(x, y, w, h, gpu_texture, tint=None):

    width = float(w)
    height = float(h)
    if width <= 0.0 or height <= 0.0 or gpu_texture is None:
        return

    previous_blend = gpu.state.blend_get()
    try:
        gpu.state.blend_set("ALPHA")
        if tint is None:
            shader = _shader("IMAGE")
            batch = _unit_quad(shader, with_uv=True)
            with gpu.matrix.push_pop():
                gpu.matrix.translate((float(x), float(y)))
                gpu.matrix.scale((width, height))
                shader.bind()
                shader.uniform_sampler("image", gpu_texture)
                batch.draw(shader)
        else:
            shader = _shader("TINT")
            batch = _unit_quad(shader, with_uv=True)
            shader.bind()
            shader.uniform_float("viewportSize", _viewport_size())
            shader.uniform_float(
                "rectData",
                (float(x), float(y), width, height),
            )
            shader.uniform_float("tintColor", tint)
            shader.uniform_sampler("image", gpu_texture)
            batch.draw(shader)
    finally:
        gpu.state.blend_set(previous_blend)


def _intersect_rect(first, second):

    left = max(first[0], second[0])
    bottom = max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    top = min(first[1] + first[3], second[1] + second[3])
    return (
        int(left),
        int(bottom),
        max(0, int(right - left)),
        max(0, int(top - bottom)),
    )


def scissor_push(x, y, w, h):

    viewport = gpu.state.viewport_get()

    left = math.ceil(viewport[0] + float(x))
    bottom = math.ceil(viewport[1] + float(y))
    right = math.floor(viewport[0] + float(x) + max(0.0, float(w)))
    top = math.floor(viewport[1] + float(y) + max(0.0, float(h)))
    requested = (
        left,
        bottom,
        max(0, right - left),
        max(0, top - bottom),
    )
    parent_rect = (
        _scissor_stack[-1]
        if _scissor_stack
        else tuple(map(int, viewport))
    )
    clipped = _intersect_rect(parent_rect, requested)
    try:
        gpu.state.scissor_test_set(True)
        gpu.state.scissor_set(*clipped)
    except Exception:
        try:
            if _scissor_stack:
                gpu.state.scissor_set(*_scissor_stack[-1])
                gpu.state.scissor_test_set(True)
            else:
                gpu.state.scissor_test_set(False)
        except Exception:
            pass
        raise
    _scissor_stack.append(clipped)


def scissor_suspend():

    gpu.state.scissor_test_set(False)


def scissor_restore():

    if _scissor_stack:
        gpu.state.scissor_set(*_scissor_stack[-1])
        gpu.state.scissor_test_set(True)
    else:
        gpu.state.scissor_test_set(False)


def scissor_pop():

    if not _scissor_stack:
        raise RuntimeError("scissor_pop() has no matching push")
    if len(_scissor_stack) > 1:
        gpu.state.scissor_set(*_scissor_stack[-2])
        gpu.state.scissor_test_set(True)
    else:
        gpu.state.scissor_test_set(False)
    _scissor_stack.pop()


def free_resources():

    while _scissor_stack:
        scissor_pop()
    _batch_cache.clear()
    _shader_cache.clear()
