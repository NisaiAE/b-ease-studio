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

import base64
import zlib

import gpu

from . import draw
from .icons_data import ICON_ALPHA, SIZES


_texture_cache = {}


def _select_size(size_px):

    requested = max(1.0, float(size_px))
    for available_size in sorted(SIZES):
        if available_size >= requested:
            return available_size
    return max(SIZES)


def _decode_alpha(name, size):

    compressed = base64.b64decode(ICON_ALPHA[name][size])
    alpha = zlib.decompress(compressed)
    expected_length = size * size
    if len(alpha) != expected_length:
        raise ValueError(
            f"Invalid icon data length: {name} {size}px "
            f"({len(alpha)} != {expected_length})"
        )


    rows = [
        alpha[row * size : (row + 1) * size]
        for row in range(size)
    ]
    return b"".join(reversed(rows))


def get_icon_texture(name, size_px):

    normalized_name = str(name).upper()
    if normalized_name not in ICON_ALPHA:
        raise KeyError(f"Undefined icon: {name}")

    actual_size = _select_size(size_px)
    cache_key = (normalized_name, actual_size)
    texture = _texture_cache.get(cache_key)
    if texture is None:
        alpha = _decode_alpha(normalized_name, actual_size)

        buffer = gpu.types.Buffer(
            "FLOAT",
            actual_size * actual_size,
            [value / 255.0 for value in alpha],
        )
        texture = gpu.types.GPUTexture(
            (actual_size, actual_size),
            format="R8",
            data=buffer,
        )
        _texture_cache[cache_key] = texture
    return texture, actual_size


def draw_icon(x, y, size, name, color):

    texture, _actual_size = get_icon_texture(name, size)
    draw.texture(x, y, size, size, texture, tint=color)


def free_icons():


    _texture_cache.clear()
