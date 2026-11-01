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

from .constants import (
    DEFAULT_BEZIER_POINTS,
    DEFAULT_IS_GRID_MODE,
    DEFAULT_LIST_ITEM_SIZE,
    DEFAULT_SNAP_ENABLED,
    DEFAULT_SPLITTER_RATIO,
    DEFAULT_WINDOW_SIZE,
)
from .core import curve as curve_model
from .data.settings_store import (
    default_settings2_state,
    load_settings2,
    save_settings2,
    settings2_snapshot,
    take_settings_reset_notice,
)


class _PresetRecord(dict):


    def __init__(self, name, curve, legacy_points=None):
        normalized = curve_model.validate(curve)
        if not isinstance(name, str) or normalized is None:
            raise ValueError("invalid preset")
        super().__init__(name=name, curve=normalized)
        converted = curve_model.to_legacy(normalized)
        if converted is not None:
            self._legacy_points = converted
        else:
            legacy_curve = curve_model.from_legacy(legacy_points)
            self._legacy_points = (
                curve_model.to_legacy(legacy_curve)
                if legacy_curve is not None
                else DEFAULT_BEZIER_POINTS.copy()
            )

    def __getitem__(self, key):
        if key == "points":
            return self._legacy_points.copy()
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        if key == "points":
            normalized = curve_model.from_legacy(value)
            if normalized is None:
                raise ValueError("invalid legacy preset points")
            super().__setitem__("curve", normalized)
            self._legacy_points = curve_model.to_legacy(normalized)
            return
        if key == "curve":
            normalized = curve_model.validate(value)
            if normalized is None:
                raise ValueError("invalid preset curve")
            super().__setitem__("curve", normalized)
            converted = curve_model.to_legacy(normalized)
            if converted is not None:
                self._legacy_points = converted
            return
        super().__setitem__(key, value)

    def get(self, key, default=None):
        if key == "points":
            return self._legacy_points.copy()
        return super().get(key, default)

    def __contains__(self, key):
        return key == "points" or super().__contains__(key)


def _coerce_preset(record):
    if isinstance(record, _PresetRecord):
        return record
    if not isinstance(record, dict) or not isinstance(record.get("name"), str):
        return None
    normalized = curve_model.validate(record.get("curve"))
    legacy_points = record.get("points")
    if normalized is None:
        normalized = curve_model.from_legacy(legacy_points)
    if normalized is None:
        return None
    return _PresetRecord(record["name"], normalized, legacy_points)


def _normalize_presets(value):

    target = value if isinstance(value, list) else []
    normalized = []
    for record in target:
        converted = _coerce_preset(record)
        if converted is not None:
            normalized.append(converted)
    target[:] = normalized
    return target


class _BEaseGlobalsMeta(type):
    @property
    def curve(cls):
        return curve_model.deep_copy(cls._curve)

    @curve.setter
    def curve(cls, value):
        normalized = curve_model.validate(value)
        if normalized is None:
            raise ValueError("invalid curve")
        cls._curve = normalized
        cls.curve_version += 1
        legacy = curve_model.to_legacy(normalized)
        if legacy is not None:
            cls._legacy_bezier_points = legacy

    @property
    def bezier_points(cls):
        legacy = curve_model.to_legacy(cls._curve)
        if legacy is not None:
            cls._legacy_bezier_points = legacy
        return cls._legacy_bezier_points.copy()

    @bezier_points.setter
    def bezier_points(cls, value):
        normalized = curve_model.from_legacy(value)
        if normalized is None:
            raise ValueError("invalid legacy bezier points")
        cls._curve = normalized
        cls.curve_version += 1
        cls._legacy_bezier_points = curve_model.to_legacy(normalized)

    @property
    def presets(cls):
        return cls._presets

    @presets.setter
    def presets(cls, value):
        cls._presets = _normalize_presets(value)

    @property
    def bezier_preset_list(cls):
        return cls._presets

    @bezier_preset_list.setter
    def bezier_preset_list(cls, value):
        cls._presets = _normalize_presets(value)


