from decimal import Decimal

from django import forms
from django.core.validators import FileExtensionValidator

from accounts.models import Usuario
from chamados.models import Chamado

from .models import Equipamento, MovimentacaoEquipamento, TipoEquipamento

# Definir especificações por tipo de equipamento
ESPECIFICACOES_POR_TIPO = {
    TipoEquipamento.NOTEBOOK_PADRAO: [
        {'nome': 'processador', 'label': 'Processador', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'memoria_gb', 'label': 'Memória (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'armazenamento_gb', 'label': 'Armazenamento (GB)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'gpu', 'label': 'GPU', 'tipo': 'text', 'obrigatorio': False},
        {'nome': 'tela_polegadas', 'label': 'Tela (polegadas)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'so', 'label': 'Sistema Operacional', 'tipo': 'text', 'obrigatorio': False},
    ],
    TipoEquipamento.NOTEBOOK_AVANCADO: [
        {'nome': 'processador', 'label': 'Processador', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'memoria_gb', 'label': 'Memória (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'armazenamento_gb', 'label': 'Armazenamento (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'gpu', 'label': 'GPU', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'tela_polegadas', 'label': 'Tela (polegadas)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'teclado_retroiluminado', 'label': 'Teclado Retroiluminado', 'tipo': 'checkbox', 'obrigatorio': False},
        {'nome': 'so', 'label': 'Sistema Operacional', 'tipo': 'text', 'obrigatorio': False},
    ],
    TipoEquipamento.ULTRABOOK: [
        {'nome': 'processador', 'label': 'Processador', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'memoria_gb', 'label': 'Memória (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'armazenamento_gb', 'label': 'Armazenamento (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'gpu', 'label': 'GPU', 'tipo': 'text', 'obrigatorio': False},
        {'nome': 'peso_kg', 'label': 'Peso (kg)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'bateria_horas', 'label': 'Duração Bateria (horas)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'tela_polegadas', 'label': 'Tela (polegadas)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'so', 'label': 'Sistema Operacional', 'tipo': 'text', 'obrigatorio': False},
    ],
    TipoEquipamento.DESKTOP_PADRAO: [
        {'nome': 'processador', 'label': 'Processador', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'memoria_gb', 'label': 'Memória (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'armazenamento_gb', 'label': 'Armazenamento (GB)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'gpu', 'label': 'GPU', 'tipo': 'text', 'obrigatorio': False},
        {'nome': 'fonte_watts', 'label': 'Fonte (Watts)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'so', 'label': 'Sistema Operacional', 'tipo': 'text', 'obrigatorio': False},
    ],
    TipoEquipamento.DESKTOP_PLUS: [
        {'nome': 'processador', 'label': 'Processador', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'memoria_gb', 'label': 'Memória (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'armazenamento_gb', 'label': 'Armazenamento (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'gpu', 'label': 'GPU', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'fonte_watts', 'label': 'Fonte (Watts)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'so', 'label': 'Sistema Operacional', 'tipo': 'text', 'obrigatorio': False},
    ],
    TipoEquipamento.DESKTOP_AVANCADO: [
        {'nome': 'processador', 'label': 'Processador', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'memoria_gb', 'label': 'Memória (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'armazenamento_gb', 'label': 'Armazenamento (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'gpu', 'label': 'GPU', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'fonte_watts', 'label': 'Fonte (Watts)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'refrigeracao', 'label': 'Refrigeração', 'tipo': 'text', 'obrigatorio': False},
        {'nome': 'so', 'label': 'Sistema Operacional', 'tipo': 'text', 'obrigatorio': False},
    ],
    TipoEquipamento.MACBOOK: [
        {'nome': 'processador', 'label': 'Processador', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'memoria_gb', 'label': 'Memória (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'armazenamento_gb', 'label': 'Armazenamento (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'tela_polegadas', 'label': 'Tela (polegadas)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'geracao', 'label': 'Geração', 'tipo': 'text', 'obrigatorio': False},
    ],
    TipoEquipamento.SWITCH: [
        {'nome': 'portas', 'label': 'Número de Portas', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'velocidade_gbps', 'label': 'Velocidade (Gbps)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'gerenciavel', 'label': 'Gerenciável', 'tipo': 'checkbox', 'obrigatorio': False},
        {'nome': 'poe', 'label': 'PoE (Power over Ethernet)', 'tipo': 'checkbox', 'obrigatorio': False},
        {'nome': 'stack', 'label': 'Suporta Stack', 'tipo': 'checkbox', 'obrigatorio': False},
    ],
    TipoEquipamento.ACCESSPOINT: [
        {'nome': 'padrao_wifi', 'label': 'Padrão WiFi', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'frequencia', 'label': 'Frequência (GHz)', 'tipo': 'text', 'obrigatorio': False},
        {'nome': 'velocidade_mbps', 'label': 'Velocidade (Mbps)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'antenas', 'label': 'Número de Antenas', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'poe', 'label': 'PoE', 'tipo': 'checkbox', 'obrigatorio': False},
    ],
    TipoEquipamento.FIBRA_OPTICA: [
        {'nome': 'tipo_fibra', 'label': 'Tipo de Fibra', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'velocidade_gbps', 'label': 'Velocidade (Gbps)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'alcance_metros', 'label': 'Alcance (metros)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'comprimento_onda_nm', 'label': 'Comprimento de Onda (nm)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'numero_filamentos', 'label': 'Número de Filamentos', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'revestimento', 'label': 'Revestimento', 'tipo': 'text', 'obrigatorio': False},
    ],
    TipoEquipamento.SFP: [
        {'nome': 'velocidade_gbps', 'label': 'Velocidade (Gbps)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'tipo_transceiver', 'label': 'Tipo de Transceiver', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'alcance', 'label': 'Alcance', 'tipo': 'text', 'obrigatorio': False},
    ],
    TipoEquipamento.QSF: [
        {'nome': 'velocidade_gbps', 'label': 'Velocidade (Gbps)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'tipo_transceiver', 'label': 'Tipo de Transceiver', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'alcance', 'label': 'Alcance', 'tipo': 'text', 'obrigatorio': False},
        {'nome': 'modos_operacao', 'label': 'Modos de Operação', 'tipo': 'text', 'obrigatorio': False},
    ],
    TipoEquipamento.MONITOR: [
        {'nome': 'tamanho_polegadas', 'label': 'Tamanho (polegadas)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'resolucao', 'label': 'Resolução', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'taxa_atualizacao_hz', 'label': 'Taxa de Atualização (Hz)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'painel_tipo', 'label': 'Tipo de Painel', 'tipo': 'text', 'obrigatorio': False},
    ],
    TipoEquipamento.CELULAR: [
        {'nome': 'processador', 'label': 'Processador', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'memoria_gb', 'label': 'Memória (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'armazenamento_gb', 'label': 'Armazenamento (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'tela_polegadas', 'label': 'Tela (polegadas)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'so', 'label': 'Sistema Operacional', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'camera_mp', 'label': 'Câmera (MP)', 'tipo': 'number', 'obrigatorio': False},
    ],
    TipoEquipamento.IPAD_TABLET: [
        {'nome': 'processador', 'label': 'Processador', 'tipo': 'text', 'obrigatorio': True},
        {'nome': 'memoria_gb', 'label': 'Memória (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'armazenamento_gb', 'label': 'Armazenamento (GB)', 'tipo': 'number', 'obrigatorio': True},
        {'nome': 'tela_polegadas', 'label': 'Tela (polegadas)', 'tipo': 'number', 'obrigatorio': False},
        {'nome': 'so', 'label': 'Sistema Operacional', 'tipo': 'text', 'obrigatorio': True},
    ],
}


def _aplicar_estilo_campos(form):
    for field in form.fields.values():
        if isinstance(field.widget, forms.Select):
            field.widget.attrs['class'] = 'form-select'
        elif isinstance(field.widget, forms.Textarea):
            field.widget.attrs['class'] = 'form-control'
        else:
            field.widget.attrs.setdefault('class', 'form-control')


class EquipamentoForm(forms.ModelForm):
    class Meta:
        model = Equipamento
        fields = [
            'id_patrimonio',
            'tipo',
            'tipo_outro',
            'marca',
            'modelo',
            'service_tag',
            'imei',
            'numero_serie',
            'usuario_windows_esperado',
            'monitor_patrimonio',
            'status',
            'condicao',
            'responsavel',
            'site',
            'setor',
            'andar_sala',
            'descricao',
            'data_aquisicao',
            'valor_aquisicao',
            'fornecedor',
            'garantia_ate',
            'vida_util_estimada_meses',
            'score_saude',
        ]
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 4}),
            'data_aquisicao': forms.DateInput(attrs={'type': 'date'}),
            'garantia_ate': forms.DateInput(attrs={'type': 'date'}),
            'valor_aquisicao': forms.NumberInput(attrs={'step': '0.01'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['responsavel'].queryset = Usuario.objects.filter(ativo=True).order_by('first_name', 'last_name')

        # Adicionar campos dinâmicos de especificações
        tipo_selecionado = self.data.get('tipo') if self.data else (self.instance.tipo if self.instance else None)
        if tipo_selecionado and tipo_selecionado in ESPECIFICACOES_POR_TIPO:
            self._adicionar_campos_especificacoes(tipo_selecionado)

        self.specification_fields = [self[field_name] for field_name in self.fields if field_name.startswith('spec_')]
        _aplicar_estilo_campos(self)

    def _adicionar_campos_especificacoes(self, tipo):
        """Adiciona campos dinâmicos baseado no tipo de equipamento"""
        especificacoes = ESPECIFICACOES_POR_TIPO.get(tipo, [])
        valores_atuais = self.instance.especificacoes if self.instance and self.instance.pk else {}

        for spec in especificacoes:
            nome = spec['nome']
            label = spec['label']
            tipo_campo = spec['tipo']
            valor = valores_atuais.get(nome, '')

            if tipo_campo == 'checkbox':
                field = forms.BooleanField(
                    label=label,
                    required=spec['obrigatorio'],
                    initial=bool(valor),
                    widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
                )
            elif tipo_campo == 'number':
                field = forms.DecimalField(
                    label=label,
                    required=spec['obrigatorio'],
                    initial=valor if valor else None,
                    widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
                )
            else:  # text
                field = forms.CharField(
                    label=label,
                    required=spec['obrigatorio'],
                    initial=valor if valor else '',
                    widget=forms.TextInput(attrs={'class': 'form-control'})
                )

            # Adicionar o campo ao formulário com prefixo 'spec_' para identificação
            self.fields[f'spec_{nome}'] = field

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get('tipo')

        if tipo == TipoEquipamento.OUTRO and not cleaned_data.get('tipo_outro'):
            self.add_error('tipo_outro', 'Informe o tipo do equipamento.')

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        # Salvar campos de especificações no JSON
        especificacoes = {}
        for field_name in self.fields:
            if field_name.startswith('spec_'):
                spec_name = field_name.replace('spec_', '')
                valor = self.cleaned_data.get(field_name)
                if valor is not None and valor != '':
                    if isinstance(valor, Decimal):
                        valor = int(valor) if valor == valor.to_integral_value() else float(valor)
                    especificacoes[spec_name] = valor

        instance.especificacoes = especificacoes

        if commit:
            instance.save()
        return instance


class MovimentacaoEquipamentoForm(forms.ModelForm):
    class Meta:
        model = MovimentacaoEquipamento
        fields = [
            'tipo',
            'descricao',
            'usuario_anterior',
            'usuario_novo',
            'chamado',
            'observacoes',
            'fornecedor_manutencao',
            'custo_manutencao',
        ]
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 3}),
            'observacoes': forms.Textarea(attrs={'rows': 3}),
            'custo_manutencao': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['usuario_anterior'].queryset = Usuario.objects.filter(ativo=True).order_by('first_name', 'last_name')
        self.fields['usuario_novo'].queryset = Usuario.objects.filter(ativo=True).order_by('first_name', 'last_name')
        self.fields['chamado'].queryset = Chamado.objects.select_related('equipamento').order_by('-created_at')
        self.fields['custo_manutencao'].min_value = Decimal('0')
        _aplicar_estilo_campos(self)

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get('tipo')
        usuario_novo = cleaned_data.get('usuario_novo')
        custo_manutencao = cleaned_data.get('custo_manutencao')
        fornecedor_manutencao = cleaned_data.get('fornecedor_manutencao')

        if tipo in {'saida', 'transferencia', 'troca'} and not usuario_novo:
            self.add_error('usuario_novo', 'Informe o novo usuário para este tipo de movimentação.')
        if tipo not in {'manutencao', 'retorno_manutencao'} and (custo_manutencao or fornecedor_manutencao):
            self.add_error('custo_manutencao', 'Custos e fornecedor so podem ser informados em manutencoes.')
        return cleaned_data


class ImportacaoEquipamentosCSVForm(forms.Form):
    arquivo = forms.FileField(
        label='Arquivo CSV',
        validators=[FileExtensionValidator(allowed_extensions=['csv'])],
        help_text='Envie o CSV com a estrutura esperada para importar ou atualizar equipamentos em lote.',
    )
    descricao = forms.CharField(
        label='Descrição do lote',
        max_length=200,
        required=False,
        help_text='Opcional. Use para identificar a origem do arquivo importado.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['arquivo'].widget.attrs.setdefault('class', 'form-control')
        self.fields['descricao'].widget.attrs.setdefault('class', 'form-control')
