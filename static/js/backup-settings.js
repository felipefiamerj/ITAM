(function () {
  function readChartData() {
    const element = document.getElementById('backup-history-data');
    if (!element) {
      return null;
    }
    try {
      return JSON.parse(element.textContent);
    } catch (error) {
      console.warn('Falha ao ler o histórico de backups.', error);
      return null;
    }
  }

  function initScheduleForm() {
    const form = document.querySelector('[data-backup-schedule-form]');
    if (!form) {
      return;
    }

    const list = form.querySelector('[data-backup-time-list]');
    const hiddenInput = form.querySelector('#id_schedule_times_json');
    const addButton = form.querySelector('[data-backup-time-add]');

    function rows() {
      return Array.from(list.querySelectorAll('[data-backup-time-row]'));
    }

    function updateRemoveButtons() {
      const disabled = rows().length === 1;
      rows().forEach((row) => {
        row.querySelector('[data-backup-time-remove]').disabled = disabled;
      });
    }

    function addTime(value = '19:00') {
      const row = document.createElement('div');
      row.className = 'backup-time-row';
      row.dataset.backupTimeRow = '';
      row.innerHTML = `
        <input class="form-control" type="time" value="${value}" aria-label="Horário do backup" data-backup-time-input required>
        <button class="btn btn-outline-danger backup-time-remove" type="button" title="Remover horário" aria-label="Remover horário" data-backup-time-remove>
          <i class="fa-solid fa-trash-can"></i>
        </button>
      `;
      list.appendChild(row);
      updateRemoveButtons();
      row.querySelector('input').focus();
    }

    list.addEventListener('click', (event) => {
      const removeButton = event.target.closest('[data-backup-time-remove]');
      if (!removeButton || rows().length === 1) {
        return;
      }
      removeButton.closest('[data-backup-time-row]').remove();
      updateRemoveButtons();
    });

    addButton.addEventListener('click', () => addTime());
    form.addEventListener('submit', () => {
      const values = rows()
        .map((row) => row.querySelector('[data-backup-time-input]').value)
        .filter(Boolean);
      hiddenInput.value = JSON.stringify(values);
    });
    updateRemoveButtons();
  }

  function initHistoryChart() {
    const canvas = document.getElementById('backup-history-chart');
    const wrap = document.querySelector('[data-backup-chart-wrap]');
    const payload = readChartData();
    if (!canvas || !wrap || !payload || !window.Chart) {
      return;
    }

    if (!payload.labels.length) {
      wrap.innerHTML = '<div class="backup-chart-empty"><i class="fa-solid fa-chart-column"></i><strong>Sem dados no período</strong></div>';
      return;
    }

    new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: payload.labels,
        datasets: [
          {
            label: 'Execuções',
            data: payload.counts,
            backgroundColor: '#134a8b',
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 34,
            yAxisID: 'y',
          },
          {
            label: 'Volume (MB)',
            data: payload.sizes_mb,
            type: 'line',
            borderColor: '#0f8b8d',
            backgroundColor: '#0f8b8d',
            pointBackgroundColor: '#ffffff',
            pointBorderColor: '#0f8b8d',
            pointBorderWidth: 2,
            pointRadius: 4,
            tension: 0.28,
            yAxisID: 'y1',
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
          x: { grid: { display: false } },
          y: {
            beginAtZero: true,
            ticks: { precision: 0 },
            grid: { color: 'rgba(16, 32, 51, 0.08)' },
            title: { display: true, text: 'Execuções' },
          },
          y1: {
            beginAtZero: true,
            position: 'right',
            grid: { drawOnChartArea: false },
            title: { display: true, text: 'MB' },
          },
        },
      },
    });
  }

  function initRunNow() {
    const form = document.querySelector('[data-backup-run-form]');
    if (!form) {
      return;
    }

    const button = form.querySelector('[data-backup-run-button]');
    const status = document.querySelector('[data-backup-run-status]');
    const statusUrl = form.dataset.statusUrl;
    const originalButtonContent = button.innerHTML;
    let pollCount = 0;

    function showStatus(message, tone = 'info') {
      status.className = `alert alert-${tone} app-alert mb-4`;
      status.innerHTML = message;
    }

    function restoreButton() {
      button.disabled = false;
      button.innerHTML = originalButtonContent;
    }

    async function poll(requestedAt) {
      pollCount += 1;
      try {
        const response = await fetch(`${statusUrl}?requested_at=${encodeURIComponent(requestedAt)}`, {
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || 'Não foi possível acompanhar o backup.');
        }
        if (payload.complete) {
          showStatus('<i class="fa-solid fa-circle-check me-2"></i>Backup concluído. Atualizando o histórico...', 'success');
          window.setTimeout(() => window.location.reload(), 700);
          return;
        }
        if (payload.failed) {
          showStatus(`<i class="fa-solid fa-triangle-exclamation me-2"></i>O backup terminou com falha: ${payload.status_label}.`, 'danger');
          restoreButton();
          return;
        }
        if (pollCount >= 600) {
          showStatus('<i class="fa-solid fa-clock me-2"></i>O backup continua em processamento. Atualize a página para consultar o resultado.', 'warning');
          restoreButton();
          return;
        }
        window.setTimeout(() => poll(requestedAt), 2000);
      } catch (error) {
        showStatus(`<i class="fa-solid fa-triangle-exclamation me-2"></i>${error.message}`, 'danger');
        restoreButton();
      }
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      button.disabled = true;
      button.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Iniciando';
      showStatus('<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Iniciando o backup...', 'info');

      try {
        const response = await fetch(form.getAttribute('action') || window.location.href, {
          method: 'POST',
          body: new FormData(form),
          headers: {
            Accept: 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
          },
          credentials: 'same-origin',
        });
        const payload = await response.json();
        if (!response.ok || !payload.started) {
          throw new Error(payload.error || 'Não foi possível iniciar o backup.');
        }

        button.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Em andamento';
        showStatus('<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Backup em andamento. Esta página será atualizada quando ele terminar.', 'info');
        poll(payload.requested_at);
      } catch (error) {
        showStatus(`<i class="fa-solid fa-triangle-exclamation me-2"></i>${error.message}`, 'danger');
        restoreButton();
      }
    });
  }

  function initRestorePoints() {
    const modalElement = document.getElementById('restorePointModal');
    const form = document.querySelector('[data-restore-form]');
    const overlay = document.querySelector('[data-restore-progress]');
    if (!modalElement || !form || !overlay) {
      return;
    }

    const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
    const manifestInput = form.querySelector('[data-restore-manifest-input]');
    const confirmationInput = form.querySelector('[data-restore-confirmation]');
    const submitButton = form.querySelector('[data-restore-submit]');
    const dateOutput = form.querySelector('[data-restore-date]');
    const progressTitle = overlay.querySelector('[data-restore-progress-title]');
    const progressMessage = overlay.querySelector('[data-restore-progress-message]');
    let pollAttempts = 0;

    const stageLabels = {
      queued: 'Preparando o ponto de restauração',
      safety_backup: 'Criando backup de segurança',
      stopping: 'Parando os serviços com segurança',
      restoring: 'Restaurando banco e arquivos',
      migrating: 'Atualizando a estrutura do sistema',
      starting: 'Reiniciando e verificando o sistema',
      finished: 'Restauração concluída',
    };

    modalElement.addEventListener('show.bs.modal', (event) => {
      const trigger = event.relatedTarget;
      manifestInput.value = trigger?.dataset.restoreManifest || '';
      dateOutput.textContent = trigger?.dataset.restoreDate || '';
      confirmationInput.value = '';
      submitButton.disabled = true;
    });

    confirmationInput.addEventListener('input', () => {
      submitButton.disabled = confirmationInput.value.trim() !== 'RESTAURAR';
    });

    async function pollRestore(statusUrl) {
      pollAttempts += 1;
      try {
        const response = await fetch(statusUrl, {
          headers: { Accept: 'application/json' },
          cache: 'no-store',
          credentials: 'same-origin',
        });
        if (!response.ok) {
          throw new Error('Sistema temporariamente indisponível.');
        }
        const payload = await response.json();
        progressTitle.textContent = stageLabels[payload.stage] || 'Restauração em andamento';
        progressMessage.textContent = payload.message || 'Não desligue o computador.';

        if (payload.status === 'completed') {
          progressTitle.textContent = 'Ponto restaurado com sucesso';
          progressMessage.textContent = 'Atualizando a página...';
          window.setTimeout(() => window.location.reload(), 1200);
          return;
        }
        if (payload.status === 'failed') {
          overlay.classList.add('is-failed');
          progressTitle.textContent = 'A restauração não foi concluída';
          return;
        }
      } catch (error) {
        progressTitle.textContent = 'Reiniciando o sistema';
        progressMessage.textContent = 'A conexão voltará automaticamente. Não feche esta página.';
      }

      if (pollAttempts < 900) {
        window.setTimeout(() => pollRestore(statusUrl), 2000);
      } else {
        overlay.classList.add('is-failed');
        progressTitle.textContent = 'A restauração está demorando mais que o esperado';
        progressMessage.textContent = 'Verifique o estado do sistema antes de tentar novamente.';
      }
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      submitButton.disabled = true;
      submitButton.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Preparando';

      try {
        const response = await fetch(form.getAttribute('action') || window.location.href, {
          method: 'POST',
          body: new FormData(form),
          headers: {
            Accept: 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
          },
          credentials: 'same-origin',
        });
        const payload = await response.json();
        if (!response.ok || !payload.started) {
          throw new Error(payload.error || 'Não foi possível iniciar a restauração.');
        }

        modal.hide();
        overlay.classList.remove('d-none', 'is-failed');
        document.body.classList.add('restore-in-progress');
        pollAttempts = 0;
        pollRestore(payload.status_url);
      } catch (error) {
        submitButton.disabled = false;
        submitButton.innerHTML = '<i class="fa-solid fa-clock-rotate-left me-1"></i>Iniciar restauração';
        confirmationInput.setCustomValidity(error.message);
        confirmationInput.reportValidity();
        confirmationInput.setCustomValidity('');
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    initScheduleForm();
    initHistoryChart();
    initRunNow();
    initRestorePoints();
  });
})();
