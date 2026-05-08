"""Autenticação por matrícula."""

from django.contrib.auth.backends import ModelBackend

from accounts.models import Usuario


class MatriculaBackend(ModelBackend):
    """Permite login usando matrícula em vez do username padrão."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        matricula = (kwargs.get('matricula') or username or '').strip()
        if not matricula or not password:
            return None

        user = Usuario.objects.filter(matricula__iexact=matricula).first()
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def get_user(self, user_id):
        try:
            return Usuario.objects.get(pk=user_id)
        except Usuario.DoesNotExist:
            return None
