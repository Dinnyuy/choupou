// Variables globales pour les graphiques
let monthlyChartInstance = null;
let weeklyChartInstance = null;
let pieChartInstance = null;

// Initialisation au chargement de la page
document.addEventListener('DOMContentLoaded', () => {
    loadUserInfo();
    loadStatistics();
    initializeCharts();
    setupMenuToggle();
    markActiveLink();
});

// Charger les informations de l'utilisateur
async function loadUserInfo() {
    try {
        console.log('Loading user info...');
        const response = await fetch('/api/profile');
        const data = await response.json();
        console.log('User data received:', data);

        if (data.success) {
            // Mettre à jour le username dans le header et le titre de bienvenue
            const usernameEl = document.getElementById('headerUsername');
            const welcomeBanner = document.getElementById('welcomeBanner');
            const welcomeTitle = document.getElementById('welcomeTitle');

            const displayUsername = data.username || data.email;
            console.log('Display username:', displayUsername);

            if (usernameEl) {
                usernameEl.textContent = displayUsername;
                console.log('Username updated in header');
            }

            if (welcomeBanner && welcomeTitle) {
                welcomeTitle.textContent = `Bonjour, ${data.username || 'utilisateur'} !`;
                welcomeBanner.style.display = 'block';
                console.log('Welcome banner displayed');
            }

            // Mettre à jour la photo de profil
            const avatarEl = document.getElementById('headerAvatar');
            if (avatarEl && data.profile_picture) {
                avatarEl.src = data.profile_picture;
                console.log('Profile picture updated:', data.profile_picture);
            } else if (avatarEl) {
                console.log('No profile picture in data, using default');
            }
        } else {
            console.error('User data fetch failed:', data);
        }
    } catch (error) {
        console.error('Erreur chargement info utilisateur:', error);
    }
}

// Charger les statistiques
async function loadStatistics() {
    try {
        console.log('Loading statistics...');
        const [currentDistribution, lastMonth, total] = await Promise.all([
            fetch('/api/stats/monthly-distribution').then(r => r.json()), // Use the general distribution point
            fetch('/api/stats/last-month').then(r => r.json()),
            fetch('/api/stats/total').then(r => r.json())
        ]);

        console.log('Current month data:', currentDistribution);
        console.log('Last month data:', lastMonth);
        console.log('Total data:', total);

        displayStats('currentMonthTotal', 'currentMonthWaste', currentDistribution);
        displayStats('lastMonthTotal', 'lastMonthWaste', lastMonth);
        displayStats('totalSince', 'totalWaste', total);

        // Initial pie chart load using the currently selected month/year filters
        await updatePieChart();
        console.log('Statistics loaded successfully');
    } catch (error) {
        console.error('Erreur lors du chargement des statistiques:', error);
    }
}

// Afficher les statistiques
function displayStats(totalId, wasteId, data) {
    document.getElementById(totalId).textContent = data.total;

    const wasteContainer = document.getElementById(wasteId);
    wasteContainer.innerHTML = '';

    const colors = {
        'Papier': '#2196F3',
        'Plastique': '#9C27B0',
        'Metal': '#FF9800',
        'Métal': '#FF9800',
        'Verre': '#00BCD4',
        'Carton': '#FF5722'
    };

    for (const [type, quantity] of Object.entries(data.waste_types)) {
        const color = colors[type] || '#4CAF50';
        const item = document.createElement('div');
        item.className = 'waste-item';
        item.innerHTML = `
            <span class="dot" style="background: ${color};"></span>
            <span>${type}: ${quantity}</span>
        `;
        wasteContainer.appendChild(item);
    }
}

// Initialiser les graphiques
async function initializeCharts() {
    await updateMonthlyChart();
    await updateWeeklyChart();
    // Pie chart is handled by loadStatistics for the first load
}

