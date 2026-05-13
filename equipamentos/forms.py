from django import forms
from django.core.validators import FileExtensionValidator

from accounts.models import Usuario
from chamados.models import Chamado

from .models import Equipamento, MovimentacaoEquipamento, TipoEquipamento


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
        self._style_fields()

    def _style_fields(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = 'form-control'
            else:
                field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('tipo') == TipoEquipamento.OUTRO and not cleaned_data.get('tipo_outro'):
            self.add_error('tipo_outro', 'Informe o tipo do equipamento.')
        return cleaned_data


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
        ]
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 3}),
            'observacoes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['usuario_anterior'].queryset = Usuario.objects.filter(ativo=True).order_by('first_name', 'last_name')
        self.fields['usuario_novo'].queryset = Usuario.objects.filter(ativo=True).order_by('first_name', 'last_name')
        self.fields['chamado'].queryset = Chamado.objects.select_related('equipamento').order_by('-created_at')
        self._style_fields()

    def _style_fields(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = 'form-control'
            else:
                field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get('tipo')
        usuario_novo = cleaned_data.get('usuario_novo')

        if tipo in {'saida', 'transferencia', 'troca'} and not usuario_novo:
            self.add_error('usuario_novo', 'Informe o novo usuário para este tipo de movimentação.')
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
