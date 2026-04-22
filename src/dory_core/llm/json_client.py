from __future__ import annotations

from typing import Any, Protocol


class JSONGenerationClient(Protocol):
    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> Any: ...
