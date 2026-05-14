from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from fastapi import UploadFile


class CandidateRegistryService:
    """Persist candidate profiles and face reference images."""

    def __init__(
        self,
        gallery_dir: str | Path = "data/face_gallery",
        registry_path: str | Path = "data/candidate_registry.json",
    ) -> None:
        self.gallery_dir = Path(gallery_dir)
        self.registry_path = Path(registry_path)
        self.allowed_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        self.gallery_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    def _clean_text(self, value: str | None) -> str:
        return str(value or "").strip()

    def _normalize_candidate_id(self, value: str | None) -> str:
        candidate_id = self._clean_text(value)
        candidate_id = re.sub(r"\s+", "", candidate_id)
        if not candidate_id:
            raise ValueError("Vui long nhap ma thi sinh.")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", candidate_id):
            raise ValueError("Ma thi sinh chi duoc gom chu cai, so, dau gach duoi hoac gach ngang.")
        return candidate_id

    def _safe_name_fragment(self, value: str) -> str:
        normalized = self._clean_text(value).replace("Đ", "D").replace("đ", "d")
        normalized = unicodedata.normalize("NFKD", normalized)
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        safe = re.sub(r"\s+", "_", normalized)
        safe = re.sub(r"[^A-Za-z0-9_-]+", "", safe)
        return safe or "candidate"

    def _load_registry(self) -> list[dict[str, Any]]:
        if not self.registry_path.exists():
            return []
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"candidate_registry.json khong hop le JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise ValueError("candidate_registry.json phai la JSON array.")
        return [item for item in payload if isinstance(item, dict)]

    def list_candidates(self) -> list[dict[str, str]]:
        candidates: list[dict[str, str]] = []
        for item in self._load_registry():
            candidate_id = self._clean_text(item.get("candidate_id"))
            if not candidate_id:
                continue
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "name": self._clean_text(item.get("name")) or candidate_id,
                    "email": self._clean_text(item.get("email")),
                    "room": self._clean_text(item.get("room")),
                    "image": self._clean_text(item.get("image")),
                }
            )
        return sorted(candidates, key=lambda item: item["candidate_id"])

    def _write_registry(self, profiles: list[dict[str, Any]]) -> None:
        self.registry_path.write_text(
            json.dumps(profiles, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def save_candidate(
        self,
        *,
        candidate_id: str,
        name: str,
        email: str,
        room: str,
        image_file: UploadFile | None = None,
    ) -> dict[str, str | bool]:
        normalized_candidate_id = self._normalize_candidate_id(candidate_id)
        display_name = self._clean_text(name)
        if not display_name:
            raise ValueError("Vui long nhap ho ten thi sinh.")

        profiles = self._load_registry()
        existing_profile = next(
            (
                item
                for item in profiles
                if self._clean_text(item.get("candidate_id")) == normalized_candidate_id
            ),
            None,
        )

        original_filename = self._clean_text(getattr(image_file, "filename", "") if image_file is not None else "")
        has_new_image = bool(original_filename)
        stored_filename = self._clean_text((existing_profile or {}).get("image"))

        if has_new_image:
            extension = Path(original_filename).suffix.lower()
            if extension not in self.allowed_extensions:
                if image_file is not None:
                    await image_file.close()
                raise ValueError("Anh thi sinh phai co dinh dang .jpg, .jpeg, .png, .bmp hoac .webp.")

            safe_name = self._safe_name_fragment(display_name)
            stored_filename = f"{normalized_candidate_id}_{safe_name}{extension}"
            destination = self.gallery_dir / stored_filename

            written_bytes = 0
            try:
                with destination.open("wb") as output_file:
                    while True:
                        chunk = await image_file.read(1024 * 1024) if image_file is not None else b""
                        if not chunk:
                            break
                        written_bytes += len(chunk)
                        output_file.write(chunk)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            finally:
                if image_file is not None:
                    await image_file.close()

            if written_bytes <= 0:
                destination.unlink(missing_ok=True)
                raise ValueError("File anh thi sinh dang rong.")
        elif existing_profile is None:
            if image_file is not None:
                await image_file.close()
            raise ValueError("Vui long chon anh khuon mat khi them thi sinh moi.")
        elif image_file is not None:
            await image_file.close()

        profile = {
            "candidate_id": normalized_candidate_id,
            "name": display_name,
            "email": self._clean_text(email),
            "room": self._clean_text(room),
            "image": stored_filename,
        }

        updated = False
        old_image = ""
        next_profiles: list[dict[str, Any]] = []
        for item in profiles:
            item_candidate_id = self._clean_text(item.get("candidate_id"))
            if item_candidate_id == normalized_candidate_id:
                updated = True
                old_image = self._clean_text(item.get("image"))
                next_profiles.append(profile)
            else:
                next_profiles.append(item)
        if not updated:
            next_profiles.append(profile)

        self._write_registry(next_profiles)

        if has_new_image and old_image and old_image != stored_filename:
            old_path = self.gallery_dir / old_image
            if old_path.exists() and old_path.is_file():
                old_path.unlink(missing_ok=True)

        return {**profile, "updated": updated}

    def delete_candidate(self, candidate_id: str) -> dict[str, str]:
        normalized_candidate_id = self._normalize_candidate_id(candidate_id)
        profiles = self._load_registry()

        deleted_profile: dict[str, Any] | None = None
        next_profiles: list[dict[str, Any]] = []
        for item in profiles:
            item_candidate_id = self._clean_text(item.get("candidate_id"))
            if item_candidate_id == normalized_candidate_id:
                deleted_profile = item
                continue
            next_profiles.append(item)

        if deleted_profile is None:
            raise ValueError("Khong tim thay thi sinh de xoa.")

        self._write_registry(next_profiles)

        image_name = self._clean_text(deleted_profile.get("image"))
        if image_name:
            image_path = self.gallery_dir / image_name
            if image_path.exists() and image_path.is_file():
                image_path.unlink(missing_ok=True)

        return {
            "candidate_id": normalized_candidate_id,
            "name": self._clean_text(deleted_profile.get("name")) or normalized_candidate_id,
            "image": image_name,
        }


candidate_registry_service = CandidateRegistryService()
