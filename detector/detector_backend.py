from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from config import settings

try:
    import cv2
except ImportError as exc:
    raise RuntimeError(
        "OpenCV (cv2) est requis pour l'inference. Installez opencv-python-headless."
    ) from exc


@dataclass
class Detection:
    class_id: int
    label: str
    confidence: float
    box_xyxy: Tuple[int, int, int, int]


class BaseBackend:
    name = "base"

    def detect(self, frame: np.ndarray) -> List[Detection]:
        raise NotImplementedError


def _probe_native_module(module_name: str, timeout: int = 15) -> None:
    """Teste l'import d'un module natif dans un sous-processus.

    Certains paquets natifs ARM peuvent provoquer un crash immediat
    (ex: Illegal instruction) qui ne peut pas etre capture en Python.
    """
    command = [sys.executable, "-c", f"import {module_name}"]
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode == 0:
        return

    stderr = (result.stderr or "").strip().splitlines()
    details = stderr[-1] if stderr else f"code={result.returncode}"
    if result.returncode < 0:
        details = f"signal={-result.returncode}"
    raise RuntimeError(f"Import natif instable pour {module_name}: {details}")


class PTBackend(BaseBackend):
    name = "pt"

    def __init__(self, model_path: Path, confidence: float, class_map: Dict[int, str]):
        _probe_native_module("torch")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "Backend PT demande ultralytics+torch. Installez les dependances PT ou utilisez ONNX."
            ) from exc

        self._model = YOLO(str(model_path))
        self._confidence = confidence
        self._class_map = class_map

    def detect(self, frame: np.ndarray) -> List[Detection]:
        results = self._model(frame, conf=self._confidence, verbose=False)
        detections: List[Detection] = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            for box in boxes:
                class_id = int(box.cls[0])
                conf = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0][:4].tolist())
                label = self._class_map.get(class_id, f"class_{class_id}")
                detections.append(
                    Detection(
                        class_id=class_id,
                        label=label,
                        confidence=conf,
                        box_xyxy=(x1, y1, x2, y2),
                    )
                )
        return detections


