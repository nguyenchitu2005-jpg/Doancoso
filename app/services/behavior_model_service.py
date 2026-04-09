from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import joblib
except ImportError:  # pragma: no cover
    joblib = None

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None


class BehaviorModelService:
    """Load and run the tabular suspicious-behavior classifier."""

    def __init__(
        self,
        model_path: str | Path = "models/suspicious_behavior_model.joblib",
        score_threshold: float = 0.82,
    ) -> None:
        self.model_path = Path(model_path)
        self.score_threshold = max(0.0, min(score_threshold, 1.0))
        self._artifact: dict[str, Any] | None = None
        self._load_attempted = False
        self._load_error: str | None = None

    def _load_artifact(self) -> dict[str, Any] | None:
        if self._load_attempted:
            return self._artifact

        self._load_attempted = True

        if joblib is None or pd is None:
            self._load_error = "Thieu joblib hoac pandas. Hay cai them dependency de bat behavior model."
            return None

        if not self.model_path.exists():
            self._load_error = f"Chua tim thay model hanh vi tai: {self.model_path}"
            return None

        try:
            artifact = joblib.load(self.model_path)
        except Exception as exc:  # pragma: no cover
            self._load_error = f"Khong the nap behavior model: {exc}"
            return None

        if not isinstance(artifact, dict) or "pipeline" not in artifact:
            self._load_error = "Artifact behavior model khong hop le."
            return None

        self._artifact = artifact
        self._load_error = None
        return artifact

    def is_available(self) -> bool:
        return self._load_artifact() is not None

    def get_status(self) -> dict[str, Any]:
        artifact = self._load_artifact()
        if artifact is None:
            return {
                "enabled": False,
                "model_path": str(self.model_path),
                "threshold": self.score_threshold,
                "message": self._load_error or "Behavior model unavailable.",
            }

        return {
            "enabled": True,
            "model_path": str(self.model_path),
            "threshold": self.score_threshold,
            "trained_at": artifact.get("trained_at"),
            "metrics": artifact.get("metrics", {}),
            "message": "Behavior model san sang.",
        }

    def _primary_detection(self, detections: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
        matches = [item for item in detections if item.get("label") == label]
        if not matches:
            return None
        return max(matches, key=lambda item: float(item.get("confidence", 0.0)))

    def _box_center(self, box: list[float]) -> tuple[float, float]:
        x1, y1, x2, y2 = box
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    def _empty_feature_record(self) -> dict[str, Any]:
        return {
            "face_present": 0,
            "no_of_face": 0,
            "face_x": 0.0,
            "face_y": 0.0,
            "face_w": 0.0,
            "face_h": 0.0,
            "left_eye_x": None,
            "left_eye_y": None,
            "right_eye_x": None,
            "right_eye_y": None,
            "nose_tip_x": None,
            "nose_tip_y": None,
            "mouth_x": None,
            "mouth_y": None,
            "face_conf": 0.0,
            "hand_count": 0,
            "left_hand_x": 0.0,
            "left_hand_y": 0.0,
            "right_hand_x": 0.0,
            "right_hand_y": 0.0,
            "hand_obj_interaction": 0,
            "head_pose": None,
            "head_pitch": None,
            "head_yaw": None,
            "head_roll": None,
            "phone_present": 0,
            "phone_loc_x": 0.0,
            "phone_loc_y": 0.0,
            "phone_conf": 0.0,
            "gaze_on_script": None,
            "gaze_direction": None,
            "gazePoint_x": None,
            "gazePoint_y": None,
            "pupil_left_x": None,
            "pupil_left_y": None,
            "pupil_right_x": None,
            "pupil_right_y": None,
        }

    def build_feature_record(
        self,
        detections: list[dict[str, Any]],
        vision_features: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self._empty_feature_record()
        if vision_features:
            for key, value in vision_features.items():
                if key in record:
                    record[key] = value

        people = [item for item in detections if item.get("label") == "person"]
        phones = [item for item in detections if item.get("label") in {"cell phone", "mobile phone"}]
        primary_person = self._primary_detection(detections, "person")
        primary_phone = None
        if phones:
            primary_phone = max(phones, key=lambda item: float(item.get("confidence", 0.0)))

        if primary_person is not None and not int(record.get("face_present") or 0):
            px1, py1, px2, py2 = [float(value) for value in primary_person["box"]]
            record["face_present"] = 1
            record["face_x"] = px1
            record["face_y"] = py1
            record["face_w"] = max(0.0, px2 - px1)
            record["face_h"] = max(0.0, py2 - py1)
            record["face_conf"] = float(primary_person.get("confidence", 0.0)) * 100.0

        if primary_phone is not None:
            phone_loc_x, phone_loc_y = self._box_center([float(value) for value in primary_phone["box"]])
            record["phone_present"] = 1
            record["phone_loc_x"] = phone_loc_x
            record["phone_loc_y"] = phone_loc_y
            record["phone_conf"] = float(primary_phone.get("confidence", 0.0))

        record["no_of_face"] = max(int(record.get("no_of_face") or 0), len(people))
        return record

    def _risk_for_score(self, score: float) -> str:
        if score >= 0.85:
            return "high"
        if score >= self.score_threshold:
            return "medium"
        return "low"

    def _build_reasons(self, feature_record: dict[str, Any], score: float) -> list[str]:
        reasons: list[str] = []
        if feature_record.get("phone_present") == 1:
            reasons.append("Khung hinh co dien thoai")
        if feature_record.get("hand_obj_interaction") == 1:
            reasons.append("Tay dang tuong tac voi vat the")
        if int(feature_record.get("no_of_face") or 0) > 1:
            reasons.append("Khung hinh co nhieu nguoi")
        if feature_record.get("face_present") == 0:
            reasons.append("Thi sinh roi khoi khung hinh")
        if feature_record.get("head_pose") in {"left", "right", "down"}:
            reasons.append(f"Tu the dau bat thuong: {feature_record['head_pose']}")
        if feature_record.get("gaze_on_script") == 0 and feature_record.get("gaze_direction") not in {None, "center"}:
            reasons.append(f"Huong nhin lech: {feature_record['gaze_direction']}")
        if not reasons and score >= self.score_threshold:
            reasons.append("Mau dac trung gan voi hanh vi nghi van trong dataset")
        return reasons

    def predict(self, feature_record: dict[str, Any]) -> dict[str, Any]:
        artifact = self._load_artifact()
        if artifact is None:
            return {
                "available": False,
                "message": self._load_error or "Behavior model unavailable.",
            }

        feature_columns: list[str] = artifact.get("feature_columns", [])
        row = {column: feature_record.get(column) for column in feature_columns}
        frame = pd.DataFrame([row])

        pipeline = artifact["pipeline"]
        probabilities = pipeline.predict_proba(frame)[0]
        score = float(probabilities[1]) if len(probabilities) > 1 else float(probabilities[0])
        predicted_label = int(pipeline.predict(frame)[0])

        return {
            "available": True,
            "score": score,
            "threshold": self.score_threshold,
            "predicted_label": predicted_label,
            "is_suspicious": score >= self.score_threshold,
            "risk": self._risk_for_score(score),
            "reasons": self._build_reasons(feature_record, score),
            "metrics": artifact.get("metrics", {}),
            "trained_at": artifact.get("trained_at"),
        }
