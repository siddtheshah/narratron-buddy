"""Serialize paid editor and canvas asset requests against an owner's balance."""

import asyncio

billing_locks: dict[int, asyncio.Lock] = {}

MAX_CONCURRENT_GENERATION_JOBS: int = 3
GENERATION_SLOT_TTL_SECONDS: float = 300.0
