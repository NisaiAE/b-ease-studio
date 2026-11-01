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

from . import properties


bl_info = {
    "name": "B-Ease Studio 2",
    "author": "Nisai",
    "version": (2, 0, 0),
    "blender": (4, 2, 0),
    "location": "Graph Editor, Dope Sheet, Timeline > Sidebar > B-Ease",
    "description": "F-Curve Preset Manager.",
    "category": "Animation",
}


_registration_active = False
_cleanup_pending = False


def _cleanup_resources(*, save):
    from . import header, preferences
    from .ops import operators
    from .ui import events, runtime

    failures = []

    def cleanup(label, callback, *, require_success=False):
        try:
            result = callback()
            if require_success and result is False:
                raise RuntimeError("Failed to save settings")
        except Exception as error:
            failures.append((label, error))

    if save:
        cleanup("save data", properties.save_data, require_success=True)
    cleanup("unregister header", header.unregister)
    cleanup("close runtime sidebars", lambda: runtime.close_all(None))
    cleanup("unregister runtime handlers", runtime.unregister_handlers)
    cleanup("unregister events", events.unregister)
    cleanup("unregister operators", operators.unregister)
    cleanup("unregister preferences", preferences.unregister)
    cleanup("unregister properties", properties.unregister)
    return failures


def _cleanup_error(failures):
    details = "; ".join(
        f"{label}: {type(error).__name__}: {error}"
        for label, error in failures
    )
    return RuntimeError(
        f"B-Ease Studio unregister failed after all cleanup steps: {details}"
    )


def register():

    global _cleanup_pending, _registration_active
    from . import header, preferences
    from .ops import operators
    from .ui import events, runtime

    if _registration_active:
        return
    if _cleanup_pending:
        failures = _cleanup_resources(save=False)
        if failures:
            raise _cleanup_error(failures)
        _cleanup_pending = False
    try:
        properties.register()
        preferences.register()
        operators.register()
        events.register()
        runtime.register_handlers()
        header.register()
        properties.load_data()
        _registration_active = True
    except Exception as registration_error:
        failures = _cleanup_resources(save=False)
        _cleanup_pending = bool(failures)
        if failures:
            raise registration_error from _cleanup_error(failures)
        raise


def unregister():

    global _cleanup_pending, _registration_active
    _registration_active = False

    failures = _cleanup_resources(save=True)
    _cleanup_pending = bool(failures)
    if failures:
        raise _cleanup_error(failures)


if __name__ == "__main__":
    register()
