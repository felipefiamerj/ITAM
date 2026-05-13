(() => {
  const root = document.querySelector('[data-global-search-page]');

  if (!root) {
    return;
  }

  const body = document.body;
  const apiUrl = body.dataset.searchApiUrl || '/api/busca/';
  const searchUrl = body.dataset.searchUrl || '/dashboard/busca/';
  const form = root.querySelector('[data-global-search-form]');
  const input = root.querySelector('[data-search-input]');
  const resultsBody = root.querySelector('[data-search-results-body]');
  const actionsBody = root.querySelector('[data-search-actions-body]');
  const summaryNode = root.querySelector('[data-search-summary]');
  const modeLabelNode = root.querySelector('[data-search-mode-label]');
  const updatedNode = root.querySelector('[data-search-updated]');

  let timerId = null;
  let controller = null;

  const escapeHtml = (value) => {
    const text = String(value ?? '');
    const entities = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    };
    return text.replace(/[&<>"']/g, (char) => entities[char]);
  };

  const buildUrl = (query) => {
    const url = new URL(apiUrl, window.location.origin);
    if (query) {
      url.searchParams.set('q', query);
    }
    return url;
  };

  const updateHistory = (query) => {
    const url = new URL(searchUrl, window.location.origin);
    if (query) {
      url.searchParams.set('q', query);
    }
    window.history.replaceState({}, '', `${url.pathname}${url.search}`);
  };

  const renderEmptyState = (query, payload) => {
    const title = query
      ? 'Nenhum resultado encontrado.'
      : 'Faça uma busca para começar.';
    const description = query
      ? `Sem correspondências para "${query}". Tente patrimônio, matrícula, chamado, site ou setor.`
      : payload.summary || 'Digite no campo acima para pesquisar.';

    return `
      <div class="search-empty-state">
        <i class="fa-solid fa-magnifying-glass"></i>
        <strong>${title}</strong>
        <p>${escapeHtml(description)}</p>
      </div>
    `;
  };

  const renderActions = (actions) => {
    if (!actions.length) {
      return '';
    }

    return actions
      .map(
        (action) => `
          <a class="search-action-card" href="${escapeHtml(action.url)}">
            <div class="search-action-icon">
              <i class="fa-solid ${escapeHtml(action.icon)}"></i>
            </div>
            <div>
              <strong>${escapeHtml(action.label)}</strong>
              <div class="small text-muted">${escapeHtml(action.description || '')}</div>
            </div>
          </a>
        `
      )
      .join('');
  };

  const renderGroup = (group) => {
    const items = Array.isArray(group.items) ? group.items : [];

    if (!items.length) {
      return '';
    }

    return `
      <section class="search-group-card">
        <div class="card-body">
          <div class="search-group-header">
            <div>
              <div class="section-kicker">${escapeHtml(group.label || '')}</div>
              <div class="small text-muted">${escapeHtml(group.description || '')}</div>
            </div>
            <div class="d-flex align-items-center gap-2 flex-wrap">
              <span class="badge badge-soft">${escapeHtml(group.count ?? items.length)}</span>
              <a href="${escapeHtml(group.see_all_url || '#')}" class="small">${escapeHtml(group.see_all_label || 'Ver tudo')}</a>
            </div>
          </div>
          <div class="stack-list">
            ${items
              .map(
                (item) => `
                  <a href="${escapeHtml(item.url)}" class="search-result-card">
                    <div class="search-result-icon">
                      <i class="fa-solid ${escapeHtml(item.icon || 'fa-circle')}"></i>
                    </div>
                    <div class="search-result-body">
                      <div class="d-flex justify-content-between align-items-start gap-2 flex-wrap">
                        <strong>${escapeHtml(item.title || '')}</strong>
                        <span class="badge ${escapeHtml(item.badge_class || 'badge-soft')}">${escapeHtml(item.badge || '')}</span>
                      </div>
                      <div class="search-result-lines">${escapeHtml(item.subtitle || '')}</div>
                      <div class="small text-muted">${escapeHtml(item.meta || '')}</div>
                    </div>
                  </a>
                `
              )
              .join('')}
          </div>
        </div>
      </section>
    `;
  };

  const renderPayload = (payload, query) => {
    if (summaryNode) {
      summaryNode.textContent = payload.summary || '';
    }

    if (modeLabelNode) {
      modeLabelNode.textContent = payload.mode_label || 'Resultados';
    }

    if (updatedNode) {
      updatedNode.textContent = `Atualizado em ${payload.updated_at || 'agora'}`;
    }

    const groups = Array.isArray(payload.groups) ? payload.groups : [];
    if (resultsBody) {
      resultsBody.innerHTML = groups.length
        ? groups.map((group) => renderGroup(group)).join('')
        : renderEmptyState(query, payload);
    }

    if (actionsBody) {
      actionsBody.innerHTML = renderActions(Array.isArray(payload.quick_actions) ? payload.quick_actions : []);
    }
  };

  const fetchPayload = async (query) => {
    if (controller) {
      controller.abort();
    }

    controller = new AbortController();
    const url = buildUrl(query);
    const response = await fetch(url, {
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
      },
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error('Falha ao consultar a busca global.');
    }

    return response.json();
  };

  const runSearch = (query) => {
    window.clearTimeout(timerId);

    timerId = window.setTimeout(async () => {
      const normalizedQuery = query.trim();

      try {
        root.classList.add('search-loading');
        const payload = await fetchPayload(normalizedQuery);
        renderPayload(payload, normalizedQuery);
        updateHistory(normalizedQuery);
      } catch (error) {
        if (error.name !== 'AbortError') {
          if (summaryNode) {
            summaryNode.textContent = 'Não foi possível atualizar a busca agora.';
          }
        }
      } finally {
        root.classList.remove('search-loading');
      }
    }, 220);
  };

  if (form) {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      runSearch(input ? input.value : '');
    });
  }

  if (input) {
    input.addEventListener('input', (event) => {
      runSearch(event.target.value);
    });

    input.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && input.value) {
        event.preventDefault();
        input.value = '';
        runSearch('');
        input.focus();
      }
    });
  }
})();
