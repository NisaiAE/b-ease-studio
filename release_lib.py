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

import ast
import io
import re
import tokenize
import tomllib
from pathlib import Path

ADDON_FILES = frozenset({
    "__init__.py", "constants.py", "properties.py", "preferences.py", "header.py",
    "core/bezier.py", "core/curve.py", "core/textfmt.py",
    "data/paths.py", "data/settings_store.py",
    "fcurve/compat.py", "fcurve/easing.py",
    "ops/__init__.py", "ops/operators.py",
    "ui/canvas.py", "ui/draw.py", "ui/events.py", "ui/icons.py",
    "ui/icons_data.py", "ui/runtime.py", "ui/theme.py", "ui/thumbnails.py",
    "ui/widgets.py", "ui/window.py",
    "README.md", "LICENSE", "ASSET_LICENSES.md", "blender_manifest.toml",
})


def reject_symlinks(root: Path) -> None:

    root = Path(root)
    if root.is_symlink():
        raise RuntimeError(f"Refusing symbolic link: {root}")
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_symlink():
                raise RuntimeError(f"Refusing symbolic link: {path}")


def validate_separate_paths(source: Path, destination: Path) -> None:

    source, destination = Path(source).resolve(), Path(destination).resolve()
    if (
        source == destination
        or source in destination.parents
        or destination in source.parents
    ):
        raise RuntimeError("Source and destination directories must not overlap")


def addon_source_files(source: Path) -> dict[Path, Path]:

    source = Path(source)
    reject_symlinks(source)
    files = {Path(name): source / name for name in sorted(ADDON_FILES)}
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise RuntimeError("Missing release source: " + ", ".join(missing))
    return files


DEFAULT_GPL_HEADER = """# B-Ease Studio
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
"""

_DOCSTRING_PARENTS = (
    ast.Module,
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
)
_CODING_COOKIE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-_.a-zA-Z0-9]+")


def _is_gpl_header(block: str) -> bool:
    normalized = block.lower()
    return (
        "gnu general public license" in normalized
        or "spdx-license-identifier: gpl" in normalized
    )


def _extract_gpl_header(source: str) -> tuple[str | None, int, int]:

    lines = source.splitlines(keepends=True)
    limit = 0
    while limit < len(lines):
        stripped = lines[limit].lstrip()
        if not stripped.strip() or stripped.startswith("#"):
            limit += 1
            continue
        break

    index = 0
    while index < limit:
        while index < limit and not lines[index].lstrip().startswith("#"):
            index += 1
        start = index
        while index < limit and lines[index].lstrip().startswith("#"):
            index += 1
        end = index
        if start == end:
            continue
        block_lines = lines[start:end]
        while block_lines and (
            block_lines[0].startswith("#!")
            or _CODING_COOKIE.match(block_lines[0])
        ):
            start += 1
            block_lines = block_lines[1:]
        block = "".join(block_lines)
        if block and _is_gpl_header(block):
            return block.rstrip("\r\n") + "\n", start, end
    return None, 0, 0


def _line_offsets(source: str) -> tuple[list[str], list[int]]:
    lines = source.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    return lines, offsets


def _character_column(line: str, utf8_column: int) -> int:
    encoded = line.encode("utf-8")
    return len(encoded[:utf8_column].decode("utf-8"))


def _source_offset(
    lines: list[str], offsets: list[int], lineno: int, utf8_column: int
) -> int:
    line = lines[lineno - 1]
    return offsets[lineno - 1] + _character_column(line, utf8_column)


def _docstring_replacements(source: str) -> list[tuple[int, int, str]]:
    tree = ast.parse(source)
    lines, offsets = _line_offsets(source)
    replacements: list[tuple[int, int, str]] = []
    for parent in ast.walk(tree):
        if not isinstance(parent, _DOCSTRING_PARENTS) or not parent.body:
            continue
        node = parent.body[0]
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        start = _source_offset(lines, offsets, node.lineno, node.col_offset)
        end = _source_offset(
            lines, offsets, node.end_lineno, node.end_col_offset
        )
        replacement = ""
        if len(parent.body) == 1 and not isinstance(parent, ast.Module):
            replacement = "pass"
        else:
            line_end = source.find("\n", end)
            if line_end < 0:
                line_end = len(source)
            semicolon = re.match(r"[ \t]*;[ \t]*", source[end:line_end])
            if semicolon:
                end += semicolon.end()
        replacements.append((start, end, replacement))
    return replacements


