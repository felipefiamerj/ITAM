from django.contrib import admin

from .models import BackupConfiguration


@admin.register(BackupConfiguration)
class BackupConfigurationAdmin(admin.ModelAdmin):
    list_display = ['retention_days', 'schedule_times', 'updated_by', 'updated_at']
    readonly_fields = ['updated_by', 'updated_at']
