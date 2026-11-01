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

import copy
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from ..constants import (
    CURVE_COORD_MAX,
    CURVE_COORD_MIN,
    DEFAULT_IS_GRID_MODE,
    DEFAULT_LIST_ITEM_SIZE,
    DEFAULT_SNAP_ENABLED,
    DEFAULT_SPLITTER_RATIO,
    DEFAULT_WINDOW_SIZE,
    MAX_SLIDER_VALUE,
    MIN_SLIDER_VALUE,
    SETTINGS2_FILE_NAME,
    SETTINGS2_SCHEMA_VERSION,
    SETTINGS_FILE_NAME,
    SPLITTER_RATIO_MAX,
    SPLITTER_RATIO_MIN,
    WINDOW_HEIGHT_MIN,
    WINDOW_SIZE_MAX,
    WINDOW_WIDTH_MIN,
)
from ..core import curve as curve_model


class _InvalidSettings(ValueError):
    pass


class _UnsupportedSettings(ValueError):
    pass


_settings2_contexts = {}
_settings_reset_notices = set()
_LOCK_TIMEOUT_SECONDS = 1.0
_LOCK_POLL_INTERVAL = 0.01


def _is_finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == value
        and value not in (float("inf"), float("-inf"))
    )


def _clamp(value, minimum, maximum):
    return min(max(float(value), float(minimum)), float(maximum))


def _validated_curve_values(value, expected_count):
    if not isinstance(value, (list, tuple)) or len(value) != expected_count:
        return None
    if not all(_is_finite_number(item) for item in value):
        return None
    return [
        _clamp(item, CURVE_COORD_MIN, CURVE_COORD_MAX)
        for item in value
    ]


