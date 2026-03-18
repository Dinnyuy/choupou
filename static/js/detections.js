// Detections page functionality
let currentPage = 1;
let currentFilters = {
    start_date: '',
    end_date: '',
    waste_type: 'all'
};

// Load detections on page load
document.addEventListener('DOMContentLoaded', function () {
    loadDetections();
});

// Apply filters
function applyFilters() {
    currentFilters.start_date = document.getElementById('startDate').value;
    currentFilters.end_date = document.getElementById('endDate').value;
    currentFilters.waste_type = document.getElementById('wasteType').value;
    currentPage = 1; // Reset to first page
    loadDetections();
}

// Reset filters
function resetFilters() {
    document.getElementById('startDate').value = '';
    document.getElementById('endDate').value = '';
    document.getElementById('wasteType').value = 'all';
    currentFilters = {
        start_date: '',
        end_date: '',
        waste_type: 'all'
    };
    currentPage = 1;
    loadDetections();
}

// Load detections from API
async function loadDetections() {
    const loadingIndicator = document.getElementById('loadingIndicator');
    const tableBody = document.getElementById('detectionsTableBody');

    loadingIndicator.style.display = 'block';
    tableBody.innerHTML = '';

    try {
        const params = new URLSearchParams({
            page: currentPage,
            ...currentFilters
        });

        const response = await fetch(`/api/detections/list?${params}`);
        const data = await response.json();

        loadingIndicator.style.display = 'none';

        if (data.success) {
            displayDetections(data.detections);
            displayPagination(data.page, data.total_pages, data.total);
        } else {
            showError('Erreur lors du chargement des détections');
        }
    } catch (error) {
        loadingIndicator.style.display = 'none';
        console.error('Erreur:', error);
        showError('Erreur de connexion au serveur');
    }
}

// Display detections in table
function displayDetections(detections) {
    const tableBody = document.getElementById('detectionsTableBody');

    if (detections.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="5">
                    <div class="empty-state">
                        <i class="fas fa-inbox"></i>
                        <h3>Aucune détection trouvée</h3>
                        <p>Aucune détection ne correspond aux critères de filtrage.</p>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    tableBody.innerHTML = detections.map(detection => `
        <tr>
            <td>${detection.id}</td>
            <td>${detection.owner || 'Utilisateur inconnu'}</td>
            <td><span class="waste-type-badge">${formatWasteType(detection.waste_type)}</span></td>
            <td>${detection.quantity}</td>
            <td>${formatDate(detection.date)}</td>
        </tr>
    `).join('');
}

// Format waste type for display
function formatWasteType(type) {
    const types = {
        'Plastique': 'Plastique',
        'Papier': 'Papier',
        'Metal': 'Métal',
        'Verre': 'Verre',
        'Carton': 'Carton'
    };
    return types[type] || type;
}

// Format date for display
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('fr-FR', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Display pagination
function displayPagination(currentPageNum, totalPages, totalItems) {
    const paginationContainer = document.getElementById('paginationContainer');

    if (totalPages <= 1) {
        paginationContainer.innerHTML = '';
        return;
    }

    let paginationHTML = `
        <div class="pagination-info">
            Page ${currentPageNum} sur ${totalPages} (${totalItems} détection${totalItems > 1 ? 's' : ''})
        </div>
        <div class="pagination-buttons">
            <button class="page-btn" onclick="goToPage(1)" ${currentPageNum === 1 ? 'disabled' : ''}>
                <i class="fas fa-angle-double-left"></i>
            </button>
            <button class="page-btn" onclick="goToPage(${currentPageNum - 1})" ${currentPageNum === 1 ? 'disabled' : ''}>
                <i class="fas fa-angle-left"></i>
            </button>
    `;

    // Show page numbers
    const maxPagesToShow = 5;
    let startPage = Math.max(1, currentPageNum - Math.floor(maxPagesToShow / 2));
    let endPage = Math.min(totalPages, startPage + maxPagesToShow - 1);

    if (endPage - startPage < maxPagesToShow - 1) {
        startPage = Math.max(1, endPage - maxPagesToShow + 1);
    }

    for (let i = startPage; i <= endPage; i++) {
        paginationHTML += `
            <button class="page-btn ${i === currentPageNum ? 'active' : ''}" onclick="goToPage(${i})">
                ${i}
            </button>
        `;
    }

    paginationHTML += `
            <button class="page-btn" onclick="goToPage(${currentPageNum + 1})" ${currentPageNum === totalPages ? 'disabled' : ''}>
                <i class="fas fa-angle-right"></i>
            </button>
            <button class="page-btn" onclick="goToPage(${totalPages})" ${currentPageNum === totalPages ? 'disabled' : ''}>
                <i class="fas fa-angle-double-right"></i>
            </button>
        </div>
    `;

    paginationContainer.innerHTML = paginationHTML;
}

// Go to specific page
function goToPage(page) {
    currentPage = page;
    loadDetections();
}

// Export to CSV
function exportCSV() {
    const params = new URLSearchParams(currentFilters);
    window.location.href = `/api/detections/export/csv?${params}`;
}

// Export to PDF
function exportPDF() {
    const params = new URLSearchParams(currentFilters);
    window.location.href = `/api/detections/export/pdf?${params}`;
}

// Show error message
function showError(message) {
    const tableBody = document.getElementById('detectionsTableBody');
    tableBody.innerHTML = `
        <tr>
            <td colspan="5">
                <div class="empty-state">
                    <i class="fas fa-exclamation-triangle" style="color: #dc3545;"></i>
                    <h3>Erreur</h3>
                    <p>${message}</p>
                </div>
            </td>
        </tr>
    `;
}