class ONNXBackend(BaseBackend):
    name = "onnx"

    def __init__(self, model_path: Path, confidence: float, class_map: Dict[int, str]):
        _probe_native_module("onnxruntime")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "Backend ONNX demande onnxruntime. Installez les dependances RPi/base."
            ) from exc

        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session_options.inter_op_num_threads = 1
        session_options.intra_op_num_threads = settings.onnx_threads

        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=session_options,
            providers=["CPUExecutionProvider"],
        )
        self._input_name = self._session.get_inputs()[0].name
        input_shape = self._session.get_inputs()[0].shape
        # shape attendu: [1,3,H,W] ou [None,3,H,W]
        self._input_h = int(input_shape[2]) if input_shape[2] not in (None, "None") else 640
        self._input_w = int(input_shape[3]) if input_shape[3] not in (None, "None") else 640
        self._confidence = confidence
        self._class_map = self._read_class_map(class_map)

    def _read_class_map(self, fallback_map: Dict[int, str]) -> Dict[int, str]:
        metadata = self._session.get_modelmeta().custom_metadata_map or {}
        names_raw = metadata.get("names")
        if not names_raw:
            return fallback_map
        try:
            parsed = ast.literal_eval(names_raw)
        except (ValueError, SyntaxError):
            return fallback_map

        if isinstance(parsed, dict):
            items = parsed.items()
        elif isinstance(parsed, list):
            items = enumerate(parsed)
        else:
            return fallback_map

        resolved: Dict[int, str] = {}
        for key, value in items:
            try:
                class_id = int(key)
            except (TypeError, ValueError):
                continue
            resolved[class_id] = str(value).strip().capitalize()
        return resolved or fallback_map

    def _prepare(self, frame: np.ndarray) -> Tuple[np.ndarray, Tuple[float, float, float]]:
        height, width = frame.shape[:2]
        scale = min(self._input_w / width, self._input_h / height)
        resized_w = max(1, int(round(width * scale)))
        resized_h = max(1, int(round(height * scale)))

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)

        padded = np.full((self._input_h, self._input_w, 3), 114, dtype=np.uint8)
        pad_x = (self._input_w - resized_w) // 2
        pad_y = (self._input_h - resized_h) // 2
        padded[pad_y : pad_y + resized_h, pad_x : pad_x + resized_w] = resized

        tensor = padded.astype(np.float32) / 255.0
        tensor = np.transpose(tensor, (2, 0, 1))[None, ...]
        return tensor, (scale, float(pad_x), float(pad_y))

    def _decode(
        self,
        raw_output: np.ndarray,
        frame_shape: Tuple[int, int, int],
        prep_info: Tuple[float, float, float],
    ) -> List[Detection]:
        height, width = frame_shape[:2]
        scale, pad_x, pad_y = prep_info
        output = np.squeeze(raw_output)
        if output.ndim != 2:
            return []
        # YOLOv8 ONNX: (84, 8400) ou (8400, 84)
        if output.shape[0] < output.shape[1]:
            output = output.T

        if output.shape[1] < 6:
            return []

        boxes = output[:, :4]
        class_scores = output[:, 4:]
        class_ids = np.argmax(class_scores, axis=1)
        confidences = np.max(class_scores, axis=1)

        keep = confidences >= self._confidence
        boxes = boxes[keep]
        class_ids = class_ids[keep]
        confidences = confidences[keep]

        if boxes.size == 0:
            return []

        converted_boxes: List[List[int]] = []
        for box in boxes:
            cx, cy, bw, bh = box.tolist()
            x1 = int(max(0, min(width - 1, (cx - bw / 2 - pad_x) / scale)))
            y1 = int(max(0, min(height - 1, (cy - bh / 2 - pad_y) / scale)))
            x2 = int(max(0, min(width - 1, (cx + bw / 2 - pad_x) / scale)))
            y2 = int(max(0, min(height - 1, (cy + bh / 2 - pad_y) / scale)))
            converted_boxes.append([x1, y1, max(1, x2 - x1), max(1, y2 - y1)])

        indices = cv2.dnn.NMSBoxes(
            bboxes=converted_boxes,
            scores=confidences.tolist(),
            score_threshold=self._confidence,
            nms_threshold=0.45,
        )
        if len(indices) == 0:
            return []

        detections: List[Detection] = []
        for idx in np.array(indices).reshape(-1):
            x, y, w, h = converted_boxes[int(idx)]
            class_id = int(class_ids[int(idx)])
            conf = float(confidences[int(idx)])
            label = self._class_map.get(class_id, f"class_{class_id}")
            detections.append(
                Detection(
                    class_id=class_id,
                    label=label,
                    confidence=conf,
                    box_xyxy=(x, y, x + w, y + h),
                )
            )
        return detections

    def detect(self, frame: np.ndarray) -> List[Detection]:
        tensor, prep_info = self._prepare(frame)
        outputs = self._session.run(None, {self._input_name: tensor})
        if not outputs:
            return []
        return self._decode(outputs[0], frame.shape, prep_info)


def choose_backend(
    requested_backend: str,
    confidence: float,
    class_map: Dict[int, str],
    onnx_model: Path,
    pt_candidates: List[Path],
) -> Tuple[Optional[BaseBackend], str]:
    backend_errors: List[str] = []

    def _try_onnx() -> Optional[BaseBackend]:
        if not onnx_model.exists():
            backend_errors.append(
                f"ONNX introuvable: {onnx_model}. Lancez scripts/export_to_onnx.py."
            )
            return None
        try:
            return ONNXBackend(onnx_model, confidence, class_map)
        except Exception as exc:  # pragma: no cover - message runtime
            backend_errors.append(f"Echec backend ONNX: {exc}")
            return None

    def _try_pt() -> Optional[BaseBackend]:
        model_path = next((candidate for candidate in pt_candidates if candidate.exists()), None)
        if model_path is None:
            backend_errors.append(
                "Aucun modele .pt trouve dans models/. Ajoutez my_model.pt ou yolov8n.pt."
            )
            return None
        try:
            return PTBackend(model_path, confidence, class_map)
        except Exception as exc:  # pragma: no cover - message runtime
            backend_errors.append(f"Echec backend PT: {exc}")
            return None

    backend = None
    if requested_backend == "onnx":
        backend = _try_onnx()
    elif requested_backend == "pt":
        backend = _try_pt()
    else:
        backend = _try_onnx() or _try_pt()

    if backend is not None:
        return backend, ""
    return None, " | ".join(backend_errors)
