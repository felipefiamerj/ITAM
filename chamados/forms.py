from django import forms
from django.db.models import Q

from accounts.models import NivelAcesso, Usuario
from equipamentos.models import Equipamento

from .models import Chamado, PrioridadeChamado, StatusChamado


class BaseChamadoForm(forms.ModelForm):
    class Meta:
        model = Chamado
        fields = []

    def _style_fields(self):
        for field_name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = 'form-control'
                field.widget.attrs.setdefault('rows', 4)
            else:
                field.widget.attrs['class'] = 'form-control'


class ChamadoCreateForm(BaseChamadoForm):
    class Meta:
        model = Chamado
        fields = ['titulo', 'descricao', 'equipamento', 'prioridade']
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['equipamento'].queryset = Equipamento.objects.select_related('responsavel').order_by('id_patrimonio')
        self._style_fields()


class ChamadoUpdateForm(BaseChamadoForm):
    class Meta:
        model = Chamado
        fields = ['titulo', 'descricao', 'equipamento', 'prioridade', 'responsavel', 'status', 'solucao']
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 4}),
            'solucao': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['equipamento'].queryset = Equipamento.objects.select_related('responsavel').order_by('id_patrimonio')
        self.fields['responsavel'].queryset = Usuario.objects.filter(
            Q(nivel_acesso__in=[NivelAcesso.TECNICO, NivelAcesso.ANALISTA, NivelAcesso.ADMIN])
            | Q(is_superuser=True)
        ).order_by('first_name', 'last_name')
        self._style_fields()
