from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from config import settings
from detector import CameraSource, choose_backend


class WasteDetector:
    """Compat layer utilisee par app.py avec backend unifie PT/ONNX."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        backend: Optional[str] = None,
        confidence_threshold: Optional[float] = None,
        db_path: Optional[str] = None,
    ):
        self._backend_name = (backend or settings.backend).lower()
        self._confidence = confidence_threshold or settings.confidence_threshold
        self._db_path = Path(db_path) if db_path else settings.db_path
        self._class_map = dict(settings.waste_classes)
        self._last_error = ""

        pt_candidates = list(settings.pt_candidates)
        if model_path:
            custom_path = Path(model_path)
            if not custom_path.is_absolute():
                custom_path = settings.base_dir / custom_path
            pt_candidates = [custom_path] + pt_candidates

        self._backend, self._last_error = choose_backend(
            requested_backend=self._backend_name,
            confidence=self._confidence,
            class_map=self._class_map,
            onnx_model=settings.onnx_model,
            pt_candidates=pt_candidates,
        )

    @property
    def backend_name(self) -> str:
        if self._backend is None:
            return "unavailable"
        return self._backend.name

    @property
    def last_error(self) -> str:
        return self._last_error

    def is_ready(self) -> bool:
        return self._backend is not None

    def detect_objects(self, frame: np.ndarray) -> List[dict]:
        if not self._backend or frame is None:
            return []

        try:
            detections = self._backend.detect(frame)
        except Exception:
            return []

        formatted = []
        frame_height, frame_width = frame.shape[:2]
        frame_area = max(1, frame_width * frame_height)
        edge_margin = max(2, int(round(min(frame_width, frame_height) * 0.02)))
        for det in detections:
            x1, y1, x2, y2 = [int(value) for value in det.box_xyxy]
            x1 = max(0, min(frame_width - 1, x1))
            y1 = max(0, min(frame_height - 1, y1))
            x2 = max(x1 + 1, min(frame_width, x2))
            y2 = max(y1 + 1, min(frame_height, y2))

            box_area = max(1, (x2 - x1) * (y2 - y1))
            area_ratio = box_area / frame_area
            if area_ratio < settings.detection_min_box_area_ratio:
                continue
            if area_ratio > settings.detection_max_box_area_ratio:
                continue

            edge_touches = sum(
                (
                    x1 <= edge_margin,
                    y1 <= edge_margin,
                    x2 >= frame_width - edge_margin,
                    y2 >= frame_height - edge_margin,
                )
            )
            if (
                area_ratio >= settings.detection_large_edge_box_area_ratio
                and edge_touches >= settings.detection_large_edge_box_min_touches
            ):
                continue

            formatted.append(
                {
                    "waste_type": det.label,
                    "confidence": det.confidence,
                    "box": np.array((x1, y1, x2, y2), dtype=np.int32),
                }
            )
        formatted.sort(key=lambda item: float(item["confidence"]), reverse=True)
        return formatted

    def summarize_detections(self, detections: List[dict]) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for det in detections:
            waste_type = det["waste_type"]
            summary[waste_type] = summary.get(waste_type, 0) + 1
        return summary

    def draw_detections(self, frame: np.ndarray, detections: List[dict]) -> np.ndarray:
        for det in detections:
            x1, y1, x2, y2 = map(int, det["box"].tolist())
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 230, 0), 2)
            cv2.putText(
                frame,
                f"{det['waste_type']} {det['confidence']:.2f}",
                (x1, max(12, y1 - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 230, 0),
                2,
            )
        return frame

    def detect_from_image(self, image_path: str):
        if not self._backend:
            return None, self._last_error or "Aucun backend disponible"

        image_file = Path(image_path)
        if not image_file.exists():
            return None, f"Image introuvable: {image_file}"

        image = cv2.imread(str(image_file))
        if image is None:
            return None, f"Impossible de lire l'image: {image_file}"

        try:
            formatted = self.detect_objects(image)
        except Exception as exc:
            return None, f"Erreur inference ({self.backend_name}): {exc}"
        return formatted, {"backend": self.backend_name}

    def detect_from_frame(self, frame: np.ndarray):
        if not self._backend:
            return frame, {}
        detections = self.detect_objects(frame)
        summary = self.summarize_detections(detections)
        frame = self.draw_detections(frame, detections)
        return frame, summary

    def detect_from_webcam(self, user_id: int, duration: int = 10):
        if not self._backend:
            return {}

        camera = CameraSource(mode=settings.camera_mode, camera_index=settings.camera_index)
        if not camera.open():
            return {}

        end_at = time.time() + max(1, int(duration))
        summary: Dict[str, int] = {}
        try:
            while time.time() < end_at:
                ok, frame = camera.read()
                if not ok or frame is None:
                    continue
                detection_frame = camera.prepare_for_detection(frame.copy())
                detections = self.detect_objects(detection_frame)
                frame_summary = self.summarize_detections(detections)
                for key, value in frame_summary.items():
                    summary[key] = summary.get(key, 0) + value
        finally:
            camera.release()
        return summary

    def save_detections_to_db(self, user_id: int, detections_dict: Dict[str, int]):
        if not detections_dict:
            return True
        conn = sqlite3.connect(str(self._db_path))
        try:
            c = conn.cursor()
            for waste_type, quantity in detections_dict.items():
                c.execute(
                    """
                    INSERT INTO waste_detection (user_id, waste_type, quantity, detection_date)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, waste_type, int(quantity), datetime.now()),
                )
            conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()
