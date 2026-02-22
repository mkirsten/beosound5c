"""
PKCE (Proof Key for Code Exchange) helpers for Spotify OAuth.

Uses the Authorization Code with PKCE flow — no client_secret needed.
Only requires a client_id (shipped with the app).

Uses blocking urllib.request intentionally — callers wrap in run_in_executor().
Keeps this module dependency-free (no aiohttp needed).

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


def refresh_access_token(client_id, refresh_token):
    """Refresh an access token using PKCE flow (client_id in body, no secret).

    Returns dict with 'access_token', optionally 'refresh_token' (rotated).
    Raises urllib.error.HTTPError on failure.
    """
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers=headers)

    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())
