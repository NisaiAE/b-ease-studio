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

def get_fcurves_for_anim_data(anim_data):

    try:
        action = anim_data.action
        if not action:
            return []

        if hasattr(action, "slots"):
            from bpy_extras.anim_utils import action_get_channelbag_for_slot

            slot = getattr(anim_data, "action_slot", None)
            if slot is None:
                return []
            channelbag = action_get_channelbag_for_slot(action, slot)
            if channelbag:
                return channelbag.fcurves
            return []

        if hasattr(action, "fcurves"):
            return action.fcurves
    except (ImportError, AttributeError, IndexError):
        return []
    return []


def get_fcurves_from_action(action):

    try:
        if not action:
            return []
        if hasattr(action, "slots"):
            from bpy_extras.anim_utils import action_get_channelbag_for_slot

            channelbag = action_get_channelbag_for_slot(action, action.slots[0])
            return channelbag.fcurves if channelbag else []
        if hasattr(action, "fcurves"):
            return action.fcurves
    except (ImportError, AttributeError, IndexError):
        return []
    return []
