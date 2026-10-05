"""Every demo scenario is automated, reproducible, and ends with a verifiable receipt where terminal."""

from __future__ import annotations

import pytest

from app.demo.scenarios import SCENARIOS

from .conftest import requires_db, run_scenario

pytestmark = requires_db

TERMINAL = {"COMMITTED_VERIFIED", "ABORTED", "COMPENSATED", "FAILED_TERMINAL"}


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_scenario_reproducible(client, name):
    for _ in range(2):  # run twice: fresh customer each time, same outcome
        r = await run_scenario(client, name)
        assert r["matches_expected"], r
        if r["state"] in TERMINAL:
            v = (await client.get(f"/api/v1/transactions/{r['root_id']}/receipt/verify")).json()
            assert v["valid"], v
