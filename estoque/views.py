from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from chamados.models import Chamado
from equipamentos.models import Equipamento, StatusEquipamento, TipoEquipamento
from itam.charting import build_choice_chart, build_top_chart

from .forms import ReservaEstoqueForm, ReservaEstoqueLoteForm, ReservaInteligenteForm
from .models import (
    ReservaEstoque,
    equipamentos_em_estoque,
    equipamentos_em_manutencao,
    lotes_recentes,
    reservas_ativas_queryset,
    resumo_por_localizacao,
    resumo_por_site,
    resumo_por_status,
    resumo_por_tipo,
)
from .services import (
    criar_reserva_estoque,
    criar_reservas_estoque_lote,
    criar_reservas_inteligentes,
    liberar_reserva_estoque,
    marcar_reserva_separada,
)


def _redirect_seguro_reserva(request):
    destino = (request.POST.get('next') or '').strip()
    if destino.startswith('/') and not destino.startswith('//'):
        return destino
    return reverse('estoque')


def _mensagem_reserva_inteligente(request, resultado):
    reservas = resultado['reservas']
    bloqueios = resultado['bloqueios']
    itens = ', '.join(
        f'{reserva.item_solicitado.tipo_display}: {reserva.equipamento.id_patrimonio}'
        for reserva in reservas
        if reserva.item_solicitado_id
    )
    mensagem = f'{len(reservas)} reserva(s) inteligente(s) criada(s).'
    if itens:
        mensagem = f'{mensagem} {itens}.'
    if bloqueios:
        messages.warning(request, f'{mensagem} {len(bloqueios)} item(ns) ficaram sem sugestao automatica.')
    else:
        messages.success(request, mensagem)


