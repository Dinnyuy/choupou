// ==================== GESTION DE LA CAMÉRA ====================

let cameraActive = false;
let videoFeedInterval = null;

// Fonction pour démarrer la caméra
async function startCamera() {
    try {
        // Appeler l'API pour activer la caméra
        const response = await fetch('/api/camera/toggle', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ action: 'start' })
        });

        const data = await response.json();

        if (data.success) {
            cameraActive = true;

            // Mettre à jour l'interface
            document.getElementById('startCameraBtn').style.display = 'none';
            document.getElementById('stopCameraBtn').style.display = 'inline-block';
            document.getElementById('noVideoDisplay').style.display = 'none';
            document.getElementById('videoFeed').style.display = 'block';

            // Mettre à jour l'état du robot
            updateRobotStatus(true);

            // Démarrer le flux vidéo
            startVideoFeed();

            console.log('✅ Caméra activée');
        } else {
            alert('Erreur lors de l\'activation de la caméra');
        }
    } catch (error) {
        console.error('Erreur:', error);
        alert('Erreur de connexion au serveur');
    }
}

// Fonction pour arrêter la caméra
async function stopCamera() {
    try {
        // Appeler l'API pour désactiver la caméra
        const response = await fetch('/api/camera/toggle', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ action: 'stop' })
        });

        const data = await response.json();

        if (data.success) {
            cameraActive = false;

            // Arrêter le flux vidéo
            stopVideoFeed();

            // Mettre à jour l'interface
            document.getElementById('startCameraBtn').style.display = 'inline-block';
            document.getElementById('stopCameraBtn').style.display = 'none';
            document.getElementById('videoFeed').style.display = 'none';
            document.getElementById('noVideoDisplay').style.display = 'block';
            document.getElementById('detectionSummary').style.display = 'none';

            // Mettre à jour l'état du robot
            updateRobotStatus(false);

            console.log('✅ Caméra désactivée');
        } else {
            alert('Erreur lors de la désactivation de la caméra');
        }
    } catch (error) {
        console.error('Erreur:', error);
        alert('Erreur de connexion au serveur');
    }
}

// Fonction pour démarrer le flux vidéo
function startVideoFeed() {
    const videoFeed = document.getElementById('videoFeed');
    videoFeed.src = '/video_feed';

    // Démarrer la récupération périodique des détections (toutes les 2 secondes)
    if (window.detectionInterval) {
        clearInterval(window.detectionInterval);
    }

    window.detectionInterval = setInterval(fetchRecentDetections, 2000);

    // Récupérer immédiatement les détections
    fetchRecentDetections();
}

// Fonction pour arrêter le flux vidéo
function stopVideoFeed() {
    const videoFeed = document.getElementById('videoFeed');
    videoFeed.src = '';

    // Arrêter la mise à jour des détections
    if (window.detectionInterval) {
        clearInterval(window.detectionInterval);
        window.detectionInterval = null;
    }
}

// Fonction pour récupérer les détections récentes
async function fetchRecentDetections() {
    try {
        const response = await fetch('/api/camera/recent-detections');
        const data = await response.json();

        if (data.success && data.detections) {
            updateDetectionsDisplay(data.detections);
        }
    } catch (error) {
        console.error('Erreur récupération détections:', error);
    }
}

// Fonction pour mettre à jour l'affichage des détections
function updateDetectionsDisplay(detections) {
    const detectionsList = document.getElementById('detectionsList');
    const detectionSummary = document.getElementById('detectionSummary');

    // Si aucune détection, masquer la section
    if (!detections || Object.keys(detections).length === 0) {
        detectionSummary.style.display = 'none';
        return;
    }

    // Afficher la section des détections
    detectionSummary.style.display = 'block';

    // Créer le HTML pour les détections
    let html = '';
    const wasteColors = {
        'Papier': '#2196F3',
        'Plastique': '#9C27B0',
        'plastique': '#9C27B0',
        'metal': '#FF9800',
        'Métal': '#FF9800',
        'Metal': '#FF9800',
        'Verre': '#00BCD4',
        'verre': '#00BCD4',
        'Carton': '#FF5722'
    };

    for (const [wasteType, count] of Object.entries(detections)) {
        const color = wasteColors[wasteType] || '#4CAF50';
        const className = wasteType.toLowerCase();

        html += `
            <div class="detection-item ${className}">
                <span>${wasteType}</span>
                <span>${count} détection${count > 1 ? 's' : ''}</span>
            </div>
        `;
    }

    detectionsList.innerHTML = html;
}

