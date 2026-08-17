/**
 * Script para formulários dinâmicos de equipamentos
 * Carrega campos específicos baseado no tipo de equipamento selecionado
 */

document.addEventListener('DOMContentLoaded', function() {
    const tipoSelect = document.getElementById('id_tipo');

    if (!tipoSelect) return;

    // Carregar campos ao mudar o tipo
    tipoSelect.addEventListener('change', function() {
        const tipo = this.value;
        if (tipo) {
            carregarCamposEspecificacoes(tipo);
        } else {
            limparCamposEspecificacoes();
        }
    });

    // Preserva os valores que o Django renderizou em edicoes e erros de validacao.
    const container = document.getElementById('especificacoes-container');
    if (tipoSelect.value && !container?.dataset.serverRendered) {
        carregarCamposEspecificacoes(tipoSelect.value);
    }
});

/**
 * Carrega os campos específicos para um tipo de equipamento
 */
function carregarCamposEspecificacoes(tipo) {
    const container = document.getElementById('especificacoes-container');

    if (!container) return;
    delete container.dataset.serverRendered;

    // Mostrar loading
    container.innerHTML = '<div class="d-flex align-items-center justify-content-center py-4"><div class="spinner-border spinner-border-sm text-primary me-2"></div><span>Carregando especificações...</span></div>';

    // Fazer requisição ao endpoint da API
    fetch(`/equipamentos/api/campos-especificacoes/?tipo=${encodeURIComponent(tipo)}`)
        .then(response => {
            if (!response.ok) throw new Error('Erro ao carregar campos');
            return response.json();
        })
        .then(data => {
            renderizarCampos(data.especificacoes, tipo);
        })
        .catch(error => {
            console.error('Erro:', error);
            container.innerHTML = '<div class="alert alert-danger"><i class="fa-solid fa-exclamation-triangle me-2"></i>Erro ao carregar especificações do equipamento</div>';
        });
}

/**
 * Renderiza os campos específicos no formulário
 */
function renderizarCampos(especificacoes, tipo) {
    const container = document.getElementById('especificacoes-container');

    if (!especificacoes || especificacoes.length === 0) {
        container.innerHTML = '';
        return;
    }

    // Separar campos obrigatórios e opcionais
    const obrigatorios = especificacoes.filter(e => e.obrigatorio);
    const opcionais = especificacoes.filter(e => !e.obrigatorio);

    let html = '';

    // SEÇÃO DE ESPECIFICAÇÕES
    html += '<div class="card card-section" style="border-left: 4px solid #0d6efd;">';
    html += '<div class="card-header" style="background: linear-gradient(135deg, #0d6efd 0%, #0861ca 100%); color: white;">';
    html += '<h5 class="card-title mb-0" style="color: white;">';
    html += '<i class="fa-solid fa-microchip me-2"></i>Especificações do Equipamento';
    html += '</h5>';
    html += '</div>';
    html += '<div class="card-body">';

    // Campos obrigatórios
    if (obrigatorios.length > 0) {
        html += '<div class="mb-4">';
        html += '<h6 class="text-muted text-uppercase" style="font-size: 0.85rem; letter-spacing: 0.5px; font-weight: 600; margin-bottom: 1rem;">';
        html += '<i class="fa-solid fa-star text-danger me-2"></i>Informações Obrigatórias';
        html += '</h6>';
        html += '<div class="row">';
        obrigatorios.forEach(spec => {
            html += renderizarCampo(spec);
        });
        html += '</div>';
        html += '</div>';
    }

    // Campos opcionais
    if (opcionais.length > 0) {
        html += '<div class="border-top pt-4">';
        html += '<h6 class="text-muted text-uppercase" style="font-size: 0.85rem; letter-spacing: 0.5px; font-weight: 600; margin-bottom: 1rem;">';
        html += '<i class="fa-solid fa-circle-info text-info me-2"></i>Informações Adicionais';
        html += '</h6>';
        html += '<div class="row">';
        opcionais.forEach(spec => {
            html += renderizarCampo(spec);
        });
        html += '</div>';
        html += '</div>';
    }

    html += '</div>';
    html += '</div>';

    container.innerHTML = html;

    // Aplicar estilos aos campos renderizados
    aplicarEstilosCamposEspecificacoes();
}

/**
 * Renderiza um campo individual
 */
function renderizarCampo(spec) {
    const fieldName = `spec_${spec.nome}`;
    const fieldId = `id_${fieldName}`;
    const valorAtual = obterValorCampo(fieldName);

    let html = '<div class="col-md-6 mb-3">';

    if (spec.tipo === 'checkbox') {
        html += `
            <div class="form-check form-switch">
                <input class="form-check-input" type="checkbox" id="${fieldId}"
                       name="${fieldName}" value="true" ${valorAtual ? 'checked' : ''}
                       style="cursor: pointer; width: 2.5em; height: 1.5em;">
                <label class="form-check-label" for="${fieldId}" style="cursor: pointer; user-select: none;">
                    ${spec.label}
                    ${spec.obrigatorio ? '<span class="text-danger ms-1">*</span>' : ''}
                </label>
            </div>
        `;
    } else if (spec.tipo === 'number') {
        const obrigatorio = spec.obrigatorio ? 'required' : '';
        html += `
            <label for="${fieldId}" class="form-label">
                ${spec.label}
                ${spec.obrigatorio ? '<span class="text-danger ms-1">*</span>' : ''}
            </label>
            <input type="number" class="form-control form-control-equipamento" id="${fieldId}"
                   name="${fieldName}" step="0.01" ${obrigatorio} value="${valorAtual || ''}"
                   placeholder="Informe o valor...">
        `;
    } else {
        const obrigatorio = spec.obrigatorio ? 'required' : '';
        html += `
            <label for="${fieldId}" class="form-label">
                ${spec.label}
                ${spec.obrigatorio ? '<span class="text-danger ms-1">*</span>' : ''}
            </label>
            <input type="text" class="form-control form-control-equipamento" id="${fieldId}"
                   name="${fieldName}" ${obrigatorio} value="${valorAtual || ''}"
                   placeholder="Informe o valor...">
        `;
    }

    html += '</div>';
    return html;
}

/**
 * Limpa os campos de especificações
 */
function limparCamposEspecificacoes() {
    const container = document.getElementById('especificacoes-container');
    if (container) {
        container.innerHTML = '';
    }
}

/**
 * Obtém o valor atual de um campo de especificação
 */
function obterValorCampo(fieldName) {
    const input = document.querySelector(`[name="${fieldName}"]`);
    if (!input) return '';

    if (input.type === 'checkbox') {
        return input.checked ? 'true' : '';
    }

    return input.value || '';
}

/**
 * Aplica estilos aos campos renderizados
 */
function aplicarEstilosCamposEspecificacoes() {
    const campos = document.querySelectorAll('.form-control-equipamento');

    campos.forEach(campo => {
        // Adicionar classe de estilo
        campo.classList.add('form-control-modern');

        // Adicionar ícone de foco
        campo.addEventListener('focus', function() {
            this.style.boxShadow = '0 0 0 0.2rem rgba(13, 110, 253, 0.25)';
        });

        campo.addEventListener('blur', function() {
            this.style.boxShadow = 'none';
        });
    });
}
