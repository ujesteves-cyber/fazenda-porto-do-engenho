/* ── Porto do Engenho – Utilities ─────────────────────────── */

const API = {
    async get(url) {
        const res = await fetch(url);
        if (res.status === 401) {
            window.location.href = '/login';
            return null;
        }
        return res.json();
    },
    async post(url, formData) {
        const res = await fetch(url, { method: 'POST', body: formData });
        if (res.status === 401) {
            window.location.href = '/login';
            return null;
        }
        return res.json();
    },
    async del(url) {
        const res = await fetch(url, { method: 'DELETE' });
        if (res.status === 401) {
            window.location.href = '/login';
            return null;
        }
        return res.json();
    }
};

function encodeAnimalId(id) {
    return id ? id.replace(/ /g, '__') : '';
}

function decaClass(d) {
    if (!d) return '';
    return `deca-${d}`;
}

function decaBadge(d) {
    if (!d) return '-';
    return `<span class="deca ${decaClass(d)}">${d}</span>`;
}

function formatNum(v, decimals = 2) {
    if (v === null || v === undefined) return '-';
    return Number(v).toFixed(decimals);
}

function percColor(perc) {
    if (perc === null || perc === undefined) return '';
    if (perc <= 30) return 'color: var(--ok)';
    if (perc >= 70) return 'color: var(--red)';
    return '';
}

function ceipBadge(v) {
    return v ? '<span class="badge badge-green">CEIP</span>' : '';
}

function precBadge(v) {
    return v ? '<span class="badge badge-black">Precoce</span>' : '';
}

function genoBadge(v) {
    return v ? '<span class="badge badge-outline">Genômica</span>' : '';
}

// Chart.js default config
function chartDefaults() {
    if (typeof Chart === 'undefined') return;
    Chart.defaults.font.family = "'Barlow', sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.color = '#666';
    Chart.defaults.plugins.legend.display = false;
}

// Set active sidebar link
document.addEventListener('DOMContentLoaded', () => {
    const path = window.location.pathname;
    document.querySelectorAll('.sidebar-nav a').forEach(a => {
        if (a.getAttribute('href') === path) {
            a.classList.add('active');
        }
    });
    chartDefaults();
});
