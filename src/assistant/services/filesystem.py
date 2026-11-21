from pathlib import Path
from typing import Any


FILESYSTEM_READ_ONLY_TOOLS = frozenset({
    "read_file",
    "read_text_file",
    "read_media_file",
    "read_multiple_files",
    "list_directory",
    "list_directory_with_sizes",
    "directory_tree",
    "search_files",
    "get_file_info",
    "list_allowed_directories",
})


def validate_filesystem_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    root = root.resolve()
    validated = dict(arguments)

    def resolve_argument_path(value: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "Filesystem paths must be non-empty strings."
            )
        candidate = Path(value)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            parts = candidate.parts
            if parts and parts[0].lower() == root.name.lower():
                resolved = root.joinpath(*parts[1:]).resolve()
            else:
                resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                "Filesystem path is outside the configured root: "
                f"{value}"
            ) from exc
        return resolved

    for field_name in (
        "path",
        "root",
        "file_path",
        "directory",
    ):
        value = validated.get(field_name)
        if value is not None:
            try:
                validated[field_name] = str(
                    resolve_argument_path(value)
                )
            except ValueError as exc:
                raise ValueError(f"{field_name}: {exc}") from exc

    paths = validated.get("paths")
    if paths is not None:
        if not isinstance(paths, list):
            raise ValueError("paths must be a list.")
        resolved_paths = []
        for value in paths:
            try:
                resolved_paths.append(
                    str(resolve_argument_path(value))
                )
            except ValueError as exc:
                raise ValueError(f"paths: {exc}") from exc
        validated["paths"] = resolved_paths

    return validated
