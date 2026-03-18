from flask import Flask, render_template, request, jsonify, session, redirect, Response, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import io
import os
import sqlite3
import tempfile
import atexit
from threading import Lock
import unicodedata
import uuid
from datetime import datetime, timedelta
from functools import wraps
import cv2
import numpy as np

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from config import settings
from detector.camera import CameraSource
from detector.stream_tracker import StreamDetectionTracker
from yolo_detector import WasteDetector

# ---------- NEW: Robotic arm import ----------
from arm_controller import RoboticArm

app = Flask(
    __name__,
    template_folder=str(settings.templates_dir),
    static_folder=str(settings.static_dir),
)
app.secret_key = settings.secret_key

# Chemin de la base de donnees
DB_PATH = str(settings.db_path)

robotic_arm = RoboticArm(
    port=settings.arm_port,
    baudrate=settings.arm_baud,
    mock=settings.arm_mock,
    ack_timeout=settings.arm_ack_timeout,
    queue_maxsize=settings.arm_queue_maxsize,
)


@atexit.register
def shutdown_robotic_arm():
    try:
        robotic_arm.shutdown()
    except Exception:
        pass

# Initialiser le detecteur
try:
    YOLO_DETECTOR = WasteDetector(backend=settings.backend, db_path=DB_PATH)
    if YOLO_DETECTOR.is_ready():
        print(f"Detecteur initialise avec backend={YOLO_DETECTOR.backend_name}")
    else:
        print(f"Detecteur indisponible: {YOLO_DETECTOR.last_error}")
except Exception as e:
    print(f"Erreur chargement detecteur: {e}")
    YOLO_DETECTOR = None

# Variable globale pour la camera
camera = None
camera_stream_lock = Lock()
active_stream_clients = 0
detection_buffer = {}  # Buffer pour accumuler les détections
frame_count = 0  # Compteur de frames
SAVE_INTERVAL = 10  # Sauvegarder toutes les 10 frames
STREAM_DETECT_EVERY_N_FRAMES = settings.stream_detect_every_n_frames
JPEG_ENCODE_PARAMS = [int(cv2.IMWRITE_JPEG_QUALITY), settings.stream_jpeg_quality]
stream_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wasteai-stream")
stream_detection_future = None
stream_latest_detections = []
stream_frames_seen = 0
stream_tracker = StreamDetectionTracker(
    required_hits=settings.stream_confirm_hits,
    max_misses=settings.stream_track_max_misses,
    iou_threshold=settings.stream_track_iou_threshold,
    fast_confirm_confidence=settings.stream_fast_confirm_confidence,
)
# ---------- NEW: Store newly confirmed detections ----------
stream_new_counts = {}

MONTH_OPTIONS = [
    ("01", "Janvier"),
    ("02", "Fevrier"),
    ("03", "Mars"),
    ("04", "Avril"),
    ("05", "Mai"),
    ("06", "Juin"),
    ("07", "Juillet"),
    ("08", "Aout"),
    ("09", "Septembre"),
    ("10", "Octobre"),
    ("11", "Novembre"),
    ("12", "Decembre"),
]
WASTE_TYPE_ALIASES = {
    "papier": "Papier",
    "plastique": "Plastique",
    "metal": "Metal",
    "verre": "Verre",
    "carton": "Carton",
}


