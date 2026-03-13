from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from .config import Settings, get_settings
from .models import UserContext

bearer_scheme = HTTPBearer(auto_error=False)


class EntraTokenValidator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if settings.entra_tenant_id:
            self.issuer = f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0"
            jwks_url = f"https://login.microsoftonline.com/{settings.entra_tenant_id}/discovery/v2.0/keys"
            self._jwk_client: Optional[PyJWKClient] = PyJWKClient(jwks_url)
        else:
            self.issuer = ""
            self._jwk_client = None

    def validate(self, token: str) -> UserContext:
        if not self._jwk_client or not self._settings.entra_client_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Entra token validation is not configured.",
            )

        signing_key = self._jwk_client.get_signing_key_from_jwt(token)
        audience = self._settings.entra_audience or self._settings.entra_client_id

        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=self.issuer,
        )

        user_id = claims.get("preferred_username") or claims.get("upn") or claims.get("oid")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token does not contain a valid user identifier.",
            )

        return UserContext(
            user_id=user_id,
            tenant_id=claims.get("tid"),
            display_name=claims.get("name"),
        )


def _get_validator(settings: Settings = Depends(get_settings)) -> EntraTokenValidator:
    return EntraTokenValidator(settings)


def get_user_context(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
    validator: EntraTokenValidator = Depends(_get_validator),
) -> UserContext:
    if settings.allow_anonymous:
        return UserContext(user_id="local-dev-user", display_name="Local Developer")

    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token.")

    try:
        return validator.validate(credentials.credentials)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {exc}",
        ) from exc
