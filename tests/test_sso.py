"""SSO (E2) — JWT bearer tokens verified against a JWKS. No network: the
JWKS is a local file built from a key pair generated in-test, which is
exactly the air-gapped PERDURA_OIDC_JWKS_FILE deployment mode."""

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from perdura_sso import SSOConfig

ISSUER = "https://idp.example.com/"
AUDIENCE = "perdura"
KID = "test-key-1"


@pytest.fixture(scope="module")
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def jwks_file(tmp_path, keypair):
    _, public_key = keypair
    jwk = RSAAlgorithm(RSAAlgorithm.SHA256).to_jwk(public_key, as_dict=True)
    jwk["kid"] = KID
    jwk["use"] = "sig"
    path = tmp_path / "jwks.json"
    path.write_text(json.dumps({"keys": [jwk]}))
    return str(path)


@pytest.fixture
def cfg(jwks_file):
    return SSOConfig(issuer=ISSUER, audience=AUDIENCE, jwks_file=jwks_file)


def _token(keypair, **claims):
    private_key, _ = keypair
    payload = {"iss": ISSUER, "aud": AUDIENCE,
               "iat": time.time(), "exp": time.time() + 300,
               "role": "worker", "tenant": "acme", **claims}
    return jwt.encode(payload, private_key, algorithm="RS256",
                      headers={"kid": KID})


def test_valid_token_yields_role_and_tenant(cfg, keypair):
    tok = _token(keypair, role="operator", tenant="acme")
    assert cfg.verify(tok) == {"role": "operator", "tenant": "acme"}


def test_from_env_is_none_when_unconfigured(monkeypatch):
    for var in ("PERDURA_OIDC_ISSUER", "PERDURA_OIDC_AUDIENCE",
               "PERDURA_OIDC_JWKS_URL", "PERDURA_OIDC_JWKS_FILE"):
        monkeypatch.delenv(var, raising=False)
    assert SSOConfig.from_env() is None


def test_from_env_builds_config(monkeypatch, jwks_file):
    monkeypatch.setenv("PERDURA_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PERDURA_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PERDURA_OIDC_JWKS_FILE", jwks_file)
    monkeypatch.delenv("PERDURA_OIDC_JWKS_URL", raising=False)
    cfg = SSOConfig.from_env()
    assert cfg is not None and cfg.issuer == ISSUER


def test_expired_token_is_rejected(cfg, keypair):
    tok = _token(keypair, exp=time.time() - 10)
    with pytest.raises(jwt.InvalidTokenError):
        cfg.verify(tok)


def test_wrong_issuer_is_rejected(cfg, keypair):
    tok = _token(keypair, iss="https://not-the-idp.example.com/")
    with pytest.raises(jwt.InvalidTokenError):
        cfg.verify(tok)


def test_wrong_audience_is_rejected(cfg, keypair):
    tok = _token(keypair, aud="someone-else")
    with pytest.raises(jwt.InvalidTokenError):
        cfg.verify(tok)


def test_missing_role_claim_is_rejected(cfg, keypair):
    private_key, _ = keypair
    payload = {"iss": ISSUER, "aud": AUDIENCE, "iat": time.time(),
               "exp": time.time() + 300, "tenant": "acme"}
    tok = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(jwt.InvalidTokenError):
        cfg.verify(tok)


def test_unrecognized_role_is_rejected(cfg, keypair):
    tok = _token(keypair, role="superuser")
    with pytest.raises(jwt.InvalidTokenError):
        cfg.verify(tok)


def test_missing_tenant_claim_is_rejected(cfg, keypair):
    private_key, _ = keypair
    payload = {"iss": ISSUER, "aud": AUDIENCE, "iat": time.time(),
               "exp": time.time() + 300, "role": "worker"}
    tok = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(jwt.InvalidTokenError):
        cfg.verify(tok)


def test_unknown_kid_is_rejected(cfg, keypair):
    private_key, _ = keypair
    payload = {"iss": ISSUER, "aud": AUDIENCE, "iat": time.time(),
               "exp": time.time() + 300, "role": "worker", "tenant": "acme"}
    tok = jwt.encode(payload, private_key, algorithm="RS256",
                     headers={"kid": "some-other-key"})
    with pytest.raises(jwt.InvalidKeyError):
        cfg.verify(tok)


def test_token_signed_by_a_different_key_is_rejected(cfg):
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    payload = {"iss": ISSUER, "aud": AUDIENCE, "iat": time.time(),
               "exp": time.time() + 300, "role": "worker", "tenant": "acme"}
    tok = jwt.encode(payload, other_private_key, algorithm="RS256",
                     headers={"kid": KID})   # claims the legit kid, wrong key
    with pytest.raises(jwt.InvalidTokenError):
        cfg.verify(tok)
