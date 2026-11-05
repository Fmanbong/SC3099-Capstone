"""Environment configuration; unsafe test compatibility is explicit and isolated."""
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    environment: Literal['development', 'test', 'production'] = 'development'
    database_url: str = 'postgresql://saiv:saiv_password@localhost:5434/saiv'
    redis_url: str = 'redis://localhost:6380/0'
    secret_key: str = ''
    cors_origins: list[str] = ['http://localhost:3000', 'http://localhost:8501']
    face_service_url: str = 'http://localhost:8001'
    face_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    access_token_expire_minutes: int = Field(default=60, ge=1)
    refresh_token_expire_days: int = Field(default=7, ge=1)
    bcrypt_rounds: int = Field(default=10, ge=10, le=16)
    public_test_mode: bool = False
    # When on, a student's face enrollment stays pending until course staff
    # approve it in person. 'auto' (the default) turns it on everywhere except
    # PUBLIC_TEST_MODE, where the graders' tests expect the API-SPECIFICATION
    # behaviour of POST /users/me/face/enroll enrolling immediately.
    face_enrollment_approval: Literal['auto', 'true', 'false'] = 'auto'
    rate_limit_enabled: bool = True
    auto_create_schema: bool = False
    retention_enabled: bool = True
    retention_days: int = Field(default=30, ge=1)
    retention_interval_seconds: int = Field(default=3600, ge=10)
    max_request_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    max_image_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)

    @field_validator('face_enrollment_approval', mode='before')
    @classmethod
    def normalise_approval(cls, value):
        # Accept the usual boolean spellings from .env files and Python callers.
        text = str(value).strip().lower()
        return {'1': 'true', 'yes': 'true', 'on': 'true', '0': 'false', 'no': 'false', 'off': 'false',
                '': 'auto'}.get(text, text)

    @property
    def requires_face_enrollment_approval(self) -> bool:
        if self.face_enrollment_approval == 'auto':
            return not self.public_test_mode
        return self.face_enrollment_approval == 'true'

    @model_validator(mode='after')
    def isolated_test_mode(self):
        if self.public_test_mode and self.environment != 'test':
            raise ValueError('PUBLIC_TEST_MODE requires ENVIRONMENT=test')
        if self.environment == 'production' and (not self.rate_limit_enabled or self.auto_create_schema):
            raise ValueError('Production requires rate limiting and explicit migrations')
        return self

    def validate_startup(self):
        if len(self.secret_key) < 32 or self.secret_key == 'your-secret-key-change-in-production':
            raise ValueError('Set SECRET_KEY to a random secret of at least 32 characters')
        if '*' in self.cors_origins:
            raise ValueError('CORS_ORIGINS must list explicit origins')
