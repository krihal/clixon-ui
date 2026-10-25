"""Run one device RPC on devices/groups (shared by the RPC page, the network map and guides). No NiceGUI."""

from __future__ import annotations

import asyncio

from .client import RestconfError


async def run_on_targets(client, req: dict, devs: list[str], grps: list[str]) -> list[tuple[str, dict]]:
    """Run one RPC (template or inline) on devices/groups, at most 4 at a time; returns (label, result) pairs."""
    jobs = [(d, {"device": d}) for d in devs] + [(f"group {g}", {"group": g}) for g in grps]
    gate = asyncio.Semaphore(4)  # the controller's web server answers 502 under heavy parallelism

    async def job(label: str, tgt: dict) -> tuple[str, dict]:
        async with gate:
            try:
                tid = await client.run_rpc(template=req.get("template"), inline=req.get("inline"),
                                           variables=req.get("variables"), **tgt)
                tr = await client.wait_transaction(tid, timeout=120, interval=0.5)
                if not tr or tr.get("result") != "SUCCESS":
                    return label, {"error": (tr or {}).get("reason", "failed").strip() or "failed", "tid": tid}
                return label, {"tid": tid, "replies": await client.rpc_result(tid), "tr": tr}
            except (RestconfError, TimeoutError) as e:
                return label, {"error": str(e)}

    return list(await asyncio.gather(*(job(lbl, t) for lbl, t in jobs)))
