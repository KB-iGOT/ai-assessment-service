
import logging
import jwt
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, Security
from fastapi.security import APIKeyHeader
from functools import lru_cache
from .config import JWKS_URL, SSO_URL, SSO_REALM, REQUIRED_ROLE

logger = logging.getLogger(__name__)

# Security Scheme for Swagger UI (API Key Header)
api_key_header = APIKeyHeader(name="x-auth-token", auto_error=False)

class KeyManager:
    """
    Manages fetching and storing Public Keys for JWT verification.
    In a real scenario, this would fetch from a JWKS URL or a secure key store.
    For this implementation, we will assume keys are provided via configuration or environment variables.
    """
    def __init__(self):
        self._keys: Dict[str, Any] = {}
        self.jwks_url = JWKS_URL

    def get_public_key(self, key_id: str) -> Optional[Any]:
        # 1. Check Cache
        if key_id in self._keys:
            return self._keys[key_id]
        
        # 2. Refresh Cache
        try:
            self._refresh_keys()
        except Exception as e:
            logger.error(f"Failed to refresh JWKS: {e}")
            return None
            
        return self._keys.get(key_id)

    def _refresh_keys(self):
        import requests
        from jwt.algorithms import RSAAlgorithm
        import json
        
        try:
            logger.info(f"Fetching JWKS from {self.jwks_url}")
            resp = requests.get(self.jwks_url, timeout=10)
            resp.raise_for_status()
            jwks = resp.json()
            
            for key in jwks.get("keys", []):
                kid = key.get("kid")
                if kid:
                    # Convert JWK to RSA Public Key
                    public_key = RSAAlgorithm.from_jwk(json.dumps(key))
                    self._keys[kid] = public_key
            logger.info(f"Refreshed {len(self._keys)} keys from JWKS")
        except Exception as e:
            logger.error(f"Error fetching JWKS: {e}")
            raise e

def check_iss(iss: str) -> bool:
    if not SSO_URL or not SSO_REALM:
        logger.warning("SSO_URL or SSO_REALM not configured — skipping issuer check")
        return True
    realm_url = SSO_URL + "realms/" + SSO_REALM
    return realm_url.lower() == iss.lower()

def check_role(payload: Dict[str, Any]) -> bool:
    # Keycloak puts realm roles under realm_access.roles
    roles = payload.get("realm_access", {}).get("roles", [])
    return REQUIRED_ROLE in roles

# Singleton Instance
key_manager = KeyManager()

def get_key_manager() -> KeyManager:
    return key_manager

async def validate_token(token: str) -> Dict[str, Any]:
    """
    Validates the 'x-auth-token' (JWT).
    Logic mirrors the provided Java exemplar:
    1. Decode Header to get 'kid'.
    2. Fetch Public Key.
    3. Verify Signature (RSA256) and Expiry.
    """
    try:
        # 1. Decode Header (Unverified) to get Key ID
        header = jwt.get_unverified_header(token)
        key_id = header.get("kid")
        
        if not key_id:
            logger.warning("Token header missing 'kid'")
            raise Exception("Invalid token: 'kid' missing in header")

        # 2. Get Public Key from KeyManager
        public_key = key_manager.get_public_key(key_id)
        if not public_key:
            logger.warning(f"Public key not found for kid: {key_id}")
            raise Exception(f"Public key not found for keyId: {key_id}")

        # 3. Verify Signature and Decode
        # PyJWT handles expiration (exp) and signature verification automatically
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"require": ["exp", "sub"]}
        )

        if not check_iss(payload.get("iss", "")):
            logger.warning(f"Token issuer mismatch: {payload.get('iss')}")
            raise HTTPException(status_code=401, detail="Token issuer invalid")

        if not check_role(payload):
            logger.warning(f"Token missing required role: {REQUIRED_ROLE}")
            raise HTTPException(status_code=403, detail=f"Forbidden: role '{REQUIRED_ROLE}' required")

        return payload

    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        logger.warning("Auth token expired")
        raise HTTPException(status_code=401, detail="Expired auth token")
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid auth token: {e}")
        raise HTTPException(status_code=401, detail="Invalid auth token")
    except Exception as e:
        logger.error(f"Token validation failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")

async def get_current_user(request: Request, token: str = Security(api_key_header)) -> str:
    if not token:
        token = request.query_params.get("token")
        
    if not token:
        # TODO: Decide if we want to allow anonymous access (return None) or enforce auth.
        # For /api/v2/generate, we enforce it.
        raise HTTPException(status_code=401, detail="Missing authentication token")
    
    # DEV OVERRIDE (For Local Testing)
    import os
    if os.getenv("DISABLE_AUTH_VERIFICATION", "false").lower() == "true":
         logger.warning("AUTH BYPASSED (DISABLE_AUTH_VERIFICATION=true). Using dummy user.")
         return "test_user_id_123"

    payload = await validate_token(token)
    
    # Extract User ID (sub is standard, but some systems use preferred_username or oid)
    # Format typically: f:provider_id:user_uuid. We want the last part.
    raw_sub = payload.get("sub")
    if not raw_sub:
        raise HTTPException(status_code=401, detail="Token payload missing 'sub' (User ID)")
    
    user_id = raw_sub.split(":")[-1] if ":" in raw_sub else raw_sub
        
    return user_id