def _validated_splitter(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(_is_finite_number(item) for item in value):
        return None
    first = float(value[0])
    second = float(value[1])
    total = first + second
    ratio = (
        first / total
        if total > 0.0
        else float(DEFAULT_SPLITTER_RATIO[0])
    )
    clamped = _clamp(ratio, SPLITTER_RATIO_MIN, SPLITTER_RATIO_MAX)
    if clamped != ratio:
        clamped = round(clamped, 12)
        return [clamped, round(1.0 - clamped, 12)]
    if total == 1.0:
        return [first, second]
    return [ratio, 1.0 - ratio]


def _validated_window_size(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(_is_finite_number(item) for item in value):
        return None
    return [
        _clamp(value[0], WINDOW_WIDTH_MIN, WINDOW_SIZE_MAX),
        _clamp(value[1], WINDOW_HEIGHT_MIN, WINDOW_SIZE_MAX),
    ]


def _validated_list_item_size(value):
    if not _is_finite_number(value):
        return None
    return int(_clamp(value, MIN_SLIDER_VALUE, MAX_SLIDER_VALUE))


def _is_valid_preset(preset):

    if not isinstance(preset, dict):
        return False
    if not isinstance(preset.get("name"), str):
        return False

    return _validated_curve_values(preset.get("points"), 4) is not None


def _validated_settings(data):

    if not isinstance(data, dict):
        return {}

    validated = {}
    if (
        "bezier_points" in data
        and isinstance(data["bezier_points"], list)
        and len(data["bezier_points"]) == 4
    ):
        points = _validated_curve_values(data["bezier_points"], 4)
        if points is not None:
            validated["bezier_points"] = points
    if (
        "splitter_ratio" in data
        and isinstance(data["splitter_ratio"], list)
        and len(data["splitter_ratio"]) == 2
    ):
        splitter = _validated_splitter(data["splitter_ratio"])
        if splitter is not None:
            validated["splitter_ratio"] = splitter
    if (
        "window_size" in data
        and isinstance(data["window_size"], list)
        and len(data["window_size"]) == 2
    ):
        window_size = _validated_window_size(data["window_size"])
        if window_size is not None:
            validated["window_size"] = window_size
    if "snap_enabled" in data and isinstance(data["snap_enabled"], bool):
        validated["snap_enabled"] = data["snap_enabled"]
    if "bezier_preset_list" in data and isinstance(
        data["bezier_preset_list"], list
    ):
        validated["bezier_preset_list"] = []
        for preset in data["bezier_preset_list"]:
            if not _is_valid_preset(preset):
                continue
            validated["bezier_preset_list"].append(
                {
                    "name": preset["name"],
                    "points": _validated_curve_values(preset["points"], 4),
                }
            )
    if "list_item_size" in data and isinstance(
        data["list_item_size"], (int, float)
    ):
        item_size = _validated_list_item_size(data["list_item_size"])
        if item_size is not None:
            validated["list_item_size"] = item_size
    if "is_grid_mode" in data and isinstance(data["is_grid_mode"], bool):
        validated["is_grid_mode"] = data["is_grid_mode"]
    return validated


def _generation_fingerprint(file_path, payload):
    try:
        modified = file_path.stat().st_mtime_ns
    except OSError:
        modified = None
    return modified, len(payload), hashlib.sha256(payload).hexdigest()


def _read_validated_generation(file_path, validator):

    try:
        payload = file_path.read_bytes()
    except FileNotFoundError:
        return {"status": "missing", "data": None, "fingerprint": None}
    except PermissionError as error:
        return {
            "status": "permission",
            "data": None,
            "fingerprint": None,
            "error": error,
        }
    except OSError as error:
        return {
            "status": "io_error",
            "data": None,
            "fingerprint": None,
            "error": error,
        }
    fingerprint = _generation_fingerprint(file_path, payload)
    try:
        data = json.loads(payload.decode("utf-8"))
        validated = validator(data)
        if validated is None:
            raise _InvalidSettings("invalid settings schema")
    except _UnsupportedSettings as error:
        return {
            "status": "unsupported",
            "data": None,
            "fingerprint": fingerprint,
            "error": error,
        }
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        _InvalidSettings,
        OverflowError,
        TypeError,
        ValueError,
        RecursionError,
    ) as error:
        return {
            "status": "invalid",
            "data": None,
            "fingerprint": fingerprint,
            "payload": payload,
            "error": error,
        }
    return {
        "status": "valid",
        "data": validated,
        "fingerprint": fingerprint,
        "payload": payload,
    }


def _load_validated_file(file_path, validator):

    backup_path = file_path.with_name(f"{file_path.name}.bak")
    main = _read_validated_generation(file_path, validator)
    outcome = {
        "data": main["data"],
        "source": "main" if main["status"] == "valid" else None,
        "main_fingerprint": main["fingerprint"],
        "reset_required": False,
        "failure_kind": None,
        "status": main["status"],
    }
    if main["status"] == "valid":
        return outcome
    if main["status"] == "unsupported":
        print("B-Ease Studio: Settings were written by a newer version; keeping the file unchanged")
        outcome["failure_kind"] = "unsupported"
        return outcome
    if main["status"] in {"invalid", "missing"}:
        backup = _read_validated_generation(backup_path, validator)
        if main["status"] == backup["status"] == "missing":
            return outcome
        if backup["status"] == "valid":
            print("B-Ease Studio: Recovered settings from backup")
            outcome["data"] = backup["data"]
            outcome["source"] = "backup"
            return outcome
        if backup["status"] == "unsupported":
            print("B-Ease Studio: Settings backup requires a newer version; keeping it unchanged")
            outcome["failure_kind"] = "unsupported"
            return outcome
        if backup["status"] in {"permission", "io_error"}:
            print("B-Ease Studio: Invalid settings file format")
            error = backup.get("error", "unknown backup I/O error")
            print(f"B-Ease Studio: Failed to load settings backup: {error}")
            outcome["failure_kind"] = "transient"
            return outcome
        if backup["status"] not in {"missing"}:
            error = backup.get("error", "invalid backup settings schema")
            print(f"B-Ease Studio: Failed to load settings backup: {error}")
        print("B-Ease Studio: Invalid settings file format. Using default settings")
        outcome["reset_required"] = True
        outcome["failure_kind"] = "corrupt"
    elif main["status"] == "permission":
        print("B-Ease Studio: No permission to read settings file")
        outcome["failure_kind"] = "transient"
    elif main["status"] == "io_error":
        error = main.get("error", "unknown I/O error")
        print(f"B-Ease Studio: Failed to load settings file: {error}")
        outcome["failure_kind"] = "transient"
    return outcome


def load_settings(data_dir):

    file_path = Path(data_dir) / SETTINGS_FILE_NAME
    outcome = _load_validated_file(file_path, _validated_settings)
    return outcome["data"] or {}


def _validated_settings2(data):

    if not isinstance(data, dict):
        return None
    version = data.get("schema_version")
    if isinstance(version, (int, float)) and version > SETTINGS2_SCHEMA_VERSION:
        raise _UnsupportedSettings(f"unsupported schema version: {version}")
    if data.get("schema_version") != SETTINGS2_SCHEMA_VERSION:
        return None

    current_curve = curve_model.validate(data.get("current_curve"))
    if current_curve is None:
        return None
    if not isinstance(data.get("snap_enabled"), bool):
        return None
    if not isinstance(data.get("is_grid_mode"), bool):
        return None

    splitter_ratio = _validated_splitter(data.get("splitter_ratio"))
    window_size = _validated_window_size(data.get("window_size"))
    if splitter_ratio is None or window_size is None:
        return None

    list_item_size = _validated_list_item_size(data.get("list_item_size"))
    if list_item_size is None:
        return None

    raw_presets = data.get("presets")
    if not isinstance(raw_presets, (list, tuple)):
        return None
    presets = []
    for preset in raw_presets:
        if not isinstance(preset, dict):
            continue
        name = preset.get("name")
        preset_curve = curve_model.validate(preset.get("curve"))
        if not isinstance(name, str) or preset_curve is None:
            continue
        presets.append({"name": name, "curve": preset_curve})

    return {
        "schema_version": SETTINGS2_SCHEMA_VERSION,
        "snap_enabled": data["snap_enabled"],
        "splitter_ratio": splitter_ratio,
        "window_size": window_size,
        "list_item_size": list_item_size,
        "is_grid_mode": data["is_grid_mode"],
        "current_curve": current_curve,
        "presets": presets,
    }


def default_settings2_state():

    return {
        "schema_version": SETTINGS2_SCHEMA_VERSION,
        "snap_enabled": DEFAULT_SNAP_ENABLED,
        "splitter_ratio": DEFAULT_SPLITTER_RATIO.copy(),
        "window_size": DEFAULT_WINDOW_SIZE.copy(),
        "list_item_size": DEFAULT_LIST_ITEM_SIZE,
        "is_grid_mode": DEFAULT_IS_GRID_MODE,
        "current_curve": curve_model.new_default_curve(),
        "presets": [],
    }


def _migrate_legacy_settings(legacy):

    migrated = default_settings2_state()

    for key in ("snap_enabled", "is_grid_mode"):
        if isinstance(legacy.get(key), bool):
            migrated[key] = legacy[key]
    for key in ("splitter_ratio", "window_size"):
        pair = (
            _validated_splitter(legacy.get(key))
            if key == "splitter_ratio"
            else _validated_window_size(legacy.get(key))
        )
        if pair is not None:
            migrated[key] = pair
    item_size = _validated_list_item_size(legacy.get("list_item_size"))
    if item_size is not None:
        migrated["list_item_size"] = item_size

    current_curve = curve_model.from_legacy(legacy.get("bezier_points"))
    if current_curve is not None:
        migrated["current_curve"] = current_curve

    migrated_presets = []
    for preset in legacy.get("bezier_preset_list", ()):
        preset_curve = curve_model.from_legacy(preset.get("points"))
        if preset_curve is not None:
            migrated_presets.append(
                {"name": preset["name"], "curve": preset_curve}
            )
    migrated["presets"] = migrated_presets
    return migrated


def _context_key(file_path):
    file_path = file_path.expanduser()
    try:
        directory = file_path.parent.resolve(strict=False)
    except RuntimeError as error:
        raise OSError(f"Unable to resolve settings directory: {file_path.parent}") from error
    return str(directory / file_path.name)


def _set_settings2_context(file_path, base, fingerprint):
    _settings2_contexts[_context_key(file_path)] = {
        "base": copy.deepcopy(base),
        "fingerprint": fingerprint,
    }


def _set_pending_import(file_path):
    _set_settings2_context(file_path, None, None)
    _settings2_contexts[_context_key(file_path)]["pending_import"] = True


def settings2_snapshot(data_dir):

    file_path = Path(data_dir) / SETTINGS2_FILE_NAME
    context = _settings2_contexts.get(_context_key(file_path))
    return None if context is None else copy.deepcopy(context["base"])


def take_settings_reset_notice(data_dir):

    file_path = Path(data_dir) / SETTINGS2_FILE_NAME
    key = _context_key(file_path)
    if key not in _settings_reset_notices:
        return False
    _settings_reset_notices.remove(key)
    return True


def load_settings2(data_dir, legacy_data_dir=None):

    directory = Path(data_dir)
    file_path = directory / SETTINGS2_FILE_NAME
    outcome = _load_validated_file(file_path, _validated_settings2)
    if (
        outcome["status"] != "missing"
        or outcome["source"] is not None
        or outcome["failure_kind"] is not None
    ):
        loaded = outcome["data"]
        if loaded is not None:
            _set_settings2_context(
                file_path,
                loaded,
                outcome["main_fingerprint"],
            )
            return loaded
        if (
            outcome["failure_kind"] in {"transient", "unsupported"}
        ):
            _set_settings2_context(file_path, None, None)
            return None
        defaults = default_settings2_state()
        _set_settings2_context(
            file_path,
            defaults,
            outcome["main_fingerprint"],
        )
        if outcome["reset_required"]:
            _settings_reset_notices.add(_context_key(file_path))
        return {}

    legacy_directory = (
        Path(legacy_data_dir) if legacy_data_dir is not None else directory
    )
    legacy_settings2_path = legacy_directory / SETTINGS2_FILE_NAME
    if legacy_settings2_path != file_path:
        legacy_outcome = _load_validated_file(
            legacy_settings2_path,
            _validated_settings2,
        )
        legacy_settings2 = legacy_outcome["data"]
        if legacy_settings2 is not None:
            _set_settings2_context(file_path, None, None)
            if save_settings2(directory, legacy_settings2):
                print(
                    "B-Ease Studio: Imported earlier settings2.json "
                    "into the extension data directory"
                )
                return settings2_snapshot(directory)
            _set_settings2_context(file_path, legacy_settings2, None)
            return legacy_settings2
        if (
            legacy_outcome["failure_kind"] in {"transient", "unsupported"}
        ):
            _set_pending_import(file_path)
            return None

    legacy_path = legacy_directory / SETTINGS_FILE_NAME
    legacy_outcome = _load_validated_file(legacy_path, _validated_settings)
    legacy = legacy_outcome["data"]
    if legacy is None:
        if (
            legacy_outcome["failure_kind"] == "transient"
        ):
            _set_pending_import(file_path)
            return None
        _set_settings2_context(file_path, default_settings2_state(), None)
        return {}

    migrated = _migrate_legacy_settings(legacy)
    _set_settings2_context(file_path, None, None)
    if save_settings2(directory, migrated):
        print(
            "B-Ease Studio: Imported settings.json into the extension "
            "data directory"
        )
        return settings2_snapshot(directory)
    _set_settings2_context(file_path, migrated, None)
    return migrated


def _three_way_merge_presets(base_presets, local_presets, disk_presets):

    if base_presets is not None and local_presets == base_presets:
        return copy.deepcopy(disk_presets)
    base_by_name = {}
    local_by_name = {}
    disk_by_name = {}
    for record in base_presets or ():
        base_by_name.setdefault(record["name"], []).append(record)
    for record in local_presets:
        local_by_name.setdefault(record["name"], []).append(record)
    for record in disk_presets:
        disk_by_name.setdefault(record["name"], []).append(record)


    unmatched_local_additions = {}
    if base_presets is not None:
        for name, local_records in local_by_name.items():
            base_records = base_by_name.get(name, [])
            if local_records == base_records:
                continue
            remaining_local = list(local_records)
            remaining_disk = list(disk_by_name.get(name, ()))
            for base_record in base_records:
                if base_record not in remaining_local or base_record not in remaining_disk:
                    break
                remaining_local.remove(base_record)
                remaining_disk.remove(base_record)
            else:
                unmatched_local_additions[name] = list(local_records)

    merged = []
    local_ordinals = {}
    for record in local_presets:
        name = record["name"]
        ordinal = local_ordinals.get(name, 0)
        local_ordinals[name] = ordinal + 1
        use_disk = (
            local_by_name[name] == base_by_name.get(name)
            or (base_presets is None and name in disk_by_name)
        )
        if use_disk:
            disk_records = disk_by_name.get(name, ())
            if ordinal < len(disk_records):
                merged.append(copy.deepcopy(disk_records[ordinal]))
            if ordinal == len(local_by_name[name]) - 1:
                merged.extend(copy.deepcopy(disk_records[ordinal + 1:]))
                if base_presets is None:
                    unmatched_disk = list(disk_records)
                    for local_record in local_by_name[name]:
                        if local_record in unmatched_disk:
                            unmatched_disk.remove(local_record)
                        else:
                            merged.append(copy.deepcopy(local_record))
        else:
            merged.append(copy.deepcopy(record))
    base_names = set(base_by_name)
    local_names = set(local_by_name)
    for record in disk_presets:
        name = record["name"]
        if name in unmatched_local_additions:
            unmatched = unmatched_local_additions[name]
            if record in unmatched:
                unmatched.remove(record)
                continue
        elif name in base_names or name in local_names:
            continue
        merged.append(copy.deepcopy(record))
    return merged


def _three_way_merge_settings2(base, local, disk):

    merged = copy.deepcopy(local if base is not None else disk)
    if base is not None:
        for key in merged:
            if key != "presets" and local[key] == base.get(key):
                merged[key] = copy.deepcopy(disk[key])
    merged["presets"] = _three_way_merge_presets(
        None if base is None else base.get("presets", ()),
        local.get("presets", ()),
        disk.get("presets", ()),
    )
    return merged


def _write_json_temporary(directory, file_name, data, allow_nan):
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{file_name}.",
        suffix=".tmp",
        dir=directory,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                indent=2,
                ensure_ascii=False,
                allow_nan=allow_nan,
            )
            file.flush()
            os.fsync(file.fileno())
    except Exception:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise
    return temporary_path


