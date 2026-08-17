(function () {
  const canvas = document.getElementById('system-health-chart');
  const payloadElement = document.getElementById('system-health-data');
  if (!canvas || !payloadElement || !window.Chart) {
    return;
  }

  let payload;
  try {
    payload = JSON.parse(payloadElement.textContent);
  } catch (error) {
    console.warn('Falha ao ler o historico de saude.', error);
    return;
  }

  new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels: payload.labels,
      datasets: [
        {
          label: 'Incidentes',
          data: payload.issues,
          backgroundColor: '#d94841',
          borderRadius: 5,
          borderSkipped: false,
          maxBarThickness: 28,
        },
        {
          label: 'Recuperações',
          data: payload.recoveries,
          backgroundColor: '#16856f',
          borderRadius: 5,
          borderSkipped: false,
          maxBarThickness: 28,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'bottom',
          labels: { usePointStyle: true, padding: 18 },
        },
      },
      scales: {
        x: { stacked: false, grid: { display: false } },
        y: {
          beginAtZero: true,
          ticks: { precision: 0 },
          grid: { color: 'rgba(16, 32, 51, 0.08)' },
        },
      },
    },
  });
})();
