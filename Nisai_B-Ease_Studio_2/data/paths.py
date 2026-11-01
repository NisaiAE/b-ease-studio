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

import os
from pathlib import Path

import bpy

from .. import __package__ as base_package
from ..constants import DATA_DIRECTORY_NAME


def _legacy_data_dir():

    user_path = _absolute_data_path(bpy.utils.resource_path("USER"))
    return user_path / "scripts" / "addons" / DATA_DIRECTORY_NAME


def _absolute_data_path(value):
    if not value or not Path(value).is_absolute():
        raise OSError("Blender did not provide an absolute user data directory")
    return Path(value)


def get_legacy_data_dir():

    override = os.environ.get("BEASE2_LEGACY_DATA_DIR")
    if override:
        return Path(override)
    data_override = os.environ.get("BEASE2_DATA_DIR")
    if data_override:
        return Path(data_override)
    return _legacy_data_dir()


def _is_extension_runtime():

    extension_path_user = getattr(bpy.utils, "extension_path_user", None)
    return (
        isinstance(base_package, str)
        and base_package.startswith("bl_ext.")
        and callable(extension_path_user)
    )


def get_data_dir():

    override = os.environ.get("BEASE2_DATA_DIR")
    if override:
        data_dir = Path(override)
    elif _is_extension_runtime():
        data_dir = _absolute_data_path(
            bpy.utils.extension_path_user(
                base_package,
                path="",
                create=False,
            )
        )
    else:
        data_dir = _legacy_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