def remove_docstrings(source: str) -> str:

    replacements = _docstring_replacements(source)
    for start, end, replacement in sorted(replacements, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def remove_comments(source: str) -> str:

    tokens = (
        token
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type != tokenize.COMMENT
    )
    return tokenize.untokenize(tokens)


def multiline_string_lines(source: str) -> set[int]:

    protected = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if (
            tokenize.tok_name[token.type] in {"STRING", "FSTRING_MIDDLE", "TSTRING_MIDDLE"}
            and token.start[0] != token.end[0]
        ):
            protected.update(range(token.start[0], token.end[0] + 1))
    return protected


def normalize_release_layout(source: str) -> str:

    protected = multiline_string_lines(source)
    normalized_lines: list[str] = []
    blank_count = 0
    for line_number, line in enumerate(source.split("\n"), 1):
        if line_number in protected:
            normalized_lines.append(line)
            blank_count = 0
            continue
        line = line.rstrip()
        if line:
            blank_count = 0
        else:
            blank_count += 1
            if blank_count > 2:
                continue
        normalized_lines.append(line)
    return "\n".join(normalized_lines).rstrip("\n") + "\n"


def _assignment_targets_name(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = (node.target,)
    else:
        return False
    return any(
        isinstance(target, ast.Name) and target.id == name
        for target in targets
    )


def manifest_metadata(source_dir: Path) -> tuple[str, str]:

    manifest_path = Path(source_dir) / "blender_manifest.toml"
    try:
        with manifest_path.open("rb") as file:
            manifest = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RuntimeError(
            f"Unable to parse extension manifest: {manifest_path}"
        ) from error
    extension_id = manifest.get("id")
    version = manifest.get("version")
    if not isinstance(extension_id, str) or not re.fullmatch(
        r"[a-z][a-z0-9_]*", extension_id
    ):
        raise RuntimeError("Manifest extension id metadata is invalid")
    if not isinstance(version, str) or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
        r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", version
    ):
        raise RuntimeError("Manifest version metadata is invalid")
    return extension_id, version


def bl_info_version(source_dir: Path) -> str:

    init_path = Path(source_dir) / "__init__.py"
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as error:
        raise RuntimeError(
            f"Unable to parse legacy metadata: {init_path}"
        ) from error
    assignments = [
        node
        for node in tree.body
        if _assignment_targets_name(node, "bl_info")
    ]
    if len(assignments) != 1:
        raise RuntimeError(
            "Expected exactly one top-level legacy bl_info assignment, "
            f"found {len(assignments)}"
        )
    try:
        value = ast.literal_eval(assignments[0].value)
    except (TypeError, ValueError) as error:
        raise RuntimeError("Legacy bl_info metadata is not literal data") from error
    version = value.get("version") if isinstance(value, dict) else None
    if (
        not isinstance(version, tuple)
        or len(version) != 3
        or not all(isinstance(part, int) for part in version)
    ):
        raise RuntimeError("Legacy bl_info version metadata is missing")
    return ".".join(str(part) for part in version)


def validate_versions_match(source_dir: Path) -> str:

    _extension_id, manifest_version = manifest_metadata(source_dir)
    legacy_version = bl_info_version(source_dir)
    if legacy_version != manifest_version:
        raise RuntimeError(
            "Version mismatch: "
            f"manifest={manifest_version}, bl_info={legacy_version}"
        )
    return manifest_version


def transform_python_source(source: str, filename: str = "<release>") -> str:

    _header, header_start, header_end = _extract_gpl_header(source)
    if _header is not None:
        lines = source.splitlines(keepends=True)
        source = "".join(lines[:header_start] + lines[header_end:])

    source = remove_docstrings(source)
    expected_ast = ast.dump(ast.parse(source), include_attributes=False)
    source = remove_comments(source)
    body = source.lstrip("\r\n")
    result = DEFAULT_GPL_HEADER.rstrip("\r\n") + "\n\n" + body
    result = normalize_release_layout(result)
    compile(result, filename, "exec")
    if ast.dump(ast.parse(result), include_attributes=False) != expected_ast:
        raise RuntimeError(f"Release transformation changed program content: {filename}")
    return result


def transform_python_file(source: Path, destination: Path | None = None) -> Path:

    source = Path(source)
    destination = source if destination is None else Path(destination)
    transformed = transform_python_source(
        source.read_text(encoding="utf-8"), str(source)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(transformed, encoding="utf-8")
    return destination


def transform_python_tree(root: Path) -> list[Path]:

    root = Path(root)
    reject_symlinks(root)
    transformed: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if path.is_file():
            transform_python_file(path)
            transformed.append(path)
    return transformed
