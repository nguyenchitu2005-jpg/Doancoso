from __future__ import annotations

import math
from typing import Any

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import mediapipe as mp
except ImportError:  # pragma: no cover
    mp = None


class MediapipeFeatureService:
    """Extract face mesh, iris, hand, gaze, and head-pose features from frames."""

    FACE_NOSE_TIP = 1
    FACE_CHIN = 152
    FACE_LEFT_EYE_CORNER = 33
    FACE_RIGHT_EYE_CORNER = 263
    FACE_MOUTH_LEFT = 61
    FACE_MOUTH_RIGHT = 291
    LEFT_EYE = {"outer": 33, "inner": 133, "upper": 159, "lower": 145}
    RIGHT_EYE = {"outer": 362, "inner": 263, "upper": 386, "lower": 374}
    LEFT_IRIS = [468, 469, 470, 471, 472]
    RIGHT_IRIS = [473, 474, 475, 476, 477]
    EYE_CENTER_LANDMARKS = {"left": [33, 133, 159, 145], "right": [362, 263, 386, 374]}
    PNP_MODEL_POINTS = [
        (0.0, 0.0, 0.0),
        (0.0, -330.0, -65.0),
        (-225.0, 170.0, -135.0),
        (225.0, 170.0, -135.0),
        (-150.0, -150.0, -125.0),
        (150.0, -150.0, -125.0),
    ]

    def __init__(
        self,
        max_num_faces: int = 3,
        max_num_hands: int = 2,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        self.max_num_faces = max(1, max_num_faces)
        self.max_num_hands = max(1, max_num_hands)
        self.min_detection_confidence = min_detection_confidence
        self.min_tracking_confidence = min_tracking_confidence
        self._face_mesh = None
        self._hands = None
        self._load_attempted = False
        self._load_error: str | None = None
        self._gaze_baseline_max_samples = 60
        self._gaze_baseline_min_samples = 10
        self.reset_session_state()

    def reset_session_state(self) -> None:
        self._gaze_baseline_horizontal = 0.0
        self._gaze_baseline_vertical = 0.0
        self._gaze_baseline_samples = 0

    def _ensure_models(self) -> bool:
        if self._load_attempted:
            return self._face_mesh is not None and self._hands is not None

        self._load_attempted = True
        if mp is None or cv2 is None:
            self._load_error = "Thieu mediapipe hoac opencv de bat face mesh va hands."
            return False

        try:
            self._face_mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=self.max_num_faces,
                refine_landmarks=True,
                min_detection_confidence=self.min_detection_confidence,
                min_tracking_confidence=self.min_tracking_confidence,
            )
            self._hands = mp.solutions.hands.Hands(
                static_image_mode=False,
                max_num_hands=self.max_num_hands,
                min_detection_confidence=self.min_detection_confidence,
                min_tracking_confidence=self.min_tracking_confidence,
            )
        except Exception as exc:  # pragma: no cover
            self._face_mesh = None
            self._hands = None
            self._load_error = f"Khong the khoi tao MediaPipe: {exc}"
            return False

        self._load_error = None
        return True

    def is_available(self) -> bool:
        return self._ensure_models()

    def get_status(self) -> dict[str, Any]:
        available = self._ensure_models()
        return {
            "enabled": available,
            "message": "MediaPipe san sang." if available else (self._load_error or "MediaPipe unavailable."),
            "max_num_faces": self.max_num_faces,
            "max_num_hands": self.max_num_hands,
        }

    def close(self) -> None:
        if self._face_mesh is not None:
            self._face_mesh.close()
        if self._hands is not None:
            self._hands.close()
        self._face_mesh = None
        self._hands = None
        self._load_attempted = False
        self.reset_session_state()

    def _empty_features(self) -> dict[str, Any]:
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

    def _normalized_to_pixel(self, landmark: Any, width: int, height: int) -> tuple[float, float]:
        return (float(landmark.x) * width, float(landmark.y) * height)

    def _landmark_pixel(self, landmarks: list[Any], index: int, width: int, height: int) -> tuple[float, float]:
        return self._normalized_to_pixel(landmarks[index], width, height)

    def _mean_point(self, points: list[tuple[float, float]]) -> tuple[float, float]:
        if not points:
            return (0.0, 0.0)
        x = sum(point[0] for point in points) / len(points)
        y = sum(point[1] for point in points) / len(points)
        return (x, y)

    def _get_face_bbox(self, landmarks: list[Any], width: int, height: int) -> tuple[float, float, float, float]:
        xs = [float(point.x) * width for point in landmarks]
        ys = [float(point.y) * height for point in landmarks]
        min_x = max(0.0, min(xs))
        min_y = max(0.0, min(ys))
        max_x = min(float(width), max(xs))
        max_y = min(float(height), max(ys))
        return (min_x, min_y, max(0.0, max_x - min_x), max(0.0, max_y - min_y))

    def _select_primary_face(self, face_landmarks: list[Any], width: int, height: int) -> tuple[list[Any] | None, int]:
        if not face_landmarks:
            return None, 0

        def area(face: Any) -> float:
            _, _, w, h = self._get_face_bbox(face.landmark, width, height)
            return w * h

        primary = max(face_landmarks, key=area)
        return primary.landmark, len(face_landmarks)

    def _estimate_head_pose(
        self,
        landmarks: list[Any],
        width: int,
        height: int,
    ) -> tuple[str | None, float | None, float | None, float | None]:
        if cv2 is None:
            return (None, None, None, None)

        image_points = [
            self._landmark_pixel(landmarks, self.FACE_NOSE_TIP, width, height),
            self._landmark_pixel(landmarks, self.FACE_CHIN, width, height),
            self._landmark_pixel(landmarks, self.FACE_LEFT_EYE_CORNER, width, height),
            self._landmark_pixel(landmarks, self.FACE_RIGHT_EYE_CORNER, width, height),
            self._landmark_pixel(landmarks, self.FACE_MOUTH_LEFT, width, height),
            self._landmark_pixel(landmarks, self.FACE_MOUTH_RIGHT, width, height),
        ]
        focal_length = float(width)

        import numpy as np

        object_points_np = np.array(self.PNP_MODEL_POINTS, dtype="double")
        image_points_np = np.array(image_points, dtype="double")
        camera_matrix_np = np.array(
            [
                [focal_length, 0.0, width / 2.0],
                [0.0, focal_length, height / 2.0],
                [0.0, 0.0, 1.0],
            ],
            dtype="double",
        )
        distortion_coeffs = np.zeros((4, 1), dtype="double")

        success, rotation_vector, _ = cv2.solvePnP(
            object_points_np,
            image_points_np,
            camera_matrix_np,
            distortion_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not success:
            return (None, None, None, None)

        rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
        pitch, yaw, roll = self._rotation_matrix_to_euler(rotation_matrix)
        pitch = -self._unwrap_pose_angle(pitch)
        yaw = self._unwrap_pose_angle(yaw)
        roll = self._unwrap_pose_angle(roll)
        head_pose = self._classify_head_pose(pitch=pitch, yaw=yaw)
        return (head_pose, pitch, yaw, roll)

    def _rotation_matrix_to_euler(self, rotation_matrix: Any) -> tuple[float, float, float]:
        sy = math.sqrt(rotation_matrix[0, 0] ** 2 + rotation_matrix[1, 0] ** 2)
        singular = sy < 1e-6
        if not singular:
            pitch = math.atan2(rotation_matrix[2, 1], rotation_matrix[2, 2])
            yaw = math.atan2(-rotation_matrix[2, 0], sy)
            roll = math.atan2(rotation_matrix[1, 0], rotation_matrix[0, 0])
        else:
            pitch = math.atan2(-rotation_matrix[1, 2], rotation_matrix[1, 1])
            yaw = math.atan2(-rotation_matrix[2, 0], sy)
            roll = 0.0
        return tuple(math.degrees(value) for value in (pitch, yaw, roll))

    def _unwrap_pose_angle(self, angle: float | None) -> float | None:
        if angle is None:
            return None
        if angle > 90.0:
            return 180.0 - angle
        if angle < -90.0:
            return -180.0 - angle
        return angle

    def _classify_head_pose(self, pitch: float, yaw: float) -> str:
        if yaw <= -24.0:
            return "left"
        if yaw >= 24.0:
            return "right"
        if pitch >= 18.0:
            return "down"
        if pitch <= -10.0:
            return "up"
        return "forward"

    def _head_pose_strength(self, pitch: float | None, yaw: float | None) -> str:
        if pitch is None or yaw is None:
            return "none"
        yaw_abs = abs(yaw)
        pitch_down = max(0.0, pitch)
        pitch_up = max(0.0, -pitch)
        if yaw_abs >= 32.0 or pitch_down >= 24.0 or pitch_up >= 18.0:
            return "strong"
        if yaw_abs >= 24.0 or pitch_down >= 18.0 or pitch_up >= 12.0:
            return "moderate"
        return "none"

    def _extract_eye_geometry(
        self,
        landmarks: list[Any],
        width: int,
        height: int,
        eye_definition: dict[str, int],
        iris_indices: list[int],
        center_indices: list[int],
    ) -> dict[str, float | tuple[float, float] | None]:
        iris_points = [self._landmark_pixel(landmarks, index, width, height) for index in iris_indices]
        iris_center = self._mean_point(iris_points)
        eye_center = self._mean_point(
            [self._landmark_pixel(landmarks, index, width, height) for index in center_indices]
        )
        eye_outer = self._landmark_pixel(landmarks, eye_definition["outer"], width, height)
        eye_inner = self._landmark_pixel(landmarks, eye_definition["inner"], width, height)
        eye_upper = self._landmark_pixel(landmarks, eye_definition["upper"], width, height)
        eye_lower = self._landmark_pixel(landmarks, eye_definition["lower"], width, height)
        x_min = min(eye_outer[0], eye_inner[0])
        x_max = max(eye_outer[0], eye_inner[0])
        y_min = min(eye_upper[1], eye_lower[1])
        y_max = max(eye_upper[1], eye_lower[1])
        x_span = max(1.0, x_max - x_min)
        y_span = max(1.0, y_max - y_min)
        return {
            "iris_center": iris_center,
            "eye_center": eye_center,
            "x_ratio": (iris_center[0] - x_min) / x_span,
            "y_ratio": (iris_center[1] - y_min) / y_span,
        }

    def _estimate_gaze(
        self,
        landmarks: list[Any],
        width: int,
        height: int,
        head_pose: str | None,
        head_yaw: float | None,
    ) -> dict[str, Any]:
        if len(landmarks) < 478:
            return {
                "gaze_on_script": None,
                "gaze_direction": None,
                "gazePoint_x": None,
                "gazePoint_y": None,
                "pupil_left_x": None,
                "pupil_left_y": None,
                "pupil_right_x": None,
                "pupil_right_y": None,
            }

        left_eye = self._extract_eye_geometry(
            landmarks,
            width,
            height,
            eye_definition=self.LEFT_EYE,
            iris_indices=self.LEFT_IRIS,
            center_indices=self.EYE_CENTER_LANDMARKS["left"],
        )
        right_eye = self._extract_eye_geometry(
            landmarks,
            width,
            height,
            eye_definition=self.RIGHT_EYE,
            iris_indices=self.RIGHT_IRIS,
            center_indices=self.EYE_CENTER_LANDMARKS["right"],
        )

        horizontal_delta_raw = ((left_eye["x_ratio"] - 0.5) + (right_eye["x_ratio"] - 0.5)) / 2.0
        vertical_delta_raw = ((left_eye["y_ratio"] - 0.5) + (right_eye["y_ratio"] - 0.5)) / 2.0

        calibrating_baseline = self._gaze_baseline_samples < self._gaze_baseline_min_samples
        if head_pose == "forward" and head_yaw is not None and abs(head_yaw) <= 14.0 and calibrating_baseline:
            baseline_tolerance = 0.12
            if (
                self._gaze_baseline_samples == 0
                or (
                    abs(horizontal_delta_raw - self._gaze_baseline_horizontal) <= baseline_tolerance
                    and abs(vertical_delta_raw - self._gaze_baseline_vertical) <= baseline_tolerance
                )
            ):
                samples = self._gaze_baseline_samples
                self._gaze_baseline_horizontal = (
                    (self._gaze_baseline_horizontal * samples + horizontal_delta_raw) / (samples + 1)
                )
                self._gaze_baseline_vertical = (
                    (self._gaze_baseline_vertical * samples + vertical_delta_raw) / (samples + 1)
                )
                self._gaze_baseline_samples += 1

        horizontal_delta = horizontal_delta_raw - self._gaze_baseline_horizontal
        vertical_delta = vertical_delta_raw - self._gaze_baseline_vertical

        horizontal_threshold = 0.145
        vertical_threshold = 0.16
        horizontal = "center"
        vertical = "center"
        if horizontal_delta <= -horizontal_threshold:
            horizontal = "right"
        elif horizontal_delta >= horizontal_threshold:
            horizontal = "left"

        if vertical_delta <= -vertical_threshold:
            vertical = "top"
        elif vertical_delta >= vertical_threshold:
            vertical = "bottom"

        if horizontal != "center" and vertical != "center":
            direction = f"{vertical}_{horizontal}"
        elif horizontal != "center":
            direction = horizontal
        elif vertical != "center":
            direction = "center"
        else:
            direction = "center"

        if direction not in {"center", "left", "right", "bottom_right", "bottom_left", "top_right", "top_left"}:
            direction = "center"

        gaze_point = self._mean_point([left_eye["iris_center"], right_eye["iris_center"]])
        center_box = (
            width * 0.25 <= gaze_point[0] <= width * 0.75 and height * 0.2 <= gaze_point[1] <= height * 0.8
        )

        return {
            "gaze_on_script": 1 if direction == "center" and center_box else 0,
            "gaze_direction": direction,
            "gaze_horizontal_delta": horizontal_delta,
            "gaze_vertical_delta": vertical_delta,
            "gazePoint_x": int(round(gaze_point[0])),
            "gazePoint_y": int(round(gaze_point[1])),
            "pupil_left_x": int(round(left_eye["iris_center"][0])),
            "pupil_left_y": int(round(left_eye["iris_center"][1])),
            "pupil_right_x": int(round(right_eye["iris_center"][0])),
            "pupil_right_y": int(round(right_eye["iris_center"][1])),
        }

    def _extract_hand_features(
        self,
        hand_results: Any,
        width: int,
        height: int,
        phone_center: tuple[float, float] | None,
        phone_present: bool,
    ) -> dict[str, Any]:
        features = {
            "hand_count": 0,
            "left_hand_x": 0.0,
            "left_hand_y": 0.0,
            "right_hand_x": 0.0,
            "right_hand_y": 0.0,
            "hand_obj_interaction": 0,
        }
        if hand_results is None or not hand_results.multi_hand_landmarks:
            return features

        features["hand_count"] = len(hand_results.multi_hand_landmarks)
        minimum_phone_distance: float | None = None

        handedness_list = hand_results.multi_handedness or []
        for index, hand_landmarks in enumerate(hand_results.multi_hand_landmarks):
            handedness = handedness_list[index] if index < len(handedness_list) else None
            wrist = hand_landmarks.landmark[0]
            wrist_x = float(wrist.x)
            wrist_y = float(wrist.y)
            label = handedness.classification[0].label.lower() if handedness.classification else ""

            if label == "left":
                features["left_hand_x"] = wrist_x
                features["left_hand_y"] = wrist_y
            elif label == "right":
                features["right_hand_x"] = wrist_x
                features["right_hand_y"] = wrist_y

            if phone_present and phone_center is not None:
                wrist_px = (wrist_x * width, wrist_y * height)
                distance = math.dist(wrist_px, phone_center)
                minimum_phone_distance = distance if minimum_phone_distance is None else min(minimum_phone_distance, distance)

        if phone_present and minimum_phone_distance is not None:
            features["hand_obj_interaction"] = int(minimum_phone_distance <= max(width, height) * 0.18)

        return features

    def extract(self, frame_bgr: Any, yolo_detections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload = {
            "available": False,
            "features": self._empty_features(),
            "signals": {
                "face_count": 0,
                "head_pose_alert": False,
                "gaze_alert": False,
                "gaze_baseline_ready": False,
                "gaze_baseline_samples": 0,
                "hand_phone_alert": False,
                "face_missing": True,
            },
            "message": self._load_error or "MediaPipe unavailable.",
        }
        if not self._ensure_models():
            return payload

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        frame_rgb.flags.writeable = False
        face_results = self._face_mesh.process(frame_rgb)
        hand_results = self._hands.process(frame_rgb)
        frame_rgb.flags.writeable = True

        height, width = frame_bgr.shape[:2]
        features = self._empty_features()
        detections = yolo_detections or []
        phone_detections = [item for item in detections if item.get("label") in {"cell phone", "mobile phone"}]

        if phone_detections:
            primary_phone = max(phone_detections, key=lambda item: float(item.get("confidence", 0.0)))
            x1, y1, x2, y2 = [float(value) for value in primary_phone["box"]]
            features["phone_present"] = 1
            features["phone_loc_x"] = (x1 + x2) / 2.0
            features["phone_loc_y"] = (y1 + y2) / 2.0
            features["phone_conf"] = float(primary_phone.get("confidence", 0.0))
            phone_center = (features["phone_loc_x"], features["phone_loc_y"])
        else:
            phone_center = None

        face_landmarks = getattr(face_results, "multi_face_landmarks", None) or []
        primary_landmarks, face_count = self._select_primary_face(face_landmarks, width, height)
        features["no_of_face"] = face_count

        if primary_landmarks is not None:
            features["face_present"] = 1
            features["face_conf"] = 100.0
            face_x, face_y, face_w, face_h = self._get_face_bbox(primary_landmarks, width, height)
            features["face_x"] = face_x
            features["face_y"] = face_y
            features["face_w"] = face_w
            features["face_h"] = face_h
            left_eye_center = self._mean_point(
                [self._landmark_pixel(primary_landmarks, index, width, height) for index in self.EYE_CENTER_LANDMARKS["left"]]
            )
            right_eye_center = self._mean_point(
                [self._landmark_pixel(primary_landmarks, index, width, height) for index in self.EYE_CENTER_LANDMARKS["right"]]
            )
            nose_tip = self._landmark_pixel(primary_landmarks, self.FACE_NOSE_TIP, width, height)
            mouth_center = self._mean_point(
                [
                    self._landmark_pixel(primary_landmarks, self.FACE_MOUTH_LEFT, width, height),
                    self._landmark_pixel(primary_landmarks, self.FACE_MOUTH_RIGHT, width, height),
                ]
            )
            features["left_eye_x"], features["left_eye_y"] = left_eye_center
            features["right_eye_x"], features["right_eye_y"] = right_eye_center
            features["nose_tip_x"], features["nose_tip_y"] = nose_tip
            features["mouth_x"], features["mouth_y"] = mouth_center
            head_pose, head_pitch, head_yaw, head_roll = self._estimate_head_pose(primary_landmarks, width, height)
            features["head_pose"] = head_pose
            features["head_pitch"] = head_pitch
            features["head_yaw"] = head_yaw
            features["head_roll"] = head_roll
            features.update(
                self._estimate_gaze(
                    primary_landmarks,
                    width,
                    height,
                    head_pose=head_pose,
                    head_yaw=head_yaw,
                )
            )

        features.update(
            self._extract_hand_features(
                hand_results=hand_results,
                width=width,
                height=height,
                phone_center=phone_center,
                phone_present=bool(features["phone_present"]),
            )
        )

        payload["available"] = True
        payload["features"] = features
        head_pose_strength = self._head_pose_strength(
            pitch=features.get("head_pitch"),
            yaw=features.get("head_yaw"),
        )
        payload["signals"] = {
            "face_count": int(features["no_of_face"] or 0),
            "head_pose_alert": head_pose_strength in {"moderate", "strong"},
            "head_pose_strong": head_pose_strength == "strong",
            "head_pose_strength": head_pose_strength,
            "gaze_alert": features.get("gaze_on_script") == 0 and features.get("gaze_direction") not in {None, "center"},
            "gaze_baseline_ready": self._gaze_baseline_samples >= self._gaze_baseline_min_samples,
            "gaze_baseline_samples": int(self._gaze_baseline_samples),
            "hand_phone_alert": features.get("hand_obj_interaction") == 1 and features.get("phone_present") == 1,
            "face_missing": features.get("face_present") == 0,
        }
        payload["message"] = "MediaPipe features extracted."
        return payload
