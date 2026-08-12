import json
from datetime import datetime

from django import forms

from .models import BackupConfiguration


class BackupConfigurationForm(forms.ModelForm):
    schedule_times_json = forms.CharField(widget=forms.HiddenInput())

    class Meta:
        model = BackupConfiguration
        fields = ['retention_days']
        widgets = {
            'retention_days': forms.NumberInput(
                attrs={
                    'class': 'form-control',
                    'min': 1,
                    'max': 30,
                    'inputmode': 'numeric',
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['retention_days'].min_value = 1
        self.fields['retention_days'].max_value = 30
        self.fields['retention_days'].widget.attrs.update({'min': 1, 'max': 30})
        initial_times = self.instance.schedule_times if self.instance and self.instance.pk else ['19:00']
        if self.is_bound:
            initial_times = self._parse_display_times(self.data.get('schedule_times_json')) or initial_times
        self.schedule_times_for_display = initial_times
        self.fields['schedule_times_json'].initial = json.dumps(initial_times)

    @staticmethod
    def _parse_display_times(raw_value):
        try:
            values = json.loads(raw_value or '[]')
        except (TypeError, json.JSONDecodeError):
            return []
        return values if isinstance(values, list) else []

    def clean_schedule_times_json(self):
        raw_value = self.cleaned_data['schedule_times_json']
        try:
            values = json.loads(raw_value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise forms.ValidationError('Os horarios informados sao invalidos.') from exc

        if not isinstance(values, list) or not values:
            raise forms.ValidationError('Informe pelo menos um horario para o backup.')

        normalized = []
        for value in values:
            try:
                parsed = datetime.strptime(str(value), '%H:%M')
            except ValueError as exc:
                raise forms.ValidationError(f'Horario invalido: {value}.') from exc
            normalized.append(parsed.strftime('%H:%M'))

        if len(normalized) != len(set(normalized)):
            raise forms.ValidationError('Nao repita o mesmo horario.')

        normalized.sort()
        self.cleaned_data['schedule_times'] = normalized
        self.schedule_times_for_display = normalized
        return json.dumps(normalized)

    def save(self, commit=True):
        configuration = super().save(commit=False)
        configuration.schedule_times = self.cleaned_data['schedule_times']
        if commit:
            configuration.save()
        return configuration
