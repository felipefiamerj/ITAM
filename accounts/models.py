from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

from auditlog.registry import auditlog


class NivelAcesso(models.TextChoices):
    VIEWER = 'viewer', 'Solicitante'
    TECNICO = 'tecnico', 'Técnico'
    ANALISTA = 'analista', 'Analista'
    ADMIN = 'admin', 'Administrador'


class UsuarioManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, matricula, password=None, **extra_fields):
        if not matricula:
            raise ValueError('A matrícula é obrigatória.')

        matricula = str(matricula).strip()
        extra_fields.setdefault('username', matricula)

        email = extra_fields.get('email')
        if email:
            extra_fields['email'] = self.normalize_email(email)

        user = self.model(matricula=matricula, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, matricula, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        extra_fields.setdefault('ativo', True)
        extra_fields.setdefault('solicitacao_pendente', False)
        extra_fields.setdefault('exigir_troca_senha', False)
        return self._create_user(matricula, password, **extra_fields)

    def create_superuser(self, matricula, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('ativo', True)
        extra_fields.setdefault('solicitacao_pendente', False)
        extra_fields.setdefault('exigir_troca_senha', False)
        extra_fields.setdefault('nivel_acesso', NivelAcesso.ADMIN)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser precisa ter is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser precisa ter is_superuser=True.')

        return self._create_user(matricula, password, **extra_fields)


class Usuario(AbstractUser):
    matricula = models.CharField('Matrícula', max_length=20, unique=True, db_index=True)
    nivel_acesso = models.CharField(
        'Nível de acesso',
        max_length=20,
        choices=NivelAcesso.choices,
        default=NivelAcesso.VIEWER,
    )
    site = models.CharField('Site/Localidade', max_length=100, blank=True)
    setor = models.CharField('Setor', max_length=100, blank=True)
    andar_sala = models.CharField('Andar/Sala', max_length=50, blank=True)
    contato = models.CharField('Contato', max_length=30, blank=True)
    foto = models.ImageField('Foto', upload_to='usuarios/', blank=True, null=True)
    gestor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='subordinados',
        verbose_name='Gestor',
        null=True,
        blank=True,
    )
    ativo = models.BooleanField('Ativo', default=True)
    solicitacao_pendente = models.BooleanField('Solicitação pendente', default=False)
    exigir_troca_senha = models.BooleanField('Exigir troca de senha', default=False)
    aprovado_em = models.DateTimeField('Aprovado em', null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='aprovacoes_realizadas',
        verbose_name='Aprovado por',
        null=True,
        blank=True,
    )
    motivo_recusa = models.TextField('Motivo da recusa', blank=True)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    objects = UsuarioManager()

    USERNAME_FIELD = 'matricula'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'Usuário'
        verbose_name_plural = 'Usuários'
        ordering = ['first_name', 'last_name', 'matricula']

    def __str__(self):
        return self.nome_completo

    @property
    def nome_completo(self):
        nome = super().get_full_name().strip()
        return nome or self.matricula

    @property
    def status_acesso(self):
        if self.solicitacao_pendente:
            return 'Aguardando aprovação'
        if self.ativo:
            return 'Ativo'
        return 'Inativo'

    @property
    def is_admin(self):
        return self.is_superuser or self.nivel_acesso == NivelAcesso.ADMIN

    @property
    def is_analista(self):
        return self.is_superuser or self.nivel_acesso == NivelAcesso.ANALISTA

    @property
    def is_tecnico(self):
        return self.is_superuser or self.nivel_acesso == NivelAcesso.TECNICO

    @property
    def is_solicitante(self):
        return self.nivel_acesso == NivelAcesso.VIEWER

    @property
    def is_operacional(self):
        return self.is_admin or self.is_analista or self.is_tecnico

    @property
    def papel_fluxo(self):
        if self.is_admin:
            return 'Administrador'
        if self.is_analista:
            return 'Estoquista'
        if self.is_tecnico:
            return 'Técnico'
        return 'Solicitante'

    def save(self, *args, **kwargs):
        matricula = (self.matricula or self.username or '').strip()
        self.matricula = matricula
        self.username = matricula

        if self.email:
            self.email = self.__class__.objects.normalize_email(self.email)

        if self.solicitacao_pendente:
            self.ativo = False
            self.exigir_troca_senha = False
            self.aprovado_em = None
            self.aprovado_por = None

        self.is_active = bool(self.ativo and not self.solicitacao_pendente)
        super().save(*args, **kwargs)


auditlog.register(Usuario, exclude_fields=['password'])
