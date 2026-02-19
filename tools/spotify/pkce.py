"""
PKCE (Proof Key for Code Exchange) helpers for Spotify OAuth.

Uses the Authorization Code with PKCE flow — no client_secret needed.
Only requires a client_id (shipped with the app).

Usage:
    from pkce import generate_code_verifier, generate_code_challenge
    from pkce import exchange_code, refresh_access_token

    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)
    # ... user completes auth flow ...
    tokens = exchange_code(code, client_id, verifier, redirect_uri)
    tokens = refresh_access_token(client_id, refresh_token)
"""

import base64
import hashlib
import json
import os
import urllib.parse
import urllib.request
import urllib.error

TOKEN_URL = "https://accounts.spotify.com/api/token"


def generate_code_verifier(length=128):
    """Generate a random code verifier string (43-128 chars, URL-safe)."""
    raw = os.urandom(length)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")[:length]


def generate_code_challenge(verifier):
    """Generate a code challenge from a verifier (S256 method)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_auth_url(client_id, redirect_uri, code_challenge, scopes):
    """Build the Spotify authorization URL for PKCE flow."""
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    })
    return f"https://accounts.spotify.com/authorize?{params}"


def exchange_code(code, client_id, code_verifier, redirect_uri):
    """Exchange an authorization code for access + refresh tokens.

    Returns dict with 'access_token', 'refresh_token', 'expires_in', etc.
    Raises urllib.error.HTTPError on failure.
    """
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def refresh_access_token(client_id, refresh_token, client_secret=None):
    """Refresh an access token.

    If client_secret is provided, uses the standard Authorization Code flow
    (Basic auth header). Otherwise uses PKCE flow (client_id in body).

    Returns dict with 'access_token', optionally 'refresh_token' (rotated).
    Raises urllib.error.HTTPError on failure.
    """
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    if client_secret:
        # Standard flow: Basic auth with client_id:client_secret
        creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"
    else:
        # PKCE flow: client_id in body, no secret
        body["client_id"] = client_id

    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers=headers)

    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())
