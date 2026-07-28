from django.contrib.auth.tokens import PasswordResetTokenGenerator


class AccountActivationTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user, timestamp):
        aprovado_em = ''
        if getattr(user, 'aprovado_em', None):
            aprovado_em = user.aprovado_em.replace(microsecond=0, tzinfo=None).isoformat()

        return (
            f'{user.pk}{user.password}{user.is_active}{user.solicitacao_pendente}'
            f'{user.exigir_troca_senha}{aprovado_em}{timestamp}'
        )


account_activation_token = AccountActivationTokenGenerator()


class PasswordRecoveryTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user, timestamp):
        updated_at = ''
        if getattr(user, 'updated_at', None):
            updated_at = user.updated_at.replace(microsecond=0, tzinfo=None).isoformat()

        return (
            f'{user.pk}{user.password}{user.is_active}{user.ativo}{user.solicitacao_pendente}'
            f'{user.exigir_troca_senha}{user.email}{updated_at}{timestamp}'
        )


password_recovery_token = PasswordRecoveryTokenGenerator()
