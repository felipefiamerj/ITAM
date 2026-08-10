(() => {
  document.addEventListener('DOMContentLoaded', () => {
    const hidden = document.getElementById('id_itens_entrega');
    const accordionItems = Array.from(document.querySelectorAll('.equipment-accordion-item'));
    const cards = Array.from(document.querySelectorAll('[data-select-equipamento]'));
    const summaryCounters = Array.from(document.querySelectorAll('[data-selected-items-count]'));
    const summaryMessage = document.querySelector('[data-selected-items-message]');
    const totalItems = accordionItems.filter((item) => item.dataset.itemRequerSelecao !== '0').length;

    if (!hidden || !accordionItems.length || !cards.length) {
      return;
    }

    let selections = {};
    try {
      selections = hidden.value ? JSON.parse(hidden.value) : {};
    } catch (error) {
      selections = {};
    }

    const normalizeItemId = (value) => String(value || '');

    const updateHidden = () => {
      hidden.value = JSON.stringify(selections);
    };

    const setBadgeState = (badge, state) => {
      if (!badge) {
        return;
      }

      const classes = ['status-pill'];
      if (state === 'selected') {
        classes.push('status-pill-success');
      } else if (state === 'pending') {
        classes.push('status-pill-warning');
      } else {
        classes.push('status-pill-neutral');
      }

      badge.className = classes.join(' ');
    };

    const equipamentoUsadoEmOutroItem = (itemId, equipamentoId) => {
      return Object.entries(selections).some(([otherItemId, otherEquipamentoId]) => {
        return normalizeItemId(otherItemId) !== normalizeItemId(itemId) && String(otherEquipamentoId) === String(equipamentoId);
      });
    };

    const syncSelection = () => {
      let selectedCount = 0;

      accordionItems.forEach((item) => {
        const itemId = normalizeItemId(item.dataset.itemId);
        const selectedEquipmentId = String(selections[itemId] || '');
        const badge = item.querySelector('[data-item-selection-badge]');
        const label = item.querySelector('[data-item-selection-label]');
        const selected = Boolean(selectedEquipmentId);
        const requerSelecao = item.dataset.itemRequerSelecao !== '0';

        item.classList.toggle('has-selected-card', selected);
        if (badge) {
          badge.textContent = selected ? 'Selecionado' : (requerSelecao ? 'Pendente' : 'Opcional');
          setBadgeState(badge, selected ? 'selected' : (requerSelecao ? 'pending' : 'neutral'));
        }
        if (label) {
          if (selected) {
            const selectedCard = item.querySelector(`[data-select-equipamento="${selectedEquipmentId}"]`);
            if (selectedCard) {
              const patrimonio = selectedCard.dataset.equipamentoPatrimonio || 'Equipamento';
              const tipo = selectedCard.dataset.equipamentoTipo || '';
              const marca = selectedCard.dataset.equipamentoMarca || '';
              const modelo = selectedCard.dataset.equipamentoModelo || '';
              const detalhes = [patrimonio, tipo].filter(Boolean);
              const complemento = [marca, modelo].filter(Boolean).join(' ');
              if (complemento) {
                detalhes.push(complemento);
              }
              label.textContent = `Selecionado: ${detalhes.join(' · ')}`;
            } else {
              label.textContent = 'Selecionado';
            }
          } else {
            label.textContent = requerSelecao ? 'Nenhum equipamento selecionado' : 'Item sem seleção de estoque';
          }
        }

        item.querySelectorAll('.equipment-stock-card').forEach((card) => {
          const active = card.dataset.selectEquipamento === selectedEquipmentId;
          card.classList.toggle('is-selected', active);
          card.setAttribute('aria-pressed', active ? 'true' : 'false');
        });

        if (selected && requerSelecao) {
          selectedCount += 1;
        }
      });

      updateHidden();

      summaryCounters.forEach((counter) => {
        counter.textContent = `${selectedCount}/${totalItems}`;
      });

      if (summaryMessage) {
        if (!totalItems) {
          summaryMessage.textContent = 'Este chamado não exige seleção de estoque.';
        } else if (selectedCount === totalItems) {
          summaryMessage.textContent = 'Todos os itens obrigatórios foram vinculados. Você já pode registrar a entrega.';
        } else if (selectedCount === 0) {
          summaryMessage.textContent = 'Nenhum item foi vinculado ainda. Abra um item e clique no equipamento correto.';
        } else {
          summaryMessage.textContent = `Faltam ${totalItems - selectedCount} item(ns) para concluir a entrega.`;
        }
      }
    };

    cards.forEach((card) => {
      card.addEventListener('click', () => {
        const itemId = normalizeItemId(card.dataset.itemId);
        const equipamentoId = String(card.dataset.selectEquipamento || '');
        if (!itemId || !equipamentoId) {
          return;
        }

        if (equipamentoUsadoEmOutroItem(itemId, equipamentoId)) {
          window.alert('Esse equipamento já está vinculado a outro item. Escolha outro card para manter 1 equipamento por item.');
          return;
        }

        selections[itemId] = equipamentoId;
        syncSelection();
      });
    });

    syncSelection();
  });
})();
