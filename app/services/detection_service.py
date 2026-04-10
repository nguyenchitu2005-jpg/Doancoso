from __future__ import annotations

import json
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover
    YOLO = None

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None

from app.services.behavior_model_service import BehaviorModelService
from app.services.face_recognition_service import FaceRecognitionService
from app.services.mediapipe_feature_service import MediapipeFeatureService
from app.services.settings_service import get_ai_settings


class DetectionService:
    """Run a YOLO-based review pass and save timeline snapshots."""

    def __init__(
        self,
        weights_dir: str | Path = "weights",
        results_dir: str | Path = "results",
        model_name: str = "yolo11n.pt",
        conf_threshold: float = 0.35,
        sample_every_n_frames: int = 30,
        sample_interval_seconds: float = 2.0,
        incident_cooldown_seconds: float = 3.0,
        max_incidents: int = 40,
        behavior_model_path: str | Path = "models/suspicious_behavior_model.joblib",
        behavior_score_threshold: float = 0.82,
        enable_mediapipe: bool = True,
        enable_face_recognition: bool = True,
        min_signal_streak: int = 2,
    ) -> None:
        self.weights_dir = Path(weights_dir)
        self.results_dir = Path(results_dir)
        self.model_name = model_name
        self.conf_threshold = conf_threshold
        self.sample_every_n_frames = max(1, sample_every_n_frames)
        self.sample_interval_seconds = max(0.5, sample_interval_seconds)
        self.incident_cooldown_seconds = max(0.0, incident_cooldown_seconds)
        self.max_incidents = max(1, max_incidents)
        self.min_signal_streak = max(1, min_signal_streak)
        self.gaze_warmup_seconds = 3.0
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.weights_dir.mkdir(parents=True, exist_ok=True)
        self._model = None
        self.behavior_model_service = BehaviorModelService(
            model_path=behavior_model_path,
            score_threshold=behavior_score_threshold,
        )
        self.enable_mediapipe = enable_mediapipe
        self.mediapipe_feature_service = MediapipeFeatureService() if enable_mediapipe else None
        self.enable_face_recognition = enable_face_recognition
        self.face_recognition_service = FaceRecognitionService() if enable_face_recognition else None
        self.apply_runtime_settings(get_ai_settings())

    def apply_runtime_settings(self, settings: dict[str, Any] | None) -> None:
        if not settings:
            return
        if "confidence_threshold" in settings:
            self.conf_threshold = max(0.4, min(float(settings["confidence_threshold"]), 0.95))
        if "extraction_interval_seconds" in settings:
            self.sample_interval_seconds = max(0.5, min(float(settings["extraction_interval_seconds"]), 5.0))
        if "behavior_threshold" in settings:
            self.behavior_model_service.score_threshold = max(0.6, min(float(settings["behavior_threshold"]), 0.98))

    def _resolve_model_source(self) -> str | Path:
        explicit_weight = self.weights_dir / self.model_name
        return explicit_weight

    def _load_model(self):
        if YOLO is None:
            raise RuntimeError("Thieu thu vien ultralytics. Hay cai `pip install ultralytics`.")
        if self._model is None:
            self._model = YOLO(str(self._resolve_model_source()))
        return self._model

    def _format_timestamp(self, seconds: float) -> str:
        total_seconds = max(0, int(seconds))
        return str(timedelta(seconds=total_seconds)).rjust(8, "0")

    def _iter_result_files(self) -> list[Path]:
        return sorted(self.results_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)

    def _write_result(self, video_path: Path, payload: dict[str, Any]) -> Path:
        output_path = self.results_dir / f"{video_path.stem}.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    def _analysis_mode(self) -> str:
        modes = ["yolo"]
        if self.enable_mediapipe and self.mediapipe_feature_service is not None and self.mediapipe_feature_service.is_available():
            modes.append("mediapipe")
        if self.behavior_model_service.is_available():
            modes.append("behavior_model")
        if self.enable_face_recognition and self.face_recognition_service is not None and self.face_recognition_service.is_available():
            modes.append("face_recognition")
        return "+".join(modes)

    def _mediapipe_status(self) -> dict[str, Any]:
        if not self.enable_mediapipe or self.mediapipe_feature_service is None:
            return {"enabled": False, "message": "MediaPipe bi tat trong cau hinh."}
        return self.mediapipe_feature_service.get_status()

    def _face_recognition_status(self) -> dict[str, Any]:
        if not self.enable_face_recognition or self.face_recognition_service is None:
            return {"enabled": False, "message": "Face recognition bi tat trong cau hinh."}
        return self.face_recognition_service.get_status()

    def _identity_key(self, identity: dict[str, Any] | None) -> str:
        if identity and identity.get("candidate_id"):
            return str(identity["candidate_id"])
        return "UNKNOWN"

    def _identity_payload(self, identity: dict[str, Any] | None) -> dict[str, str]:
        if identity:
            return {
                "candidate_id": str(identity.get("candidate_id") or "UNKNOWN"),
                "candidate_name": str(identity.get("name") or "Unknown Candidate"),
                "candidate_email": str(identity.get("email") or ""),
                "candidate_room": str(identity.get("room") or ""),
            }
        return {
            "candidate_id": "UNKNOWN",
            "candidate_name": "Unknown Candidate",
            "candidate_email": "",
            "candidate_room": "",
        }

    def _incident_with_identity(self, payload: dict[str, Any], identity: dict[str, Any] | None) -> dict[str, Any]:
        enriched = dict(payload)
        enriched.update(self._identity_payload(identity))
        return enriched

    def _incident_risk_rank(self, risk: str | None) -> int:
        return {"high": 3, "medium": 2, "low": 1}.get(str(risk or "low"), 1)

    def _incident_confidence_value(self, confidence: Any) -> float:
        if confidence is None:
            return 0.0
        raw = str(confidence).strip()
        digits = "".join(ch for ch in raw if ch.isdigit() or ch == ".")
        if not digits:
            return 0.0
        try:
            value = float(digits)
        except ValueError:
            return 0.0
        if "%" in raw:
            return value
        return value * 100.0 if value <= 1.0 else value

    def _incident_event_rank(self, event_type: str | None) -> int:
        return {
            "cell_phone": 6,
            "hand_phone": 5,
            "multiple_people": 4,
            "behavior_model": 3,
            "head_pose": 2,
            "gaze": 1,
            "face_missing": 0,
        }.get(str(event_type or ""), 0)

    def _incident_score(self, incident: dict[str, Any]) -> tuple[int, float, int]:
        return (
            self._incident_risk_rank(str(incident.get("risk") or "low")),
            self._incident_confidence_value(incident.get("confidence")),
            self._incident_event_rank(str(incident.get("event_type") or "")),
        )

    def _deduplicate_incidents_per_second(self, incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        best_by_second: dict[int, dict[str, Any]] = {}
        for incident in incidents:
            second_key = int(float(incident.get("time_seconds") or 0.0))
            existing = best_by_second.get(second_key)
            if existing is None or self._incident_score(incident) > self._incident_score(existing):
                best_by_second[second_key] = incident

        selected = list(best_by_second.values())
        selected.sort(key=lambda item: float(item.get("time_seconds") or 0.0))
        return selected

    def _risk_level_for_student(self, alerts: int, behavior_risks: list[str]) -> str:
        if "high" in behavior_risks or alerts >= 4:
            return "high"
        if "medium" in behavior_risks or alerts >= 2:
            return "medium"
        return "low"

    def _build_students_report(
        self,
        incidents: list[dict[str, Any]],
        frame_identity_counter: Counter[str],
    ) -> list[dict[str, Any]]:
        student_map: dict[str, dict[str, Any]] = {}

        for incident in incidents:
            candidate_id = str(incident.get("candidate_id") or "UNKNOWN")
            if candidate_id not in student_map:
                student_map[candidate_id] = {
                    "name": str(incident.get("candidate_name") or "Unknown Candidate"),
                    "email": str(incident.get("candidate_email") or ""),
                    "candidate_id": candidate_id,
                    "room": str(incident.get("candidate_room") or ""),
                    "behaviors": [],
                    "alerts": 0,
                    "risk": "low",
                    "_risk_marks": [],
                }

            row = student_map[candidate_id]
            row["alerts"] += 1
            behavior_label = str(incident.get("label") or "Khong xac dinh")
            if behavior_label not in row["behaviors"]:
                row["behaviors"].append(behavior_label)
            row["_risk_marks"].append(str(incident.get("risk") or "low"))

        for candidate_id, row in student_map.items():
            alerts = int(row.get("alerts") or 0)
            risk_marks = [str(item) for item in row.get("_risk_marks", [])]
            row["risk"] = self._risk_level_for_student(alerts=alerts, behavior_risks=risk_marks)
            row.pop("_risk_marks", None)
            if candidate_id in frame_identity_counter and not row.get("room"):
                row["room"] = "Exam Room"

        report = sorted(
            student_map.values(),
            key=lambda item: (
                0 if item.get("candidate_id") != "UNKNOWN" else 1,
                -int(item.get("alerts") or 0),
                str(item.get("candidate_id") or ""),
            ),
        )
        return report

    def _pick_primary_candidate(self, students_report: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not students_report:
            return None

        def score(item: dict[str, Any]) -> tuple[int, int, int]:
            candidate_id = str(item.get("candidate_id") or "")
            known_flag = 1 if candidate_id and candidate_id != "UNKNOWN" else 0
            alerts = int(item.get("alerts") or 0)
            risk_weight = {"high": 3, "medium": 2, "low": 1}.get(str(item.get("risk") or "low"), 1)
            return (known_flag, alerts, risk_weight)

        primary = max(students_report, key=score)
        return {
            "candidate_id": str(primary.get("candidate_id") or "UNKNOWN"),
            "name": str(primary.get("name") or "Unknown Candidate"),
            "email": str(primary.get("email") or ""),
            "room": str(primary.get("room") or ""),
            "alerts": int(primary.get("alerts") or 0),
            "risk": str(primary.get("risk") or "low"),
            "behaviors": list(primary.get("behaviors") or []),
        }

    def _advance_streak(self, streaks: dict[str, int], key: str, active: bool) -> int:
        streaks[key] = streaks.get(key, 0) + 1 if active else 0
        return streaks[key]

    def _support_signal_count(
        self,
        mediapipe_signals: dict[str, Any],
        phone_detections: list[dict[str, Any]],
        person_detections: list[dict[str, Any]],
    ) -> int:
        count = 0
        if phone_detections:
            count += 1
        if len(person_detections) > 1:
            count += 1
        if mediapipe_signals.get("hand_phone_alert"):
            count += 1
        if mediapipe_signals.get("face_missing"):
            count += 1
        return count

    def _phone_signal_is_strong(self, phone_detections: list[dict[str, Any]], frame_shape: tuple[int, int]) -> bool:
        if not phone_detections:
            return False
        frame_height, frame_width = frame_shape
        frame_area = max(1.0, float(frame_height * frame_width))
        best_area_ratio = 0.0
        best_confidence = 0.0
        for detection in phone_detections:
            x1, y1, x2, y2 = detection["box"]
            area = max(0.0, (x2 - x1) * (y2 - y1))
            best_area_ratio = max(best_area_ratio, area / frame_area)
            best_confidence = max(best_confidence, float(detection.get("confidence", 0.0)))
        return best_confidence >= 0.35 and best_area_ratio >= 0.05

    def _review_sample_interval_seconds(self) -> float:
        # Offline review should not skip over short glances or brief phone appearances.
        return min(self.sample_interval_seconds, 0.25)

    def _inference_conf_threshold(self) -> float:
        return min(self.conf_threshold, 0.25)

    def _should_keep_detection(self, label: str, confidence: float) -> bool:
        if label in {"cell phone", "mobile phone"}:
            return confidence >= min(self.conf_threshold, 0.35)
        return confidence >= self.conf_threshold

    def _event_cooldown_seconds(self, event_type: str) -> float:
        if event_type == "gaze":
            return 1.0
        if event_type == "head_pose":
            return 1.25
        if event_type in {"cell_phone", "hand_phone"}:
            return 0.75
        if event_type == "multiple_people":
            return 2.0
        if event_type == "face_missing":
            return 4.0
        if event_type == "behavior_model":
            return 2.5
        return self.incident_cooldown_seconds

    def _event_required_streak(self, event_type: str) -> int:
        if event_type in {"head_pose", "cell_phone", "hand_phone"}:
            return 1
        if event_type == "gaze":
            return 2
        if event_type == "face_missing":
            return max(self.min_signal_streak + 6, 10)
        return self.min_signal_streak

    def _is_face_missing_signal_active(
        self,
        timestamp_seconds: float,
        mediapipe_signals: dict[str, Any],
        person_detections: list[dict[str, Any]],
        frame_identity: dict[str, Any] | None,
        frame_face_matches: list[dict[str, Any]],
    ) -> bool:
        # Delay missing-face checks to avoid startup jitter and baseline calibration noise.
        if timestamp_seconds < 8.0:
            return False
        if not bool(mediapipe_signals.get("face_missing")):
            return False
        if int(mediapipe_signals.get("face_count") or 0) > 0:
            return False
        if person_detections:
            return False
        if frame_identity is not None or frame_face_matches:
            return False
        return True

    def _crossed_streak_threshold(self, previous_streak: int, current_streak: int, required_streak: int) -> bool:
        return previous_streak < required_streak <= current_streak

    def _head_pose_label(self, head_pose: str) -> str:
        if head_pose == "left":
            return "Quay dau sang trai"
        if head_pose == "right":
            return "Quay dau sang phai"
        if head_pose == "up":
            return "Ngang dau len tren"
        if head_pose == "down":
            return "Cui dau xuong duoi"
        return "Tu the dau bat thuong"

    def _head_pose_details(self, head_pose: str, strength: str) -> str:
        pose_copy = {
            "left": "sang trai",
            "right": "sang phai",
            "up": "len tren",
            "down": "xuong duoi",
        }.get(head_pose, "bat thuong")
        strength_copy = {
            "strong": "manh",
            "moderate": "vua",
            "none": "nhe",
        }.get(strength, strength)
        return f"Huong dau: {pose_copy}, muc do: {strength_copy}"

    def _gaze_label(self, direction: str) -> str:
        label_map = {
            "left": "Liec mat sang trai",
            "right": "Liec mat sang phai",
            "top_left": "Liec mat sang trai",
            "top_right": "Liec mat sang phai",
            "bottom_left": "Liec mat sang trai",
            "bottom_right": "Liec mat sang phai",
        }
        return label_map.get(direction, "Huong nhin lech khoi bai thi")

    def _gaze_details(self, direction: str) -> str:
        detail_map = {
            "left": "Huong nhin sang trai",
            "right": "Huong nhin sang phai",
            "top_left": "Huong nhin len tren ben trai",
            "top_right": "Huong nhin len tren ben phai",
            "bottom_left": "Huong nhin xuong duoi ben trai",
            "bottom_right": "Huong nhin xuong duoi ben phai",
        }
        return detail_map.get(direction, f"Huong nhin: {direction}")

    def _is_gaze_signal_active(
        self,
        timestamp_seconds: float,
        mediapipe_features: dict[str, Any],
        mediapipe_signals: dict[str, Any],
        phone_detections: list[dict[str, Any]],
    ) -> bool:
        if timestamp_seconds < self.gaze_warmup_seconds:
            return False
        if phone_detections:
            return False
        if mediapipe_features.get("head_pose") != "forward":
            return False
        if not mediapipe_signals.get("gaze_alert"):
            return False
        if mediapipe_signals.get("gaze_baseline_ready"):
            return True

        horizontal_delta = abs(float(mediapipe_features.get("gaze_horizontal_delta") or 0.0))
        vertical_delta = abs(float(mediapipe_features.get("gaze_vertical_delta") or 0.0))
        return max(horizontal_delta, vertical_delta) >= 0.22

    def _should_emit_behavior_incident(
        self,
        behavior_prediction: dict[str, Any],
        mediapipe_signals: dict[str, Any],
        phone_detections: list[dict[str, Any]],
        person_detections: list[dict[str, Any]],
        streak: int,
    ) -> bool:
        score = float(behavior_prediction.get("score") or 0.0)
        threshold = float(behavior_prediction.get("threshold") or self.behavior_model_service.score_threshold)
        support_count = self._support_signal_count(
            mediapipe_signals=mediapipe_signals,
            phone_detections=phone_detections,
            person_detections=person_detections,
        )
        has_hard_evidence = bool(phone_detections) or len(person_detections) > 1 or mediapipe_signals.get("hand_phone_alert")
        face_missing = bool(mediapipe_signals.get("face_missing"))
        if score >= max(0.96, threshold + 0.12):
            return streak >= self.min_signal_streak and (has_hard_evidence or face_missing)
        if score >= threshold and support_count >= 2 and has_hard_evidence:
            return streak >= self.min_signal_streak + 1
        return False

    def get_latest_result(self) -> dict[str, Any] | None:
        for file_path in self._iter_result_files():
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            data["result_filename"] = file_path.name
            data["result_path"] = str(file_path)
            return data
        return None

    def list_results(self, limit: int = 5) -> list[dict]:
        results = []
        for file_path in self._iter_result_files()[:limit]:
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            results.append(
                {
                    "filename": file_path.name,
                    "status": data.get("status", "unknown"),
                    "violations": data.get("summary", {}).get("total_violations", 0),
                    "analysis_mode": data.get("analysis_mode", "n/a"),
                }
            )
        return results

    def _label_for_class(self, names: dict[int, str] | list[str], class_id: int) -> str:
        if isinstance(names, dict):
            return str(names.get(class_id, class_id))
        if isinstance(names, list) and 0 <= class_id < len(names):
            return str(names[class_id])
        return str(class_id)

    def _prepare_snapshot_dir(self, video_path: Path) -> Path:
        snapshot_dir = self.results_dir / f"{video_path.stem}_snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for stale_file in snapshot_dir.glob("*.jpg"):
            stale_file.unlink(missing_ok=True)
        return snapshot_dir

    def _save_snapshot(
        self,
        frame,
        detections: list[dict[str, Any]],
        snapshot_dir: Path,
        frame_index: int,
        event_slug: str,
        headline: str,
    ) -> str | None:
        if cv2 is None:
            return None

        annotated = frame.copy()
        for detection in detections:
            x1, y1, x2, y2 = [int(value) for value in detection["box"]]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 82, 255), 2)
            tag = f"{detection['label']} {detection['confidence'] * 100:.0f}%"
            cv2.putText(
                annotated,
                tag,
                (x1, max(16, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

        cv2.putText(
            annotated,
            headline,
            (18, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (50, 50, 245),
            2,
            cv2.LINE_AA,
        )

        snapshot_name = f"frame_{frame_index:06d}_{event_slug}.jpg"
        snapshot_path = snapshot_dir / snapshot_name
        if not cv2.imwrite(str(snapshot_path), annotated):
            return None

        return f"/results/{snapshot_dir.name}/{snapshot_name}"

    def detect_from_video(self, video_path: str | Path) -> dict[str, Any]:
        self.apply_runtime_settings(get_ai_settings())
        source_path = Path(video_path)
        if not source_path.exists():
            raise FileNotFoundError(f"Khong tim thay video: {source_path}")

        if cv2 is None:
            result: dict[str, Any] = {
                "status": "skipped",
                "analysis_mode": "unavailable",
                "video_path": str(source_path),
                "summary": {"total_violations": 0, "reviewed_frames": 0},
                "incidents": [],
                "engines": {
                    "yolo": {
                        "enabled": False,
                        "confidence_threshold": self.conf_threshold,
                        "configured_extraction_interval_seconds": self.sample_interval_seconds,
                        "effective_extraction_interval_seconds": self._review_sample_interval_seconds(),
                    },
                    "mediapipe": self._mediapipe_status(),
                    "behavior_model": self.behavior_model_service.get_status(),
                    "face_recognition": self._face_recognition_status(),
                },
                "message": "Thieu OpenCV. Cai `pip install opencv-python-headless` de bat hau kiem YOLO.",
            }
            result["result_path"] = str(self._write_result(source_path, result))
            return result

        try:
            model = self._load_model()
        except Exception as exc:  # pragma: no cover
            result = {
                "status": "skipped",
                "analysis_mode": "unavailable",
                "video_path": str(source_path),
                "summary": {"total_violations": 0, "reviewed_frames": 0},
                "incidents": [],
                "engines": {
                    "yolo": {
                        "enabled": False,
                        "confidence_threshold": self.conf_threshold,
                        "configured_extraction_interval_seconds": self.sample_interval_seconds,
                        "effective_extraction_interval_seconds": self._review_sample_interval_seconds(),
                    },
                    "mediapipe": self._mediapipe_status(),
                    "behavior_model": self.behavior_model_service.get_status(),
                    "face_recognition": self._face_recognition_status(),
                },
                "message": str(exc),
            }
            result["result_path"] = str(self._write_result(source_path, result))
            return result

        capture = cv2.VideoCapture(str(source_path))
        if not capture.isOpened():
            raise RuntimeError("Khong the mo video de phan tich.")

        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        fps = fps if fps > 0 else 25.0
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration_seconds = round(frame_count / fps, 2) if frame_count > 0 else 0.0
        effective_sample_interval_seconds = self._review_sample_interval_seconds()
        sample_every_n_frames = max(1, int(round(fps * effective_sample_interval_seconds)))
        snapshot_dir = self._prepare_snapshot_dir(source_path)
        processing_started_at = time.perf_counter()

        incidents: list[dict[str, Any]] = []
        reviewed_frames = 0
        frame_index = 0
        last_incident_time: dict[str, float] = {}
        signal_streaks: dict[str, int] = {}
        frame_identity_counter: Counter[str] = Counter()
        behavior_status = self.behavior_model_service.get_status()
        mediapipe_status = self._mediapipe_status()
        face_recognition_status = self._face_recognition_status()
        yolo_inference_ms_total = 0.0
        yolo_pipeline_ms_total = 0.0
        yolo_speed_samples = 0
        compute_backend = "cpu"
        gpu_memory_peak_percent: float | None = None
        gpu_memory_peak_mb: float | None = None
        if torch is not None and torch.cuda.is_available():
            compute_backend = "cuda"
            try:
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
            except Exception:
                pass
        if mediapipe_status.get("enabled") and self.mediapipe_feature_service is not None:
            self.mediapipe_feature_service.reset_session_state()

        try:
            while True:
                success, frame = capture.read()
                if not success:
                    break

                if frame_index % sample_every_n_frames != 0:
                    frame_index += 1
                    continue

                reviewed_frames += 1
                timestamp_seconds = frame_index / fps
                prediction = model.predict(
                    source=frame,
                    conf=self._inference_conf_threshold(),
                    verbose=False,
                )[0]
                speed = getattr(prediction, "speed", None) or {}
                if speed:
                    inference_ms = float(speed.get("inference") or 0.0)
                    preprocess_ms = float(speed.get("preprocess") or 0.0)
                    postprocess_ms = float(speed.get("postprocess") or 0.0)
                    yolo_inference_ms_total += inference_ms
                    yolo_pipeline_ms_total += preprocess_ms + inference_ms + postprocess_ms
                    yolo_speed_samples += 1

                names = prediction.names if prediction.names is not None else {}
                boxes = prediction.boxes
                detections: list[dict[str, Any]] = []

                if boxes is not None and boxes.cls is not None and boxes.conf is not None and boxes.xyxy is not None:
                    class_ids = boxes.cls.tolist()
                    confidences = boxes.conf.tolist()
                    coordinates = boxes.xyxy.tolist()
                    for class_id, confidence, coord in zip(class_ids, confidences, coordinates):
                        label = self._label_for_class(names, int(class_id))
                        confidence_value = float(confidence)
                        if not self._should_keep_detection(label=label, confidence=confidence_value):
                            continue
                        detections.append(
                            {
                                "label": label,
                                "confidence": confidence_value,
                                "box": [float(value) for value in coord],
                            }
                        )

                person_detections = [item for item in detections if item["label"] == "person"]
                phone_detections = [
                    item for item in detections if item["label"] in {"cell phone", "mobile phone"}
                ]
                mediapipe_payload = {
                    "available": False,
                    "features": {},
                    "signals": {
                        "face_count": 0,
                        "head_pose_alert": False,
                        "head_pose_strong": False,
                        "head_pose_strength": "none",
                        "gaze_alert": False,
                        "gaze_baseline_ready": False,
                        "gaze_baseline_samples": 0,
                        "hand_phone_alert": False,
                        "face_missing": False,
                    },
                    "message": mediapipe_status.get("message", "MediaPipe unavailable."),
                }
                if mediapipe_status.get("enabled") and self.mediapipe_feature_service is not None:
                    mediapipe_payload = self.mediapipe_feature_service.extract(frame_bgr=frame, yolo_detections=detections)
                    mediapipe_status = self.mediapipe_feature_service.get_status()

                frame_face_matches: list[dict[str, Any]] = []
                frame_identity: dict[str, Any] | None = None
                if face_recognition_status.get("enabled") and self.face_recognition_service is not None:
                    frame_face_matches = self.face_recognition_service.identify_faces(frame_bgr=frame)
                    frame_identity = self.face_recognition_service.select_primary_identity(frame_face_matches)
                    if frame_identity is not None:
                        frame_identity_counter[self._identity_key(frame_identity)] += 1

                previous_multiple_people_streak = signal_streaks.get("multiple_people", 0)
                multiple_people_streak = self._advance_streak(
                    signal_streaks,
                    "multiple_people",
                    len(person_detections) > 1,
                )
                if (
                    self._crossed_streak_threshold(
                        previous_multiple_people_streak,
                        multiple_people_streak,
                        self._event_required_streak("multiple_people"),
                    )
                    and timestamp_seconds - last_incident_time.get("multiple_people", -999.0)
                    >= self._event_cooldown_seconds("multiple_people")
                ):
                    snapshot_url = self._save_snapshot(
                        frame=frame,
                        detections=person_detections,
                        snapshot_dir=snapshot_dir,
                        frame_index=frame_index,
                        event_slug="multiple_people",
                        headline="Phat hien nhieu nguoi",
                    )
                    incidents.append(
                        self._incident_with_identity(
                            {
                                "time": self._format_timestamp(timestamp_seconds),
                                "time_seconds": round(timestamp_seconds, 2),
                                "label": "Phat hien nhieu nguoi",
                                "confidence": f"{max(item['confidence'] for item in person_detections) * 100:.0f}%",
                                "risk": "high",
                                "event_type": "multiple_people",
                                "snapshot_url": snapshot_url,
                            },
                            identity=frame_identity,
                        )
                    )
                    last_incident_time["multiple_people"] = timestamp_seconds

                previous_phone_streak = signal_streaks.get("cell_phone", 0)
                phone_streak = self._advance_streak(
                    signal_streaks,
                    "cell_phone",
                    bool(phone_detections),
                )
                strong_phone_signal = self._phone_signal_is_strong(
                    phone_detections=phone_detections,
                    frame_shape=frame.shape[:2],
                )
                if (
                    (
                        self._crossed_streak_threshold(
                            previous_phone_streak,
                            phone_streak,
                            self._event_required_streak("cell_phone"),
                        )
                        or strong_phone_signal
                    )
                    and timestamp_seconds - last_incident_time.get("cell_phone", -999.0)
                    >= self._event_cooldown_seconds("cell_phone")
                ):
                    snapshot_url = self._save_snapshot(
                        frame=frame,
                        detections=phone_detections,
                        snapshot_dir=snapshot_dir,
                        frame_index=frame_index,
                        event_slug="cell_phone",
                        headline="Su dung dien thoai",
                    )
                    incidents.append(
                        self._incident_with_identity(
                            {
                                "time": self._format_timestamp(timestamp_seconds),
                                "time_seconds": round(timestamp_seconds, 2),
                                "label": "Su dung dien thoai",
                                "confidence": f"{max(item['confidence'] for item in phone_detections) * 100:.0f}%",
                                "risk": "high",
                                "event_type": "cell_phone",
                                "snapshot_url": snapshot_url,
                            },
                            identity=frame_identity,
                        )
                    )
                    last_incident_time["cell_phone"] = timestamp_seconds

                mediapipe_features = mediapipe_payload.get("features", {})
                mediapipe_signals = mediapipe_payload.get("signals", {})

                previous_head_pose_streak = signal_streaks.get("head_pose", 0)
                head_pose_streak = self._advance_streak(
                    signal_streaks,
                    "head_pose",
                    bool(
                        mediapipe_signals.get("head_pose_strong")
                        and mediapipe_features.get("head_pose") in {"left", "right", "down"}
                        and not phone_detections
                        and len(person_detections) <= 1
                    )
                    or bool(
                        mediapipe_signals.get("head_pose_alert")
                        and mediapipe_features.get("head_pose") == "up"
                        and not phone_detections
                        and len(person_detections) <= 1
                    ),
                )
                if (
                    self._crossed_streak_threshold(
                        previous_head_pose_streak,
                        head_pose_streak,
                        self._event_required_streak("head_pose"),
                    )
                    and timestamp_seconds - last_incident_time.get("head_pose", -999.0)
                    >= self._event_cooldown_seconds("head_pose")
                ):
                    head_pose = mediapipe_features.get("head_pose") or "bat thuong"
                    head_pose_strength = mediapipe_signals.get("head_pose_strength", "none")
                    confidence = "92%" if mediapipe_signals.get("head_pose_strong") else "82%"
                    head_pose_label = self._head_pose_label(head_pose)
                    snapshot_targets = person_detections or detections[:1]
                    snapshot_url = self._save_snapshot(
                        frame=frame,
                        detections=snapshot_targets,
                        snapshot_dir=snapshot_dir,
                        frame_index=frame_index,
                        event_slug="head_pose",
                        headline=f"Huong dau {head_pose}",
                    )
                    incidents.append(
                        self._incident_with_identity(
                            {
                                "time": self._format_timestamp(timestamp_seconds),
                                "time_seconds": round(timestamp_seconds, 2),
                                "label": head_pose_label,
                                "confidence": confidence,
                                "risk": "medium",
                                "event_type": "head_pose",
                                "snapshot_url": snapshot_url,
                                "details": self._head_pose_details(head_pose, head_pose_strength),
                            },
                            identity=frame_identity,
                        )
                    )
                    last_incident_time["head_pose"] = timestamp_seconds

                previous_gaze_streak = signal_streaks.get("gaze", 0)
                gaze_streak = self._advance_streak(
                    signal_streaks,
                    "gaze",
                    self._is_gaze_signal_active(
                        timestamp_seconds=timestamp_seconds,
                        mediapipe_features=mediapipe_features,
                        mediapipe_signals=mediapipe_signals,
                        phone_detections=phone_detections,
                    ),
                )
                if (
                    self._crossed_streak_threshold(
                        previous_gaze_streak,
                        gaze_streak,
                        self._event_required_streak("gaze"),
                    )
                    and timestamp_seconds - last_incident_time.get("gaze", -999.0)
                    >= self._event_cooldown_seconds("gaze")
                ):
                    direction = mediapipe_features.get("gaze_direction") or "unknown"
                    gaze_label = self._gaze_label(direction)
                    snapshot_targets = person_detections or detections[:1]
                    snapshot_url = self._save_snapshot(
                        frame=frame,
                        detections=snapshot_targets,
                        snapshot_dir=snapshot_dir,
                        frame_index=frame_index,
                        event_slug="gaze",
                        headline=f"Huong nhin {direction}",
                    )
                    incidents.append(
                        self._incident_with_identity(
                            {
                                "time": self._format_timestamp(timestamp_seconds),
                                "time_seconds": round(timestamp_seconds, 2),
                                "label": gaze_label,
                                "confidence": "84%",
                                "risk": "medium",
                                "event_type": "gaze",
                                "snapshot_url": snapshot_url,
                                "details": self._gaze_details(direction),
                            },
                            identity=frame_identity,
                        )
                    )
                    last_incident_time["gaze"] = timestamp_seconds

                previous_hand_phone_streak = signal_streaks.get("hand_phone", 0)
                hand_phone_streak = self._advance_streak(
                    signal_streaks,
                    "hand_phone",
                    bool(mediapipe_signals.get("hand_phone_alert")),
                )
                if (
                    self._crossed_streak_threshold(
                        previous_hand_phone_streak,
                        hand_phone_streak,
                        self._event_required_streak("hand_phone"),
                    )
                    and timestamp_seconds - last_incident_time.get("hand_phone", -999.0)
                    >= self._event_cooldown_seconds("hand_phone")
                ):
                    snapshot_targets = phone_detections or person_detections or detections[:1]
                    snapshot_url = self._save_snapshot(
                        frame=frame,
                        detections=snapshot_targets,
                        snapshot_dir=snapshot_dir,
                        frame_index=frame_index,
                        event_slug="hand_phone",
                        headline="Tay gan dien thoai",
                    )
                    incidents.append(
                        self._incident_with_identity(
                            {
                                "time": self._format_timestamp(timestamp_seconds),
                                "time_seconds": round(timestamp_seconds, 2),
                                "label": "Tay co tuong tac voi dien thoai",
                                "confidence": "90%",
                                "risk": "high",
                                "event_type": "hand_phone",
                                "snapshot_url": snapshot_url,
                            },
                            identity=frame_identity,
                        )
                    )
                    last_incident_time["hand_phone"] = timestamp_seconds

                previous_face_missing_streak = signal_streaks.get("face_missing", 0)
                face_missing_streak = self._advance_streak(
                    signal_streaks,
                    "face_missing",
                    self._is_face_missing_signal_active(
                        timestamp_seconds=timestamp_seconds,
                        mediapipe_signals=mediapipe_signals,
                        person_detections=person_detections,
                        frame_identity=frame_identity,
                        frame_face_matches=frame_face_matches,
                    ),
                )
                if (
                    self._crossed_streak_threshold(
                        previous_face_missing_streak,
                        face_missing_streak,
                        self._event_required_streak("face_missing"),
                    )
                    and timestamp_seconds - last_incident_time.get("face_missing", -999.0)
                    >= self._event_cooldown_seconds("face_missing")
                ):
                    incidents.append(
                        self._incident_with_identity(
                            {
                                "time": self._format_timestamp(timestamp_seconds),
                                "time_seconds": round(timestamp_seconds, 2),
                                "label": "Khong tim thay khuon mat",
                                "confidence": "80%",
                                "risk": "medium",
                                "event_type": "face_missing",
                                "snapshot_url": None,
                            },
                            identity=frame_identity,
                        )
                    )
                    last_incident_time["face_missing"] = timestamp_seconds

                if behavior_status.get("enabled"):
                    behavior_features = self.behavior_model_service.build_feature_record(
                        detections,
                        vision_features=mediapipe_features,
                    )
                    behavior_prediction = self.behavior_model_service.predict(behavior_features)
                    behavior_streak = self._advance_streak(
                        signal_streaks,
                        "behavior_model",
                        bool(
                            behavior_prediction.get("is_suspicious")
                            and (
                                phone_detections
                                or len(person_detections) > 1
                                or mediapipe_signals.get("hand_phone_alert")
                                or mediapipe_signals.get("face_missing")
                            )
                        ),
                    )
                    if (
                        self._should_emit_behavior_incident(
                            behavior_prediction=behavior_prediction,
                            mediapipe_signals=mediapipe_signals,
                            phone_detections=phone_detections,
                            person_detections=person_detections,
                            streak=behavior_streak,
                        )
                        and timestamp_seconds - last_incident_time.get("behavior_model", -999.0)
                        >= self._event_cooldown_seconds("behavior_model")
                    ):
                        reason_copy = ", ".join(behavior_prediction.get("reasons", []))
                        headline = f"Nghi van hanh vi {behavior_prediction['score'] * 100:.0f}%"
                        snapshot_targets = phone_detections or person_detections or detections[:1]
                        snapshot_url = self._save_snapshot(
                            frame=frame,
                            detections=snapshot_targets,
                            snapshot_dir=snapshot_dir,
                            frame_index=frame_index,
                            event_slug="behavior_model",
                            headline=headline,
                        )
                        incidents.append(
                            self._incident_with_identity(
                                {
                                    "time": self._format_timestamp(timestamp_seconds),
                                    "time_seconds": round(timestamp_seconds, 2),
                                    "label": "Mo hinh hanh vi danh gia nghi van",
                                    "confidence": f"{behavior_prediction['score'] * 100:.0f}%",
                                    "risk": behavior_prediction["risk"],
                                    "event_type": "behavior_model",
                                    "snapshot_url": snapshot_url,
                                    "details": reason_copy or "Khung hinh co dac trung gan voi mau nghi van trong dataset.",
                                },
                                identity=frame_identity,
                            )
                        )
                        last_incident_time["behavior_model"] = timestamp_seconds

                if len(incidents) >= self.max_incidents:
                    break

                frame_index += 1
        finally:
            capture.release()

        dominant_identity: dict[str, str] | None = None
        if frame_identity_counter:
            dominant_candidate_id = frame_identity_counter.most_common(1)[0][0]
            for incident in incidents:
                if str(incident.get("candidate_id") or "") == dominant_candidate_id:
                    dominant_identity = {
                        "candidate_id": str(incident.get("candidate_id") or dominant_candidate_id),
                        "name": str(incident.get("candidate_name") or dominant_candidate_id),
                        "email": str(incident.get("candidate_email") or ""),
                        "room": str(incident.get("candidate_room") or ""),
                    }
                    break

        if dominant_identity is not None:
            for incident in incidents:
                if str(incident.get("candidate_id") or "UNKNOWN") == "UNKNOWN":
                    incident.update(self._identity_payload(dominant_identity))

        incidents = self._deduplicate_incidents_per_second(incidents)

        students_report = self._build_students_report(
            incidents=incidents,
            frame_identity_counter=frame_identity_counter,
        )
        primary_candidate = self._pick_primary_candidate(students_report)

        processing_runtime_seconds = max(0.0, time.perf_counter() - processing_started_at)
        avg_frame_processing_ms = (
            round((processing_runtime_seconds * 1000.0) / reviewed_frames, 2) if reviewed_frames > 0 else None
        )
        effective_processing_fps = round(reviewed_frames / processing_runtime_seconds, 2) if processing_runtime_seconds > 0 else None
        yolo_avg_inference_ms = (
            round(yolo_inference_ms_total / yolo_speed_samples, 2) if yolo_speed_samples > 0 else None
        )
        yolo_avg_pipeline_ms = (
            round(yolo_pipeline_ms_total / yolo_speed_samples, 2) if yolo_speed_samples > 0 else None
        )
        if torch is not None and compute_backend == "cuda":
            try:
                torch.cuda.synchronize()
                peak_bytes = float(torch.cuda.max_memory_allocated())
                total_bytes = float(torch.cuda.get_device_properties(0).total_memory)
                if total_bytes > 0:
                    gpu_memory_peak_percent = round((peak_bytes / total_bytes) * 100.0, 2)
                gpu_memory_peak_mb = round(peak_bytes / (1024.0 * 1024.0), 2)
            except Exception:
                gpu_memory_peak_percent = None
                gpu_memory_peak_mb = None

        summary = {
            "total_violations": len(incidents),
            "reviewed_frames": reviewed_frames,
            "video_frames": frame_count,
            "duration_seconds": duration_seconds,
            "fps": round(fps, 2),
            "processing_runtime_seconds": round(processing_runtime_seconds, 3),
            "avg_frame_processing_ms": avg_frame_processing_ms,
            "effective_processing_fps": effective_processing_fps,
            "yolo_avg_inference_ms": yolo_avg_inference_ms,
            "yolo_avg_pipeline_ms": yolo_avg_pipeline_ms,
            "compute_backend": compute_backend,
            "gpu_memory_peak_percent": gpu_memory_peak_percent,
            "gpu_memory_peak_mb": gpu_memory_peak_mb,
            "configured_extraction_interval_seconds": self.sample_interval_seconds,
            "effective_extraction_interval_seconds": effective_sample_interval_seconds,
            "behavior_model_enabled": bool(behavior_status.get("enabled")),
            "mediapipe_enabled": bool(mediapipe_status.get("enabled")),
            "face_recognition_enabled": bool(face_recognition_status.get("enabled")),
            "recognized_candidates": len([row for row in students_report if row.get("candidate_id") != "UNKNOWN"]),
            "students_report": students_report,
            "primary_candidate": primary_candidate,
        }
        result = {
            "status": "completed",
            "analysis_mode": self._analysis_mode(),
            "video_path": str(source_path),
            "summary": summary,
            "incidents": incidents,
            "students_report": students_report,
            "primary_candidate": primary_candidate,
            "engines": {
                "yolo": {
                    "enabled": True,
                    "model": self.model_name,
                    "confidence_threshold": self.conf_threshold,
                    "configured_extraction_interval_seconds": self.sample_interval_seconds,
                    "effective_extraction_interval_seconds": effective_sample_interval_seconds,
                },
                "mediapipe": mediapipe_status,
                "behavior_model": behavior_status,
                "face_recognition": face_recognition_status,
            },
            "message": (
                f"Phan tich xong {reviewed_frames} frame mau, ghi nhan {len(incidents)} su co. "
                f"Che do: {self._analysis_mode()}."
            ),
        }
        result["result_path"] = str(self._write_result(source_path, result))
        return result
