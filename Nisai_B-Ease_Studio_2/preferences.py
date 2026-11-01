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
from bpy.props import BoolProperty
from bpy.types import AddonPreferences

from . import __package__ as base_package


DEFAULT_SHOW_IN_DOPE_SHEET = True
DEFAULT_SHOW_IN_TIMELINE = False
_registered = False


def _sidebar_visibility_updated(_self, _context):

    try:
        from .ui import runtime

        runtime.on_sidebar_preferences_changed()
    except (AttributeError, ImportError, RuntimeError):
        return


class BEASE2_AddonPreferences(AddonPreferences):
    bl_idname = base_package

    show_in_dope_sheet: BoolProperty(
        name="Show in Dope Sheet",
        description="Show B-Ease in non-Timeline Dope Sheet modes",
        default=DEFAULT_SHOW_IN_DOPE_SHEET,
        update=_sidebar_visibility_updated,
    )
    show_in_timeline: BoolProperty(
        name="Show in Timeline",
        description="Show B-Ease in the Timeline",
        default=DEFAULT_SHOW_IN_TIMELINE,
        update=_sidebar_visibility_updated,
    )

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "show_in_dope_sheet")
        layout.prop(self, "show_in_timeline")
        layout.label(
            text="Graph Editor is always enabled; these options control additional editors."
        )


def get_addon_preferences(context=None):

    root = getattr(context, "preferences", None)
    if root is None:
        root = getattr(bpy.context, "preferences", None)
    addons = getattr(root, "addons", None)
    getter = getattr(addons, "get", None)
    if not callable(getter):
        return None
    addon = getter(base_package)
    return getattr(addon, "preferences", None)


def get_sidebar_visibility(context=None):

    preferences = get_addon_preferences(context)
    return (
        bool(
            getattr(
                preferences,
                "show_in_dope_sheet",
                DEFAULT_SHOW_IN_DOPE_SHEET,
            )
        ),
        bool(
            getattr(
                preferences,
                "show_in_timeline",
                DEFAULT_SHOW_IN_TIMELINE,
            )
        ),
    )


def _class_is_registered():
    try:
        return (
            bpy.types.AddonPreferences.bl_rna_get_subclass_py(
                BEASE2_AddonPreferences.__name__
            )
            is BEASE2_AddonPreferences
        )
    except (AttributeError, ReferenceError):
        return False


def register():
    global _registered
    if _registered and not _class_is_registered():
        _registered = False
    if _registered:
        return
    bpy.utils.register_class(BEASE2_AddonPreferences)
    _registered = True


def unregister():
    global _registered
    if not _registered and not _class_is_registered():
        return
    try:
        bpy.utils.unregister_class(BEASE2_AddonPreferences)
    except Exception:
        if _class_is_registered():
            raise
    _registered = False
