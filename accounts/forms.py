import secrets
import string

from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q
from django.utils import timezone

from .models import NivelAcesso, Usuario


def _apply_bootstrap(form):
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.Select):
            widget.attrs.setdefault('class', 'form-select')
        elif isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault('class', 'form-check-input')
        elif isinstance(widget, forms.ClearableFileInput):
            widget.attrs.setdefault('class', 'form-control')
        else:
            widget.attrs.setdefault('class', 'form-control')


def _split_nome_completo(nome_completo):
    partes = [parte for parte in (nome_completo or '').strip().split() if parte]
    if not partes:
        return '', ''
    if len(partes) == 1:
        return partes[0], ''
    return partes[0], ' '.join(partes[1:])


def _gerar_senha_temporaria(tamanho=14):
    alfabeto = string.ascii_letters + string.digits + '@#%*+-_'
    return ''.join(secrets.choice(alfabeto) for _ in range(tamanho))


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label='Identificador',
        max_length=20,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Matricula, RG ou CPF',
                'autocomplete': 'username',
                'autofocus': True,
            }
        ),
    )
    password = forms.CharField(
        label='Senha',
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Digite sua senha',
                'autocomplete': 'current-password',
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap(self)


class SolicitacaoAcessoForm(forms.Form):
    nome_completo = forms.CharField(
        label='Nome completo',
        max_length=150,
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Nome do solicitante',
                'autocomplete': 'name',
            }
        ),
        help_text='Seu nome completo para identificação no sistema.',
    )
    matricula = forms.CharField(
        label='Identificador',
        max_length=20,
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Matricula, RG ou CPF do solicitante',
                'autocomplete': 'off',
            }
        ),
        help_text='Informe a matrícula, RG ou CPF para manter o cadastro permanente.',
    )
    email = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(
            attrs={
                'placeholder': 'seu.email@empresa.com',
                'autocomplete': 'email',
            }
        ),
        help_text='Você receberá um link de primeiro acesso neste email após aprovação.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap(self)

    def clean_nome_completo(self):
        nome_completo = ' '.join((self.cleaned_data.get('nome_completo') or '').split())
        if len(nome_completo) < 3:
            raise forms.ValidationError('Informe o nome completo do solicitante.')
        return nome_completo

    def clean_matricula(self):
        matricula = (self.cleaned_data.get('matricula') or '').strip()
        if not matricula:
            raise forms.ValidationError('Informe a matrícula, RG ou CPF.')
        if Usuario.objects.filter(matricula__iexact=matricula).exists():
            raise forms.ValidationError('Já existe um cadastro com este identificador.')
        return matricula

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip()
        if email and Usuario.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Este email já está registrado no sistema.')
        return email

    def save(self, commit=True):
        nome_completo = self.cleaned_data['nome_completo']
        matricula = self.cleaned_data['matricula']
        email = self.cleaned_data.get('email', '')
        primeiro_nome, resto_nome = _split_nome_completo(nome_completo)
        usuario = Usuario(
            matricula=matricula,
            username=matricula,
            email=email,
            first_name=primeiro_nome,
            last_name=resto_nome,
            nivel_acesso=NivelAcesso.VIEWER,
            ativo=False,
            solicitacao_pendente=True,
            exigir_troca_senha=False,
        )
        usuario.set_unusable_password()
        if commit:
            usuario.save()
        return usuario


