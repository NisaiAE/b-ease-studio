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

import bpy

_addon_keymaps = []


_KEYMAP_SPECS = (
    ("LEFTMOUSE", "PRESS", {}),
    ("LEFTMOUSE", "PRESS", {"shift": True}),
    ("LEFTMOUSE", "PRESS", {"ctrl": True}),
    ("LEFTMOUSE", "PRESS", {"oskey": True}),
    ("LEFTMOUSE", "DOUBLE_CLICK", {}),
    ("MIDDLEMOUSE", "PRESS", {}),
    ("MIDDLEMOUSE", "PRESS", {"shift": True}),
    ("RIGHTMOUSE", "PRESS", {}),
    ("MOUSEMOVE", "ANY", {}),
    ("WHEELUPMOUSE", "PRESS", {}),
    ("WHEELDOWNMOUSE", "PRESS", {}),

    ("TRACKPADPAN", "ANY", {}),
    ("DEL", "PRESS", {}),
    ("BACK_SPACE", "PRESS", {}),


    ("X", "PRESS", {}),
    ("A", "PRESS", {}),
    ("A", "PRESS", {"alt": True}),
    ("V", "PRESS", {}),
    ("HOME", "PRESS", {}),
)

EXPECTED_KEYMAP_ITEM_COUNT = len(_KEYMAP_SPECS)


def registered_keymap_item_count():
    return len(_addon_keymaps)


def active_keymap_item_count():
    count = 0
    for _keymap, item in tuple(_addon_keymaps):
        try:
            count += int(item.active)
        except ReferenceError:
            pass
    return count


def _keymap_item_exists(keymap, item):
    try:
        item_pointer = int(item.as_pointer())
    except (AttributeError, ReferenceError, TypeError):
        return False
    try:
        return any(
            int(candidate.as_pointer()) == item_pointer
            for candidate in keymap.keymap_items
        )
    except (AttributeError, ReferenceError, TypeError):
        return False


def register():
    _addon_keymaps[:] = [
        (keymap, item)
        for keymap, item in _addon_keymaps
        if _keymap_item_exists(keymap, item)
    ]
    if _addon_keymaps:
        return
    window_manager = getattr(bpy.context, "window_manager", None)
    keyconfigs = getattr(window_manager, "keyconfigs", None)
    addon_config = getattr(keyconfigs, "addon", None)
    if addon_config is None:
        return

    keymap = addon_config.keymaps.new(
        name="View2D Buttons List",
        space_type="EMPTY",
        region_type="WINDOW",
    )
    try:
        for event_type, value, modifiers in _KEYMAP_SPECS:
            item = keymap.keymap_items.new(
                "blenderease2.route_event",
                event_type,
                value,
                **modifiers,
            )
            item.active = True
            _addon_keymaps.append((keymap, item))
    except Exception:
        unregister()
        raise


def unregister():
    failures = []
    failed_items = []
    for keymap, item in reversed(tuple(_addon_keymaps)):
        if not _keymap_item_exists(keymap, item):
            continue
        try:
            keymap.keymap_items.remove(item)
        except Exception as error:
            if _keymap_item_exists(keymap, item):
                failures.append(error)
                failed_items.append((keymap, item))
    _addon_keymaps[:] = reversed(failed_items)
    if failures:
        details = "; ".join(
            f"{type(error).__name__}: {error}" for error in failures
        )
        raise RuntimeError(
            "Keymap cleanup failed after all items were tried: " + details
        )
