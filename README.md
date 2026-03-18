# WasteAI Flask - Raspberry Pi Ready

Application Flask de suivi des dechets avec detection d'image YOLO.

## Objectifs de cette version

- Execution stable sur Raspberry Pi OS Bookworm (Pi 4/5, CPU).
- Environnement Python reproductible avec `.venv`.
- Backend d'inference unifie: `auto -> onnx -> pt`.
- Scripts de diagnostic et smoke test pour valider rapidement le runtime.

## Structure du projet

```text
app.py
config.py
bootstrap.sh
run.sh
detector/
  detector_backend.py
  camera.py
scripts/
  diagnose_env.py
  smoke_test.py
  export_to_onnx.py
requirements/
  requirements-base.txt
  requirements-rpi.txt
  requirements-dev.txt
models/
  my_model.pt
  yolov8n.pt
  my_model.onnx   # genere via scripts/export_to_onnx.py
```

## Prerequis

- Python 3.10 ou 3.11 recommande (Bookworm: Python 3.11).
- `python3-venv` installe.
- `git` installe.
- (Option camera Pi) `python3-picamera2` via apt.

Exemple Raspberry Pi:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git libatlas-base-dev python3-picamera2
```

## Installation Raspberry Pi (Bookworm)

```bash
cd /chemin/vers/choupou
cp .env.example .env
./bootstrap.sh
python scripts/diagnose_env.py
python scripts/smoke_test.py
./run.sh
```

Par defaut, `bootstrap.sh` detecte ARM et installe `requirements/requirements-rpi.txt`.

## Installation Desktop Linux

```bash
cd /chemin/vers/choupou
cp .env.example .env
./bootstrap.sh
python scripts/diagnose_env.py
python scripts/smoke_test.py
./run.sh
```

Si vous voulez utiliser `.pt` (ultralytics + torch) ou exporter en ONNX:

```bash
source .venv/bin/activate
python -m pip install -r requirements/requirements-dev.txt
python scripts/export_to_onnx.py --pt models/my_model.pt --onnx models/my_model.onnx
```

## Backend d'inference (choix retenu)

- Backend par defaut: `WASTEAI_BACKEND=auto`
  - essaie ONNX d'abord (`models/my_model.onnx` + onnxruntime CPU)
  - fallback PT ensuite (`models/my_model.pt` ou `models/yolov8n.pt`)
- Forcer ONNX:
  - `WASTEAI_BACKEND=onnx`
- Forcer PT:
  - `WASTEAI_BACKEND=pt`

Ce choix privilegie une inference CPU plus legere sur Raspberry Pi.

## Camera

Variables `.env`:

- `WASTEAI_CAMERA_MODE=auto|opencv|picamera2`
- `WASTEAI_CAMERA_INDEX=0`

En mode `auto`, le projet tente `picamera2` puis fallback OpenCV.
Sans camera disponible, l'application reste utilisable en mode image.

## VS Code (important)

Le projet inclut `.vscode/settings.json` pour utiliser `.venv`.

Dans VS Code:

1. Ouvrir le dossier du projet.
2. `Ctrl+Shift+P` -> `Python: Select Interpreter`.
3. Choisir:
   - Linux/Pi: `.venv/bin/python`
   - Windows local: `.venv\\Scripts\\python.exe`

Si erreur "environment not found":

- relancer `./bootstrap.sh` (Linux/Pi) ou recreer `.venv`
- verifier que le dossier `.venv/` existe
- rouvrir VS Code

## Commandes utiles

```bash
python scripts/diagnose_env.py
python scripts/smoke_test.py
python verificationyolo.py --backend auto --image test.jpg
python test_onnx.py --model models/my_model.onnx --image test.jpg
python test_ort.py
```

Lancer Flask:

```bash
./run.sh
```

Puis ouvrir: `http://<ip_machine>:5000`

## Demarrage automatique au boot (Raspberry Pi)

Le depot inclut un service `systemd` et un script d'installation:

```bash
cd /home/danie/me/choupou
chmod +x scripts/install_systemd_service.sh
./scripts/install_systemd_service.sh
```

Le service utilise `run.sh`, donc il reprend:
- `.venv`
- `.env`
- le preflight
- le lancement Flask

Commandes utiles:

```bash
sudo systemctl status wasteai.service
sudo journalctl -u wasteai.service -f
sudo systemctl restart wasteai.service
sudo systemctl disable wasteai.service
```

### Ouvrir automatiquement le navigateur au login graphique

Si vous voulez que l'interface web s'ouvre toute seule sur le Raspberry Pi apres le demarrage du bureau:

```bash
cd /home/danie/me/choupou
chmod +x scripts/open_browser_on_boot.sh scripts/install_browser_autostart.sh
./scripts/install_browser_autostart.sh
```

Le script attend que `http://127.0.0.1:5000/login` reponde, puis ouvre Chromium automatiquement.

## Dependance PDF (reportlab)

L'export PDF reste optionnel.
Si l'API PDF renvoie "reportlab non installe", installer:

```bash
source .venv/bin/activate
python -m pip install reportlab
```

## Resolution des erreurs courantes

1. `Detecteur non pret` dans smoke test
- generer `models/my_model.onnx` avec `scripts/export_to_onnx.py`
- ou installer `requirements-dev.txt` pour utiliser backend PT

2. `onnxruntime indisponible`
- verifier Python (3.10/3.11 recommande)
- sur ARM 32-bit, onnxruntime pip peut etre indisponible; preferer aarch64

3. Camera inaccessible
- tester `WASTEAI_CAMERA_MODE=opencv`
- verifier permissions camera et connectique
- sur Pi camera CSI, installer `python3-picamera2`

4. Base SQLite manquante
- verifier `waste.db` a la racine du projet
- lancer l'app une fois pour initialiser les tables

## Note de maintenance

- Les fichiers `my_model.pt` et `yolov8n.pt` sont dans `models/`.
- Le backend ONNX est la voie recommandee pour Raspberry Pi CPU.
- Les scripts de validation (`diagnose_env.py`, `smoke_test.py`) doivent rester verts avant de deployer.
