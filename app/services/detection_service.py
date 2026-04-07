from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover
    YOLO = None


class DetectionService:
    """Run a lightweight YOLO-based review pass on an uploaded video."""

    def __init__(
        self,
        weights_dir: str | Path = "weights",
        results_dir: str | Path = "results",
        model_name: str = "yolo11n.pt",
        conf_threshold: float = 0.35,
        sample_every_n_frames: int = 30,
    ) -> None:
        self.weights_dir = Path(weights_dir)
        self.results_dir = Path(results_dir)
        self.model_name = model_name
        self.conf_threshold = conf_threshold
        self.sample_every_n_frames = sample_every_n_frames
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.weights_dir.mkdir(parents=True, exist_ok=True)
        self._model = None

    def _resolve_model_source(self) -> str | Path:
        explicit_weight = self.weights_dir / self.model_name
        return explicit_weight

    def _load_model(self):
        if YOLO is None:
            raise RuntimeError("Thiếu thư viện ultralytics. Hãy cài `pip install ultralytics`.")
        if self._model is None:
            self._model = YOLO(str(self._resolve_model_source()))
        return self._model

    def _format_timestamp(self, seconds: float) -> str:
        total_seconds = max(0, int(seconds))
        return str(timedelta(seconds=total_seconds)).rjust(8, "0")

    def _write_result(self, video_path: Path, payload: dict) -> Path:
        output_path = self.results_dir / f"{video_path.stem}.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    def list_results(self, limit: int = 5) -> list[dict]:
        results = []
        for file_path in sorted(self.results_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]:
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

    def detect_from_video(self, video_path: str | Path) -> dict:
        source_path = Path(video_path)
        if not source_path.exists():
            raise FileNotFoundError(f"Không tìm thấy video: {source_path}")

        if cv2 is None:
            result = {
                "status": "skipped",
                "analysis_mode": "unavailable",
                "video_path": str(source_path),
                "summary": {"total_violations": 0, "reviewed_frames": 0},
                "incidents": [],
                "message": "Thiếu OpenCV. Hãy cài `pip install opencv-python-headless` để bật hậu kiểm YOLO.",
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
            raise RuntimeError("Không thể mở video để phân tích.")

        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        incidents: list[dict] = []
        reviewed_frames = 0
        frame_index = 0

        try:
            while True:
                success, frame = capture.read()
                if not success:
                    break

                if frame_index % self.sample_every_n_frames != 0:
                    frame_index += 1
                    continue

                reviewed_frames += 1
                prediction = model.predict(source=frame, conf=self.conf_threshold, verbose=False)[0]
                names = prediction.names
                classes = prediction.boxes.cls.tolist() if prediction.boxes is not None else []
                confidences = prediction.boxes.conf.tolist() if prediction.boxes is not None else []

                people_conf = [conf for cls, conf in zip(classes, confidences) if names[int(cls)] == "person"]
                phone_conf = [conf for cls, conf in zip(classes, confidences) if names[int(cls)] in {"cell phone", "mobile phone"}]

                if len(people_conf) > 1:
                    incidents.append(
                        {
                            "time": self._format_timestamp(frame_index / fps),
                            "label": "Nhiều người trong khung hình",
                            "confidence": f"{max(people_conf) * 100:.0f}%",
                            "risk": "high",
                        }
                    )

                if phone_conf:
                    incidents.append(
                        {
                            "time": self._format_timestamp(frame_index / fps),
                            "label": "Sử dụng điện thoại",
                            "confidence": f"{max(phone_conf) * 100:.0f}%",
                            "risk": "high",
                        }
                    )

                frame_index += 1
        finally:
            capture.release()

        summary = {
            "total_violations": len(incidents),
            "reviewed_frames": reviewed_frames,
            "video_frames": frame_count,
            "fps": round(fps, 2),
        }
        result = {
            "status": "completed",
            "analysis_mode": "yolo",
            "video_path": str(source_path),
            "summary": summary,
            "incidents": incidents[:20],
            "message": f"Phân tích xong {reviewed_frames} frame mẫu, ghi nhận {len(incidents)} tín hiệu nghi ngờ.",
        }
        result["result_path"] = str(self._write_result(source_path, result))
        return result
