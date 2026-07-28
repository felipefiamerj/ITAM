(() => {
  const form = document.querySelector('[data-reserva-form]');
  if (!form) {
    return;
  }

  const chamadoSelect = form.querySelector('[data-reserva-chamado]');
  const itemSelect = form.querySelector('[data-reserva-item]');
  const equipamentoSelect = form.querySelector('[data-reserva-equipamento]');
  const hint = form.querySelector('[data-reserva-hint]');
  const chamadosApiBase = form.dataset.chamadosApiBase || '/api/chamados/';
  const equipamentosApiUrl = form.dataset.equipamentosApiUrl || '/api/equipamentos/';
  const baseEquipmentPlaceholder = equipamentoSelect?.dataset.placeholderBase || 'Selecione um chamado ou item';
  const baseItemPlaceholder = itemSelect?.dataset.placeholderBase || 'Selecione um chamado primeiro';

  let requestToken = 0;

  function getEquipmentValue(item) {
    return String(item?.id_patrimonio ?? item?.pk ?? item?.id ?? '').trim();
  }

  function setHint(message, tone = 'muted') {
    if (!hint) {
      return;
    }

    hint.textContent = message;
    hint.classList.toggle('text-danger', tone === 'error');
    hint.classList.toggle('text-success', tone === 'success');
    hint.classList.toggle('text-muted', tone === 'muted');
  }

  function clearSelect(select, placeholder, disabled = true) {
    if (!select) {
      return;
    }

    select.innerHTML = '';

    const option = document.createElement('option');
    option.value = '';
    option.textContent = placeholder;
    select.appendChild(option);
    select.disabled = disabled;
  }

  function fillItemSelect(items, selectedValue = '') {
    if (!itemSelect) {
      return;
    }

    clearSelect(itemSelect, 'Itens do chamado', false);
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = 'Sem item específico';
    blank.selected = !selectedValue;
    itemSelect.appendChild(blank);

    let matched = false;
    items.forEach((item) => {
      const option = document.createElement('option');
      option.value = String(item.id);
      option.dataset.tipo = item.tipo_equipamento || '';
      option.dataset.tipoDisplay = item.tipo_display || '';
      option.dataset.quantidade = String(item.quantidade || 1);
      option.dataset.observacao = item.observacao || '';
      const observacao = item.observacao ? ` - ${item.observacao}` : '';
      option.textContent = `${item.tipo_display} x${item.quantidade || 1}${observacao}`;
      if (selectedValue && String(item.id) === selectedValue) {
        option.selected = true;
        matched = true;
      }
      itemSelect.appendChild(option);
    });

    if (selectedValue && !matched) {
      blank.selected = true;
    }

    itemSelect.disabled = false;
  }

  function fillEquipmentSelect(results, selectedValue = '') {
    if (!equipamentoSelect) {
      return;
    }

    clearSelect(equipamentoSelect, 'Equipamentos compatíveis', false);

    results.forEach((equipamento) => {
      const value = getEquipmentValue(equipamento);
      if (!value) {
        return;
      }

      const option = document.createElement('option');
      option.value = value;
      const marcas = [equipamento.marca, equipamento.modelo].filter(Boolean).join(' ');
      const localizacao = equipamento.localizacao || 'Sem localização';
      option.textContent = `${equipamento.id_patrimonio} · ${equipamento.tipo_display}${marcas ? ` · ${marcas}` : ''} · ${localizacao}`;
      if (selectedValue && value === selectedValue) {
        option.selected = true;
      }
      equipamentoSelect.appendChild(option);
    });

    equipamentoSelect.disabled = false;
  }

  async function loadEquipments(tipo, selectedValue = equipamentoSelect?.value?.trim() || '') {
    const token = ++requestToken;
    const params = new URLSearchParams({ status: 'em_estoque' });
    if (tipo && tipo !== 'outro') {
      params.set('tipo', tipo);
    }

    clearSelect(equipamentoSelect, 'Carregando equipamentos...', true);
    try {
      const response = await fetch(`${equipamentosApiUrl}?${params.toString()}`, {
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
        },
      });
      if (!response.ok) {
        throw new Error('Falha ao carregar equipamentos.');
      }

      const payload = await response.json();
      if (token !== requestToken) {
        return;
      }

      const results = payload.results || [];
      if (!results.length) {
        clearSelect(equipamentoSelect, 'Nenhum equipamento compatível encontrado', true);
        setHint('Não há equipamentos compatíveis para o critério selecionado.', 'error');
        return;
      }

      fillEquipmentSelect(results, selectedValue);
      setHint('Escolha o equipamento que ficará reservado para o chamado.', 'success');
    } catch (error) {
      console.error(error);
      clearSelect(equipamentoSelect, baseEquipmentPlaceholder, true);
      setHint('Não foi possível carregar os equipamentos disponíveis.', 'error');
    }
  }

  async function loadCalledDetails(chamadoId, preserveSelection = false) {
    const token = ++requestToken;
    const currentItemValue = preserveSelection ? itemSelect?.value?.trim() || '' : '';
    const currentEquipmentValue = preserveSelection ? equipamentoSelect?.value?.trim() || '' : '';
    clearSelect(itemSelect, 'Carregando itens do chamado...', true);
    clearSelect(equipamentoSelect, 'Selecione um chamado primeiro', true);

    if (!chamadoId) {
      setHint('Escolha um chamado para carregar os itens e equipamentos compatíveis.', 'muted');
      return;
    }

    try {
      const response = await fetch(`${chamadosApiBase}${chamadoId}/`, {
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
        },
      });
      if (!response.ok) {
        throw new Error('Falha ao carregar chamado.');
      }

      const payload = await response.json();
      if (token !== requestToken) {
        return;
      }

      const items = payload.itens_solicitados || [];
      fillItemSelect(items, currentItemValue);

      if (!items.length) {
        setHint('Esse chamado ainda não tem itens formais. Você pode reservar um equipamento livre.', 'muted');
        await loadEquipments('', currentEquipmentValue);
        return;
      }

      const selectedItem = itemSelect?.selectedOptions?.[0];
      const tipo = selectedItem?.dataset.tipo || '';
      await loadEquipments(tipo, currentEquipmentValue);
    } catch (error) {
      console.error(error);
      clearSelect(itemSelect, 'Não foi possível carregar os itens', true);
      clearSelect(equipamentoSelect, 'Não foi possível carregar os equipamentos', true);
      setHint('Não foi possível carregar os dados do chamado selecionado.', 'error');
    }
  }

  if (chamadoSelect) {
    chamadoSelect.addEventListener('change', () => {
      loadCalledDetails(chamadoSelect.value.trim(), false);
    });
  }

  if (itemSelect) {
    itemSelect.addEventListener('change', () => {
      const selectedOption = itemSelect.selectedOptions[0];
      const tipo = selectedOption?.dataset.tipo || '';
      loadEquipments(tipo);
    });
  }

  const initialCalled = chamadoSelect?.value?.trim() || '';
  if (initialCalled) {
    loadCalledDetails(initialCalled, true);
  } else {
    clearSelect(itemSelect, baseItemPlaceholder, true);
    clearSelect(equipamentoSelect, baseEquipmentPlaceholder, true);
    setHint('Escolha um chamado para carregar os itens e equipamentos compatíveis.', 'muted');
  }

  const bulkForm = document.querySelector('[data-reserva-lote-form]');
  if (!bulkForm) {
    return;
  }

  const bulkSearch = bulkForm.querySelector('[data-reserva-lote-search]');
  const bulkSearchButton = bulkForm.querySelector('[data-reserva-lote-search-go]');
  const bulkSelectPageButton = bulkForm.querySelector('[data-reserva-lote-select-page]');
  const bulkLoadMoreButton = bulkForm.querySelector('[data-reserva-lote-load-more]');
  const bulkResetButton = bulkForm.querySelector('[data-reserva-lote-reset]');
  const bulkReserveAllButton = bulkForm.querySelector('[data-reserva-lote-reservar-todos]');
  const bulkResults = bulkForm.querySelector('[data-reserva-lote-results]');
  const bulkEmpty = bulkForm.querySelector('[data-reserva-lote-empty]');
  const bulkStatus = bulkForm.querySelector('[data-reserva-lote-status]');
  const bulkCount = bulkForm.querySelector('[data-reserva-lote-count]');
  const bulkSelectedList = bulkForm.querySelector('[data-reserva-lote-selected-list]');
  const bulkHiddenInputs = bulkForm.querySelector('[data-reserva-lote-hidden-inputs]');
  const bulkFilterInput = bulkForm.querySelector('[data-reserva-lote-filtro-input]');
  const bulkReserveAllInput = bulkForm.querySelector('[data-reserva-lote-all-input]');
  const bulkEquipmentsApiUrl = bulkForm.dataset.equipamentosApiUrl || equipamentosApiUrl;
  const bulkSelectedItems = new Map();
  const bulkPageSize = 24;

  let bulkQuery = bulkSearch?.value.trim() || bulkFilterInput?.value.trim() || '';
  let bulkOffset = 0;
  let bulkHasMore = false;
  let bulkLoading = false;
  let bulkRequestToken = 0;
  let bulkSearchTimer = null;

  function bulkSelectionLabel(item) {
    return [item.id_patrimonio, item.tipo_display].filter(Boolean).join(' · ');
  }

  function bulkSelectionId(item) {
    return String(item?.id_patrimonio ?? item?.pk ?? item?.id ?? '').trim();
  }

  function setBulkReserveAllFlag(enabled) {
    if (bulkReserveAllInput) {
      bulkReserveAllInput.value = enabled ? '1' : '0';
    }
  }

  function mergeBulkSelection(items) {
    items.forEach((item) => {
      const id = bulkSelectionId(item);
      if (!id) {
        return;
      }

      bulkSelectedItems.set(id, {
        id,
        label: bulkSelectionLabel(item) || id,
      });
    });

    setBulkReserveAllFlag(false);
    syncBulkHiddenInputs();
    syncBulkSelectedList();
    syncBulkCount();
    syncBulkResultCards();
  }

  async function addBulkResultsToSelection() {
    const query = bulkQuery.trim();
    if (!query) {
      setBulkStatus('Faça uma busca antes de selecionar todos os ativos filtrados.');
      setBulkEmptyState('A busca vazia nao pode adicionar todo o estoque de uma vez.', true);
      return;
    }

    if (bulkLoading) {
      setBulkStatus('Aguarde o carregamento da busca antes de selecionar todos os ativos.');
      return;
    }

    const token = ++bulkRequestToken;
    bulkLoading = true;
    if (bulkReserveAllButton) {
      bulkReserveAllButton.disabled = true;
    }

    setBulkStatus(`Carregando todos os ativos encontrados para "${query}"...`);
    setBulkEmptyState('Carregando todos os resultados encontrados...', false);

    try {
      const collected = [];
      let offset = 0;

      while (true) {
        const params = new URLSearchParams({
          status: 'em_estoque',
          limit: '200',
          offset: String(offset),
        });
        params.set('q', query);

        const response = await fetch(`${bulkEquipmentsApiUrl}?${params.toString()}`, {
          headers: {
            'X-Requested-With': 'XMLHttpRequest',
          },
        });
        if (!response.ok) {
          throw new Error('Falha ao carregar os ativos para reserva.');
        }

        const payload = await response.json();
        if (token !== bulkRequestToken) {
          return;
        }

        const results = payload.results || [];
        collected.push(...results);

        if (!payload.has_more) {
          break;
        }

        const nextOffset = payload.next_offset ?? (offset + results.length);
        if (nextOffset <= offset) {
          break;
        }
        offset = nextOffset;
      }

      if (!collected.length) {
        setBulkStatus(`Nenhum ativo encontrado para "${query}".`);
        setBulkEmptyState('Nenhum ativo encontrado com esse filtro.', true);
        return;
      }

      mergeBulkSelection(collected);
      setBulkStatus(`${bulkSelectedItems.size} ativo(s) na selecao. Revise os chips e conclua pela reserva selecionada.`);
      setBulkEmptyState('Use a busca para carregar ativos em estoque.', false);
    } catch (error) {
      console.error(error);
      setBulkStatus('Nao foi possivel carregar os ativos em estoque.');
      setBulkEmptyState('Nao foi possivel carregar os ativos em estoque.', true);
    } finally {
      bulkLoading = false;
      if (bulkReserveAllButton) {
        bulkReserveAllButton.disabled = false;
      }
    }
  }

  function syncBulkHiddenInputs() {
    if (bulkHiddenInputs) {
      bulkHiddenInputs.innerHTML = '';
      bulkSelectedItems.forEach((item, id) => {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'equipamentos';
        input.value = id;
        input.dataset.label = item.label;
        bulkHiddenInputs.appendChild(input);
      });
    }

    if (bulkFilterInput) {
      bulkFilterInput.value = bulkQuery;
    }
  }

  function syncBulkCount() {
    if (!bulkCount) {
      return;
    }

    const selected = bulkSelectedItems.size;
    bulkCount.textContent = selected > 0
      ? `${selected} ativo(s) selecionado(s). Revise antes de reservar.`
      : 'Selecione um ou mais equipamentos.';

    if (bulkSelectPageButton) {
      bulkSelectPageButton.disabled = !bulkResults || bulkResults.querySelectorAll('[data-reserva-lote-result-card="true"]').length === 0;
    }
  }

  function syncBulkSelectedList() {
    if (!bulkSelectedList) {
      return;
    }

    bulkSelectedList.innerHTML = '';

    if (!bulkSelectedItems.size) {
      const empty = document.createElement('span');
      empty.className = 'reserve-selection-chip reserve-selection-chip-empty';
      empty.textContent = 'Nenhum ativo selecionado.';
      bulkSelectedList.appendChild(empty);
      return;
    }

    bulkSelectedItems.forEach((item, id) => {
      const chip = document.createElement('span');
      chip.className = 'reserve-selection-chip';

      const label = document.createElement('span');
      label.textContent = item.label;

      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'reserve-selection-chip-remove';
      remove.dataset.removeId = id;
      remove.setAttribute('aria-label', `Remover ${item.label}`);
      remove.textContent = '×';

      chip.append(label, remove);
      bulkSelectedList.appendChild(chip);
    });
  }

  function syncBulkResultCards() {
    if (!bulkResults) {
      return;
    }

    bulkResults.querySelectorAll('[data-reserva-lote-result-card="true"]').forEach((card) => {
      const id = card.dataset.reservaLoteId || '';
      const selected = bulkSelectedItems.has(id);
      card.classList.toggle('is-selected', selected);
      card.setAttribute('aria-pressed', selected ? 'true' : 'false');

      const state = card.querySelector('[data-reserva-lote-card-state]');
      if (state) {
        state.textContent = selected ? 'Selecionado' : 'Adicionar';
      }

      const icon = card.querySelector('[data-reserva-lote-card-icon]');
      if (icon) {
        icon.className = selected ? 'fa-solid fa-circle-check' : 'fa-regular fa-circle';
      }
    });
  }

  function selectBulkItem(item) {
    const id = bulkSelectionId(item);
    if (!id) {
      return;
    }

    bulkSelectedItems.set(id, {
      id,
      label: bulkSelectionLabel(item) || id,
    });
    setBulkReserveAllFlag(false);
    syncBulkHiddenInputs();
    syncBulkSelectedList();
    syncBulkCount();
    syncBulkResultCards();
  }

  function deselectBulkItem(id) {
    bulkSelectedItems.delete(String(id));
    setBulkReserveAllFlag(false);
    syncBulkHiddenInputs();
    syncBulkSelectedList();
    syncBulkCount();
    syncBulkResultCards();
  }

  function toggleBulkItem(item) {
    const id = bulkSelectionId(item);
    if (!id) {
      return;
    }

    if (bulkSelectedItems.has(id)) {
      deselectBulkItem(id);
      return;
    }

    selectBulkItem(item);
  }

  function createBulkCard(item) {
    const id = bulkSelectionId(item);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'equipment-pick-card';
    button.dataset.reservaLoteResultCard = 'true';
    button.dataset.reservaLoteId = id;
    button.dataset.reservaLoteTipoDisplay = item.tipo_display || '';
    button.dataset.reservaLoteMarca = item.marca || '';
    button.dataset.reservaLoteModelo = item.modelo || '';
    button.dataset.reservaLoteLocalizacao = item.localizacao || '';
    button.dataset.reservaLoteStatusDisplay = item.status_display || 'Em estoque';

    const selected = bulkSelectedItems.has(id);
    button.classList.toggle('is-selected', selected);
    button.setAttribute('aria-pressed', selected ? 'true' : 'false');

    const head = document.createElement('div');
    head.className = 'equipment-pick-card__head';

    const titleWrap = document.createElement('div');
    titleWrap.className = 'equipment-pick-card__title-wrap';

    const title = document.createElement('strong');
    title.textContent = item.id_patrimonio || 'Sem patrimônio';

    const subtitle = document.createElement('span');
    subtitle.className = 'equipment-pick-card__subtitle';
    subtitle.textContent = item.tipo_display || 'Sem tipo';

    titleWrap.append(title, subtitle);

    const badge = document.createElement('span');
    badge.className = 'badge badge-soft equipment-pick-card__badge';
    badge.textContent = item.status_display || 'Em estoque';

    head.append(titleWrap, badge);

    const body = document.createElement('div');
    body.className = 'equipment-pick-card__body';

    const meta = document.createElement('div');
    meta.className = 'equipment-pick-card__meta';
    meta.textContent = [item.marca, item.modelo].filter(Boolean).join(' · ') || 'Sem marca/modelo';

    const location = document.createElement('div');
    location.className = 'equipment-pick-card__location';
    location.textContent = item.localizacao || 'Sem localização';

    body.append(meta, location);

    const footer = document.createElement('div');
    footer.className = 'equipment-pick-card__footer';

    const state = document.createElement('span');
    state.className = 'equipment-pick-card__state';
    state.dataset.reservaLoteCardState = 'true';
    state.textContent = selected ? 'Selecionado' : 'Adicionar';

    const icon = document.createElement('i');
    icon.className = selected ? 'fa-solid fa-circle-check' : 'fa-regular fa-circle';
    icon.dataset.reservaLoteCardIcon = 'true';
    icon.setAttribute('aria-hidden', 'true');

    footer.append(state, icon);

    button.append(head, body, footer);

    button.addEventListener('click', () => {
      toggleBulkItem(item);
    });

    return button;
  }

  function setBulkStatus(message) {
    if (bulkStatus) {
      bulkStatus.textContent = message;
    }
  }

  function setBulkEmptyState(message, visible) {
    if (!bulkEmpty) {
      return;
    }

    bulkEmpty.textContent = message;
    bulkEmpty.hidden = !visible;
  }

  async function loadBulkResults({ append = false } = {}) {
    if (bulkLoading || !bulkResults) {
      return;
    }

    const token = ++bulkRequestToken;
    bulkLoading = true;

    if (!append) {
      bulkOffset = 0;
      bulkResults.innerHTML = '';
    }

    setBulkStatus('Carregando ativos em estoque...');
    setBulkEmptyState('Carregando ativos...', false);

    try {
      const params = new URLSearchParams({
        status: 'em_estoque',
        limit: String(bulkPageSize),
        offset: String(bulkOffset),
      });
      if (bulkQuery) {
        params.set('q', bulkQuery);
      }

      const response = await fetch(`${bulkEquipmentsApiUrl}?${params.toString()}`, {
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
        },
      });
      if (!response.ok) {
        throw new Error('Falha ao carregar os ativos para reserva.');
      }

      const payload = await response.json();
      if (token !== bulkRequestToken) {
        return;
      }

      const results = payload.results || [];
      if (!append) {
        bulkResults.innerHTML = '';
      }

      results.forEach((item) => {
        bulkResults.appendChild(createBulkCard(item));
      });

      bulkHasMore = Boolean(payload.has_more);
      bulkOffset = payload.next_offset ?? (bulkOffset + results.length);

      if (bulkLoadMoreButton) {
        bulkLoadMoreButton.hidden = !bulkHasMore;
      }

      if (!results.length && !append) {
        setBulkEmptyState('Nenhum ativo encontrado com esse filtro.', true);
      } else {
        setBulkEmptyState('Use a busca para carregar ativos em estoque.', false);
      }

      const summaryPrefix = bulkQuery ? `para "${bulkQuery}"` : 'em estoque';
      setBulkStatus(`Mostrando ${results.length} de ${payload.count} ativos ${summaryPrefix}.`);
      syncBulkSelectedList();
      syncBulkCount();
      syncBulkResultCards();
    } catch (error) {
      console.error(error);
      setBulkStatus('Não foi possível carregar os ativos em estoque.');
      setBulkEmptyState('Não foi possível carregar os ativos em estoque.', true);
      if (bulkLoadMoreButton) {
        bulkLoadMoreButton.hidden = true;
      }
    } finally {
      bulkLoading = false;
    }
  }

  function refreshBulkSearch(query) {
    bulkQuery = query.trim();
    setBulkReserveAllFlag(false);
    if (bulkFilterInput) {
      bulkFilterInput.value = bulkQuery;
    }
    loadBulkResults({ append: false });
  }

  if (bulkHiddenInputs) {
    Array.from(bulkHiddenInputs.querySelectorAll('input[name="equipamentos"]')).forEach((input) => {
      const id = String(input.value || '').trim();
      if (id) {
        bulkSelectedItems.set(id, {
          id,
          label: id,
        });
      }
    });
  }

  syncBulkHiddenInputs();
  syncBulkSelectedList();
  syncBulkCount();
  setBulkReserveAllFlag(false);

  if (bulkSearchButton && bulkSearch) {
    bulkSearchButton.addEventListener('click', () => {
      refreshBulkSearch(bulkSearch.value || '');
    });
  }

  if (bulkSearch) {
    bulkSearch.addEventListener('input', () => {
      window.clearTimeout(bulkSearchTimer);
      bulkSearchTimer = window.setTimeout(() => {
        refreshBulkSearch(bulkSearch.value || '');
      }, 280);
    });

    bulkSearch.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        refreshBulkSearch(bulkSearch.value || '');
      }
    });
  }

  if (bulkLoadMoreButton) {
    bulkLoadMoreButton.addEventListener('click', () => {
      loadBulkResults({ append: true });
    });
  }

  if (bulkSelectPageButton) {
    bulkSelectPageButton.addEventListener('click', () => {
      setBulkReserveAllFlag(false);
      bulkResults?.querySelectorAll('[data-reserva-lote-result-card="true"]').forEach((card) => {
        const item = {
          pk: card.dataset.reservaLoteId || '',
          id_patrimonio: card.dataset.reservaLoteId || '',
          tipo_display: card.dataset.reservaLoteTipoDisplay || '',
          marca: card.dataset.reservaLoteMarca || '',
          modelo: card.dataset.reservaLoteModelo || '',
          localizacao: card.dataset.reservaLoteLocalizacao || '',
          status_display: card.dataset.reservaLoteStatusDisplay || 'Em estoque',
        };
        selectBulkItem(item);
      });
    });
  }

  if (bulkResetButton) {
    bulkResetButton.addEventListener('click', () => {
      setBulkReserveAllFlag(false);
      bulkSelectedItems.clear();
      if (bulkSearch) {
        bulkSearch.value = '';
      }
      bulkQuery = '';
      syncBulkHiddenInputs();
      syncBulkSelectedList();
      syncBulkCount();
      loadBulkResults({ append: false });
    });
  }

  if (bulkReserveAllButton) {
    bulkReserveAllButton.addEventListener('click', async () => {
      await addBulkResultsToSelection();
    });
  }

  if (bulkSelectedList) {
    bulkSelectedList.addEventListener('click', (event) => {
      const removeButton = event.target.closest('[data-remove-id]');
      if (!removeButton) {
        return;
      }

      deselectBulkItem(removeButton.dataset.removeId || '');
    });
  }

  if (bulkResults) {
    bulkResults.addEventListener('click', (event) => {
      const card = event.target.closest('[data-reserva-lote-result-card="true"]');
      if (!card) {
        return;
      }

      toggleBulkItem({
        pk: card.dataset.reservaLoteId || '',
        id_patrimonio: card.dataset.reservaLoteId || '',
        tipo_display: card.dataset.reservaLoteTipoDisplay || '',
        marca: card.dataset.reservaLoteMarca || '',
        modelo: card.dataset.reservaLoteModelo || '',
        localizacao: card.dataset.reservaLoteLocalizacao || '',
        status_display: card.dataset.reservaLoteStatusDisplay || 'Em estoque',
      });
    });
  }

  loadBulkResults({ append: false });
})();
