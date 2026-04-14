from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover
    ort = None

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None

try:
    from insightface.app import FaceAnalysis
except ImportError:  # pragma: no cover
    FaceAnalysis = None


class FaceRecognitionService:
    """Identify candidates in review frames using InsightFace embeddings."""

    def __init__(
        self,
        gallery_dir: str | Path = "data/face_gallery",
        registry_path: str | Path = "data/candidate_registry.json",
        similarity_threshold: float = 0.35,
        det_size: tuple[int, int] = (384, 384),
        max_faces_per_frame: int = 1,
    ) -> None:
        self.gallery_dir = Path(gallery_dir)
        self.registry_path = Path(registry_path)
        self.similarity_threshold = max(-1.0, min(1.0, similarity_threshold))
        self.det_size = det_size
        self.max_faces_per_frame = max(1, max_faces_per_frame)

        self._app = None
        self._load_error: str | None = None
        self._profiles_by_id: dict[str, dict[str, Any]] = {}
        self._gallery_embeddings: dict[str, Any] = {}
        self._available_providers: list[str] = []
        self._active_providers: list[str] = []
        self._execution_backend = "cpu"
        self._execution_context_id = -1
        self._runtime_message: str | None = None

        self.gallery_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _resolve_runtime(self) -> tuple[list[str], int, str, str | None]:
        available_providers = list(ort.get_available_providers()) if ort is not None else []
        self._available_providers = available_providers

        torch_cuda_available = bool(torch is not None and torch.cuda.is_available())
        if "CUDAExecutionProvider" in available_providers:
            return (
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
                0,
                "cuda",
                None,
            )

        if torch_cuda_available:
            return (
                ["CPUExecutionProvider"],
                -1,
                "cpu",
                "GPU co san nhung onnxruntime chua co CUDAExecutionProvider; hay cai onnxruntime-gpu.",
            )

        return (["CPUExecutionProvider"], -1, "cpu", None)

    def _collect_active_providers(self) -> list[str]:
        if self._app is None:
            return []

        providers: list[str] = []
        seen: set[str] = set()
        models = getattr(self._app, "models", {}) or {}
        for model in models.values():
            session = getattr(model, "session", None)
            if session is None or not hasattr(session, "get_providers"):
                continue
            try:
                session_providers = session.get_providers()
            except Exception:
                continue
            for provider in session_providers:
                if provider not in seen:
                    seen.add(provider)
                    providers.append(provider)
        return providers

    def _initialize(self) -> None:
        if FaceAnalysis is None or cv2 is None or np is None:
            self._load_error = "Thieu insightface/opencv/numpy."
            return

        try:
            providers, ctx_id, backend, runtime_message = self._resolve_runtime()
            self._app = FaceAnalysis(name="buffalo_l", providers=providers)
            self._app.prepare(ctx_id=ctx_id, det_size=self.det_size)
            self._execution_backend = backend
            self._execution_context_id = ctx_id
            self._runtime_message = runtime_message
            self._active_providers = self._collect_active_providers() or list(providers)
        except Exception as exc:  # pragma: no cover
            self._app = None
            self._load_error = f"Khong the khoi tao InsightFace: {exc}"
            return

        try:
            self._load_gallery_embeddings()
            if not self._profiles_by_id:
                self._load_error = "Chua co du lieu face gallery."
            else:
                self._load_error = None
        except Exception as exc:  # pragma: no cover
            self._load_error = f"Khong the nap face gallery: {exc}"

    def _image_extensions(self) -> set[str]:
        return {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

    def _normalize_profile(self, raw_profile: dict[str, Any]) -> dict[str, str]:
        candidate_id = str(raw_profile.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("Thieu candidate_id trong candidate_registry.json.")

        name = str(raw_profile.get("name") or candidate_id).strip()
        email = str(raw_profile.get("email") or "").strip()
        room = str(raw_profile.get("room") or "").strip()
        image = str(raw_profile.get("image") or "").strip()
        if not image:
            raise ValueError(f"Thieu image cho candidate_id={candidate_id}.")

        return {
            "candidate_id": candidate_id,
            "name": name,
            "email": email,
            "room": room,
            "image": image,
        }

    def _profiles_from_registry(self) -> list[dict[str, str]]:
        if not self.registry_path.exists():
            return []
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"candidate_registry.json khong hop le JSON: {exc}") from exc

        if not isinstance(payload, list):
            raise ValueError("candidate_registry.json phai la JSON array.")

        profiles: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            profiles.append(self._normalize_profile(item))
        return profiles

    def _profile_from_filename(self, image_path: Path) -> dict[str, str]:
        stem = image_path.stem
        parts = [part for part in stem.replace("-", "_").split("_") if part]
        if not parts:
            candidate_id = stem.upper()
            name = stem
        elif len(parts) == 1:
            candidate_id = parts[0].upper()
            name = parts[0]
        else:
            candidate_id = parts[0].upper()
            name = " ".join(parts[1:])

        return {
            "candidate_id": candidate_id,
            "name": name,
            "email": "",
            "room": "",
            "image": image_path.name,
        }

    def _discover_profiles(self) -> list[dict[str, str]]:
        explicit_profiles = self._profiles_from_registry()
        if explicit_profiles:
            return explicit_profiles

        profiles: list[dict[str, str]] = []
        for image_path in sorted(self.gallery_dir.iterdir()):
            if not image_path.is_file() or image_path.suffix.lower() not in self._image_extensions():
                continue
            profiles.append(self._profile_from_filename(image_path))
        return profiles

    def _read_embedding_from_image(self, image_path: Path) -> Any | None:
        if self._app is None or cv2 is None:
            return None
        frame_bgr = cv2.imread(str(image_path))
        if frame_bgr is None:
            return None
        faces = self._app.get(frame_bgr)
        if not faces:
            return None
        primary_face = max(faces, key=lambda face: float(getattr(face, "det_score", 0.0)))
        embedding = getattr(primary_face, "embedding", None)
        if embedding is None:
            return None
        return embedding

    def _load_gallery_embeddings(self) -> None:
        profiles = self._discover_profiles()
        self._profiles_by_id = {}
        self._gallery_embeddings = {}

        for profile in profiles:
            candidate_id = profile["candidate_id"]
            image_path = self.gallery_dir / profile["image"]
            if not image_path.exists():
                continue
            embedding = self._read_embedding_from_image(image_path)
            if embedding is None:
                continue
            self._profiles_by_id[candidate_id] = profile
            self._gallery_embeddings[candidate_id] = embedding

    def is_available(self) -> bool:
        return self._app is not None and bool(self._gallery_embeddings)

    def get_status(self) -> dict[str, Any]:
        return {
            "enabled": self.is_available(),
            "gallery_dir": str(self.gallery_dir),
            "registry_path": str(self.registry_path),
            "candidate_count": len(self._gallery_embeddings),
            "similarity_threshold": self.similarity_threshold,
            "det_size": list(self.det_size),
            "max_faces_per_frame": self.max_faces_per_frame,
            "execution_backend": self._execution_backend,
            "execution_context_id": self._execution_context_id,
            "available_providers": self._available_providers,
            "active_providers": self._active_providers,
            "message": self._load_error or self._runtime_message or "Face recognition san sang.",
        }

    def _cosine_similarity(self, source_embedding: Any, target_embedding: Any) -> float:
        if np is None:
            return -1.0
        source = np.asarray(source_embedding, dtype=np.float32)
        target = np.asarray(target_embedding, dtype=np.float32)
        denominator = float(np.linalg.norm(source) * np.linalg.norm(target))
        if denominator <= 1e-9:
            return -1.0
        return float(np.dot(source, target) / denominator)

    def identify_faces(self, frame_bgr) -> list[dict[str, Any]]:
        if not self.is_available() or self._app is None:
            return []

        try:
            faces = self._app.get(frame_bgr)
        except Exception:  # pragma: no cover
            return []

        faces = sorted(
            faces,
            key=lambda face: float(getattr(face, "det_score", 0.0)),
            reverse=True,
        )[: self.max_faces_per_frame]

        results: list[dict[str, Any]] = []
        for face in faces:
            embedding = getattr(face, "embedding", None)
            if embedding is None:
                continue

            best_id: str | None = None
            best_similarity = -1.0
            for candidate_id, candidate_embedding in self._gallery_embeddings.items():
                similarity = self._cosine_similarity(embedding, candidate_embedding)
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_id = candidate_id

            if best_id is None or best_similarity < self.similarity_threshold:
                continue

            profile = self._profiles_by_id.get(best_id, {})
            bbox = getattr(face, "bbox", None)
            bbox_list: list[float] = []
            if bbox is not None:
                bbox_list = [float(value) for value in bbox.tolist()]

            results.append(
                {
                    "candidate_id": best_id,
                    "name": str(profile.get("name") or best_id),
                    "email": str(profile.get("email") or ""),
                    "room": str(profile.get("room") or ""),
                    "similarity": round(best_similarity, 4),
                    "bbox": bbox_list,
                }
            )

        return sorted(results, key=lambda item: float(item.get("similarity", 0.0)), reverse=True)

    def select_primary_identity(self, matches: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not matches:
            return None
        return matches[0]
