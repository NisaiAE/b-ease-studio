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

from ..constants import SNAP_GRID_DIVISIONS


def cubic_point_generic(control_points, t):

    inverse_t = 1.0 - t
    coefficients = (
        inverse_t**3,
        3.0 * inverse_t**2 * t,
        3.0 * inverse_t * t**2,
        t**3,
    )
    return tuple(
        sum(
            coefficient * point[axis]
            for coefficient, point in zip(coefficients, control_points)
        )
        for axis in range(len(control_points[0]))
    )


def cubic_derivative_generic(control_points, t):

    inverse_t = 1.0 - t
    return tuple(
        3.0
        * (
            inverse_t**2 * (control_points[1][axis] - control_points[0][axis])
            + 2.0
            * inverse_t
            * t
            * (control_points[2][axis] - control_points[1][axis])
            + t**2 * (control_points[3][axis] - control_points[2][axis])
        )
        for axis in range(len(control_points[0]))
    )


def fit_points_to_square(points, size, padding=0.1):

    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    min_x = min(x_values)
    max_x = max(x_values)
    min_y = min(y_values)
    max_y = max(y_values)

    width = max_x - min_x
    height = max_y - min_y
    min_x -= width * padding
    max_x += width * padding
    min_y -= height * padding
    max_y += height * padding

    scale_x = size / (max_x - min_x) if max_x > min_x else size
    scale_y = size / (max_y - min_y) if max_y > min_y else size
    scale = min(scale_x, scale_y)
    offset_x = (size - (max_x - min_x) * scale) / 2 - min_x * scale
    offset_y = (size - (max_y - min_y) * scale) / 2 - min_y * scale
    return scale, offset_x, offset_y


def cubic_point(points, t):

    x1, y1, x2, y2 = points
    return cubic_point_generic(
        (
            (0.0, 0.0),
            (x1, y1),
            (x2, y2),
            (1.0, 1.0),
        ),
        t,
    )


def sample_curve(points, n=50):

    sampled_points = []
    for index in range(n + 1):
        t = index / n
        x, y = cubic_point(points, t)
        sampled_points.append((x, 1 - y))
    return sampled_points


def curve_bbox_fit(points, size, padding=0.1):

    sampled_points = sample_curve(points)
    return fit_points_to_square(sampled_points, size, padding)


def reverse_points(points):

    x1, y1, x2, y2 = points
    return [1 - x2, 1 - y2, 1 - x1, 1 - y1]


def snap_value(value):

    return round(value * SNAP_GRID_DIVISIONS) / SNAP_GRID_DIVISIONS


def clamp01(value):

    return min(max(value, 0), 1)