def normalize_waste_type_label(value: str | None) -> str | None:
    if value is None:
        return None
    label = str(value).strip()
    if not label:
        return None
    normalized_key = (
        unicodedata.normalize("NFKD", label)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    return WASTE_TYPE_ALIASES.get(normalized_key, label)


def aggregate_waste_rows(rows) -> dict[str, int]:
    totals: dict[str, int] = {}
    for waste_type, quantity in rows:
        if quantity in (None, 0):
            continue
        canonical = normalize_waste_type_label(waste_type)
        if canonical is None:
            continue
        totals[canonical] = totals.get(canonical, 0) + int(quantity)

    ordered: dict[str, int] = {}
    for label in dict.fromkeys(settings.waste_classes.values()):
        if label in totals:
            ordered[label] = totals[label]
    for label in sorted(key for key in totals if key not in ordered):
        ordered[label] = totals[label]
    return ordered


def parse_year_month(year_raw, month_raw) -> tuple[str, str]:
    now = datetime.now()
    try:
        parsed = datetime(int(year_raw), int(month_raw), 1)
    except (TypeError, ValueError):
        parsed = datetime(now.year, now.month, 1)
    return parsed.strftime("%Y"), parsed.strftime("%m")


def normalize_detection_types_in_db(cursor) -> None:
    replacements = {
        "papier": "Papier",
        "plastique": "Plastique",
        "metal": "Metal",
        "Métal": "Metal",
        "métal": "Metal",
        "verre": "Verre",
        "carton": "Carton",
    }
    for source, target in replacements.items():
        cursor.execute(
            "UPDATE waste_detection SET waste_type = ? WHERE waste_type = ?",
            (target, source),
        )


def build_global_detection_filters(start_date, end_date, waste_type):
    normalized_waste_type = normalize_waste_type_label(waste_type or 'all')
    clauses = []
    params = []

    if start_date:
        clauses.append('DATE(wd.detection_date) >= ?')
        params.append(start_date)

    if end_date:
        clauses.append('DATE(wd.detection_date) <= ?')
        params.append(end_date)

    if normalized_waste_type and normalized_waste_type != 'all':
        clauses.append('wd.waste_type = ?')
        params.append(normalized_waste_type)

    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ''
    return where_sql, params


def load_yolo():
    global YOLO_DETECTOR
    if YOLO_DETECTOR and YOLO_DETECTOR.is_ready():
        return True
    YOLO_DETECTOR = WasteDetector(backend=settings.backend, db_path=DB_PATH)
    return YOLO_DETECTOR.is_ready()


def get_camera() -> CameraSource:
    global camera
    if camera is None:
        source = CameraSource(mode=settings.camera_mode, camera_index=settings.camera_index)
        if not source.open():
            raise RuntimeError(
                "Impossible d'ouvrir la camera. "
                "Verifiez WASTEAI_CAMERA_MODE/WASTEAI_CAMERA_INDEX."
            )
        camera = source
    return camera


def reset_stream_state():
    global detection_buffer, frame_count, stream_detection_future, stream_latest_detections, stream_frames_seen, stream_new_counts
    detection_buffer = {}
    frame_count = 0
    stream_latest_detections = []
    stream_frames_seen = 0
    stream_new_counts = {}
    stream_tracker.reset()
    if stream_detection_future is not None:
        stream_detection_future.cancel()
        stream_detection_future = None


def _collect_async_detections():
    global stream_detection_future, stream_latest_detections, stream_new_counts, detection_buffer
    if stream_detection_future is None or not stream_detection_future.done():
        return
    try:
        raw_detections = stream_detection_future.result()
        stream_latest_detections, new_counts = stream_tracker.update(raw_detections)
        stream_new_counts = new_counts
        for waste_type, count in new_counts.items():
            detection_buffer[waste_type] = detection_buffer.get(waste_type, 0) + count
    except Exception as exc:
        print(f"Erreur detection async: {exc}")
    finally:
        stream_detection_future = None


def release_camera():
    global camera
    if camera is not None:
        camera.release()
        camera = None


def flush_detection_buffer(user_id=None):
    global detection_buffer, frame_count
    if user_id and detection_buffer and YOLO_DETECTOR and YOLO_DETECTOR.is_ready():
        try:
            YOLO_DETECTOR.save_detections_to_db(user_id, detection_buffer)
            print(f"Detections sauvegardees: {detection_buffer}")
        except Exception as e:
            print(f"Erreur sauvegarde detections: {e}")
    detection_buffer = {}
    frame_count = 0


# Initialiser la base de données
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Table des utilisateurs
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY, 
                  email TEXT UNIQUE, 
                  password TEXT, 
                  role TEXT DEFAULT 'user',
                  created_at TIMESTAMP,
                  last_login TIMESTAMP)''')
    
    # Migration : ajouter les colonnes role et last_login si elles n'existent pas
    try:
        c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
    except sqlite3.OperationalError:
        pass  # La colonne existe déjà
    
    try:
        c.execute("ALTER TABLE users ADD COLUMN last_login TIMESTAMP")
    except sqlite3.OperationalError:
        pass  # La colonne existe déjà
    
    # Migration : ajouter username et profile_picture
    try:
        c.execute("ALTER TABLE users ADD COLUMN username TEXT")
    except sqlite3.OperationalError:
        pass  # La colonne existe déjà
    
    try:
        c.execute("ALTER TABLE users ADD COLUMN profile_picture TEXT")
    except sqlite3.OperationalError:
        pass  # La colonne existe déjà
    
    # Table des déchets détectés
    c.execute('''CREATE TABLE IF NOT EXISTS waste_detection
                 (id INTEGER PRIMARY KEY, 
                  user_id INTEGER, 
                  waste_type TEXT, 
                  quantity INTEGER,
                  detection_date TIMESTAMP,
                  FOREIGN KEY(user_id) REFERENCES users(id))''')
    normalize_detection_types_in_db(c)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_waste_detection_user_date "
        "ON waste_detection (user_id, detection_date)"
    )
    
    # Table des robots
    c.execute('''CREATE TABLE IF NOT EXISTS robots
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  location TEXT,
                  battery_level INTEGER,
                  is_active BOOLEAN,
                  camera_status TEXT,
                  FOREIGN KEY(user_id) REFERENCES users(id))''')
    
    # Table des notifications
    c.execute('''CREATE TABLE IF NOT EXISTS notifications
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  message TEXT,
                  type TEXT DEFAULT 'info',
                  is_read BOOLEAN DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(user_id) REFERENCES users(id))''')
    
    conn.commit()
    conn.close()


init_db()

# Décorateur pour vérifier l'authentification
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function


# Décorateur pour vérifier les droits admin
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/login')
        if session.get('role') != 'admin':
            return jsonify({'success': False, 'message': 'Accès refusé - Admin uniquement'}), 403
        return f(*args, **kwargs)
    return decorated_function


# ==================== ROUTES D'AUTHENTIFICATION ====================

@app.route('/')
def index():
    return redirect('/login')


@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect('/dashboard')
    return render_template('login.html')


@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    if not email or not password:
        return jsonify({'success': False, 'message': 'Email et mot de passe requis'}), 400
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, password, role FROM users WHERE email = ?', (email,))
    user = c.fetchone()
    
    if user and check_password_hash(user[1], password):
        # Mettre à jour last_login
        c.execute('UPDATE users SET last_login = ? WHERE id = ?', (datetime.now(), user[0]))
        conn.commit()
        conn.close()
        
        # Stocker les infos en session
        session['user_id'] = user[0]
        session['email'] = email
        session['role'] = user[2] if user[2] else 'user'
        return jsonify({'success': True, 'message': 'Connexion réussie'})
    
    conn.close()
    return jsonify({'success': False, 'message': 'Email ou mot de passe incorrect'}), 401


@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    if not email or not password:
        return jsonify({'success': False, 'message': 'Email et mot de passe requis'}), 400
    
    hashed_password = generate_password_hash(password)
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Vérifier si c'est le premier utilisateur
        c.execute('SELECT COUNT(*) FROM users')
        user_count = c.fetchone()[0]
        
        # Premier utilisateur = admin, sinon user
        role = 'admin' if user_count == 0 else 'user'
        
        c.execute('INSERT INTO users (email, password, role, created_at) VALUES (?, ?, ?, ?)',
                  (email, hashed_password, role, datetime.now()))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Inscription réussie'})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': 'Cet email est déjà utilisé'}), 400


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Déconnexion réussie'})


# ==================== ROUTES PROFIL ====================

# Configuration upload
UPLOAD_FOLDER = str(settings.upload_dir)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/profile')
@login_required
def profile_page():
    """Page de profil utilisateur"""
    return render_template('profile.html', email=session.get('email'))


@app.route('/logout')
def logout_user():
    """Déconnexion de l'utilisateur"""
    session.clear()
    return redirect('/login')


@app.route('/api/profile', methods=['GET'])
@login_required
def get_profile():
    """Récupérer les informations du profil"""
    user_id = session.get('user_id')
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT email, username, profile_picture, role, created_at FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    conn.close()
    
    if user:
        return jsonify({
            'success': True,
            'email': user[0],
            'username': user[1] or '',
            'profile_picture': user[2] or '',
            'role': user[3] if user[3] else 'user',
            'created_at': user[4]
        })
    
    return jsonify({'success': False, 'message': 'Utilisateur non trouvé'}), 404


@app.route('/api/profile/update', methods=['POST'])
@login_required
def update_profile():
    """Mettre à jour le profil (username)"""
    user_id = session.get('user_id')
    data = request.json
    username = data.get('username', '').strip()
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE users SET username = ? WHERE id = ?', (username, user_id))
        conn.commit()
        conn.close()
        
        # Mettre à jour la session
        session['username'] = username
        
        return jsonify({'success': True, 'message': 'Profil mis à jour'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/profile/change-password', methods=['POST'])
@login_required
def change_password():
    """Changer le mot de passe"""
    user_id = session.get('user_id')
    data = request.json
    
    current_password = data.get('current_password')
    new_password = data.get('new_password')
    confirm_password = data.get('confirm_password')
    
    if not current_password or not new_password or not confirm_password:
        return jsonify({'success': False, 'message': 'Tous les champs sont requis'}), 400
    
    if new_password != confirm_password:
        return jsonify({'success': False, 'message': 'Les mots de passe ne correspondent pas'}), 400
    
    if len(new_password) < 6:
        return jsonify({'success': False, 'message': 'Le mot de passe doit contenir au moins 6 caractères'}), 400
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT password FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    
    if not user or not check_password_hash(user[0], current_password):
        conn.close()
        return jsonify({'success': False, 'message': 'Mot de passe actuel incorrect'}), 401
    
    hashed_password = generate_password_hash(new_password)
    c.execute('UPDATE users SET password = ? WHERE id = ?', (hashed_password, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Mot de passe modifié avec succès'})


@app.route('/api/profile/upload-picture', methods=['POST'])#jfhjh
@login_required
def upload_profile_picture():
    """Uploader une photo de profil"""
    user_id = session.get('user_id')
    
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Aucun fichier envoyé'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Aucun fichier sélectionné'}), 400
    
    if file and allowed_file(file.filename):
        # Créer le dossier s'il n'existe pas
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        
        # Générer un nom unique
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{user_id}_{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        
        # Supprimer l'ancienne photo si elle existe
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT profile_picture FROM users WHERE id = ?', (user_id,))
        old_picture = c.fetchone()
        
        if old_picture and old_picture[0]:
            static_prefix = '/static/'
            stored_path = old_picture[0]
            if stored_path.startswith(static_prefix):
                relative_path = stored_path[len(static_prefix):]
            else:
                relative_path = stored_path.lstrip('/')
            old_path = os.path.join(str(settings.static_dir), relative_path)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except:
                    pass
        
        # Sauvegarder le nouveau fichier
        file.save(filepath)
        
        # Mettre à jour la BDD
        picture_url = f"/static/uploads/profiles/{filename}"
        c.execute('UPDATE users SET profile_picture = ? WHERE id = ?', (picture_url, user_id))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Photo de profil mise à jour',
            'picture_url': picture_url
        })
    
    return jsonify({'success': False, 'message': 'Type de fichier non autorisé (png, jpg, jpeg, gif)'}), 400


@app.route('/api/user/info', methods=['GET'])
@login_required
def get_user_info():
    """Récupérer les infos utilisateur pour le header"""
    user_id = session.get('user_id')
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT username, profile_picture, email FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    conn.close()
    
    if user:
        return jsonify({
            'success': True,
            'username': user[0] or user[2].split('@')[0],  # Username ou partie avant @ de l'email
            'profile_picture': user[1] or '',
            'email': user[2]
        })
    
    return jsonify({'success': False}), 404


# ==================== ROUTES DASHBOARD ====================

@app.route('/dashboard')
@login_required
def dashboard():
    current_now = datetime.now()
    chart_years = list(range(max(2023, current_now.year - 3), current_now.year + 1))
    return render_template(
        'dashboard.html',
        email=session.get('email'),
        current_year=current_now.year,
        current_month=current_now.strftime('%m'),
        chart_years=chart_years,
        month_options=MONTH_OPTIONS,
    )


@app.route('/api/waste/add', methods=['POST'])
@login_required
def add_waste_detection():
    user_id = session.get('user_id')
    data = request.json
    
    waste_type = normalize_waste_type_label(data.get('waste_type'))
    quantity = data.get('quantity', 1)
    detection_date = data.get('detection_date', datetime.now())
    
    if not waste_type:
        return jsonify({'success': False, 'message': 'Type de déchet requis'}), 400
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO waste_detection (user_id, waste_type, quantity, detection_date)
                 VALUES (?, ?, ?, ?)''',
              (user_id, waste_type, quantity, detection_date))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Déchet ajouté'})