class UsuarioCreateForm(forms.ModelForm):
    password1 = forms.CharField(
        label='Senha temporária',
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Crie uma senha forte',
                'autocomplete': 'new-password',
            }
        ),
    )
    password2 = forms.CharField(
        label='Confirmar senha',
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Repita a senha',
                'autocomplete': 'new-password',
            }
        ),
    )

    class Meta:
        model = Usuario
        labels = {
            'matricula': 'Identificador',
        }
        fields = [
            'matricula',
            'first_name',
            'last_name',
            'email',
            'contato',
            'nivel_acesso',
            'site',
            'setor',
            'andar_sala',
            'gestor',
            'foto',
            'ativo',
            'solicitacao_pendente',
            'exigir_troca_senha',
        ]
        widgets = {
            'matricula': forms.TextInput(attrs={'placeholder': 'Matrícula, RG ou CPF'}),
            'first_name': forms.TextInput(attrs={'placeholder': 'Nome'}),
            'last_name': forms.TextInput(attrs={'placeholder': 'Sobrenome'}),
            'email': forms.EmailInput(attrs={'placeholder': 'nome@empresa.com'}),
            'contato': forms.TextInput(attrs={'placeholder': 'Telefone ou ramal'}),
            'site': forms.TextInput(attrs={'placeholder': 'Unidade / site'}),
            'setor': forms.TextInput(attrs={'placeholder': 'Setor'}),
            'andar_sala': forms.TextInput(attrs={'placeholder': 'Andar / sala'}),
            'foto': forms.ClearableFileInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['gestor'].queryset = Usuario.objects.filter(ativo=True).order_by('first_name', 'last_name')
        self.fields['gestor'].empty_label = 'Sem gestor'
        self.fields['matricula'].help_text = 'Informe a matrícula, RG ou CPF do usuário.'
        self.fields['nivel_acesso'].help_text = 'Solicitante é o cadastro mínimo. Técnico e analista operam o sistema.'
        self.fields['solicitacao_pendente'].help_text = 'Use para cadastrar alguém ainda aguardando aprovação.'
        self.fields['exigir_troca_senha'].help_text = 'Força a troca da senha no primeiro acesso.'
        if not self.is_bound:
            self.fields['ativo'].initial = True
            self.fields['exigir_troca_senha'].initial = True
        _apply_bootstrap(self)

    def clean_matricula(self):
        matricula = (self.cleaned_data.get('matricula') or '').strip()
        if not matricula:
            raise forms.ValidationError('Informe a matrícula, RG ou CPF.')
        if Usuario.objects.filter(matricula__iexact=matricula).exists():
            raise forms.ValidationError('Já existe um usuário com este identificador.')
        return matricula

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')

        if password1 or password2:
            if not password1 or not password2:
                raise forms.ValidationError('Informe e confirme a senha temporária.')
            if password1 != password2:
                raise forms.ValidationError('As senhas não coincidem.')
            validate_password(password1, user=self.instance)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.matricula = self.cleaned_data['matricula']
        user.username = self.cleaned_data['matricula']
        user.set_password(self.cleaned_data['password1'])
        user.solicitacao_pendente = self.cleaned_data.get('solicitacao_pendente', False)
        user.exigir_troca_senha = self.cleaned_data.get('exigir_troca_senha', False)
        if commit:
            user.save()
            self.save_m2m()
        return user


class UsuarioApprovalForm(forms.ModelForm):
    password1 = forms.CharField(
        label='Senha temporária',
        required=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Deixe em branco para gerar automaticamente',
                'autocomplete': 'new-password',
            }
        ),
    )
    password2 = forms.CharField(
        label='Confirmar senha',
        required=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Repita a senha temporária',
                'autocomplete': 'new-password',
            }
        ),
    )

    class Meta:
        model = Usuario
        labels = {
            'matricula': 'Identificador',
        }
        fields = ['nivel_acesso', 'exigir_troca_senha']
        widgets = {
            'nivel_acesso': forms.Select(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['nivel_acesso'].help_text = 'Defina o perfil operacional antes de liberar a conta.'
        self.fields['exigir_troca_senha'].help_text = 'Recomendado para o primeiro acesso ou quando o fluxo de segurança exigir troca de senha.'
        if not self.is_bound:
            self.fields['exigir_troca_senha'].initial = True
        _apply_bootstrap(self)

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')

        if password1 or password2:
            if not password1 or not password2:
                raise forms.ValidationError('Informe e confirme a senha temporária.')
            if password1 != password2:
                raise forms.ValidationError('As senhas não coincidem.')
            validate_password(password1, user=self.instance)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        senha_temporaria = self.cleaned_data.get('password1') or _gerar_senha_temporaria()

        user.set_password(senha_temporaria)
        user.ativo = True
        user.solicitacao_pendente = False
        user.exigir_troca_senha = self.cleaned_data.get('exigir_troca_senha', True)
        user.motivo_recusa = ''
        user.aprovado_em = timezone.now()

        if commit:
            user.save()
            self.save_m2m()

        self.generated_password = senha_temporaria
        self.generated_password_is_auto = not bool(self.cleaned_data.get('password1'))
        return user


class UsuarioUpdateForm(forms.ModelForm):
    class Meta:
        model = Usuario
        labels = {
            'matricula': 'Identificador',
        }
        fields = [
            'matricula',
            'first_name',
            'last_name',
            'email',
            'contato',
            'nivel_acesso',
            'site',
            'setor',
            'andar_sala',
            'gestor',
            'foto',
            'ativo',
            'solicitacao_pendente',
            'exigir_troca_senha',
        ]
        widgets = {
            'matricula': forms.TextInput(attrs={'placeholder': 'Matrícula, RG ou CPF'}),
            'first_name': forms.TextInput(attrs={'placeholder': 'Nome'}),
            'last_name': forms.TextInput(attrs={'placeholder': 'Sobrenome'}),
            'email': forms.EmailInput(attrs={'placeholder': 'nome@empresa.com'}),
            'contato': forms.TextInput(attrs={'placeholder': 'Telefone ou ramal'}),
            'site': forms.TextInput(attrs={'placeholder': 'Unidade / site'}),
            'setor': forms.TextInput(attrs={'placeholder': 'Setor'}),
            'andar_sala': forms.TextInput(attrs={'placeholder': 'Andar / sala'}),
            'foto': forms.ClearableFileInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        gestor_qs = Usuario.objects.filter(ativo=True).order_by('first_name', 'last_name')
        if self.instance.pk:
            gestor_qs = gestor_qs.exclude(pk=self.instance.pk)
        self.fields['gestor'].queryset = gestor_qs
        self.fields['gestor'].empty_label = 'Sem gestor'
        self.fields['matricula'].help_text = 'Informe a matrícula, RG ou CPF do usuário.'
        self.fields['nivel_acesso'].help_text = 'Define o nível de acesso do usuário dentro do sistema.'
        self.fields['solicitacao_pendente'].help_text = 'Marque apenas para devolvê-lo à fila de aprovação.'
        self.fields['exigir_troca_senha'].help_text = 'Força a troca de senha no próximo login.'
        _apply_bootstrap(self)

    def clean_matricula(self):
        matricula = (self.cleaned_data.get('matricula') or '').strip()
        if not matricula:
            raise forms.ValidationError('Informe a matrícula, RG ou CPF.')
        if Usuario.objects.filter(matricula__iexact=matricula).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Já existe um usuário com este identificador.')
        return matricula

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data['matricula']
        if commit:
            user.save()
            self.save_m2m()
        return user


class TrocaSenhaInicialForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['new_password1'].label = 'Nova senha'
        self.fields['new_password2'].label = 'Confirmar nova senha'
        self.fields['new_password1'].help_text = 'Escolha uma senha forte e fácil de lembrar apenas para você.'
        self.fields['new_password2'].help_text = 'Repita a senha para confirmar.'
        _apply_bootstrap(self)


class SolicitacaoRecuperacaoSenhaForm(forms.Form):
    identificador = forms.CharField(
        label='Identificador ou e-mail',
        max_length=150,
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Matrícula, RG, CPF ou e-mail',
                'autocomplete': 'username',
                'autofocus': True,
            }
        ),
        help_text='Se o cadastro existir e tiver e-mail, você receberá um link seguro para redefinir a senha.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap(self)

    def clean_identificador(self):
        identificador = ' '.join((self.cleaned_data.get('identificador') or '').split())
        if len(identificador) < 3:
            raise forms.ValidationError('Informe um identificador válido.')
        return identificador

    def get_usuario(self):
        identificador = self.cleaned_data.get('identificador', '')
        return (
            Usuario.objects.filter(Q(matricula__iexact=identificador) | Q(email__iexact=identificador))
            .filter(ativo=True, solicitacao_pendente=False)
            .first()
        )