class BEaseGlobals(metaclass=_BEaseGlobalsMeta):


    _curve = curve_model.new_default_curve()
    _legacy_bezier_points = DEFAULT_BEZIER_POINTS.copy()
    splitter_ratio = DEFAULT_SPLITTER_RATIO.copy()
    window_size = DEFAULT_WINDOW_SIZE.copy()
    snap_enabled = DEFAULT_SNAP_ENABLED
    current_preset_name = ""
    _presets = []
    preset_model_version = 0
    curve_version = 0
    list_item_size = DEFAULT_LIST_ITEM_SIZE
    is_grid_mode = DEFAULT_IS_GRID_MODE
    settings_reset_notice_pending = False
    settings_load_notice_pending = ""
    settings_access_failed = False


def _state_dict():

    BEaseGlobals.presets = BEaseGlobals.presets
    return {
        "current_curve": BEaseGlobals.curve,
        "splitter_ratio": BEaseGlobals.splitter_ratio,
        "window_size": BEaseGlobals.window_size,
        "snap_enabled": BEaseGlobals.snap_enabled,
        "presets": BEaseGlobals.presets,
        "list_item_size": BEaseGlobals.list_item_size,
        "is_grid_mode": BEaseGlobals.is_grid_mode,
    }


def _apply_loaded_state(state, *, force_presets=False):

    for key in (
        "splitter_ratio",
        "window_size",
        "snap_enabled",
        "list_item_size",
        "is_grid_mode",
    ):
        setattr(BEaseGlobals, key, state[key])
    if (
        curve_model.canonical_signature(BEaseGlobals.curve)
        != curve_model.canonical_signature(state["current_curve"])
    ):
        BEaseGlobals.curve = state["current_curve"]
    presets_changed = list(BEaseGlobals.presets) != list(state["presets"])
    if force_presets or presets_changed:
        BEaseGlobals.presets = state["presets"]
        mark_preset_model_changed()


def load_data():

    from .data.paths import get_data_dir, get_legacy_data_dir

    try:
        data_dir = get_data_dir()
        data = load_settings2(
            data_dir,
            legacy_data_dir=get_legacy_data_dir(),
        )
    except OSError as error:
        print(f"B-Ease Studio: Unable to access settings directory: {error}")
        BEaseGlobals.settings_access_failed = True
        data = None
    if data is None:
        BEaseGlobals.settings_load_notice_pending = (
            "Settings could not be loaded; existing files kept"
        )
        return False
    BEaseGlobals.settings_load_notice_pending = ""
    BEaseGlobals.settings_access_failed = False
    state = default_settings2_state()
    state.update(data)
    _apply_loaded_state(state, force_presets=True)
    BEaseGlobals.current_preset_name = ""
    if take_settings_reset_notice(data_dir):
        BEaseGlobals.settings_reset_notice_pending = True
    return True


def save_data():

    from .data.paths import get_data_dir

    if BEaseGlobals.settings_access_failed:
        print("B-Ease Studio: Reload the add-on after restoring access to its settings directory")
        return False
    try:
        data_dir = get_data_dir()
        saved = save_settings2(data_dir, _state_dict())
    except OSError as error:
        print(f"B-Ease Studio: Unable to access settings directory: {error}")
        return False
    if not saved:
        return False
    merged = settings2_snapshot(data_dir)
    if merged is not None:
        _apply_loaded_state(merged)
    return True


def consume_settings_reset_notice():

    if not BEaseGlobals.settings_reset_notice_pending:
        return False
    BEaseGlobals.settings_reset_notice_pending = False
    return True


def consume_settings_load_notice():
    message = BEaseGlobals.settings_load_notice_pending
    BEaseGlobals.settings_load_notice_pending = ""
    return message


def mark_preset_model_changed():

    BEaseGlobals.preset_model_version += 1
    return BEaseGlobals.preset_model_version


def register():
    pass


def unregister():
    pass
