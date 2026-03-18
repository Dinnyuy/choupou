from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
DB_PATH = BASE_DIR / "waste.db"
UPLOAD_DIR = STATIC_DIR / "uploads" / "profiles"

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None
else:
    try:
        load_dotenv(BASE_DIR / ".env")
    except Exception:
        pass

DEFAULT_WASTE_CLASSES: Dict[int, str] = {
    0: "Plastique",
    1: "Metal",
    2: "Papier",
    3: "Carton",
    4: "Verre",
}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    models_dir: Path
    db_path: Path
    templates_dir: Path
    static_dir: Path
    upload_dir: Path
    backend: str
    confidence_threshold: float
    camera_mode: str
    camera_index: int
    camera_width: int
    camera_height: int
    camera_lock_awb: bool
    camera_lock_exposure: bool
    camera_awb_settle_frames: int
    camera_manual_red_gain: float | None
    camera_manual_blue_gain: float | None
    camera_manual_exposure_time: int | None
    camera_manual_analogue_gain: float | None
    detection_min_box_area_ratio: float
    detection_max_box_area_ratio: float
    detection_large_edge_box_area_ratio: float
    detection_large_edge_box_min_touches: int
    onnx_threads: int
    stream_confirm_hits: int
    stream_fast_confirm_confidence: float
    stream_track_max_misses: int
    stream_track_iou_threshold: float
    stream_detect_every_n_frames: int
    stream_jpeg_quality: int
    arm_port: str
    arm_baud: int
    arm_mock: bool
    arm_ack_timeout: float
    arm_queue_maxsize: int
    flask_host: str
    flask_port: int
    flask_debug: bool
    secret_key: str
    waste_classes: Dict[int, str]
    pt_candidates: List[Path]
    onnx_model: Path

    @property
    def is_raspberry_pi(self) -> bool:
        machine = platform.machine().lower()
        if "arm" in machine or "aarch64" in machine:
            model_file = Path("/proc/device-tree/model")
            if model_file.exists():
                try:
                    return "raspberry pi" in model_file.read_text().lower()
                except OSError:
                    return False
        return False


