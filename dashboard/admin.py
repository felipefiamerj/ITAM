from django.contrib import admin

from .models import BackupConfiguration, RestoreValidation, SystemHealthComponent, SystemHealthEvent


@admin.register(BackupConfiguration)
class BackupConfigurationAdmin(admin.ModelAdmin):
    list_display = ['retention_days', 'schedule_times', 'updated_by', 'updated_at']
    readonly_fields = ['updated_by', 'updated_at']


@admin.register(SystemHealthComponent)
class SystemHealthComponentAdmin(admin.ModelAdmin):
    list_display = ['name', 'status', 'summary', 'checked_at', 'status_changed_at']
    list_filter = ['status', 'source']
    search_fields = ['name', 'summary']
    readonly_fields = [
        'component_key',
        'name',
        'status',
        'summary',
        'details',
        'source',
        'checked_at',
        'status_changed_at',
        'last_notified_status',
        'updated_at',
    ]


@admin.register(SystemHealthEvent)
class SystemHealthEventAdmin(admin.ModelAdmin):
    list_display = ['component_name', 'previous_status', 'status', 'summary', 'occurred_at']
    list_filter = ['status', 'component_key']
    search_fields = ['component_name', 'summary']
    readonly_fields = [
        'component_key',
        'component_name',
        'previous_status',
        'status',
        'summary',
        'details',
        'occurred_at',
    ]


@admin.register(RestoreValidation)
class RestoreValidationAdmin(admin.ModelAdmin):
    list_display = ['tested_at', 'result', 'backup_manifest', 'recorded_by', 'created_at']
    list_filter = ['result']
    search_fields = ['backup_manifest', 'notes']
    readonly_fields = ['recorded_by', 'created_at']
