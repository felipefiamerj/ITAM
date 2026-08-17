import json
from datetime import datetime

from django import forms
from django.utils import timezone

from .models import BackupConfiguration, RestoreValidation


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


class RestoreValidationForm(forms.ModelForm):
    class Meta:
        model = RestoreValidation
        fields = ['tested_at', 'result', 'backup_manifest', 'notes']
        widgets = {
            'tested_at': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'result': forms.Select(attrs={'class': 'form-select'}),
            'backup_manifest': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, backup_sets=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tested_at'].input_formats = ['%Y-%m-%dT%H:%M']
        choices = [
            (backup.manifest_file, backup.created_at.strftime('%d/%m/%Y %H:%M')) for backup in backup_sets
        ]
        self.fields['backup_manifest'].widget.choices = choices
        self._allowed_manifests = {value for value, _label in choices}

    def clean_backup_manifest(self):
        manifest = self.cleaned_data['backup_manifest']
        if manifest not in self._allowed_manifests:
            raise forms.ValidationError('Selecione um ponto de restauracao disponivel.')
        return manifest

    def clean_tested_at(self):
        tested_at = self.cleaned_data['tested_at']
        if tested_at > timezone.now():
            raise forms.ValidationError('A data do teste nao pode estar no futuro.')
        return tested_at