# ==================== ROUTES CAMERA ====================

@app.route('/camera')
@login_required
def camera_page():
    return render_template('camera.html', email=session.get('email'))


def gen_frames(user_id=None):
    global frame_count, detection_buffer, stream_detection_future, stream_frames_seen, active_stream_clients, stream_new_counts

    try:
        cam = get_camera()
    except RuntimeError as exc:
        error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(error_frame, "CAMERA INDISPONIBLE", (40, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        cv2.putText(error_frame, str(exc)[:65], (20, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)
        ret, buffer = cv2.imencode('.jpg', error_frame)
        if ret:
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        return

    with camera_stream_lock:
        active_stream_clients += 1

    if not cam.is_opened():
        print("Camera non accessible (is_opened=False)")
    else:
        print("Flux camera demarre")

    try:
        while True:
            _collect_async_detections()

            # ---------- NEW: Trigger arm for newly confirmed detections ----------
            for waste_type, count in stream_new_counts.items():
                for _ in range(count):
                    robotic_arm.pick_up(waste_type)
            stream_new_counts = {}   # clear after triggering

            success, frame = cam.read()
            if not success:
                print("Echec lecture frame camera")
                error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(error_frame, "ERREUR CAMERA", (50, 240), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                ret, buffer = cv2.imencode('.jpg', error_frame)
                frame = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                break
            else:
                if YOLO_DETECTOR and YOLO_DETECTOR.is_ready():
                    stream_frames_seen += 1
                    if stream_detection_future is None and (
                        stream_frames_seen == 1 or stream_frames_seen % STREAM_DETECT_EVERY_N_FRAMES == 0
                    ):
                        detection_frame = cam.prepare_for_detection(frame.copy())
                        stream_detection_future = stream_executor.submit(
                            YOLO_DETECTOR.detect_objects,
                            detection_frame,
                        )
                    frame = YOLO_DETECTOR.draw_detections(frame, stream_latest_detections)

                frame_count += 1
                if frame_count >= SAVE_INTERVAL and detection_buffer:
                    flush_detection_buffer(user_id=user_id)

                ret, buffer = cv2.imencode('.jpg', frame, JPEG_ENCODE_PARAMS)
                frame = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    finally:
        should_release_camera = False
        with camera_stream_lock:
            active_stream_clients = max(0, active_stream_clients - 1)
            should_release_camera = active_stream_clients == 0
        if should_release_camera:
            flush_detection_buffer(user_id=user_id)
            release_camera()
            reset_stream_state()
            robotic_arm.stop_motion(clear_queue=True)


@app.route('/video_feed')
@login_required
def video_feed():
    user_id = session.get('user_id')
    return Response(gen_frames(user_id=user_id), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/api/robot/status', methods=['GET'])
@login_required
def get_robot_status():
    user_id = session.get('user_id')
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT location, battery_level, is_active FROM robots WHERE user_id = ?', (user_id,))
    robot = c.fetchone()
    conn.close()
    
    if robot:
        return jsonify({
            'success': True,
            'location': robot[0],
            'battery': robot[1],
            'is_active': robot[2]
        })
    
    return jsonify({
        'success': True,
        'location': 'Glacier Moderne, Bonamoussadi, Douala Cameroun',
        'battery': 85,
        'is_active': False
    })


@app.route('/api/camera/toggle', methods=['POST'])
@login_required
def toggle_camera_route():
    data = request.json
    action = data.get('action')
    
    if action == 'start':
        try:
            get_camera()
        except RuntimeError as exc:
            return jsonify({'success': False, 'message': str(exc)}), 500
        reset_stream_state()
    elif action == 'stop':
        user_id = session.get('user_id')
        if detection_buffer:
            flush_detection_buffer(user_id=user_id)
        release_camera()
        reset_stream_state()
        robotic_arm.stop_motion(clear_queue=True)
    
    return jsonify({
        'success': True,
        'message': f'Caméra {"activée" if action == "start" else "désactivée"}',
        'status': action
    })


@app.route('/api/arm/status', methods=['GET'])
@login_required
def arm_status_route():
    return jsonify({
        'success': True,
        'arm': robotic_arm.status(),
    })


@app.route('/api/arm/home', methods=['POST'])
@login_required
def arm_home_route():
    ok = robotic_arm.home_now(clear_queue=True)
    return jsonify({
        'success': ok,
        'message': 'Retour du bras a la position home demande' if ok else (robotic_arm.status().get('last_error') or 'Impossible de ramener le bras en home'),
        'arm': robotic_arm.status(),
    }), (200 if ok else 500)


@app.route('/api/arm/move', methods=['POST'])
@login_required
def arm_move_route():
    data = request.json or {}
    joint = data.get('joint')
    angle_raw = data.get('angle')

    if joint in (None, '') or angle_raw in (None, ''):
        return jsonify({
            'success': False,
            'message': 'joint et angle requis',
        }), 400

    try:
        angle = int(angle_raw)
    except (TypeError, ValueError):
        return jsonify({
            'success': False,
            'message': 'angle doit etre un entier',
        }), 400

    ok = robotic_arm.move_joint(joint, angle, clear_queue=True)
    return jsonify({
        'success': ok,
        'message': f'Mouvement demande pour {joint} a {angle} degres' if ok else (robotic_arm.status().get('last_error') or 'Impossible de bouger cette articulation'),
        'arm': robotic_arm.status(),
    }), (200 if ok else 500)


@app.route('/api/arm/test', methods=['POST'])
@login_required
def arm_test_route():
    data = request.json or {}
    waste_type = normalize_waste_type_label(data.get('waste_type'))
    if not waste_type:
        return jsonify({'success': False, 'message': 'waste_type requis'}), 400

    queued = robotic_arm.pick_up(waste_type)
    if not queued:
        return jsonify({
            'success': False,
            'message': robotic_arm.status().get('last_error') or 'Impossible de mettre la commande en file',
            'arm': robotic_arm.status(),
        }), 500

    return jsonify({
        'success': True,
        'message': f'Commande envoyee au bras pour {waste_type}',
        'arm': robotic_arm.status(),
    })


@app.route('/api/arm/stop', methods=['POST'])
@login_required
def arm_stop_route():
    ok = robotic_arm.stop_motion(clear_queue=True)
    return jsonify({
        'success': ok,
        'message': 'Arret du bras demande',
        'arm': robotic_arm.status(),
    }), (200 if ok else 500)


@app.route('/api/camera/recent-detections', methods=['GET'])
@login_required
def get_recent_detections():
    """Récupérer les détections des 30 dernières secondes"""
    user_id = session.get('user_id')
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Récupérer les détections des 30 dernières secondes
        c.execute('''SELECT waste_type, SUM(quantity) as total 
                     FROM waste_detection 
                     WHERE user_id = ? 
                     AND detection_date >= datetime('now', '-30 seconds')
                     GROUP BY waste_type''', (user_id,))
        
        detections = c.fetchall()
        conn.close()
        
        # Formater les résultats
        result = {row[0]: row[1] for row in detections}
        
        return jsonify({
            'success': True,
            'detections': result
        })
    except Exception as e:
        print(f"❌ Erreur récupération détections: {e}")
        return jsonify({
            'success': False,
            'detections': {}
        })


@app.route('/api/robot/stats', methods=['GET'])
@login_required
def get_robot_stats():
    user_id = session.get('user_id')
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''SELECT COUNT(*), SUM(quantity) FROM waste_detection 
                 WHERE user_id = ? AND DATE(detection_date) = DATE('now')''', (user_id,))
    result = c.fetchone()
    detections_today = result[0] or 0
    quantity_today = result[1] or 0
    
    c.execute('''SELECT COUNT(*), SUM(quantity) FROM waste_detection 
                 WHERE user_id = ?''', (user_id,))
    result = c.fetchone()
    total_detections = result[0] or 0
    total_quantity = result[1] or 0
    
    conn.close()
    
    return jsonify({
        'today': {'detections': detections_today, 'quantity': quantity_today},
        'total': {'detections': total_detections, 'quantity': total_quantity}
    })


@app.route('/api/robot/add', methods=['POST'])
@login_required
def add_robot():
    user_id = session.get('user_id')
    data = request.json
    
    location = data.get('location', 'Glacier Moderne, Bonamoussadi, Douala Cameroun')
    battery = data.get('battery', 85)
    is_active = data.get('is_active', False)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id FROM robots WHERE user_id = ?', (user_id,))
    
    if c.fetchone():
        c.execute('''UPDATE robots SET location = ?, battery_level = ?, is_active = ? 
                     WHERE user_id = ?''',
                  (location, battery, is_active, user_id))
    else:
        c.execute('''INSERT INTO robots (user_id, location, battery_level, is_active, camera_status)
                     VALUES (?, ?, ?, ?, ?)''',
                  (user_id, location, battery, is_active, 'inactive'))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Robot mis à jour'})


# ==================== ROUTES DÉTECTION ROBOT ====================

@app.route('/api/detection/record', methods=['POST'])
def record_detection():
    """
    Route pour enregistrer les détections du robot
    Le robot envoie les données quand il détecte un déchet
    """
    data = request.json
    
    # L'utilisateur peut être identifié par un token ou un ID robot
    user_id = data.get('user_id')
    robot_id = data.get('robot_id')
    waste_type = normalize_waste_type_label(data.get('waste_type'))
    quantity = data.get('quantity', 1)
    detection_date = data.get('detection_date', datetime.now())
    
    if not waste_type or not user_id:
        return jsonify({'success': False, 'message': 'user_id et waste_type requis'}), 400
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT INTO waste_detection (user_id, waste_type, quantity, detection_date)
                     VALUES (?, ?, ?, ?)''',
                  (user_id, waste_type, quantity, detection_date))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True, 
            'message': f'{waste_type} ({quantity}) détecté et enregistré'
        }), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/detection/batch', methods=['POST'])
def record_batch_detection():
    """
    Route pour enregistrer plusieurs détections à la fois
    Utile pour les données collectées offline
    """
    data = request.json
    
    user_id = data.get('user_id')
    detections = data.get('detections', [])
    
    if not user_id or not detections:
        return jsonify({'success': False, 'message': 'user_id et detections requis'}), 400
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        for detection in detections:
            waste_type = normalize_waste_type_label(detection.get('waste_type'))
            quantity = detection.get('quantity', 1)
            detection_date = detection.get('detection_date', datetime.now())
            
            if waste_type:
                c.execute('''INSERT INTO waste_detection (user_id, waste_type, quantity, detection_date)
                             VALUES (?, ?, ?, ?)''',
                          (user_id, waste_type, quantity, detection_date))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True, 
            'message': f'{len(detections)} détection(s) enregistrée(s)'
        }), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== ROUTES YOLO ====================