@login_required
def estoque_view(request):
    if not request.user.is_operacional:
        return redirect('chamados')

    reserva_form = ReservaEstoqueForm()
    reserva_lote_form = ReservaEstoqueLoteForm()
    reserva_inteligente_form = ReservaInteligenteForm()
    estoque_form_tab = 'inteligente'
    if request.method == 'POST':
        acao = (request.POST.get('acao') or '').strip()
        if acao == 'reservar':
            estoque_form_tab = 'manual'
            reserva_form = ReservaEstoqueForm(request.POST)
            if reserva_form.is_valid():
                try:
                    reserva = criar_reserva_estoque(
                        chamado=reserva_form.cleaned_data['chamado'],
                        item_solicitado=reserva_form.cleaned_data.get('item_solicitado'),
                        equipamento=reserva_form.cleaned_data['equipamento'],
                        solicitante=request.user,
                        observacoes=reserva_form.cleaned_data.get('observacoes', ''),
                    )
                except ValidationError as exc:
                    reserva_form.add_error(None, exc.messages[0] if exc.messages else 'Não foi possível reservar o equipamento.')
                else:
                    messages.success(
                        request,
                        f'Equipamento {reserva.equipamento.id_patrimonio} reservado para o chamado #{reserva.chamado.pk}.',
                    )
                    return redirect('estoque')
        elif acao == 'reservar_lote':
            estoque_form_tab = 'lote'
            reserva_lote_form = ReservaEstoqueLoteForm(request.POST)
            if reserva_lote_form.is_valid():
                try:
                    equipamentos = reserva_lote_form.cleaned_data['equipamentos']
                    reservas = criar_reservas_estoque_lote(
                        chamado=reserva_lote_form.cleaned_data['chamado'],
                        equipamentos=equipamentos,
                        solicitante=request.user,
                        observacoes=reserva_lote_form.cleaned_data.get('observacoes', ''),
                    )
                except ValidationError as exc:
                    reserva_lote_form.add_error(None, exc.messages[0] if exc.messages else 'Não foi possível reservar os equipamentos.')
                else:
                    messages.success(
                        request,
                        f'{len(reservas)} equipamento(s) reservados para o chamado #{reserva_lote_form.cleaned_data["chamado"].pk}.',
                    )
                    return redirect('estoque')
        elif acao in {'separar', 'liberar'}:
            reserva = get_object_or_404(ReservaEstoque, pk=request.POST.get('reserva_id'))
            try:
                if acao == 'separar':
                    marcar_reserva_separada(reserva=reserva, usuario=request.user)
                    messages.success(request, f'Reserva #{reserva.pk} marcada como separada.')
                else:
                    liberar_reserva_estoque(
                        reserva=reserva,
                        usuario=request.user,
                        motivo=request.POST.get('motivo', ''),
                    )
                    messages.success(request, f'Reserva #{reserva.pk} liberada e devolvida ao estoque.')
            except ValidationError as exc:
                messages.error(request, exc.messages[0] if exc.messages else 'Não foi possível atualizar a reserva.')
            return redirect('estoque')

    tipos_resumo = list(resumo_por_tipo())
    status_resumo = list(resumo_por_status())
    reservas_ativas = reservas_ativas_queryset()
    context = {
        'total_equipamentos': Equipamento.objects.count(),
        'total_sites': Equipamento.objects.exclude(site='').values('site').distinct().count(),
        'total_localizacoes': Equipamento.objects.exclude(site='').exclude(setor='').exclude(andar_sala='').values(
            'site', 'setor', 'andar_sala'
        ).distinct().count(),
        'total_em_estoque': equipamentos_em_estoque().count(),
        'total_reservados': Equipamento.objects.filter(status=StatusEquipamento.RESERVADO).count(),
        'total_em_uso': Equipamento.objects.filter(status=StatusEquipamento.EM_USO).count(),
        'total_descartados': Equipamento.objects.filter(status=StatusEquipamento.DESCARTADO).count(),
        'total_aguardando': Equipamento.objects.filter(status=StatusEquipamento.AGUARDANDO_APROVACAO).count(),
        'resumo_por_tipo': tipos_resumo,
        'resumo_por_status': status_resumo,
        'resumo_por_site': resumo_por_site(),
        'resumo_por_localizacao': resumo_por_localizacao(),
        'lotes': lotes_recentes(),
        'limite_alerta': settings.ITAM_ESTOQUE_ALERTA_MINIMO,
        'equipamentos_alerta': equipamentos_em_estoque().order_by('tipo', 'id_patrimonio')[:20],
        'equipamentos_alerta_total': Equipamento.objects.filter(score_saude__lt=70).count(),
        'equipamentos_total': Equipamento.objects.count(),
        'equipamentos_em_manutencao': equipamentos_em_manutencao().count(),
        'reservas_ativas': reservas_ativas[:20],
        'reservas_ativas_total': reservas_ativas.count(),
        'reserva_form': reserva_form,
        'reserva_lote_form': reserva_lote_form,
        'reserva_inteligente_form': reserva_inteligente_form,
        'estoque_form_tab': estoque_form_tab,
        'equipamentos_para_reserva_total': equipamentos_em_estoque().count(),
        'chamados_operacionais': Chamado.objects.select_related('solicitante', 'destinatario', 'responsavel').order_by(
            '-updated_at',
            '-created_at',
        )[:20],
        'estoque_charts': {
            'equipamentos_por_status': build_choice_chart(
                status_resumo,
                StatusEquipamento.choices,
                'status',
            ),
            'equipamentos_por_tipo': build_top_chart(
                tipos_resumo,
                'tipo',
                label_map=dict(TipoEquipamento.choices),
                limit=8,
            ),
        },
    }
    return render(request, 'estoque/index.html', context)


@login_required
@require_POST
def reserva_inteligente_view(request):
    if not request.user.is_operacional:
        messages.error(request, 'Acesso negado.')
        return redirect('chamados')

    form = ReservaInteligenteForm(request.POST)
    destino = _redirect_seguro_reserva(request)
    if not form.is_valid():
        messages.error(request, 'Selecione um chamado valido para a reserva inteligente.')
        return redirect(destino)

    try:
        resultado = criar_reservas_inteligentes(
            chamado=form.cleaned_data['chamado'],
            solicitante=request.user,
            observacoes=form.cleaned_data.get('observacoes', ''),
        )
    except ValidationError as exc:
        messages.warning(request, exc.messages[0] if exc.messages else 'Nao foi possivel criar reservas inteligentes.')
    else:
        _mensagem_reserva_inteligente(request, resultado)

    return redirect(destino)
