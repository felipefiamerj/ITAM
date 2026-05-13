(function () {
  const formatter = new Intl.NumberFormat('pt-BR');

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatNumber(value) {
    return formatter.format(Number(value || 0));
  }

  function getBarWidth(value, total) {
    if (!total) {
      return 0;
    }
    return Math.max(4, Math.round((Number(value || 0) / Number(total)) * 100));
  }

  function updateCounters(data) {
    const totals = data.totais || {};
    document.querySelectorAll('[data-live-total]').forEach((element) => {
      const key = element.dataset.liveTotal;
      if (key in totals) {
        element.textContent = formatNumber(totals[key]);
      }
    });

    const updatedAt = data.updated_at || 'agora';
    const updatedTargets = ['estoque-updated-at', 'estoque-updated-at-inline'];
    updatedTargets.forEach((id) => {
      const element = document.getElementById(id);
      if (element) {
        element.textContent = updatedAt;
      }
    });
  }

  function renderSites(items) {
    const container = document.getElementById('estoque-sites-list');
    if (!container) {
      return;
    }

    if (!items || !items.length) {
      container.innerHTML = '<div class="text-muted">Sem dados de localização ainda.</div>';
      return;
    }

    container.innerHTML = items
      .map((item) => {
        const total = Number(item.total || 0);
        return `
          <article class="location-card">
            <div class="d-flex justify-content-between align-items-start gap-3">
              <div>
                <strong>${escapeHtml(item.site || 'Sem site')}</strong>
                <div class="small text-muted">${formatNumber(total)} equipamentos</div>
              </div>
              <span class="badge badge-soft">${formatNumber(total)}</span>
            </div>
            <div class="location-bars mt-3">
              <div class="location-bar">
                <span class="location-bar-fill location-bar-fill-uso" style="width:${getBarWidth(item.em_uso, total)}%"></span>
              </div>
              <div class="location-bar">
                <span class="location-bar-fill location-bar-fill-estoque" style="width:${getBarWidth(item.em_estoque, total)}%"></span>
              </div>
              <div class="location-bar">
                <span class="location-bar-fill location-bar-fill-manutencao" style="width:${getBarWidth(item.em_manutencao, total)}%"></span>
              </div>
            </div>
            <div class="small text-muted mt-2">
              Em uso ${formatNumber(item.em_uso)} · Em estoque ${formatNumber(item.em_estoque)} · Manutenção ${formatNumber(item.em_manutencao)} · Descartados ${formatNumber(item.descartado)}
            </div>
          </article>
        `;
      })
      .join('');
  }

  function renderLocations(items) {
    const tbody = document.getElementById('estoque-localizacoes-list');
    if (!tbody) {
      return;
    }

    if (!items || !items.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-muted">Sem localizações registradas.</td></tr>';
      return;
    }

    tbody.innerHTML = items
      .map((item) => {
        const label = item.label || [item.site, item.setor, item.andar_sala].filter(Boolean).join(' · ');
        return `
          <tr>
            <td>
              <div class="fw-semibold">${escapeHtml(item.site || 'Sem site')}</div>
              <div class="small text-muted">${escapeHtml(item.setor || 'Sem setor')} · ${escapeHtml(item.andar_sala || 'Sem andar/sala')}</div>
              <div class="small text-muted">${escapeHtml(label)}</div>
            </td>
            <td>${formatNumber(item.total)}</td>
            <td>${formatNumber(item.em_uso)}</td>
            <td>${formatNumber(item.em_estoque)}</td>
            <td>${formatNumber(item.em_manutencao)}</td>
          </tr>
        `;
      })
      .join('');
  }

  function renderLotes(items) {
    const container = document.getElementById('estoque-lotes-list');
    if (!container) {
      return;
    }

    if (!items || !items.length) {
      container.innerHTML = '<div class="text-muted">Nenhum lote cadastrado.</div>';
      return;
    }

    container.innerHTML = items
      .map((item) => {
        return `
          <article class="list-card">
            <div class="d-flex justify-content-between align-items-start gap-3">
              <div>
                <strong>${escapeHtml(item.descricao || '-')}</strong>
                <div class="small text-muted">${escapeHtml(item.created_at || '')}</div>
              </div>
              <span class="badge badge-soft">${escapeHtml(item.status_display || item.status || '')}</span>
            </div>
            <div class="small text-muted mt-2">
              ${formatNumber(item.itens_importados)} / ${formatNumber(item.total_itens)} importados
            </div>
          </article>
        `;
      })
      .join('');
  }

  function renderAlerts(items) {
    const container = document.getElementById('estoque-alertas-list');
    if (!container) {
      return;
    }

    if (!items || !items.length) {
      container.innerHTML = '<div class="text-muted">Nenhum alerta de saúde no momento.</div>';
      return;
    }

    container.innerHTML = items
      .map((item) => {
        return `
          <article class="list-card">
            <div class="d-flex justify-content-between align-items-start gap-3">
              <div>
                <strong><a href="/equipamentos/${encodeURIComponent(item.id_patrimonio)}/">${escapeHtml(item.id_patrimonio)}</a></strong>
                <div class="small text-muted">${escapeHtml(item.tipo_display || '')} · ${escapeHtml(item.localizacao || 'Sem localização')}</div>
              </div>
              <span class="badge badge-soft">${formatNumber(item.score_saude)}</span>
            </div>
            <div class="small text-muted mt-2">${escapeHtml(item.status_display || item.status || '')}</div>
          </article>
        `;
      })
      .join('');
  }

  async function refresh(apiUrl) {
    try {
      const response = await fetch(apiUrl, {
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
        },
      });
      if (!response.ok) {
        return;
      }

      const data = await response.json();
      updateCounters(data);
      renderSites(data.por_site);
      renderLocations(data.por_localizacao);
      renderLotes(data.lotes);
      renderAlerts(data.alertas);
    } catch (error) {
      console.warn('Falha ao atualizar o estoque em tempo real.', error);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const root = document.querySelector('[data-estoque-live]');
    if (!root) {
      return;
    }

    const apiUrl = root.dataset.apiUrl;
    if (!apiUrl) {
      return;
    }

    refresh(apiUrl);
    window.setInterval(() => refresh(apiUrl), 15000);
  });
})();