def _write_bytes_temporary(directory, file_name, payload, *, suffix=".tmp"):
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{file_name}.", suffix=suffix, dir=directory,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
    except Exception:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise
    return temporary_path


def _atomic_save_json(
    data_dir,
    file_name,
    data,
    *,
    backup_data=None,
    backup_payload=None,
    allow_nan=True,
):

    temporary_paths = []
    try:
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / file_name
        backup_path = file_path.with_name(f"{file_path.name}.bak")
        main_temporary = _write_json_temporary(
            directory,
            file_name,
            data,
            allow_nan,
        )
        temporary_paths.append(main_temporary)
        if backup_data is not None or backup_payload is not None:
            if backup_payload is None:
                backup_temporary = _write_json_temporary(
                    directory, f"{file_name}.bak", backup_data, allow_nan,
                )
            else:
                backup_temporary = _write_bytes_temporary(
                    directory, f".{file_name}.bak", backup_payload,
                )
            temporary_paths.append(backup_temporary)
            os.replace(backup_temporary, backup_path)
            temporary_paths.remove(backup_temporary)
        os.replace(main_temporary, file_path)
        temporary_paths.remove(main_temporary)
        return True
    except PermissionError:
        print("B-Ease Studio: No permission to save settings file")
    except OSError as error:
        print(f"B-Ease Studio: Failed to save settings file: {error}")
    except Exception as error:
        print(f"B-Ease Studio: An unexpected error occurred: {error}")
    finally:
        for temporary_path in temporary_paths:
            try:
                temporary_path.unlink()
            except OSError:
                pass
    return False


