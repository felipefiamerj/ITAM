import json
from collections import defaultdict
from types import SimpleNamespace

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from accounts.models import NivelAcesso, Usuario
from equipamentos.models import Equipamento, StatusEquipamento, TipoEquipamento

from .models import Chamado, ChamadoItemSolicitado


TIPOS_EQUIPAMENTOS_SELECIONAVEIS = [
    (valor, label)
    for valor, label in TipoEquipamento.choices
]

_TIPO_EQUIPAMENTO_LABELS = dict(TipoEquipamento.choices)

REQUEST_TEMPLATE_DEFINITIONS = [
    {
        'key': 'workplace',
        'eyebrow': 'Solicitacao',
        'title': 'Solicitacao de Equipamento',
        'highlight': 'Workplace as a Service',
        'description': 'Fluxo para onboarding, troca ou entrega de notebook, desktop, monitor e kit de trabalho.',
        'icon': 'fa-laptop',
        'tone': 'blue',
        'tipos': [
            TipoEquipamento.NOTEBOOK_PADRAO,
            TipoEquipamento.NOTEBOOK_AVANCADO,
            TipoEquipamento.ULTRABOOK,
            TipoEquipamento.MACBOOK,
            TipoEquipamento.DESKTOP_PADRAO,
            TipoEquipamento.DESKTOP_AVANCADO,
            TipoEquipamento.DESKTOP_PLUS,
            TipoEquipamento.MONITOR,
            TipoEquipamento.DOCKSTATION,
            TipoEquipamento.TECLADO,
            TipoEquipamento.MOUSE,
            TipoEquipamento.FONE,
            TipoEquipamento.ADAPTADOR,
            TipoEquipamento.CADEADO_TRAVA,
            TipoEquipamento.CELULAR,
            TipoEquipamento.IPAD_TABLET,
            TipoEquipamento.MOCHILA,
        ],
    },
    {
        'key': 'perifericos',
        'eyebrow': 'Solicitacao',
        'title': 'Solicitacao de Equipamento',
        'highlight': 'Perifericos',
        'description': 'Fluxo mais enxuto para itens de apoio como teclado, mouse, fone, dockstation e adaptadores.',
        'icon': 'fa-computer-mouse',
        'tone': 'teal',
        'tipos': [
            TipoEquipamento.ADAPTADOR,
            TipoEquipamento.CADEADO_TRAVA,
            TipoEquipamento.DOCKSTATION,
            TipoEquipamento.FONE,
            TipoEquipamento.MONITOR,
            TipoEquipamento.MOUSE,
            TipoEquipamento.TECLADO,
        ],
    },
    {
        'key': 'padrao',
        'eyebrow': 'Geral',
        'title': 'Chamado padrao',
        'highlight': 'Fluxo livre',
        'description': 'Use quando a demanda nao se encaixar em nenhum dos fluxos guiados.',
        'icon': 'fa-circle-plus',
        'tone': 'neutral',
        'tipos': [valor for valor, _ in TipoEquipamento.choices if valor != TipoEquipamento.OUTRO],
    },
]

# Mantemos os templates internos para compatibilidade, mas exibimos apenas o fluxo principal no catalogo inicial.
REQUEST_TEMPLATE_CARDS = [
    REQUEST_TEMPLATE_DEFINITIONS[0],
]

for template in REQUEST_TEMPLATE_DEFINITIONS:
    tipos_labels = [_TIPO_EQUIPAMENTO_LABELS.get(tipo, tipo) for tipo in template['tipos']]
    template['tipos_labels'] = tipos_labels
    template['tipos_total'] = len(tipos_labels)
    resumo = ', '.join(tipos_labels[:4])
    if len(tipos_labels) > 4:
        resumo = f'{resumo} +{len(tipos_labels) - 4}'
    template['tipos_resumo'] = resumo

REQUEST_TEMPLATE_MAP = {template['key']: template for template in REQUEST_TEMPLATE_DEFINITIONS}


def get_request_template(key):
    return REQUEST_TEMPLATE_MAP.get((key or '').strip(), REQUEST_TEMPLATE_MAP['padrao'])


def _choices_for_template(key):
    template = get_request_template(key)
    tipos = template['tipos']
    choices = [(valor, label) for valor, label in TipoEquipamento.choices if valor in tipos]
    if TipoEquipamento.OUTRO not in tipos:
        choices.append((TipoEquipamento.OUTRO, dict(TipoEquipamento.choices)[TipoEquipamento.OUTRO]))
    return choices


class UsuarioDisplayChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        nome = obj.nome_completo
        if obj.matricula and obj.matricula != nome:
            return f'{nome} ({obj.matricula})'
        return nome


class BaseChamadoForm(forms.ModelForm):
    class Meta:
        model = Chamado
        fields = []

    def _style_fields(self):
        for field_name, field in self.fields.items():
            if field.widget.is_hidden:
                continue
            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                continue
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = 'form-control'
                field.widget.attrs.setdefault('rows', 4)
            else:
                field.widget.attrs['class'] = 'form-control'


class ChamadoCreateForm(BaseChamadoForm):
    template = forms.CharField(required=False, widget=forms.HiddenInput())
    destinatario = UsuarioDisplayChoiceField(
        label='Para quem e essa solicitacao?',
        queryset=Usuario.objects.none(),
        help_text='Informe o colaborador que vai receber o equipamento. Se estiver abrindo em nome dele, o chamado sera registrado como solicitado pelo gestor.',
    )
    equipamentos_solicitados = forms.MultipleChoiceField(
        label='Equipamentos solicitados',
        required=False,
        choices=TIPOS_EQUIPAMENTOS_SELECIONAVEIS,
        widget=forms.CheckboxSelectMultiple,
        help_text='Marque um ou mais equipamentos. Cada selecao vira um item do termo.',
    )
    outros_itens_solicitados = forms.CharField(
        label='Outros itens',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text='Use apenas se precisar complementar com itens que nao aparecem na lista acima. Uma linha por item.',
    )

    class Meta:
        model = Chamado
        fields = ['titulo', 'descricao', 'servico_realizado', 'destinatario', 'prioridade']
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        usuario_padrao = kwargs.pop('usuario_padrao', None)
        template = (kwargs.pop('template', '') or '').strip()
        super().__init__(*args, **kwargs)
        self.template = get_request_template(template)['key']
        self.template_info = get_request_template(self.template)
        self.fields['destinatario'].queryset = Usuario.objects.order_by('first_name', 'last_name', 'matricula')
        if usuario_padrao and not self.is_bound:
            self.fields['destinatario'].initial = usuario_padrao
        self.fields['template'].initial = self.template
        self.fields['servico_realizado'].help_text = 'Selecione o tipo de atendimento que este chamado representa.'
        if self.template != 'padrao' and not self.is_bound:
            self.fields['titulo'].initial = f"{self.template_info['title']} {self.template_info['highlight']}"
            self.fields['servico_realizado'].initial = 'entrega'
        self.fields['equipamentos_solicitados'].choices = _choices_for_template(self.template)
        self.fields['outros_itens_solicitados'].help_text = (
            f"{self.template_info['description']} Use apenas se precisar complementar com itens fora da lista."
        )
        self.order_fields(['template', 'titulo', 'descricao', 'servico_realizado', 'destinatario', 'equipamentos_solicitados', 'outros_itens_solicitados', 'prioridade'])
        self._style_fields()


