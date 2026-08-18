import base64
import hashlib
import io
import secrets
from datetime import datetime

import pyotp
import qrcode
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

RECOVERY_CODE_COUNT = 8
RECOVERY_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
TRUSTED_DEVICE_COOKIE = 'itam_2fa_trusted'
TRUSTED_DEVICE_SALT = 'accounts.two_factor.trusted_device'


def two_factor_required_for(user):
    return bool(getattr(settings, 'ITAM_ADMIN_2FA_REQUIRED', True) and getattr(user, 'is_admin', False))


def trusted_device_max_age():
    days = max(1, min(365, int(getattr(settings, 'ITAM_TWO_FACTOR_TRUST_DAYS', 30))))
    return days * 24 * 60 * 60


def _trusted_device_payload(user):
    confirmed_at = user.two_factor_confirmed_at.isoformat() if user.two_factor_confirmed_at else ''
    return {
        'user_id': user.pk,
        'password': user.password,
        'confirmed_at': confirmed_at,
    }


def trusted_device_cookie_value(user):
    return signing.dumps(_trusted_device_payload(user), salt=TRUSTED_DEVICE_SALT, compress=True)


def is_trusted_device(request, user):
    if not user.two_factor_enabled:
        return False
    cookie_value = request.COOKIES.get(TRUSTED_DEVICE_COOKIE)
    if not cookie_value:
        return False
    try:
        payload = signing.loads(cookie_value, salt=TRUSTED_DEVICE_SALT, max_age=trusted_device_max_age())
    except signing.BadSignature:
        return False
    expected = _trusted_device_payload(user)
    return (
        payload.get('user_id') == expected['user_id']
        and constant_time_compare(str(payload.get('password', '')), expected['password'])
        and constant_time_compare(str(payload.get('confirmed_at', '')), expected['confirmed_at'])
    )


def trust_device_response(response, user):
    response.set_cookie(
        TRUSTED_DEVICE_COOKIE,
        trusted_device_cookie_value(user),
        max_age=trusted_device_max_age(),
        httponly=True,
        secure=getattr(settings, 'SESSION_COOKIE_SECURE', False),
        samesite=getattr(settings, 'SESSION_COOKIE_SAMESITE', 'Lax') or 'Lax',
    )
    return response


def forget_trusted_device_response(response):
    response.delete_cookie(
        TRUSTED_DEVICE_COOKIE,
        samesite=getattr(settings, 'SESSION_COOKIE_SAMESITE', 'Lax') or 'Lax',
    )
    return response


def generate_secret():
    return pyotp.random_base32()


def _fernet():
    configured = (getattr(settings, 'ITAM_TWO_FACTOR_ENCRYPTION_KEY', '') or '').strip()
    material = configured or settings.SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode('utf-8')).digest())
    return Fernet(key)


def encrypt_secret(secret):
    return _fernet().encrypt(secret.encode('ascii')).decode('ascii')


def decrypt_secret(encrypted_secret):
    if not encrypted_secret:
        return ''
    try:
        return _fernet().decrypt(encrypted_secret.encode('ascii')).decode('ascii')
    except (InvalidToken, ValueError, TypeError):
        return ''


def provisioning_uri(secret, user):
    issuer = (getattr(settings, 'ITAM_TWO_FACTOR_ISSUER', '') or settings.APP_NAME).strip()
    account_name = user.email or user.matricula
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def qr_code_data_url(uri):
    image = qrcode.make(uri)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def _normalize_totp(token):
    return ''.join(character for character in str(token or '') if character.isdigit())


