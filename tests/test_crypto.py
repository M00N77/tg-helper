"""Проверка шифрования: encrypt → decrypt → исходная строка."""

import pytest
from cryptography.fernet import Fernet

from src.config import settings
from src.crypto import decrypt, encrypt


def test_encrypt_decrypt_roundtrip():
    original = "Hello, World! 123"
    encrypted = encrypt(original)
    assert encrypted != original
    decrypted = decrypt(encrypted)
    assert decrypted == original


def test_encrypt_decrypt_special_chars():
    original = "пароль!@#$%^&*()_+-=[]{}|;':\",./<>?`~ Привет Мир"
    encrypted = encrypt(original)
    decrypted = decrypt(encrypted)
    assert decrypted == original


def test_encrypt_empty_string():
    original = ""
    encrypted = encrypt(original)
    decrypted = decrypt(encrypted)
    assert decrypted == original


def test_encrypt_long_string():
    original = "a" * 10000
    encrypted = encrypt(original)
    decrypted = decrypt(encrypted)
    assert decrypted == original


def test_encrypt_api_hash():
    original = "0123456789abcdef0123456789abcdef"
    encrypted = encrypt(original)
    decrypted = decrypt(encrypted)
    assert decrypted == original


def test_encrypt_deterministic():
    original = "test"
    encrypted1 = encrypt(original)
    encrypted2 = encrypt(original)
    assert encrypted1 != encrypted2


def test_decrypt_invalid():
    with pytest.raises(ValueError, match="Не удалось расшифровать"):
        decrypt("invalid_base64==")


def test_decrypt_wrong_key():
    other_key = Fernet.generate_key().decode()
    other_fernet = Fernet(other_key.encode())
    encrypted = other_fernet.encrypt(b"secret").decode()
    with pytest.raises(ValueError, match="Не удалось расшифровать"):
        decrypt(encrypted)


def test_encryption_key_loaded():
    Fernet(settings.encryption_key.encode())
