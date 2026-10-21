"""Strict deployment file map; no vault access or application imports."""
import json
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# Exact, case-sensitive identifiers: application/mode names, never authority.
Selector = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]


class GuideFiles(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    files: list[str]

    @field_validator("files")
    @classmethod
    def safe_paths(cls, paths: list[str]) -> list[str]:
        for path in paths:
            # The same path length bound as vault.MAX_PATH_CHARS. Filesystem
            # containment/visibility is checked again by the selected-vault read.
            if (
                not path
                or len(path) > 1024
                or "\\" in path
                or not path.isprintable()
                or PurePosixPath(path).is_absolute()
                or any(part.startswith(".") or not part for part in path.split("/"))
            ):
                raise ValueError(
                    "files must contain visible, printable, canonical "
                    "vault-relative POSIX paths (max 1024 characters)"
                )
            try:
                path.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("files paths must be valid UTF-8") from None
        return paths


class GuideTopic(GuideFiles):
    clients: dict[Selector, GuideFiles] = Field(default_factory=dict)


class VaultGuideConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base: GuideFiles
    topics: dict[Selector, GuideTopic] = Field(default_factory=dict)

    @field_validator("base")
    @classmethod
    def nonempty_base(cls, base: GuideFiles) -> GuideFiles:
        if not base.files:
            raise ValueError("base.files must contain at least one file")
        return base


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def parse_guide_config(value):
    if value is None or isinstance(value, VaultGuideConfig):
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value, object_pairs_hook=_unique_object)
        except ValueError:
            raise ValueError("VAULT_GUIDE_CONFIG must be valid JSON without duplicate keys") from None
        if value is None:
            raise ValueError("VAULT_GUIDE_CONFIG must be an object")
    return VaultGuideConfig.model_validate(value)
