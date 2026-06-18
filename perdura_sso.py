"""
perdura_sso.py — enterprise track E2: SSO-issued bearer tokens.

E1's tokens (perdura_service.py) are static secrets minted at startup
(PERDURA_WORKER_TOKEN / PERDURA_OPERATOR_TOKEN) — fine for a single tenant,
not for an org with a directory. E2 adds a second, optional bearer-token
mode: the token is a JWT issued by the org's identity provider (Okta,
Auth0, Azure AD, ...), verified against the provider's published JWKS.
Role (worker | operator | admin) and tenant come from token claims, so
*authorization* stays the IdP's job — this module only verifies the
signature and reads what the IdP already vouched for.

No hand-rolled crypto: signature verification is PyJWT + `cryptography`
(an `enterprise` extra, `pip install -e ".[enterprise]"`). Static tokens
keep working when SSO env vars are unset — this is additive, never a
breaking change to E1 deployments.

Configuration (all via env, read by `SSOConfig.from_env`):
    PERDURA_OIDC_ISSUER     required — exact `iss` claim to accept
    PERDURA_OIDC_AUDIENCE   required — exact `aud` claim to accept
    PERDURA_OIDC_JWKS_URL   the IdP's JWKS endpoint (fetched + cached)
    PERDURA_OIDC_JWKS_FILE  a local JWKS JSON file instead of a URL
                            (air-gapped deployments, tests)
Exactly one of JWKS_URL / JWKS_FILE must be set alongside ISSUER+AUDIENCE.

Claims read (first match wins): role from `PERDURA_OIDC_ROLE_CLAIM`
(default "role"), tenant from `PERDURA_OIDC_TENANT_CLAIM` (default
"tenant"). Both must be present and the role must be one of ROLES, or the
token is rejected — there is no default role for an SSO-authenticated
caller, unlike a missing local config which simply has no such caller.
"""

import json
import os
import threading
import time
import urllib.request

import jwt

ROLES = ("worker", "operator", "admin")
DEFAULT_ROLE_CLAIM = "role"
DEFAULT_TENANT_CLAIM = "tenant"
JWKS_CACHE_TTL = 300  # seconds; IdPs rotate keys infrequently and publish overlap


class SSOConfig:
    def __init__(self, issuer, audience, jwks_url=None, jwks_file=None,
                 role_claim=DEFAULT_ROLE_CLAIM, tenant_claim=DEFAULT_TENANT_CLAIM,
                 algorithms=("RS256", "ES256")):
        if not (jwks_url or jwks_file):
            raise ValueError("SSOConfig needs jwks_url or jwks_file")
        self.issuer = issuer
        self.audience = audience
        self.jwks_url = jwks_url
        self.jwks_file = jwks_file
        self.role_claim = role_claim
        self.tenant_claim = tenant_claim
        self.algorithms = list(algorithms)
        self._jwks_cache = None
        self._jwks_cached_at = 0.0
        self._jwks_lock = threading.Lock()

    @classmethod
    def from_env(cls):
        """None when SSO isn't configured — the caller falls back to
        static bearer tokens (E1 behavior, unchanged)."""
        issuer = os.environ.get("PERDURA_OIDC_ISSUER")
        audience = os.environ.get("PERDURA_OIDC_AUDIENCE")
        jwks_url = os.environ.get("PERDURA_OIDC_JWKS_URL")
        jwks_file = os.environ.get("PERDURA_OIDC_JWKS_FILE")
        if not issuer or not audience or not (jwks_url or jwks_file):
            return None
        return cls(issuer=issuer, audience=audience,
                   jwks_url=jwks_url, jwks_file=jwks_file,
                   role_claim=os.environ.get("PERDURA_OIDC_ROLE_CLAIM",
                                              DEFAULT_ROLE_CLAIM),
                   tenant_claim=os.environ.get("PERDURA_OIDC_TENANT_CLAIM",
                                                DEFAULT_TENANT_CLAIM))

    # -- JWKS -----------------------------------------------------------
    def _jwks(self) -> dict:
        if self.jwks_file:
            with open(self.jwks_file, encoding="utf-8") as f:
                return json.load(f)
        now = time.monotonic()
        if self._jwks_cache is None or now - self._jwks_cached_at > JWKS_CACHE_TTL:
            with self._jwks_lock:
                now = time.monotonic()
                if self._jwks_cache is None \
                        or now - self._jwks_cached_at > JWKS_CACHE_TTL:
                    with urllib.request.urlopen(self.jwks_url, timeout=5) as resp:
                        self._jwks_cache = json.load(resp)
                    self._jwks_cached_at = now
        return self._jwks_cache

    def _public_key_for(self, token: str):
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        for raw_key in self._jwks().get("keys", []):
            if kid is None or raw_key.get("kid") == kid:
                return jwt.PyJWK(raw_key).key
        raise jwt.InvalidKeyError(f"no JWKS key matches kid={kid!r}")

    # -- verification -----------------------------------------------------
    def verify(self, token: str) -> dict:
        """Returns {"role": ..., "tenant": ...} for a valid token, or
        raises jwt.InvalidTokenError (signature, expiry, issuer, audience,
        or a missing/unrecognized role/tenant claim)."""
        key = self._public_key_for(token)
        claims = jwt.decode(token, key=key, algorithms=self.algorithms,
                            issuer=self.issuer, audience=self.audience)
        role = claims.get(self.role_claim)
        if role not in ROLES:
            raise jwt.InvalidTokenError(
                f"missing or unrecognized {self.role_claim!r} claim: {role!r}")
        tenant = claims.get(self.tenant_claim)
        if not tenant:
            raise jwt.InvalidTokenError(
                f"missing {self.tenant_claim!r} claim")
        return {"role": role, "tenant": str(tenant)}
