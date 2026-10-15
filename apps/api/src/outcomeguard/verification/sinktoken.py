"""Short-lived HMAC tokens that authorise one verification run to post to a form sink.

The token is attached by the verifier's browser route handler (the submitted page never
sees it). Without a valid token for a *running* run the sink stores nothing, so it is not
usable as a general-purpose public endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import time

TTL_SECONDS = 15 * 60


def issue(secret: str, run_id: str, ref: str, *, ttl: int = TTL_SECONDS) -> str:
    exp = int(time.time()) + ttl
    msg = f"{run_id}.{ref}.{exp}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}.{sig}"


def verify(secret: str, token: str, ref: str) -> str | None:
    """Return the run id if the token is valid for this sink ref, else None."""
    try:
        run_id, tok_ref, exp_s, sig = token.split(".")
        exp = int(exp_s)
    except ValueError:
        return None
    msg = f"{run_id}.{tok_ref}.{exp}"
    expected = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig) or tok_ref != ref or exp < time.time():
        return None
    return run_id