class ChamadoUpdateForm(BaseChamadoForm):
    destinatario = UsuarioDisplayChoiceField(
        label='Colaborador / recebedor',
        queryset=Usuario.objects.none(),
        help_text='Selecione quem vai receber o equipamento neste chamado. Se o pedido for em nome do proprio colaborador, o sistema registra a solicitacao como propria.',
    )
    equipamentos_solicitados = forms.MultipleChoiceField(
        label='Equipamentos solicitados',
        required=False,
        choices=TIPOS_EQUIPAMENTOS_SELECIONAVEIS,
        widget=forms.CheckboxSelectMultiple,
        help_text='Marque um ou mais equipamentos. Cada selecao vira um item do termo.',
    )
    outros_itens_solicitados = forms.CharField(
        label='Outros itens',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text='Use apenas se precisar complementar com itens que nao aparecem na lista acima. Uma linha por item.',
    )

    class Meta:
        model = Chamado
        fields = [
            'titulo',
            'descricao',
            'servico_realizado',
            'destinatario',
            'prioridade',
            'responsavel',
            'status',
            'solucao',
        ]
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 4}),
            'solucao': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        usuario_padrao = kwargs.pop('usuario_padrao', None)
        super().__init__(*args, **kwargs)
        self.fields['destinatario'].queryset = Usuario.objects.order_by('first_name', 'last_name', 'matricula')
        if self.instance and self.instance.pk:
            itens_selecionados = []
            outros_linhas = []

            for item in self.instance.itens_solicitados.all().order_by('id'):
                if item.tipo_equipamento != TipoEquipamento.OUTRO:
                    itens_selecionados.append(item.tipo_equipamento)
                    continue

                linha = item.tipo_display
                if item.quantidade != 1:
                    linha = f'{linha}; {item.quantidade}'
                if item.observacao:
                    linha = f'{linha}; {item.observacao}'
                outros_linhas.append(linha)

            if not itens_selecionados and self.instance.tipo_equipamento_solicitado:
                itens_selecionados = [self.instance.tipo_equipamento_solicitado]

            self.fields['equipamentos_solicitados'].initial = itens_selecionados
            self.fields['outros_itens_solicitados'].initial = '\n'.join(outros_linhas)
            if not self.instance.destinatario_id and self.instance.solicitante_id:
                self.fields['destinatario'].initial = self.instance.solicitante
        elif usuario_padrao and not self.is_bound:
            self.fields['destinatario'].initial = usuario_padrao

        self.fields['responsavel'].queryset = Usuario.objects.filter(
            Q(nivel_acesso__in=[NivelAcesso.TECNICO, NivelAcesso.ANALISTA, NivelAcesso.ADMIN])
            | Q(is_superuser=True)
        ).order_by('first_name', 'last_name')
        self.fields['servico_realizado'].help_text = 'Escolha o tipo principal do atendimento realizado.'
        self.order_fields(['titulo', 'descricao', 'servico_realizado', 'destinatario', 'equipamentos_solicitados', 'outros_itens_solicitados', 'prioridade', 'responsavel', 'status', 'solucao'])
        self._style_fields()