// Fonction pour mettre à jour l'état du robot
function updateRobotStatus(isActive) {
    const statusIndicator = document.getElementById('robotStatusIndicator');
    const statusText = document.getElementById('robotStatusText');
    const batteryState = document.getElementById('batteryState');

    if (isActive) {
        statusIndicator.style.backgroundColor = '#10b981';
        statusText.textContent = 'Actif';
        batteryState.textContent = 'En fonctionnement';
    } else {
        statusIndicator.style.backgroundColor = '#ef4444';
        statusText.textContent = 'Inactif';
        batteryState.textContent = 'Éteint';
    }
}

// ==================== CHARGEMENT DES DONNÉES ====================

// Charger les informations utilisateur
async function loadUserInfo() {
    try {
        const response = await fetch('/api/user/info');
        const data = await response.json();

        if (data.success) {
            document.getElementById('headerUsername').textContent = data.email;

            if (data.profile_picture) {
                document.getElementById('headerAvatar').src = data.profile_picture;
            }
        }
    } catch (error) {
        console.error('Erreur chargement infos utilisateur:', error);
    }
}

// Charger le statut du robot
async function loadRobotStatus() {
    try {
        const response = await fetch('/api/robot/status');
        const data = await response.json();

        if (data.success) {
            document.getElementById('robotLocation').textContent = data.location.split(',')[0];
            document.getElementById('robotCity').textContent = data.location.split(',').slice(1).join(',').trim();
            document.getElementById('positionText').textContent = data.location.split(',')[0];

            const battery = data.battery || 0;
            document.getElementById('batteryFill').style.width = battery + '%';
            document.getElementById('batteryPercent').textContent = battery + '%';

            // Couleur de la batterie
            const batteryFill = document.getElementById('batteryFill');
            if (battery > 50) {
                batteryFill.style.backgroundColor = '#10b981';
            } else if (battery > 20) {
                batteryFill.style.backgroundColor = '#f59e0b';
            } else {
                batteryFill.style.backgroundColor = '#ef4444';
            }

            updateRobotStatus(data.is_active);
        }
    } catch (error) {
        console.error('Erreur chargement statut robot:', error);
    }
}

// Charger les statistiques
async function loadStats() {
    try {
        const response = await fetch('/api/robot/stats');
        const data = await response.json();

        document.getElementById('detectionToday').textContent = data.today.detections;
        document.getElementById('quantityToday').textContent = data.today.quantity;
        document.getElementById('totalDetections').textContent = data.total.detections;
        document.getElementById('totalQuantity').textContent = data.total.quantity;
    } catch (error) {
        console.error('Erreur chargement stats:', error);
    }
}

// Fonction de déconnexion
function logout() {
    fetch('/api/logout', { method: 'POST' })
        .then(() => {
            window.location.href = '/login';
        })
        .catch(error => {
            console.error('Erreur déconnexion:', error);
            window.location.href = '/login';
        });
}

// ==================== INITIALISATION ====================

document.addEventListener('DOMContentLoaded', function () {
    loadUserInfo();
    loadRobotStatus();
    loadStats();

    // Rafraîchir les stats toutes les 10 secondes
    setInterval(loadStats, 10000);

    // Menu toggle pour mobile
    const menuToggle = document.querySelector('.menu-toggle');
    if (menuToggle) {
        menuToggle.addEventListener('click', function () {
            document.querySelector('.sidebar').classList.toggle('active');
        });
    }
});