def build_settings() -> Settings:
    machine = platform.machine().lower()
    backend = os.getenv("WASTEAI_BACKEND", "auto").strip().lower()
    if backend not in {"auto", "pt", "onnx"}:
        backend = "auto"

    camera_mode = os.getenv("WASTEAI_CAMERA_MODE", "auto").strip().lower()
    if camera_mode not in {"auto", "opencv", "picamera2"}:
        camera_mode = "auto"

    pt_custom = MODELS_DIR / "my_model.pt"
    pt_fallback = MODELS_DIR / "yolov8n.pt"
    onnx_default = MODELS_DIR / "my_model.onnx"
    default_lock_awb = camera_mode == "picamera2" or ("arm" in machine or "aarch64" in machine)
    default_detection_max_box_area_ratio = 0.995 if camera_mode == "picamera2" else 0.90
    default_detection_large_edge_box_area_ratio = 0.999 if camera_mode == "picamera2" else 0.55
    default_detection_large_edge_box_min_touches = 4 if camera_mode == "picamera2" else 3

    return Settings(
        base_dir=BASE_DIR,
        models_dir=MODELS_DIR,
        db_path=DB_PATH,
        templates_dir=TEMPLATES_DIR,
        static_dir=STATIC_DIR,
        upload_dir=UPLOAD_DIR,
        backend=backend,
        confidence_threshold=_env_float("WASTEAI_CONFIDENCE", 0.5),
        camera_mode=camera_mode,
        camera_index=_env_int("WASTEAI_CAMERA_INDEX", 0),
        camera_width=max(320, _env_int("WASTEAI_CAMERA_WIDTH", 640)),
        camera_height=max(240, _env_int("WASTEAI_CAMERA_HEIGHT", 480)),
        camera_lock_awb=_env_bool("WASTEAI_CAMERA_LOCK_AWB", default_lock_awb),
        camera_lock_exposure=_env_bool("WASTEAI_CAMERA_LOCK_EXPOSURE", False),
        camera_awb_settle_frames=max(3, _env_int("WASTEAI_CAMERA_AWB_SETTLE_FRAMES", 12)),
        camera_manual_red_gain=_env_optional_float("WASTEAI_CAMERA_RED_GAIN"),
        camera_manual_blue_gain=_env_optional_float("WASTEAI_CAMERA_BLUE_GAIN"),
        camera_manual_exposure_time=_env_optional_int("WASTEAI_CAMERA_EXPOSURE_TIME"),
        camera_manual_analogue_gain=_env_optional_float("WASTEAI_CAMERA_ANALOGUE_GAIN"),
        detection_min_box_area_ratio=max(0.0, _env_float("WASTEAI_DETECTION_MIN_BOX_AREA_RATIO", 0.01)),
        detection_max_box_area_ratio=min(
            1.0,
            _env_float("WASTEAI_DETECTION_MAX_BOX_AREA_RATIO", default_detection_max_box_area_ratio),
        ),
        detection_large_edge_box_area_ratio=min(
            1.0,
            _env_float(
                "WASTEAI_DETECTION_LARGE_EDGE_BOX_AREA_RATIO",
                default_detection_large_edge_box_area_ratio,
            ),
        ),
        detection_large_edge_box_min_touches=max(
            1,
            min(4, _env_int("WASTEAI_DETECTION_LARGE_EDGE_BOX_MIN_TOUCHES", default_detection_large_edge_box_min_touches)),
        ),
        onnx_threads=max(
            1,
            _env_int("WASTEAI_ONNX_THREADS", max(1, min(4, os.cpu_count() or 1))),
        ),
        stream_confirm_hits=max(1, _env_int("WASTEAI_STREAM_CONFIRM_HITS", 2)),
        stream_fast_confirm_confidence=min(
            1.0,
            max(0.0, _env_float("WASTEAI_STREAM_FAST_CONFIRM_CONFIDENCE", 0.90)),
        ),
        stream_track_max_misses=max(0, _env_int("WASTEAI_STREAM_TRACK_MAX_MISSES", 2)),
        stream_track_iou_threshold=min(
            1.0,
            max(0.0, _env_float("WASTEAI_STREAM_TRACK_IOU_THRESHOLD", 0.35)),
        ),
        stream_detect_every_n_frames=max(
            1,
            _env_int(
                "WASTEAI_STREAM_DETECT_EVERY_N_FRAMES",
                3 if ("arm" in machine or "aarch64" in machine) else 1,
            ),
        ),
        stream_jpeg_quality=min(
            95,
            max(55, _env_int("WASTEAI_STREAM_JPEG_QUALITY", 75)),
        ),
        arm_port=os.getenv("WASTEAI_ARM_PORT", "/dev/ttyUSB0").strip() or "/dev/ttyUSB0",
        arm_baud=max(300, _env_int("WASTEAI_ARM_BAUD", 9600)),
        arm_mock=_env_bool("WASTEAI_ARM_MOCK", True),
        arm_ack_timeout=max(0.5, _env_float("WASTEAI_ARM_ACK_TIMEOUT", 4.0)),
        arm_queue_maxsize=max(1, _env_int("WASTEAI_ARM_QUEUE_MAXSIZE", 16)),
        flask_host=os.getenv("FLASK_HOST", "0.0.0.0"),
        flask_port=_env_int("FLASK_PORT", 5000),
        flask_debug=os.getenv("FLASK_DEBUG", "0").strip().lower() in {"1", "true", "yes"},
        secret_key=os.getenv("FLASK_SECRET_KEY", "wasteai-dev-secret"),
        waste_classes=DEFAULT_WASTE_CLASSES,
        pt_candidates=[pt_custom, pt_fallback],
        onnx_model=onnx_default,
    )


settings = build_settings()
