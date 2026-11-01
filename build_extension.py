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


import argparse
import ast
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from release_lib import (
    ADDON_FILES,
    addon_source_files,
    manifest_metadata,
    reject_symlinks,
    transform_python_tree,
    validate_separate_paths,
    validate_versions_match as _validate_versions_match,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent
SOURCE_DIR = "Nisai_B-Ease_Studio_2"
OUTPUT_DIR = "release"
DEFAULT_SOURCE_DIR = REPOSITORY_ROOT / SOURCE_DIR
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / OUTPUT_DIR
DEFAULT_BLENDER = Path(
    shutil.which("blender") or "/Applications/Blender4.5.app/Contents/MacOS/Blender"
)
REQUIRED_ARCHIVE_FILES = set(ADDON_FILES)


def _assignment_targets_bl_info(node):
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = (node.target,)
    else:
        return False
    return any(
        isinstance(target, ast.Name) and target.id == "bl_info"
        for target in targets
    )


def remove_bl_info_assignment(init_path):

    source = Path(init_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = [
        node for node in tree.body if _assignment_targets_bl_info(node)
    ]
    if len(assignments) != 1:
        raise RuntimeError(
            "Extensions staging expected exactly one top-level bl_info "
            f"assignment, found {len(assignments)}"
        )

    lines = source.splitlines(keepends=True)
    node = assignments[0]
    del lines[node.lineno - 1 : node.end_lineno]
    result = "".join(lines)
    staged_tree = ast.parse(result)
    if any(_assignment_targets_bl_info(item) for item in staged_tree.body):
        raise RuntimeError("Failed to remove bl_info from Extensions staging")
    Path(init_path).write_text(result, encoding="utf-8")


def validate_versions_match(source_dir=DEFAULT_SOURCE_DIR):

    return _validate_versions_match(source_dir)


def _run_blender(blender, arguments):
    command = [
        str(blender),
        "--background",
        "--factory-startup",
        "--command",
        "extension",
        *arguments,
    ]
    print("RUN:", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def validate_archive_contents(zip_path):

    with zipfile.ZipFile(zip_path) as archive:
        members = archive.namelist()
        names = set(members)
    if len(members) != len(names):
        raise RuntimeError("Extension ZIP contains duplicate members")
    missing = REQUIRED_ARCHIVE_FILES.difference(names)
    if missing:
        raise RuntimeError(
            "Extension ZIP is missing required files: "
            + ", ".join(sorted(missing))
        )
    forbidden = {
        name
        for name in names
        if Path(name).name == "THIRD_PARTY_NOTICES.md"
    }
    if forbidden:
        raise RuntimeError(
            "Extension ZIP contains obsolete third-party notices: "
            + ", ".join(sorted(forbidden))
        )
    unexpected = names.difference(REQUIRED_ARCHIVE_FILES)
    if unexpected:
        raise RuntimeError(
            "Extension ZIP contains unexpected files: "
            + ", ".join(sorted(unexpected))
        )


def build_extension(blender, source_dir, output_dir):

    blender = Path(blender).resolve()
    source_dir = Path(source_dir).expanduser()
    output_dir = Path(output_dir).expanduser()
    reject_symlinks(source_dir)
    reject_symlinks(output_dir)
    validate_separate_paths(source_dir, output_dir)
    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()
    if not blender.is_file():
        raise FileNotFoundError(f"Blender executable not found: {blender}")
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Extension source not found: {source_dir}")
    source_files = addon_source_files(source_dir)

    version = validate_versions_match(source_dir)
    extension_id, manifest_version = manifest_metadata(source_dir)
    if manifest_version != version:
        raise RuntimeError(
            f"Source manifest version {manifest_version} != release version {version}"
        )

    expected_zip = output_dir / f"{extension_id}-{version}.zip"
    with tempfile.TemporaryDirectory(prefix="bease2-extension-") as temp_name:
        staging_dir = Path(temp_name) / extension_id
        for relative, source in source_files.items():
            target = staging_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        staging_output = Path(temp_name) / "output"
        staging_output.mkdir()
        remove_bl_info_assignment(staging_dir / "__init__.py")
        transformed = transform_python_tree(staging_dir)
        print(
            f"SANITIZED_PYTHON: {len(transformed)} files",
            flush=True,
        )
        _run_blender(
            blender,
            [
                "build",
                "--source-dir",
                str(staging_dir),
                "--output-dir",
                str(staging_output),
            ],
        )
        staged_zip = staging_output / expected_zip.name
        if not staged_zip.is_file():
            raise RuntimeError(f"Extension build did not create {staged_zip}")
        _run_blender(blender, ["validate", str(staged_zip)])
        validate_archive_contents(staged_zip)
        reject_symlinks(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".bease2-extension-", suffix=".zip", dir=output_dir, delete=False
        ) as file:
            output_temp = Path(file.name)
        try:
            shutil.copyfile(staged_zip, output_temp)
            output_temp.replace(expected_zip)
        finally:
            output_temp.unlink(missing_ok=True)
    print(f"BUILT_AND_VALIDATED: {expected_zip}", flush=True)
    return expected_zip


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Build and validate the B-Ease Studio 2 Extension ZIP"
    )
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main():
    args = _parse_args()
    build_extension(args.blender, args.source_dir, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
