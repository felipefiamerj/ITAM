from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def forwards(apps, schema_editor):
    Chamado = apps.get_model('chamados', 'Chamado')

    mapping = {
        'aberto': 'solicitado',
        'em_analise': 'triagem',
        'fila': 'solicitado',
        'em_atendimento': 'triagem',
        'aguardando_usuario': 'aguardando_aprovacao',
        'aguardando_atendimento': 'aguardando_aprovacao',
        'resolvido': 'encerrado',
        'fechado': 'encerrado',
        'encerrado': 'encerrado',
    }

    for status_antigo, fluxo_novo in mapping.items():
        Chamado.objects.filter(status=status_antigo).update(fluxo_etapa=fluxo_novo)


def backwards(apps, schema_editor):
    Chamado = apps.get_model('chamados', 'Chamado')

    mapping = {
        'solicitado': 'fila',
        'triagem': 'em_atendimento',
        'aguardando_estoque': 'aguardando_atendimento',
        'aguardando_aprovacao': 'aguardando_atendimento',
        'aprovado_para_retirada': 'em_atendimento',
        'em_separacao': 'em_atendimento',
        'pronto_para_entrega': 'em_atendimento',
        'encerrado': 'encerrado',
    }

    for fluxo_antigo, status_novo in mapping.items():
        Chamado.objects.filter(fluxo_etapa=fluxo_antigo).update(status=status_novo)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('chamados', '0007_chamado_destinatario'),
    ]

    operations = [
        migrations.AddField(
            model_name='chamado',
            name='fluxo_etapa',
            field=models.CharField(
                choices=[
                    ('solicitado', 'Solicitado'),
                    ('triagem', 'Em triagem'),
                    ('aguardando_estoque', 'Aguardando estoque'),
                    ('aguardando_aprovacao', 'Aguardando aprovação'),
                    ('aprovado_para_retirada', 'Aprovado para retirada'),
                    ('em_separacao', 'Em separação'),
                    ('pronto_para_entrega', 'Pronto para entrega'),
                    ('encerrado', 'Encerrado'),
                ],
                db_index=True,
                default='solicitado',
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name='chamado',
            name='aprovado_em',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='chamado',
            name='aprovado_por',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='chamados_aprovados',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
