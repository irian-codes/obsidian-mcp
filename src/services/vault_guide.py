"""Fresh, complete guide assembly through the existing selected-vault reader."""
from pydantic import TypeAdapter, ValidationError

from src.config import settings
from src.services import vault
from src.vault_guide_config import Selector


class GuideError(ValueError):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def assemble_guide(client: str, topics: list[str], user_id: int | None) -> str:
    try:
        TypeAdapter(Selector).validate_python(client)
        TypeAdapter(list[Selector]).validate_python(topics, strict=True)
    except ValidationError:
        raise GuideError("get_vault_guide: client and topics must be nonempty identifiers containing ASCII letters, digits, hyphens or underscores, starting with a letter or digit.", "invalid_argument") from None
    config = settings.vault_guide_config
    if config is None:
        raise GuideError("Incomplete vault guide: VAULT_GUIDE_CONFIG is not configured. Ask the operator to configure the deployment file map.", "validation_failed")
    if any(topic not in config.topics for topic in topics):
        raise GuideError("get_vault_guide: unknown topic; call with topics=[] to discover available topics.", "invalid_argument")

    requested = list(dict.fromkeys(topics))
    paths = list(config.base.files)
    for name in requested:
        paths.extend(config.topics[name].files)
    for name in requested:
        addition = config.topics[name].clients.get(client)
        if addition is not None:
            paths.extend(addition.files)
    paths = list(dict.fromkeys(paths))

    # Catalog is structural metadata only; body instructions come from files.
    catalog = ["# Vault guide", "", "## Available topics"]
    for name, topic in config.topics.items():
        clients = ", ".join(topic.clients)
        catalog.append(f"- {name}" + (f" (client additions: {clients})" if clients else ""))
    if not config.topics:
        catalog.append("(none configured)")
    result = "\n".join(catalog) + "\n\n"
    cap = settings.max_read_response_chars
    if len(result) > cap:
        raise GuideError("Incomplete vault guide: generated catalog exceeds MAX_READ_RESPONSE_CHARS. Ask the operator to reduce the map or adjust the existing limit.", "size_limit")
    for path in paths:
        try:
            # Byte cap applies before allocation; decode strictly, preserving
            # frontmatter and newlines rather than extracting the note body.
            content = vault.read_bytes(
                path, user_id=user_id, max_bytes=settings.max_file_read_bytes,
            ).decode("utf-8")
        except (OSError, ValueError, RuntimeError):
            # Do not echo OS paths/errors: they can contain host/tenant paths.
            raise GuideError(f"Incomplete vault guide: required file {path!r} is missing, unreadable, unsafe, invalid UTF-8 or exceeds MAX_FILE_READ_BYTES. No guide content was returned.", "validation_failed") from None
        if not content.strip():
            raise GuideError(f"Incomplete vault guide: required file {path!r} is empty. No guide content was returned.", "validation_failed")
        section = f"## Source: {path}\n\n{content}\n\n"
        if len(result) + len(section) > cap:
            raise GuideError("Incomplete vault guide: complete selected files and source labels exceed MAX_READ_RESPONSE_CHARS. No guide content was returned; ask the operator to reduce selected guidance or adjust the existing limit.", "size_limit")
        result += section
    return result
