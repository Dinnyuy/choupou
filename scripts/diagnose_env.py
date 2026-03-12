from __future__ import annotations

import importlib
import platform
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings


def check_module(name: str):
    native_probe_modules = {
        "onnxruntime": "import onnxruntime",
        "torch": "import torch",
        "ultralytics": "import ultralytics",
    }
    if name in native_probe_modules:
        result = subprocess.run(
            [sys.executable, "-c", native_probe_modules[name]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0:
            module = importlib.import_module(name)
            version = getattr(module, "__version__", "unknown")
            return True, str(version)
        if result.returncode < 0:
            return False, f"import natif instable (signal {-result.returncode})"
        stderr = (result.stderr or "").strip().splitlines()
        return False, stderr[-1] if stderr else f"retcode={result.returncode}"
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "unknown")
        return True, str(version)
    except Exception as exc:
        return False, str(exc)


def check_camera() -> str:
    camera_script = f"""
import cv2
cam = cv2.VideoCapture({settings.camera_index})
try:
    if not cam.isOpened():
        print("camera index {settings.camera_index} non ouverte")
    else:
        ok, _ = cam.read()
        if ok:
            print("camera OK (opencv)")
        else:
            print("camera ouverte mais lecture frame echouee")
finally:
    cam.release()
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", camera_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"camera index {settings.camera_index} timeout ouverture/lecture"

    if result.returncode == 0:
        return (result.stdout or "").strip() or "camera test termine"
    stderr = (result.stderr or "").strip().splitlines()
    return stderr[-1] if stderr else f"camera test erreur: {result.returncode}"


def check_db() -> str:
    if not settings.db_path.exists():
        return f"DB absente: {settings.db_path}"
    try:
        conn = sqlite3.connect(str(settings.db_path))
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in c.fetchall()]
        conn.close()
        return "tables=" + ", ".join(tables) if tables else "aucune table"
    except Exception as exc:
        return f"DB erreur: {exc}"


def main() -> int:
    print("=== ENVIRONNEMENT ===")
    print(f"python: {sys.version.split()[0]}")
    print(f"executable: {sys.executable}")
    print(f"os: {platform.platform()}")
    print(f"arch: {platform.machine()}")
    print(f"raspberry_pi: {settings.is_raspberry_pi}")
    print(f"cwd: {Path.cwd()}")
    print()

    print("=== CONFIG PROJET ===")
    print(f"base_dir: {settings.base_dir}")
    print(f"db_path: {settings.db_path} (exists={settings.db_path.exists()})")
    print(f"backend demande: {settings.backend}")
    print(f"camera_mode: {settings.camera_mode} camera_index: {settings.camera_index}")
    print(f"pt_candidates:")
    for candidate in settings.pt_candidates:
        print(f"  - {candidate} (exists={candidate.exists()})")
    print(f"onnx_model: {settings.onnx_model} (exists={settings.onnx_model.exists()})")
    print()

    print("=== MODULES ===")
    for module_name in ["flask", "cv2", "numpy", "ultralytics", "torch", "onnxruntime", "reportlab"]:
        ok, info = check_module(module_name)
        status = "OK" if ok else "KO"
        print(f"{module_name:12} {status:2} {info}")
    print()

    print("=== CAMERA ===")
    print(check_camera())
    print()

    print("=== SQLITE ===")
    print(check_db())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
