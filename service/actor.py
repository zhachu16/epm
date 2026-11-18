"""Who is making a request.

There are no accounts: whoever holds the service's access token (``auth.py``) is the
anonymous actor and may do anything except change the example project. If sign-in arrives,
``get_actor`` reads the session from the request instead, and the routes and tools that
depend on it stay as they are.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    id: str = "anonymous"
    authenticated: bool = False


ANONYMOUS = Actor()


def get_actor() -> Actor:
    return ANONYMOUS
