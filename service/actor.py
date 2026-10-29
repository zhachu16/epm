"""Who is making a request.

The alpha has no sign-in: everyone is the anonymous actor and may do anything except change
the example project. When Supabase Auth arrives, ``get_actor`` reads the session from the
request instead, and the routes and tools that depend on it stay as they are.
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
