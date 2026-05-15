(function () {
  const numberFormatter = new Intl.NumberFormat('pt-BR');
  const defaultPalette = [
    '#134a8b',
    '#2563eb',
    '#0d7ba7',
    '#0f8b8d',
    '#21a179',
    '#f5b700',
    '#d97706',
    '#f97316',
    '#ef4444',
    '#8b5cf6',
    '#64748b',
    '#38bdf8',
  ];

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatNumber(value) {
    return numberFormatter.format(Number(value || 0));
  }

  function readJsonScript(scriptId) {
    const element = document.getElementById(scriptId);
    if (!element) {
      return null;
    }

    try {
      return JSON.parse(element.textContent);
    } catch (error) {
      console.warn(`Falha ao ler ${scriptId}.`, error);
      return null;
    }
  }

  function toSeries(payload) {
    if (!payload || !Array.isArray(payload.labels) || !Array.isArray(payload.values)) {
      return [];
    }

    return payload.labels.map((label, index) => ({
      label,
      value: Number(payload.values[index] || 0),
    }));
  }

  function cycleColors(count, palette = defaultPalette) {
    return Array.from({ length: count }, (_, index) => palette[index % palette.length]);
  }

  function renderEmptyState(wrap, icon, title, body) {
    if (!wrap) {
      return;
    }

    wrap.innerHTML = `
      <div class="dashboard-chart-empty">
        <i class="fa-solid ${escapeHtml(icon)}" aria-hidden="true"></i>
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(body)}</span>
      </div>
    `;
  }

  function renderDoughnutChart(canvasId, wrapId, series, colors, emptyTitle, emptyBody) {
    const canvas = document.getElementById(canvasId);
    const wrap = document.getElementById(wrapId);
    if (!canvas || !wrap) {
      return;
    }

    if (!series.length || series.every((item) => item.value === 0)) {
      renderEmptyState(wrap, 'fa-chart-pie', emptyTitle, emptyBody);
      return;
    }

    new Chart(canvas.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: series.map((item) => item.label),
        datasets: [
          {
            data: series.map((item) => item.value),
            backgroundColor: cycleColors(series.length, colors),
            borderColor: '#ffffff',
            borderWidth: 2,
            hoverOffset: 8,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '68%',
        animation: {
          duration: 900,
          easing: 'easeOutQuart',
        },
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              usePointStyle: true,
              pointStyle: 'circle',
              padding: 16,
            },
          },
          tooltip: {
            callbacks: {
              label(context) {
                return `${context.label}: ${formatNumber(context.raw)}`;
              },
            },
          },
        },
      },
    });
  }

  function renderHorizontalBarChart(canvasId, wrapId, series, colors, emptyTitle, emptyBody) {
    const canvas = document.getElementById(canvasId);
    const wrap = document.getElementById(wrapId);
    if (!canvas || !wrap) {
      return;
    }

    if (!series.length || series.every((item) => item.value === 0)) {
      renderEmptyState(wrap, 'fa-chart-column', emptyTitle, emptyBody);
      return;
    }

    new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: series.map((item) => item.label),
        datasets: [
          {
            data: series.map((item) => item.value),
            backgroundColor: cycleColors(series.length, colors),
            borderRadius: 12,
            borderSkipped: false,
            maxBarThickness: 24,
          },
        ],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        animation: {
          duration: 900,
          easing: 'easeOutQuart',
        },
        plugins: {
          legend: {
            display: false,
          },
          tooltip: {
            callbacks: {
              label(context) {
                return ` ${formatNumber(context.raw)}`;
              },
            },
          },
        },
        scales: {
          x: {
            beginAtZero: true,
            grid: {
              color: 'rgba(16, 32, 51, 0.08)',
            },
            ticks: {
              precision: 0,
              callback(value) {
                return formatNumber(value);
              },
            },
          },
          y: {
            grid: {
              display: false,
            },
          },
        },
      },
    });
  }

  function initCharts() {
    if (!window.Chart) {
      return;
    }

    Chart.defaults.font.family = '"Inter", "Segoe UI", sans-serif';
    Chart.defaults.color = '#607084';
    if (Chart.defaults.plugins && Chart.defaults.plugins.legend && Chart.defaults.plugins.legend.labels) {
      Chart.defaults.plugins.legend.labels.usePointStyle = true;
      Chart.defaults.plugins.legend.labels.padding = 16;
    }

    const dashboardData = readJsonScript('dashboard-charts-data');
    if (dashboardData) {
      renderDoughnutChart(
        'dashboard-status-chart',
        'dashboard-status-chart-wrap',
        toSeries(dashboardData.equipamentos_por_status),
        ['#134a8b', '#21a179', '#f5b700', '#64748b', '#8b5cf6'],
        'Sem dados de inventário',
        'Ainda não há informações suficientes para este gráfico.'
      );
      renderHorizontalBarChart(
        'dashboard-priority-chart',
        'dashboard-priority-chart-wrap',
        toSeries(dashboardData.chamados_abertos_por_prioridade),
        ['#21a179', '#f5b700', '#f97316', '#ef4444'],
        'Sem chamados abertos',
        'Nenhum chamado aberto está disponível para análise agora.'
      );
      if (dashboardData.fluxo_chamados) {
        renderDoughnutChart(
          'dashboard-flow-chart',
          'dashboard-flow-chart-wrap',
          toSeries(dashboardData.fluxo_chamados),
          ['#134a8b', '#0f8b8d', '#64748b'],
          'Sem fluxo operacional',
          'Ainda não há chamados suficientes para exibir o fluxo.'
        );
      }
    }

    const estoqueData = readJsonScript('estoque-charts-data');
    if (estoqueData) {
      renderDoughnutChart(
        'estoque-status-chart',
        'estoque-status-chart-wrap',
        toSeries(estoqueData.equipamentos_por_status),
        ['#2563eb', '#21a179', '#f5b700', '#64748b', '#8b5cf6'],
        'Sem dados de status',
        'Ainda não há equipamentos suficientes para este gráfico.'
      );
      renderHorizontalBarChart(
        'estoque-types-chart',
        'estoque-types-chart-wrap',
        toSeries(estoqueData.equipamentos_por_tipo),
        ['#0f8b8d', '#21a179', '#38bdf8', '#f5b700', '#d97706', '#ef4444', '#8b5cf6', '#64748b'],
        'Sem tipos em estoque',
        'Ainda não há tipos suficientes em estoque para exibir o gráfico.'
      );
    }

    const copilotData = readJsonScript('ia-copilot-data');
    if (copilotData) {
      renderDoughnutChart(
        'ia-copilot-risk-chart',
        'ia-copilot-risk-chart-wrap',
        toSeries(copilotData.recomendacoes_por_origem),
        ['#134a8b', '#f97316', '#0f8b8d', '#8b5cf6'],
        'Sem recomendações',
        'Nenhum sinal relevante foi encontrado para o copiloto operacional.'
      );
      renderHorizontalBarChart(
        'ia-copilot-horizon-chart',
        'ia-copilot-horizon-chart-wrap',
        toSeries(copilotData.recomendacoes_por_horizonte),
        ['#ef4444', '#f5b700', '#2563eb'],
        'Sem horizonte definido',
        'Ainda não há recomendações suficientes para classificar o horizonte.'
      );
    }
  }

  document.addEventListener('DOMContentLoaded', initCharts);
})();

