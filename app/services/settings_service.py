from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_AI_SETTINGS: dict[str, float | bool] = {
    "confidence_threshold": 0.75,
    "extraction_interval_seconds": 2.0,
    "behavior_threshold": 0.82,
    "enable_gaze_alerts": True,
    "enable_cell_phone_alerts": True,
    "enable_multiple_people_alerts": False,
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

    def _normalize_bool(self, value: Any, fallback: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return fallback

    def normalize(self, payload: dict[str, Any] | None) -> dict[str, float | bool]:
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
            "enable_gaze_alerts": self._normalize_bool(
                source.get("enable_gaze_alerts"),
                fallback=bool(DEFAULT_AI_SETTINGS["enable_gaze_alerts"]),
            ),
            "enable_cell_phone_alerts": self._normalize_bool(
                source.get("enable_cell_phone_alerts"),
                fallback=bool(DEFAULT_AI_SETTINGS["enable_cell_phone_alerts"]),
            ),
            "enable_multiple_people_alerts": self._normalize_bool(
                source.get("enable_multiple_people_alerts"),
                fallback=bool(DEFAULT_AI_SETTINGS["enable_multiple_people_alerts"]),
            ),
        }

    def load(self) -> dict[str, float | bool]:
        if not self.config_path.exists():
            return dict(DEFAULT_AI_SETTINGS)

        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(DEFAULT_AI_SETTINGS)
        return self.normalize(payload)

    def save(self, payload: dict[str, Any]) -> dict[str, float | bool]:
        normalized = self.normalize(payload)
        self.config_path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return normalized

    def reset(self) -> dict[str, float | bool]:
        return self.save(DEFAULT_AI_SETTINGS)


settings_service = SettingsService()


def get_ai_settings() -> dict[str, float | bool]:
    return settings_service.load()
