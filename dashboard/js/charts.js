/* ============================================================================
   SteamGuard Dashboard — Chart.js helpers (dark theme)
   Requires Chart.js loaded from CDN before this file.
   ========================================================================== */

const CHART_COLORS = {
  canvas:  '#0B0F1A',
  surface: '#131A2A',
  border:  '#1E2A3A',
  accent:  '#3DA9FC',
  neon:    '#36F1CD',
  purple:  '#A855F7',
  text:    '#E2E8F0',
  muted:   '#94A3B8',
  success: '#22C55E',
};

const _chartRegistry = {};

function _destroyExisting(canvasId) {
  if (_chartRegistry[canvasId]) {
    _chartRegistry[canvasId].destroy();
    delete _chartRegistry[canvasId];
  }
}

function _applyDarkDefaults() {
  if (typeof Chart === 'undefined' || Chart.__darkApplied) return;
  Chart.defaults.color = CHART_COLORS.muted;
  Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
  Chart.defaults.borderColor = CHART_COLORS.border;
  Chart.__darkApplied = true;
}

/* Bar chart — heals over the last 7 days. data = [{date, heals}, ...] */
function createHealsChart(canvasId, data) {
  _applyDarkDefaults();
  const el = document.getElementById(canvasId);
  if (!el) return null;
  _destroyExisting(canvasId);

  const labels = (data || []).map((d) => {
    const dt = new Date(d.date + 'T00:00:00');
    return dt.toLocaleDateString(undefined, { weekday: 'short' });
  });
  const values = (data || []).map((d) => d.heals || 0);

  const ctx = el.getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, el.height || 220);
  grad.addColorStop(0, 'rgba(54, 241, 205, 0.95)');
  grad.addColorStop(1, 'rgba(61, 169, 252, 0.45)');

  _chartRegistry[canvasId] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Heals',
        data: values,
        backgroundColor: grad,
        borderRadius: 6,
        borderSkipped: false,
        maxBarThickness: 38,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: CHART_COLORS.surface,
          borderColor: CHART_COLORS.border,
          borderWidth: 1,
          titleColor: CHART_COLORS.text,
          bodyColor: CHART_COLORS.text,
          padding: 10,
          displayColors: false,
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: CHART_COLORS.muted } },
        y: {
          beginAtZero: true,
          grid: { color: 'rgba(30,42,58,0.6)' },
          ticks: { color: CHART_COLORS.muted, precision: 0 },
        },
      },
    },
  });
  return _chartRegistry[canvasId];
}

/* Doughnut gauge — uptime percentage */
function createUptimeGauge(canvasId, pct) {
  _applyDarkDefaults();
  const el = document.getElementById(canvasId);
  if (!el) return null;
  _destroyExisting(canvasId);

  const value = Math.max(0, Math.min(100, pct || 0));
  const color = value >= 99 ? CHART_COLORS.neon
              : value >= 95 ? CHART_COLORS.accent
              : '#F59E0B';

  _chartRegistry[canvasId] = new Chart(el.getContext('2d'), {
    type: 'doughnut',
    data: {
      datasets: [{
        data: [value, 100 - value],
        backgroundColor: [color, 'rgba(30,42,58,0.55)'],
        borderWidth: 0,
        circumference: 360,
        rotation: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '78%',
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
    plugins: [{
      id: 'centerText',
      afterDraw(chart) {
        const { ctx, chartArea } = chart;
        if (!chartArea) return;
        const cx = (chartArea.left + chartArea.right) / 2;
        const cy = (chartArea.top + chartArea.bottom) / 2;
        ctx.save();
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = CHART_COLORS.text;
        ctx.font = "700 26px 'Inter', sans-serif";
        ctx.fillText(`${value.toFixed(1)}%`, cx, cy - 6);
        ctx.fillStyle = CHART_COLORS.muted;
        ctx.font = "500 11px 'Inter', sans-serif";
        ctx.fillText('UPTIME', cx, cy + 16);
        ctx.restore();
      },
    }],
  });
  return _chartRegistry[canvasId];
}