// Mettre à jour le diagramme circulaire
async function updatePieChart(existingData = null) {
    let data;
    if (existingData) {
        data = existingData;
    } else {
        const year = document.getElementById('pieYearFilter').value;
        const month = document.getElementById('pieMonthFilter').value;
        try {
            const response = await fetch(`/api/stats/monthly-distribution?year=${year}&month=${month}`);
            data = await response.json();
        } catch (error) {
            console.error('Erreur lors du chargement du diagramme circulaire:', error);
            return;
        }
    }

    const canvas = document.getElementById('pieChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    if (pieChartInstance) {
        pieChartInstance.destroy();
    }

    const labels = Object.keys(data.waste_types);
    const values = Object.values(data.waste_types);

    if (labels.length === 0) {
        // Draw informational message if empty
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = "#999";
        ctx.textAlign = "center";
        ctx.fillText("Aucune donnée pour ce mois", canvas.width / 2, canvas.height / 2);
        return;
    }

    const colors = {
        'Papier': '#2196F3',
        'Plastique': '#9C27B0',
        'Metal': '#FF9800',
        'Métal': '#FF9800',
        'Verre': '#00BCD4',
        'Carton': '#FF5722',
        'Organique': '#4CAF50'
    };

    const backgroundColors = labels.map(label => colors[label] || '#4CAF50');

    pieChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: backgroundColors,
                borderWidth: 2,
                borderColor: '#ffffff',
                hoverOffset: 15
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            cutout: '70%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        padding: 20,
                        usePointStyle: true,
                        font: { size: 12 }
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(255, 255, 255, 0.9)',
                    titleColor: '#333',
                    bodyColor: '#333',
                    borderColor: '#ddd',
                    borderWidth: 1,
                    padding: 12,
                    displayColors: true,
                    callbacks: {
                        label: function (context) {
                            const label = context.label || '';
                            const value = context.parsed || 0;
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const percentage = Math.round((value / total) * 100);
                            return `${label}: ${value} (${percentage}%)`;
                        }
                    }
                }
            }
        }
    });
}

// Mettre à jour le graphique mensuel
async function updateMonthlyChart() {
    const year = document.getElementById('yearFilter').value;
    const wasteType = document.getElementById('monthWasteFilter').value;

    // Mettre à jour le titre
    const typeLabel = wasteType === 'all' ? 'tri mensuel' : `tri mensuel (${wasteType})`;
    document.getElementById('selectedMonthWaste').textContent = typeLabel;

    try {
        const response = await fetch(`/api/chart/monthly?year=${year}&waste_type=${wasteType}`);
        const data = await response.json();

        const ctx = document.getElementById('monthlyChart').getContext('2d');

        if (monthlyChartInstance) {
            monthlyChartInstance.destroy();
        }

        monthlyChartInstance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.months,
                datasets: [{
                    label: 'Objets triés',
                    data: data.data,
                    backgroundColor: '#4CAF50',
                    borderRadius: 5,
                    borderSkipped: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: {
                        display: false
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        max: Math.max(...data.data, 100)
                    }
                }
            }
        });
    } catch (error) {
        console.error('Erreur lors du chargement du graphique mensuel:', error);
    }
}

// Mettre à jour le graphique hebdomadaire
async function updateWeeklyChart() {
    const weekOffset = document.getElementById('weekFilter').value;
    const wasteType = document.getElementById('weekWasteFilter').value;

    // Mettre à jour le titre
    const typeLabel = wasteType === 'all' ? 'tri hebdomadaire' : `tri hebdomadaire (${wasteType})`;
    document.getElementById('selectedWeeklyWaste').textContent = typeLabel;

    try {
        const response = await fetch(`/api/chart/weekly?week_offset=${weekOffset}&waste_type=${wasteType}`);
        const data = await response.json();

        const ctx = document.getElementById('weeklyChart').getContext('2d');

        if (weeklyChartInstance) {
            weeklyChartInstance.destroy();
        }

        weeklyChartInstance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.days,
                datasets: [{
                    label: 'Objets triés',
                    data: data.data,
                    backgroundColor: '#4CAF50',
                    borderRadius: 5,
                    borderSkipped: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: {
                        display: false
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        max: Math.max(...data.data, 100)
                    }
                }
            }
        });
    } catch (error) {
        console.error('Erreur lors du chargement du graphique hebdomadaire:', error);
    }
}

// Marquer le lien actif
function markActiveLink() {
    const currentPath = window.location.pathname;
    const links = document.querySelectorAll('.sidebar nav a');

    links.forEach(link => {
        link.parentElement.classList.remove('active');
        if (link.getAttribute('href') === currentPath) {
            link.parentElement.classList.add('active');
        }
    });
}

// Gérer le menu toggle mobile
function setupMenuToggle() {
    const toggle = document.querySelector('.menu-toggle');
    const sidebar = document.querySelector('.sidebar');

    if (toggle) {
        toggle.addEventListener('click', () => {
            sidebar.classList.toggle('active');
        });
    }
}

// Déconnexion
async function logout() {
    try {
        const response = await fetch('/api/logout', {
            method: 'POST'
        });

        if (response.ok) {
            window.location.href = '/';
        }
    } catch (error) {
        console.error('Erreur de déconnexion:', error);
    }
}