def matching_totp_counter(secret, token, *, at=None):
    normalized = _normalize_totp(token)
    if len(normalized) != 6 or not secret:
        return None

    totp = pyotp.TOTP(secret)
    instant = at or timezone.now()
    if isinstance(instant, datetime) and timezone.is_naive(instant):
        instant = timezone.make_aware(instant, timezone.get_current_timezone())
    current_counter = totp.timecode(instant)
    valid_window = max(0, min(2, int(getattr(settings, 'ITAM_TWO_FACTOR_VALID_WINDOW', 1))))
    for offset in range(-valid_window, valid_window + 1):
        counter = current_counter + offset
        if counter < 0:
            continue
        if constant_time_compare(totp.generate_otp(counter), normalized):
            return counter
    return None


def verify_user_totp(user, token, *, consume=True):
    if not user.two_factor_enabled:
        return False

    def verify(candidate):
        secret = decrypt_secret(candidate.two_factor_secret_encrypted)
        counter = matching_totp_counter(secret, token)
        if counter is None:
            return False
        if consume and candidate.two_factor_last_counter is not None and counter <= candidate.two_factor_last_counter:
            return False
        if consume:
            candidate.two_factor_last_counter = counter
            candidate.save(update_fields=['two_factor_last_counter', 'updated_at'])
            user.two_factor_last_counter = counter
        return True

    if not consume or not user.pk:
        return verify(user)
    with transaction.atomic():
        locked_user = user._meta.model.objects.select_for_update().filter(pk=user.pk).first()
        return bool(locked_user and verify(locked_user))


def _normalize_recovery_code(code):
    return ''.join(character for character in str(code or '').upper() if character.isalnum())


def _recovery_hash(code):
    normalized = _normalize_recovery_code(code)
    return salted_hmac(
        'accounts.two_factor.recovery',
        normalized,
        secret=settings.SECRET_KEY,
        algorithm='sha256',
    ).hexdigest()


def generate_recovery_codes():
    codes = []
    while len(codes) < RECOVERY_CODE_COUNT:
        raw = ''.join(secrets.choice(RECOVERY_ALPHABET) for _ in range(12))
        code = f'{raw[:4]}-{raw[4:8]}-{raw[8:]}'
        if code not in codes:
            codes.append(code)
    return codes


def recovery_code_hashes(codes):
    return [_recovery_hash(code) for code in codes]


def consume_recovery_code(user, code):
    candidate = _recovery_hash(code)

    def consume(locked_user):
        hashes = list(locked_user.two_factor_recovery_hashes or [])
        for index, stored_hash in enumerate(hashes):
            if constant_time_compare(stored_hash, candidate):
                hashes.pop(index)
                locked_user.two_factor_recovery_hashes = hashes
                locked_user.save(update_fields=['two_factor_recovery_hashes', 'updated_at'])
                user.two_factor_recovery_hashes = hashes
                return True
        return False

    if not user.pk:
        return False
    with transaction.atomic():
        locked_user = user._meta.model.objects.select_for_update().filter(pk=user.pk).first()
        return bool(locked_user and consume(locked_user))


def verify_two_factor_credential(user, credential, *, consume=True):
    if verify_user_totp(user, credential, consume=consume):
        return 'totp'
    if consume and consume_recovery_code(user, credential):
        return 'recovery'
    return None


def activate_two_factor(user, secret, counter):
    recovery_codes = generate_recovery_codes()
    user.two_factor_secret_encrypted = encrypt_secret(secret)
    user.two_factor_enabled = True
    user.two_factor_recovery_hashes = recovery_code_hashes(recovery_codes)
    user.two_factor_last_counter = counter
    user.two_factor_confirmed_at = timezone.now()
    user.save(
        update_fields=[
            'two_factor_secret_encrypted',
            'two_factor_enabled',
            'two_factor_recovery_hashes',
            'two_factor_last_counter',
            'two_factor_confirmed_at',
            'updated_at',
        ]
    )
    return recovery_codes


def regenerate_recovery_codes(user):
    recovery_codes = generate_recovery_codes()
    user.two_factor_recovery_hashes = recovery_code_hashes(recovery_codes)
    user.save(update_fields=['two_factor_recovery_hashes', 'updated_at'])
    return recovery_codes