@contextmanager
def _advisory_settings_lock(file_path, timeout=_LOCK_TIMEOUT_SECONDS):

    lock_file = None
    acquired = False
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = file_path.with_name(f".{file_path.name}.lock")
        lock_file = lock_path.open("a+b")
        if os.name == "nt":
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            try:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(
                        lock_file.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    break
                time.sleep(_LOCK_POLL_INTERVAL)
    except OSError:
        pass

    try:
        yield acquired
    finally:
        if acquired and lock_file is not None:
            try:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        if lock_file is not None:
            lock_file.close()


def _save_settings2_locked(data_dir, file_path, data):

    context = _settings2_contexts.get(_context_key(file_path))
    if context is None:
        context = {
            "base": None,
            "fingerprint": None,
        }
    disk = _read_validated_generation(file_path, _validated_settings2)
    if context.get("pending_import") and disk["status"] != "valid":
        print("B-Ease Studio: Earlier settings could not be imported; reload the add-on before saving")
        return False
    if disk["status"] in {"permission", "io_error", "unsupported"}:
        error = disk.get("error", "unknown I/O error")
        print(f"B-Ease Studio: Failed to read settings before save: {error}")
        return False
    disk_data = disk["data"]
    if disk["status"] in {"invalid", "missing"}:
        recovered = _load_validated_file(file_path, _validated_settings2)
        if recovered["failure_kind"] in {"transient", "unsupported"}:
            return False
        disk_data = recovered["data"]
    merged = data
    if (
        disk_data is not None
        and (
            context["base"] is None
            or disk["fingerprint"] != context["fingerprint"]
        )
    ):
        merged = _three_way_merge_settings2(
            context["base"],
            data,
            disk_data,
        )
        merged = _validated_settings2(merged)
        if merged is None:
            print("B-Ease Studio: Refusing to save invalid merged settings2 data")
            return False
    if disk["status"] == "valid" and merged == disk_data:
        _set_settings2_context(file_path, disk_data, disk["fingerprint"])
        return True
    if disk["status"] == "invalid":
        try:
            recovery_path = _write_bytes_temporary(
                file_path.parent, f"{file_path.name}.recovery", disk["payload"],
                suffix=".json",
            )
        except OSError as error:
            print(f"B-Ease Studio: Unable to preserve damaged settings: {error}")
            return False
        print(f"B-Ease Studio: Preserved damaged settings at {recovery_path}")
    if not _atomic_save_json(
        data_dir,
        SETTINGS2_FILE_NAME,
        merged,
        backup_payload=disk.get("payload") if disk["status"] == "valid" else None,
        allow_nan=False,
    ):
        return False
    saved = _read_validated_generation(file_path, _validated_settings2)
    if saved["status"] != "valid":
        print("B-Ease Studio: Failed to verify saved settings2 data")
        return False
    _set_settings2_context(file_path, saved["data"], saved["fingerprint"])
    return True


def save_settings2(data_dir, state_dict):

    try:
        raw_data = {
            "schema_version": SETTINGS2_SCHEMA_VERSION,
            "snap_enabled": state_dict["snap_enabled"],
            "splitter_ratio": state_dict["splitter_ratio"],
            "window_size": state_dict["window_size"],
            "list_item_size": state_dict["list_item_size"],
            "is_grid_mode": state_dict["is_grid_mode"],
            "current_curve": state_dict["current_curve"],
            "presets": state_dict["presets"],
        }
        data = _validated_settings2(raw_data)
    except Exception as error:
        print(f"B-Ease Studio: An unexpected error occurred: {error}")
        return False
    if data is None:
        print("B-Ease Studio: Refusing to save invalid settings2 data")
        return False
    directory = Path(data_dir)
    file_path = directory / SETTINGS2_FILE_NAME
    with _advisory_settings_lock(file_path) as acquired:
        if not acquired:
            print("B-Ease Studio: Settings are busy or cannot be locked; save was not performed")
            return False
        return _save_settings2_locked(data_dir, file_path, data)