@app.route('/yolo-detect')
@login_required
def yolo_detect():
    """Page de détection YOLO"""
    yolo_template = settings.templates_dir / 'yolo_detect.html'
    if yolo_template.exists():
        return render_template('yolo_detect.html', email=session.get('email'))
    return redirect('/camera')


@app.route('/api/yolo/detect-image', methods=['POST'])
@login_required
def yolo_detect_image():
    """Détecter les déchets dans une image uploadée"""
    user_id = session.get('user_id')
    
    # Charger YOLO
    if not load_yolo():
        details = YOLO_DETECTOR.last_error if YOLO_DETECTOR else "Detecteur non initialise"
        return jsonify({'success': False, 'message': f'Modele YOLO non disponible: {details}'}), 500
    
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Aucun fichier uploadé'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Fichier vide'}), 400
    
    temp_path = None
    try:
        # Sauvegarder le fichier temporairement dans le dossier du projet
        suffix = Path(file.filename).suffix if file.filename else '.jpg'
        with tempfile.NamedTemporaryFile(
            mode='wb',
            suffix=suffix or '.jpg',
            prefix='upload_',
            dir=str(settings.base_dir),
            delete=False,
        ) as tmp:
            temp_path = tmp.name
        file.save(temp_path)
        
        # Détection
        detections, details = YOLO_DETECTOR.detect_from_image(temp_path)
        if detections is None:
            return jsonify({'success': False, 'message': str(details)}), 500
        
        if detections:
            # Résumer les détections
            summary = {}
            for det in detections:
                waste_type = det['waste_type']
                if waste_type not in summary:
                    summary[waste_type] = 0
                summary[waste_type] += 1
            
            return jsonify({
                'success': True,
                'detections': summary,
                'total': sum(summary.values()),
                'message': f'{sum(summary.values())} déchet(s) détecté(s)'
            })
        else:
            return jsonify({
                'success': True,
                'detections': {},
                'total': 0,
                'message': 'Aucun déchet détecté'
            })
    
    except Exception as e:
        return jsonify({'success': False, 'message': f'Erreur: {str(e)}'}), 500
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


