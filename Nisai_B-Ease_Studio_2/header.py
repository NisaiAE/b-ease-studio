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

import bpy

from .ui import runtime


_registered = False


def _header_types():
    return (
        bpy.types.GRAPH_HT_header,
        bpy.types.DOPESHEET_HT_header,
    )


def _header_callbacks(header_type):
    return tuple(header_type._dyn_ui_initialize())


def draw_blenderease_button(self, context):
    if not runtime.host_is_enabled(
        getattr(context, "area", None),
        getattr(context, "space_data", None),
        context,
    ):
        return
    self.layout.operator(
        "blenderease2.open_editor",
        text="B-Ease",
        icon="IPO_BEZIER",
    )


def _registered_header_types():
    return tuple(
        header_type
        for header_type in _header_types()
        if draw_blenderease_button in _header_callbacks(header_type)
    )


def register():
    global _registered
    registered = set(_registered_header_types())
    if _registered and len(registered) != len(_header_types()):
        _registered = False
    if _registered:
        return
    try:
        for header_type in _header_types():
            if header_type in registered:
                continue
            header_type.append(draw_blenderease_button)
            registered.add(header_type)
    except Exception:
        _registered = bool(_registered_header_types())
        raise
    _registered = True


def _remove_header_callback():

    failures = []
    for header_type in reversed(_header_types()):
        if draw_blenderease_button not in _header_callbacks(header_type):
            continue
        try:
            header_type.remove(draw_blenderease_button)
        except Exception as error:
            if draw_blenderease_button in _header_callbacks(header_type):
                failures.append((header_type.__name__, error))
    if failures:
        details = "; ".join(
            f"{name}: {type(error).__name__}: {error}"
            for name, error in failures
        )
        raise RuntimeError("Header callback cleanup failed: " + details)


def unregister():
    global _registered
    if not _registered and not _registered_header_types():
        return
    try:
        _remove_header_callback()
    except Exception:
        if _registered_header_types():
            _registered = True
            raise
    _registered = False
