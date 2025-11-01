// Funciones para la página de configuración
function showManualMapping() {
    document.getElementById('manualMappingForm').style.display = 'block';
    document.getElementById('learnMappingForm').style.display = 'none';
}

function showLearnMapping() {
    document.getElementById('manualMappingForm').style.display = 'none';
    document.getElementById('learnMappingForm').style.display = 'block';
}

function cancelMapping() {
    document.getElementById('manualMappingForm').style.display = 'none';
    document.getElementById('learnMappingForm').style.display = 'none';
}

// Event listeners
document.addEventListener('DOMContentLoaded', function() {
    const addManualBtn = document.getElementById('addManualBtn');
    const addLearnBtn = document.getElementById('addLearnBtn');
    
    if (addManualBtn) {
        addManualBtn.onclick = showManualMapping;
    }
    
    if (addLearnBtn) {
        addLearnBtn.onclick = showLearnMapping;
    }
});