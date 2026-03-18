from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

from config import settings
from detector.camera import CameraSource


def probe_import(module_name: str, timeout: int = 10) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode == 0:
        return True, "ok"
    if result.returncode < 0:
        return False, f"signal {-result.returncode}"
    lines = (result.stderr or "").strip().splitlines()
    return False, lines[-1] if lines else f"code {result.returncode}"


def check_db() -> tuple[bool, str]:
    if not settings.db_path.exists():
        return False, f"SQLite absente: {settings.db_path}"
    try:
        conn = sqlite3.connect(str(settings.db_path))
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in c.fetchall()}
    except Exception as exc:
        return False, f"SQLite erreur: {exc}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    needed = {"users", "waste_detection", "robots", "notifications"}
    missing = sorted(needed - tables)
    if missing:
        return False, "Tables manquantes: " + ", ".join(missing)
    return True, "SQLite OK"


def detector_status() -> tuple[str, list[str]]:
    details: list[str] = []

    onnx_exists = settings.onnx_model.exists()
    onnx_import_ok, onnx_import_msg = probe_import("onnxruntime")
    if onnx_exists and onnx_import_ok:
        return "ready", [f"backend ONNX disponible: {settings.onnx_model}"]
    if not onnx_exists:
        details.append(f"modele ONNX absent: {settings.onnx_model.name}")
    else:
        details.append(f"onnxruntime indisponible: {onnx_import_msg}")

    pt_model = next((candidate for candidate in settings.pt_candidates if candidate.exists()), None)
    torch_ok, torch_msg = probe_import("torch")
    ultra_ok, ultra_msg = probe_import("ultralytics")
    if pt_model and torch_ok and ultra_ok:
        return "ready", [f"backend PT disponible: {pt_model.name}"]
    if pt_model is None:
        details.append("aucun modele PT trouve")
    if not torch_ok:
        details.append(f"torch indisponible: {torch_msg}")
    if not ultra_ok:
        details.append(f"ultralytics indisponible: {ultra_msg}")

    return "degraded", details


def camera_status(retries: int = 3, delay_seconds: float = 0.75) -> tuple[bool, str]:
    last_message = (
        f"Camera indisponible (mode={settings.camera_mode}, index={settings.camera_index})"
    )

    for attempt in range(max(1, retries)):
        source = CameraSource(mode=settings.camera_mode, camera_index=settings.camera_index)
        try:
            if source.open():
                mode = source.active_mode or settings.camera_mode
                if mode == "opencv":
                    active_index = source.active_camera_index
                    suffix = f", index={active_index}" if active_index is not None else ""
                    return True, f"Camera OK: backend {mode}{suffix}"
                return True, f"Camera OK: backend {mode}"
        except Exception as exc:
            last_message = f"Camera erreur: {exc}"
        finally:
            try:
                source.release()
            except Exception:
                pass

        if attempt < retries - 1:
            time.sleep(max(0.0, delay_seconds))

    return False, last_message


def main() -> int:
    print("[PRECHECK] Python:", sys.version.split()[0])
    print("[PRECHECK] Base dir:", settings.base_dir)

    db_ok, db_msg = check_db()
    print(f"[PRECHECK] {db_msg}")

    env_file = settings.base_dir / ".env"
    print(f"[PRECHECK] .env: {'present' if env_file.exists() else 'absent'}")

    camera_ok, camera_msg = camera_status()
    print(f"[PRECHECK] {camera_msg}")

    detector_state, detector_details = detector_status()
    if detector_state == "ready":
        print(f"[PRECHECK] Detecteur: {detector_details[0]}")
    else:
        print("[PRECHECK] Detecteur degrade:")
        for item in detector_details:
            print(f"  - {item}")
        print("[PRECHECK] L'application web demarrera, mais la detection restera indisponible.")

    if not db_ok or not camera_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
