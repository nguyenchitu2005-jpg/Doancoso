from __future__ import annotations

import json
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


class DetectionService:
    """Run a YOLO-based review pass and save timeline snapshots."""

    def __init__(
        self,
        weights_dir: str | Path = "weights",
        results_dir: str | Path = "results",
        model_name: str = "yolo11n.pt",
        conf_threshold: float = 0.35,
        sample_every_n_frames: int = 30,
        incident_cooldown_seconds: float = 3.0,
        max_incidents: int = 40,
    ) -> None:
        self.weights_dir = Path(weights_dir)
        self.results_dir = Path(results_dir)
        self.model_name = model_name
        self.conf_threshold = conf_threshold
        self.sample_every_n_frames = max(1, sample_every_n_frames)
        self.incident_cooldown_seconds = max(0.0, incident_cooldown_seconds)
        self.max_incidents = max(1, max_incidents)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.weights_dir.mkdir(parents=True, exist_ok=True)
        self._model = None

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
        snapshot_dir = self._prepare_snapshot_dir(source_path)

        incidents: list[dict[str, Any]] = []
        reviewed_frames = 0
        frame_index = 0
        last_incident_time: dict[str, float] = {}

        try:
            while True:
                success, frame = capture.read()
                if not success:
                    break

                if frame_index % self.sample_every_n_frames != 0:
                    frame_index += 1
                    continue

                reviewed_frames += 1
                timestamp_seconds = frame_index / fps
                prediction = model.predict(source=frame, conf=self.conf_threshold, verbose=False)[0]

                names = prediction.names if prediction.names is not None else {}
                boxes = prediction.boxes
                detections: list[dict[str, Any]] = []

                if boxes is not None and boxes.cls is not None and boxes.conf is not None and boxes.xyxy is not None:
                    class_ids = boxes.cls.tolist()
                    confidences = boxes.conf.tolist()
                    coordinates = boxes.xyxy.tolist()
                    for class_id, confidence, coord in zip(class_ids, confidences, coordinates):
                        detections.append(
                            {
                                "label": self._label_for_class(names, int(class_id)),
                                "confidence": float(confidence),
                                "box": [float(value) for value in coord],
                            }
                        )

                person_detections = [item for item in detections if item["label"] == "person"]
                phone_detections = [
                    item for item in detections if item["label"] in {"cell phone", "mobile phone"}
                ]

                if (
                    len(person_detections) > 1
                    and timestamp_seconds - last_incident_time.get("multiple_people", -999.0)
                    >= self.incident_cooldown_seconds
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
                        {
                            "time": self._format_timestamp(timestamp_seconds),
                            "time_seconds": round(timestamp_seconds, 2),
                            "label": "Phat hien nhieu nguoi",
                            "confidence": f"{max(item['confidence'] for item in person_detections) * 100:.0f}%",
                            "risk": "high",
                            "event_type": "multiple_people",
                            "snapshot_url": snapshot_url,
                        }
                    )
                    last_incident_time["multiple_people"] = timestamp_seconds

                if (
                    phone_detections
                    and timestamp_seconds - last_incident_time.get("cell_phone", -999.0)
                    >= self.incident_cooldown_seconds
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
                        {
                            "time": self._format_timestamp(timestamp_seconds),
                            "time_seconds": round(timestamp_seconds, 2),
                            "label": "Su dung dien thoai",
                            "confidence": f"{max(item['confidence'] for item in phone_detections) * 100:.0f}%",
                            "risk": "high",
                            "event_type": "cell_phone",
                            "snapshot_url": snapshot_url,
                        }
                    )
                    last_incident_time["cell_phone"] = timestamp_seconds

                if len(incidents) >= self.max_incidents:
                    break

                frame_index += 1
        finally:
            capture.release()

        summary = {
            "total_violations": len(incidents),
            "reviewed_frames": reviewed_frames,
            "video_frames": frame_count,
            "duration_seconds": duration_seconds,
            "fps": round(fps, 2),
        }
        result = {
            "status": "completed",
            "analysis_mode": "yolo",
            "video_path": str(source_path),
            "summary": summary,
            "incidents": incidents,
            "message": f"Phan tich xong {reviewed_frames} frame mau, ghi nhan {len(incidents)} su co.",
        }
        result["result_path"] = str(self._write_result(source_path, result))
        return result