@app.route("/predict", methods=['POST'])
def predict():
    if not load_yolo():
        details = YOLO_DETECTOR.last_error if YOLO_DETECTOR else "Detecteur non initialise"
        return jsonify({'success': False, 'message': f'Modele YOLO non disponible: {details}'}), 500

    if 'image' not in request.files:
        return "No image found", 400
    
    file = request.files['image']
    image = cv2.imdecode(np.frombuffer(file.read(), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return jsonify({'success': False, 'message': 'Image invalide'}), 400

    image, _ = YOLO_DETECTOR.detect_from_frame(image)
    ok, img_encoded = cv2.imencode(".jpg", image)
    if not ok:
        return jsonify({'success': False, 'message': "Echec d'encodage image"}), 500

    return send_file(io.BytesIO(img_encoded.tobytes()), mimetype="image/jpeg")


@app.route('/api/yolo/detect-webcam', methods=['POST'])
@login_required
def yolo_detect_webcam():
    """Détecter en temps réel depuis la webcam"""
    user_id = session.get('user_id')
    data = request.json
    duration = data.get('duration', 10)
    save_to_db = data.get('save_to_db', True)
    
    if not load_yolo():
        details = YOLO_DETECTOR.last_error if YOLO_DETECTOR else "Detecteur non initialise"
        return jsonify({'success': False, 'message': f'Modele YOLO non disponible: {details}'}), 500
    
    try:
        # Détection webcam
        detections = YOLO_DETECTOR.detect_from_webcam(user_id, duration)
        
        # Enregistrer dans la BD si demandé
        if save_to_db and detections:
            YOLO_DETECTOR.save_detections_to_db(user_id, detections)
        
        return jsonify({
            'success': True,
            'detections': detections,
            'total': sum(detections.values()) if detections else 0,
            'message': f'{sum(detections.values()) if detections else 0} déchet(s) détecté(s)'
        })
    
    except Exception as e:
        return jsonify({'success': False, 'message': f'Erreur: {str(e)}'}), 500


@app.route('/api/yolo/save-detections', methods=['POST'])
@login_required
def yolo_save_detections():
    """Enregistrer les détections YOLO dans la BD"""
    user_id = session.get('user_id')
    data = request.json
    detections = data.get('detections', {})
    
    if not detections:
        return jsonify({'success': False, 'message': 'Aucune détection à enregistrer'}), 400
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        for waste_type, quantity in detections.items():
            waste_type = normalize_waste_type_label(waste_type)
            if not waste_type:
                continue
            c.execute('''INSERT INTO waste_detection (user_id, waste_type, quantity, detection_date)
                         VALUES (?, ?, ?, ?)''',
                     (user_id, waste_type, quantity, datetime.now()))
        
        conn.commit()
        conn.close()
        
        print(f"✅ {len(detections)} détection(s) enregistrée(s) dans la BD")
        
        return jsonify({
            'success': True,
            'message': f'{len(detections)} type(s) de déchet enregistré(s)'
        })
    
    except Exception as e:
        return jsonify({'success': False, 'message': f'Erreur: {str(e)}'}), 500


# ==================== ROUTES STATS & CHARTS ====================

@app.route('/api/stats/monthly-distribution', methods=['GET'])
@login_required
def get_monthly_distribution():
    """Statistiques d'un mois spécifique pour le diagramme circulaire"""
    year, month = parse_year_month(
        request.args.get('year', datetime.now().strftime('%Y')),
        request.args.get('month', datetime.now().strftime('%m')),
    )
    
    target_month = f"{year}-{month}"
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('''SELECT waste_type, SUM(quantity) 
                     FROM waste_detection 
                     WHERE strftime('%Y-%m', detection_date) = ?
                     GROUP BY waste_type''', (target_month,))
        
        results = c.fetchall()
        conn.close()
        
        waste_types = aggregate_waste_rows(results)
        total = sum(waste_types.values())
        
        return jsonify({
            'total': total,
            'waste_types': waste_types,
            'year': int(year),
            'month': month,
            'scope': 'global'
        })
    except Exception as e:
        return jsonify({'total': 0, 'waste_types': {}}), 500


@app.route('/api/stats/last-month', methods=['GET'])
@login_required
def get_last_month_stats():
    """Statistiques du mois dernier"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Get last month data
        c.execute('''SELECT waste_type, SUM(quantity) 
                     FROM waste_detection 
                     WHERE strftime('%Y-%m', detection_date) = strftime('%Y-%m', 'now', '-1 month')
                     GROUP BY waste_type''')
        
        results = c.fetchall()
        conn.close()
        
        waste_types = aggregate_waste_rows(results)
        total = sum(waste_types.values())
        
        return jsonify({
            'total': total,
            'waste_types': waste_types
        })
    except Exception as e:
        return jsonify({'total': 0, 'waste_types': {}}), 500


@app.route('/api/stats/total', methods=['GET'])
@login_required
def get_total_stats():
    """Statistiques totales"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Get all time data
        c.execute('''SELECT waste_type, SUM(quantity) 
                     FROM waste_detection 
                     GROUP BY waste_type''')
        
        results = c.fetchall()
        conn.close()
        
        waste_types = aggregate_waste_rows(results)
        total = sum(waste_types.values())
        
        return jsonify({
            'total': total,
            'waste_types': waste_types
        })
    except Exception as e:
        return jsonify({'total': 0, 'waste_types': {}}), 500


@app.route('/api/chart/monthly', methods=['GET'])
@login_required
def get_monthly_chart():
    """Données pour le graphique mensuel"""
    year = request.args.get('year', datetime.now().year, type=int)
    waste_type = normalize_waste_type_label(request.args.get('waste_type', 'all'))
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        months = ['Jan', 'Fev', 'Mar', 'Avr', 'Mai', 'Juin', 'Juil', 'Aout', 'Sep', 'Oct', 'Nov', 'Dec']
        data = []
        
        for month_num in range(1, 13):
            c.execute('''SELECT waste_type, SUM(quantity) 
                         FROM waste_detection 
                         WHERE strftime('%Y', detection_date) = ? 
                         AND strftime('%m', detection_date) = ?
                         GROUP BY waste_type''',
                     (str(year), f'{month_num:02d}'))
            summary = aggregate_waste_rows(c.fetchall())
            if waste_type == 'all':
                data.append(sum(summary.values()))
            else:
                data.append(summary.get(waste_type, 0))
        
        conn.close()
        
        return jsonify({
            'months': months,
            'data': data
        })
    except Exception as e:
        print(f"Error in monthly chart: {e}")
        return jsonify({'months': [], 'data': []}), 500


@app.route('/api/chart/weekly', methods=['GET'])
@login_required
def get_weekly_chart():
    """Données pour le graphique hebdomadaire"""
    week_offset = request.args.get('week_offset', 0, type=int)
    waste_type = normalize_waste_type_label(request.args.get('waste_type', 'all'))
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        days = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim']
        data = []
        
        # Calculate start of week
        from datetime import timedelta
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday()) - timedelta(weeks=week_offset)
        
        for day_num in range(7):
            target_date = start_of_week + timedelta(days=day_num)
            date_str = target_date.strftime('%Y-%m-%d')
            
            c.execute('''SELECT waste_type, SUM(quantity) 
                         FROM waste_detection 
                         WHERE DATE(detection_date) = ?
                         GROUP BY waste_type''',
                     (date_str,))
            summary = aggregate_waste_rows(c.fetchall())
            if waste_type == 'all':
                data.append(sum(summary.values()))
            else:
                data.append(summary.get(waste_type, 0))
        
        conn.close()
        
        return jsonify({
            'days': days,
            'data': data
        })
    except Exception as e:
        print(f"Error in weekly chart: {e}")
        return jsonify({'days': [], 'data': []}), 500


# ==================== ROUTES ADMIN ====================

@app.route('/admin/users')
@login_required
@admin_required
def admin_users_page():
    """Page de gestion des utilisateurs (admin uniquement)"""
    return render_template('admin_users.html', email=session.get('email'))


@app.route('/api/admin/users', methods=['GET'])
@login_required
@admin_required
def get_all_users():
    """Récupérer la liste de tous les utilisateurs"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''SELECT id, email, role, created_at, last_login 
                     FROM users ORDER BY created_at DESC''')
        users = c.fetchall()
        conn.close()
        
        users_list = []
        for user in users:
            users_list.append({
                'id': user[0],
                'email': user[1],
                'role': user[2] if user[2] else 'user',
                'created_at': user[3],
                'last_login': user[4]
            })
        
        return jsonify({'success': True, 'users': users_list})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/users/<int:user_id>/role', methods=['PUT'])
@login_required
@admin_required
def update_user_role(user_id):
    """Modifier le rôle d'un utilisateur"""
    data = request.json
    new_role = data.get('role')
    
    if new_role not in ['admin', 'user']:
        return jsonify({'success': False, 'message': 'Rôle invalide'}), 400
    
    # Empêcher l'admin de se rétrograder lui-même
    if user_id == session.get('user_id') and new_role == 'user':
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas vous rétrograder vous-même'}), 400
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE users SET role = ? WHERE id = ?', (new_role, user_id))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': f'Rôle mis à jour en {new_role}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_user(user_id):
    """Supprimer un utilisateur"""
    # Empêcher l'admin de se supprimer lui-même
    if user_id == session.get('user_id'):
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas vous supprimer vous-même'}), 400
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Supprimer les détections de l'utilisateur
        c.execute('DELETE FROM waste_detection WHERE user_id = ?', (user_id,))
        
        # Supprimer le robot de l'utilisateur
        c.execute('DELETE FROM robots WHERE user_id = ?', (user_id,))
        
        # Supprimer l'utilisateur
        c.execute('DELETE FROM users WHERE id = ?', (user_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Utilisateur supprimé'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== ROUTES NOTIFICATIONS ====================

@app.route('/api/notifications', methods=['GET'])
@login_required
def get_notifications():
    """Récupérer toutes les notifications de l'utilisateur"""
    user_id = session.get('user_id')
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('''SELECT id, message, type, is_read, created_at 
                     FROM notifications 
                     WHERE user_id = ? 
                     ORDER BY created_at DESC 
                     LIMIT 50''', (user_id,))
        
        notifications = []
        for row in c.fetchall():
            notifications.append({
                'id': row[0],
                'message': row[1],
                'type': row[2],
                'is_read': bool(row[3]),
                'created_at': row[4]
            })
        
        # Compter les non-lues
        c.execute('SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0', (user_id,))
        unread_count = c.fetchone()[0]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'notifications': notifications,
            'unread_count': unread_count
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/notifications/<int:notification_id>/mark-read', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    """Marquer une notification comme lue"""
    user_id = session.get('user_id')
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?', 
                  (notification_id, user_id))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Notification marquée comme lue'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/notifications/mark-all-read', methods=['POST'])
@login_required
def mark_all_notifications_read():
    """Marquer toutes les notifications comme lues"""
    user_id = session.get('user_id')
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('UPDATE notifications SET is_read = 1 WHERE user_id = ?', (user_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Toutes les notifications marquées comme lues'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/notifications/<int:notification_id>', methods=['DELETE'])
@login_required
def delete_notification(notification_id):
    """Supprimer une notification"""
    user_id = session.get('user_id')
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('DELETE FROM notifications WHERE id = ? AND user_id = ?', 
                  (notification_id, user_id))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Notification supprimée'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== ROUTES DETECTION PAGE ====================

@app.route('/detections')
@login_required
def detections_page():
    """Page de détection avec liste et exports"""
    return render_template('detections.html', email=session.get('email'))


@app.route('/api/detections/list', methods=['GET'])
@login_required
def get_detections_list():
    """Récupérer la liste des détections avec filtres et pagination"""
    # Get filters from query params
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    waste_type = request.args.get('waste_type', 'all')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        where_sql, filter_params = build_global_detection_filters(start_date, end_date, waste_type)

        count_query = f'''SELECT COUNT(*)
                          FROM waste_detection wd
                          {where_sql}'''
        c.execute(count_query, filter_params)
        total = c.fetchone()[0]

        query = f'''SELECT wd.id, wd.waste_type, wd.quantity, wd.detection_date,
                           COALESCE(NULLIF(u.username, ''), u.email) AS owner_label
                    FROM waste_detection wd
                    LEFT JOIN users u ON u.id = wd.user_id
                    {where_sql}
                    ORDER BY wd.detection_date DESC
                    LIMIT ? OFFSET ?'''
        params = list(filter_params) + [per_page, (page - 1) * per_page]
        c.execute(query, params)
        detections = c.fetchall()
        
        conn.close()
        
        return jsonify({
            'success': True,
            'scope': 'global',
            'detections': [
                {
                    'id': d[0],
                    'waste_type': d[1],
                    'quantity': d[2],
                    'date': d[3],
                    'owner': d[4] or 'Utilisateur inconnu',
                }
                for d in detections
            ],
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/detections/export/csv', methods=['GET'])
@login_required
def export_detections_csv():
    """Exporter les détections en CSV"""
    import csv
    from io import StringIO
    
    # Get filters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    waste_type = request.args.get('waste_type', 'all')
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        where_sql, params = build_global_detection_filters(start_date, end_date, waste_type)
        query = f'''SELECT wd.id, COALESCE(NULLIF(u.username, ''), u.email) AS owner_label,
                           wd.waste_type, wd.quantity, wd.detection_date
                    FROM waste_detection wd
                    LEFT JOIN users u ON u.id = wd.user_id
                    {where_sql}
                    ORDER BY wd.detection_date DESC'''

        c.execute(query, params)
        detections = c.fetchall()
        conn.close()
        
        si = StringIO()
        writer = csv.writer(si)
        writer.writerow(['ID', 'Profil source', 'Type de déchet', 'Quantité', 'Date de détection'])
        writer.writerows(detections)
        
        output = si.getvalue()
        si.close()
        
        return Response(
            output,
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=detections.csv'}
        )
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/detections/export/pdf', methods=['GET'])
@login_required
def export_detections_pdf():
    """Exporter les détections en PDF"""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from io import BytesIO
    except ImportError:
        return jsonify({'success': False, 'message': 'reportlab non installé. Exécutez: pip install reportlab'}), 500
    
    # Get filters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    waste_type = request.args.get('waste_type', 'all')
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        where_sql, params = build_global_detection_filters(start_date, end_date, waste_type)
        query = f'''SELECT wd.id, COALESCE(NULLIF(u.username, ''), u.email) AS owner_label,
                           wd.waste_type, wd.quantity, wd.detection_date
                    FROM waste_detection wd
                    LEFT JOIN users u ON u.id = wd.user_id
                    {where_sql}
                    ORDER BY wd.detection_date DESC'''

        c.execute(query, params)
        detections = c.fetchall()
        conn.close()
        
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        elements = []
        
        # Styles
        styles = getSampleStyleSheet()
        title_style = styles['Heading1']
        
        # Title
        title = Paragraph("Rapport Global de Détections - WasteAI", title_style)
        elements.append(title)
        elements.append(Spacer(1, 20))
        
        # Table data
        data = [['ID', 'Profil source', 'Type de déchet', 'Quantité', 'Date de détection']]
        for detection in detections:
            data.append([
                str(detection[0]),
                detection[1] or 'Utilisateur inconnu',
                detection[2],
                str(detection[3]),
                detection[4],
            ])
        
        # Create table
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        elements.append(table)
        doc.build(elements)
        
        buffer.seek(0)
        
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name='detections.pdf'
        )
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== ROUTE DE TEST ====================

@app.route('/test-api')
@login_required
def test_api():
    """Page de test des API"""
    return render_template('test_api.html')


if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.run(
        host=settings.flask_host,
        port=settings.flask_port,
        debug=settings.flask_debug,
    )
