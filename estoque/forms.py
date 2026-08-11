from django import forms

from chamados.models import Chamado, ChamadoItemSolicitado, StatusChamado
from equipamentos.models import Equipamento, StatusEquipamento

from .models import ReservaEstoque


class ReservaEstoqueForm(forms.ModelForm):
    class Meta:
        model = ReservaEstoque
        fields = ['chamado', 'item_solicitado', 'equipamento', 'observacoes']
        widgets = {
            'observacoes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        chamado_id = (
            self.data.get(self.add_prefix('chamado'))
            or self.initial.get('chamado')
            or getattr(self.instance, 'chamado_id', None)
        )

        self.fields['chamado'].empty_label = 'Selecione o chamado'
        self.fields['chamado'].queryset = (
            Chamado.objects.filter(status__in=[StatusChamado.FILA, StatusChamado.EM_ATENDIMENTO, StatusChamado.AGUARDANDO_ATENDIMENTO])
            .select_related('solicitante', 'destinatario', 'responsavel')
            .order_by('-updated_at', '-created_at')
        )

        if chamado_id:
            self.fields['item_solicitado'].queryset = ChamadoItemSolicitado.objects.filter(chamado_id=chamado_id).order_by('id')
        else:
            self.fields['item_solicitado'].queryset = ChamadoItemSolicitado.objects.none()
        self.fields['item_solicitado'].empty_label = 'Selecione um chamado acima'
        self.fields['item_solicitado'].help_text = 'Os itens aparecem quando você escolhe um chamado.'

        self.fields['equipamento'].queryset = Equipamento.objects.filter(status=StatusEquipamento.EM_ESTOQUE).order_by(
            'tipo',
            'id_patrimonio',
        )
        self.fields['equipamento'].to_field_name = 'id_patrimonio'
        self.fields['equipamento'].empty_label = 'Selecione um item para filtrar'
        self.fields['equipamento'].help_text = 'O sistema mostra os equipamentos disponíveis e, quando possível, já filtra pelo item selecionado.'

        self.fields['chamado'].widget.attrs.setdefault('class', 'form-select')
        self.fields['chamado'].widget.attrs['data-reserva-chamado'] = 'true'
        self.fields['item_solicitado'].widget.attrs.setdefault('class', 'form-select')
        self.fields['item_solicitado'].widget.attrs['data-reserva-item'] = 'true'
        self.fields['item_solicitado'].widget.attrs['data-placeholder-base'] = 'Selecione um chamado primeiro'
        self.fields['equipamento'].widget.attrs.setdefault('class', 'form-select')
        self.fields['equipamento'].widget.attrs['data-reserva-equipamento'] = 'true'
        self.fields['equipamento'].widget.attrs['data-placeholder-base'] = 'Selecione um chamado ou item'
        self.fields['observacoes'].widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned_data = super().clean()
        chamado = cleaned_data.get('chamado')
        item_solicitado = cleaned_data.get('item_solicitado')
        equipamento = cleaned_data.get('equipamento')

        if not chamado:
            return cleaned_data

        if item_solicitado and item_solicitado.chamado_id != chamado.pk:
            self.add_error('item_solicitado', 'Selecione um item que pertença ao chamado escolhido.')

        if equipamento and equipamento.status != StatusEquipamento.EM_ESTOQUE:
            self.add_error('equipamento', 'O equipamento precisa estar em estoque para ser reservado.')

        if item_solicitado and equipamento and item_solicitado.tipo_equipamento != equipamento.tipo:
            # Mantém a regra flexível para itens livres, mas avisa quando o tipo não bate.
            self.add_error('equipamento', 'O patrimônio selecionado não corresponde ao tipo solicitado.')

        return cleaned_data


class ReservaEstoqueLoteForm(forms.Form):
    chamado = forms.ModelChoiceField(queryset=Chamado.objects.none(), label='Chamado')
    equipamentos = forms.ModelMultipleChoiceField(
        queryset=Equipamento.objects.none(),
        label='Equipamentos em estoque',
        required=False,
        widget=forms.MultipleHiddenInput,
        help_text='Marque um ou mais ativos em estoque e revise a selecao antes de enviar.',
    )
    filtro_busca = forms.CharField(required=False, widget=forms.HiddenInput())
    observacoes = forms.CharField(
        label='Observações da reserva em lote',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['chamado'].queryset = (
            Chamado.objects.filter(status__in=[StatusChamado.FILA, StatusChamado.EM_ATENDIMENTO, StatusChamado.AGUARDANDO_ATENDIMENTO])
            .select_related('solicitante', 'destinatario', 'responsavel')
            .order_by('-updated_at', '-created_at')
        )
        self.fields['chamado'].empty_label = 'Selecione o chamado'

        self.fields['equipamentos'].queryset = Equipamento.objects.filter(status=StatusEquipamento.EM_ESTOQUE).order_by(
            'tipo',
            'id_patrimonio',
        )
        self.fields['equipamentos'].to_field_name = 'id_patrimonio'
        self.fields['equipamentos'].widget.attrs['data-reserva-lote-hidden'] = 'true'
        self.fields['filtro_busca'].widget.attrs['data-reserva-lote-filtro-input'] = 'true'
        self.fields["equipamentos"].help_text = "Os cards abaixo mostram apenas ativos disponiveis em estoque."

        self.fields['chamado'].widget.attrs.setdefault('class', 'form-select')
        self.fields['observacoes'].widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned_data = super().clean()
        equipamentos = cleaned_data.get('equipamentos')
        chamado = cleaned_data.get('chamado')

        if not chamado:
            return cleaned_data

        if not equipamentos:
            self.add_error('equipamentos', 'Selecione pelo menos um equipamento para reservar em lote.')

        return cleaned_data


class ReservaInteligenteForm(forms.Form):
    chamado = forms.ModelChoiceField(queryset=Chamado.objects.none(), label='Chamado')
    observacoes = forms.CharField(
        label='Observacoes',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['chamado'].queryset = (
            Chamado.objects.filter(status__in=[StatusChamado.FILA, StatusChamado.EM_ATENDIMENTO, StatusChamado.AGUARDANDO_ATENDIMENTO])
            .select_related('solicitante', 'destinatario', 'responsavel')
            .order_by('-updated_at', '-created_at')
        )
        self.fields['chamado'].empty_label = 'Selecione o chamado'
        self.fields['chamado'].widget.attrs.setdefault('class', 'form-select')
        self.fields['observacoes'].widget.attrs.setdefault('class', 'form-control')
