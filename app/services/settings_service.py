from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_AI_SETTINGS: dict[str, float] = {
    "confidence_threshold": 0.75,
    "extraction_interval_seconds": 2.0,
    "behavior_threshold": 0.82,
}


class SettingsService:
    """Persist lightweight dashboard settings in a JSON file."""

    def __init__(self, config_path: str | Path = "data/ai_settings.json") -> None:
        self.config_path = Path(config_path)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _clamp(self, value: Any, minimum: float, maximum: float, fallback: float) -> float:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return fallback
        return max(minimum, min(maximum, numeric_value))

    def normalize(self, payload: dict[str, Any] | None) -> dict[str, float]:
        source = payload or {}
        return {
            "confidence_threshold": self._clamp(
                source.get("confidence_threshold"),
                minimum=0.25,
                maximum=0.95,
                fallback=DEFAULT_AI_SETTINGS["confidence_threshold"],
            ),
            "extraction_interval_seconds": self._clamp(
                source.get("extraction_interval_seconds"),
                minimum=0.25,
                maximum=5.0,
                fallback=DEFAULT_AI_SETTINGS["extraction_interval_seconds"],
            ),
            "behavior_threshold": self._clamp(
                source.get("behavior_threshold"),
                minimum=0.6,
                maximum=0.98,
                fallback=DEFAULT_AI_SETTINGS["behavior_threshold"],
            ),
        }

    def load(self) -> dict[str, float]:
        if not self.config_path.exists():
            return dict(DEFAULT_AI_SETTINGS)

        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(DEFAULT_AI_SETTINGS)
        return self.normalize(payload)

    def save(self, payload: dict[str, Any]) -> dict[str, float]:
        normalized = self.normalize(payload)
        self.config_path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return normalized

    def reset(self) -> dict[str, float]:
        return self.save(DEFAULT_AI_SETTINGS)


settings_service = SettingsService()


def get_ai_settings() -> dict[str, float]:
    return settings_service.load()