class EntregaEquipamentoChamadoForm(forms.Form):
    itens_entrega = forms.CharField(required=False, widget=forms.HiddenInput())
    observacoes = forms.CharField(
        label='ObservaÃ§Ãµes da entrega',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    concluir_chamado = forms.BooleanField(
        label='Encerrar chamado apÃ³s concluir todos os itens',
        required=False,
        initial=True,
        help_text='Marque apenas quando cada item solicitado jÃ¡ tiver um equipamento selecionado.',
    )

    def __init__(self, *args, **kwargs):
        self.chamado = kwargs.pop('chamado', None)
        self.filtrado_por_tipo_solicitado = False
        self.equipamentos_compativeis_por_tipo = []
        self.equipamentos_compativeis_total = 0
        self.itens_solicitados_total = 0
        self.itens_selecionados_total = 0
        self.selecoes_por_item = {}
        super().__init__(*args, **kwargs)

        queryset = (
            Equipamento.objects.select_related('responsavel')
            .filter(status=StatusEquipamento.EM_ESTOQUE)
            .order_by('tipo', 'id_patrimonio')
        )

        tipos_solicitados = self._tipos_solicitados()
        if tipos_solicitados:
            filtrado = queryset.filter(tipo__in=tipos_solicitados)
            if filtrado.exists():
                queryset = filtrado

        self.itens_solicitados = self._itens_solicitados_entrega()
        self.itens_solicitados_total = sum(
            1 for item in self.itens_solicitados if getattr(item, 'tipo_equipamento', None) != TipoEquipamento.OUTRO
        )
        self.selecoes_por_item = self._carregar_selecoes_iniciais()
        self.equipamentos_compativeis_por_tipo = self._montar_grupos_compativeis(queryset)
        self.equipamentos_compativeis_total = sum(grupo['equipamentos_total'] for grupo in self.equipamentos_compativeis_por_tipo)
        self.itens_selecionados_total = sum(
            1
            for grupo in self.equipamentos_compativeis_por_tipo
            if grupo['requer_selecao'] and grupo['selecionado_id']
        )
        self.filtrado_por_tipo_solicitado = bool(tipos_solicitados and queryset.exists())

        self.fields['itens_entrega'].initial = (
            json.dumps({str(chave): valor for chave, valor in self.selecoes_por_item.items()})
            if self.selecoes_por_item
            else ''
        )
        self.fields['concluir_chamado'].help_text = 'Se desmarcado, a equipe pode salvar entregas parciais sem encerrar o chamado.'
        self._style_fields()

    def _style_fields(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                continue
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = 'form-control'
            else:
                field.widget.attrs.setdefault('class', 'form-control')

    def clean_itens_entrega(self):
        return self._normalizar_selecoes_raw(self.cleaned_data.get('itens_entrega', ''))

    def _normalizar_selecoes_raw(self, selecoes):
        if selecoes in (None, '', {}):
            return {}

        if isinstance(selecoes, str):
            try:
                selecoes = json.loads(selecoes)
            except json.JSONDecodeError as exc:
                raise ValidationError('SeleÃ§Ãµes de equipamentos invÃ¡lidas.') from exc

        if not isinstance(selecoes, dict):
            raise ValidationError('SeleÃ§Ãµes de equipamentos invÃ¡lidas.')

        normalizadas = {}
        for item_id_raw, equipamento_id_raw in selecoes.items():
            try:
                item_id = int(item_id_raw)
                equipamento_id = int(equipamento_id_raw)
            except (TypeError, ValueError) as exc:
                raise ValidationError('SeleÃ§Ãµes de equipamentos invÃ¡lidas.') from exc
            normalizadas[item_id] = equipamento_id

        return normalizadas

    def _carregar_selecoes_iniciais(self):
        if self.is_bound:
            raw = self.data.get(self.add_prefix('itens_entrega'), self.data.get('itens_entrega', ''))
            try:
                return self._normalizar_selecoes_raw(raw)
            except ValidationError:
                return {}

        selecoes = {}
        for item in self.itens_solicitados:
            if getattr(item, 'equipamento_entregue_id', None):
                selecoes[item.id] = item.equipamento_entregue_id
        return selecoes

    def _tipos_solicitados(self):
        if not self.chamado:
            return []

        tipos = list(self.chamado.itens_solicitados.values_list('tipo_equipamento', flat=True))
        if not tipos and self.chamado.tipo_equipamento_solicitado:
            tipos = [self.chamado.tipo_equipamento_solicitado]

        return list(dict.fromkeys(tipo for tipo in tipos if tipo and tipo != 'outro'))

    def _itens_solicitados_entrega(self):
        if not self.chamado:
            return []

        itens = list(
            self.chamado.itens_solicitados.select_related('equipamento_entregue', 'entregue_por').order_by('id')
        )
        if itens:
            return itens

        if self.chamado.tipo_equipamento_solicitado:
            return [
                SimpleNamespace(
                    id=0,
                    pk=0,
                    tipo_equipamento=self.chamado.tipo_equipamento_solicitado,
                    tipo_display=self.chamado.get_tipo_equipamento_solicitado_display(),
                    quantidade=1,
                    observacao='',
                    equipamento_entregue=None,
                )
            ]

        return []

    def _montar_grupos_compativeis(self, queryset):
        itens = self.itens_solicitados
        if not itens:
            return []

        equipamentos_por_tipo = defaultdict(list)
        equipamentos_por_id = {}
        for equipamento in queryset:
            equipamentos_por_tipo[equipamento.tipo].append(equipamento)
            equipamentos_por_id[equipamento.id] = equipamento

        grupos = []
        for indice, item in enumerate(itens, start=1):
            equipamentos = equipamentos_por_tipo.get(item.tipo_equipamento, [])
            selecionado_id = self.selecoes_por_item.get(item.id)
            selecionado = equipamentos_por_id.get(selecionado_id)
            if not selecionado and getattr(item, 'equipamento_entregue_id', None):
                selecionado = item.equipamento_entregue

            grupos.append(
                {
                    'ordem': indice,
                    'item_id': item.id,
                    'tipo': item.tipo_equipamento,
                    'label': getattr(item, 'tipo_display', '') or dict(
                        ChamadoItemSolicitado._meta.get_field('tipo_equipamento').choices
                    ).get(item.tipo_equipamento, item.tipo_equipamento),
                    'quantidade': getattr(item, 'quantidade', 1) or 1,
                    'observacao': getattr(item, 'observacao', ''),
                    'requer_selecao': item.tipo_equipamento != TipoEquipamento.OUTRO,
                    'equipamentos_total': len(equipamentos),
                    'equipamentos': equipamentos[:6],
                    'selecionado_id': selecionado.id if selecionado else None,
                    'selecionado_label': selecionado.id_patrimonio if selecionado else '',
                    'selecionado_tipo': selecionado.tipo_display if selecionado else '',
                    'selecionado_meta': ' '.join(
                        part for part in [selecionado.marca if selecionado else '', selecionado.modelo if selecionado else ''] if part
                    ).strip(),
                }
            )

        return grupos

