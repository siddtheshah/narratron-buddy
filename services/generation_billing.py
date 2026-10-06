"""Serialize paid editor and canvas asset requests against an owner's balance."""

import asyncio

billing_locks: dict[int, asyncio.Lock] = {}
