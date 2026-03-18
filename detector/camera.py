from __future__ import annotations

import subprocess
import time
from typing import Optional, Tuple

import numpy as np

from config import settings

try:
    import cv2
except ImportError as exc:
    raise RuntimeError(
        "OpenCV (cv2) est requis pour la camera. Installez opencv-python-headless."
    ) from exc


class CameraSource:
    """Abstraction camera: OpenCV d'abord, Picamera2 en option."""

    def __init__(self, mode: str = "auto", camera_index: int = 0):
        self.mode = mode
        self.camera_index = camera_index
        self._capture = None
        self._picam2 = None
        self._active_mode = None
        self._active_camera_index = None
        self._picamera_needs_channel_swap = False
        self._picamera_pixel_format = None

    def open(self) -> bool:
        if self.mode in {"auto", "picamera2"}:
            if self._open_picamera2():
                return True
            if self.mode == "picamera2":
                return False
        return self._open_opencv()

    def _open_picamera2(self) -> bool:
        try:
            from picamera2 import Picamera2
        except Exception:
            return False

        try:
            self._picam2 = Picamera2()
            # On privilegie la couleur native pour l'affichage du flux.
            # L'IA peut ensuite normaliser une copie separee pour l'inference.
            for pixel_format, needs_channel_swap in (("RGB888", False), ("BGR888", True)):
                try:
                    config = self._picam2.create_preview_configuration(
                        main={
                            "size": (settings.camera_width, settings.camera_height),
                            "format": pixel_format,
                        },
                        buffer_count=4,
                    )
                    self._picam2.configure(config)
                    self._picam2.start()
                    self._lock_picamera2_controls()
                    self._picamera_needs_channel_swap = needs_channel_swap
                    self._picamera_pixel_format = pixel_format
                    self._active_mode = "picamera2"
                    return True
                except Exception:
                    try:
                        self._picam2.stop()
                    except Exception:
                        pass
                    try:
                        self._picam2.close()
                    except Exception:
                        pass
                    self._picam2 = Picamera2()
                    self._picamera_needs_channel_swap = False
                    self._picamera_pixel_format = None
            return False
        except Exception:
            self._picam2 = None
            self._picamera_needs_channel_swap = False
            self._picamera_pixel_format = None
            return False

    def _lock_picamera2_controls(self) -> None:
        if self._picam2 is None:
            return
        has_manual_awb = (
            settings.camera_manual_red_gain is not None
            and settings.camera_manual_blue_gain is not None
        )
        has_manual_exposure = (
            settings.camera_manual_exposure_time is not None
            or settings.camera_manual_analogue_gain is not None
        )
        if not (
            settings.camera_lock_awb
            or settings.camera_lock_exposure
            or has_manual_awb
            or has_manual_exposure
        ):
            return

        metadata = None
        settle_frames = max(1, settings.camera_awb_settle_frames)

        for _ in range(settle_frames):
            try:
                self._picam2.capture_array()
                metadata = self._picam2.capture_metadata()
            except Exception:
                return
            time.sleep(0.05)

        if metadata is None:
            return

        controls = {}
        colour_gains = metadata.get("ColourGains") or ()
        if has_manual_awb:
            controls["AwbEnable"] = False
            controls["ColourGains"] = (
                float(settings.camera_manual_red_gain),
                float(settings.camera_manual_blue_gain),
            )
        elif settings.camera_lock_awb and len(colour_gains) == 2:
            controls["AwbEnable"] = False
            controls["ColourGains"] = (
                float(colour_gains[0]),
                float(colour_gains[1]),
            )

        if has_manual_exposure:
            controls["AeEnable"] = False
            if settings.camera_manual_exposure_time is not None:
                controls["ExposureTime"] = int(settings.camera_manual_exposure_time)
            if settings.camera_manual_analogue_gain is not None:
                controls["AnalogueGain"] = float(settings.camera_manual_analogue_gain)
        elif settings.camera_lock_exposure:
            exposure_time = metadata.get("ExposureTime")
            analogue_gain = metadata.get("AnalogueGain")
            if exposure_time is not None:
                controls["AeEnable"] = False
                controls["ExposureTime"] = int(exposure_time)
            if analogue_gain is not None:
                controls["AnalogueGain"] = float(analogue_gain)

        if controls:
            try:
                self._picam2.set_controls(controls)
            except Exception:
                pass

    def _open_opencv(self) -> bool:
        candidates = [self.camera_index]
        if self.mode == "auto":
            for extra_index in range(5):
                if extra_index not in candidates:
                    candidates.append(extra_index)
        else:
            for extra_index in self._discover_uvc_camera_indexes():
                if extra_index not in candidates:
                    candidates.append(extra_index)

        for candidate_index in candidates:
            capture = self._create_opencv_capture(candidate_index)
            if capture is None:
                continue
            self._capture = capture
            self._active_camera_index = candidate_index
            self._active_mode = "opencv"
            return True
        return False

    def _create_opencv_capture(self, camera_index: int):
        backends = [cv2.CAP_V4L2] if settings.is_raspberry_pi else [cv2.CAP_V4L2, cv2.CAP_ANY]
        for backend in backends:
            capture = cv2.VideoCapture(camera_index, backend)
            capture.set(cv2.CAP_PROP_FOURCC, float(cv2.VideoWriter_fourcc(*"MJPG")))
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(settings.camera_width))
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(settings.camera_height))
            capture.set(cv2.CAP_PROP_FPS, 30.0)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not capture.isOpened():
                capture.release()
                continue

            frame = None
            for _ in range(5):
                ok, frame = capture.read()
                if ok and frame is not None:
                    return capture
                time.sleep(0.05)

            capture.release()
        return None

    def _discover_uvc_camera_indexes(self) -> list[int]:
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--list-devices"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception:
            return []

        if result.returncode != 0 or not result.stdout:
            return []

        discovered_indexes: list[int] = []
        current_is_uvc = False
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                current_is_uvc = False
                continue

            if not raw_line.startswith("\t"):
                current_is_uvc = "uvc" in line.lower()
                continue

            if not current_is_uvc or not line.startswith("/dev/video"):
                continue

            try:
                discovered_index = int(line.rsplit("video", 1)[1])
            except (IndexError, ValueError):
                continue
            discovered_indexes.append(discovered_index)

        return discovered_indexes

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._active_mode == "picamera2" and self._picam2 is not None:
            try:
                frame = self._picam2.capture_array()
                if frame is None:
                    return False, None
                if self._picamera_needs_channel_swap:
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                return True, frame
            except Exception:
                return False, None

        if self._capture is not None:
            return self._capture.read()
        return False, None

    def prepare_for_detection(self, frame: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if frame is None:
            return None
        if self._active_mode != "picamera2":
            return frame
        # Le flux affiche la couleur native; pour l'IA on normalise en BGR.
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    def is_opened(self) -> bool:
        if self._active_mode == "picamera2":
            return self._picam2 is not None
        if self._capture is not None:
            return self._capture.isOpened()
        return False

    def release(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        if self._picam2 is not None:
            try:
                self._picam2.stop()
                self._picam2.close()
            except Exception:
                pass
            self._picam2 = None
        self._active_camera_index = None
        self._picamera_needs_channel_swap = False
        self._picamera_pixel_format = None
        self._active_mode = None

    @property
    def active_mode(self) -> Optional[str]:
        return self._active_mode

    @property
    def active_camera_index(self) -> Optional[int]:
        return self._active_camera_index
