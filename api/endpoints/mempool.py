from fastapi import APIRouter, Query
from typing import Any, Dict, List, Literal, Optional
import time

from api.cache import cache
from api.utils import rpc_call

router = APIRouter()


def _get_raw_mempool_verbose() -> Dict[str, Any]:
    cached = cache.get("mempool:raw:verbose")
    if cached is not None:
        return cached
    raw = rpc_call("getrawmempool", [True])
    cache.set("mempool:raw:verbose", raw, 3)
    return raw


def _fee_rxd(entry: Dict[str, Any]) -> float:
    fees = entry.get("fees")
    if isinstance(fees, dict):
        for k in ("base", "modified", "ancestor", "descendant"):
            v = fees.get(k)
            if v is not None:
                return float(v)
    for k in ("fee", "modifiedfee", "ancestorfee", "descendantfee"):
        v = entry.get(k)
        if v is not None:
            return float(v)
    return 0.0


def _vsize(entry: Dict[str, Any]) -> int:
    v = entry.get("vsize")
    if v is None:
        v = entry.get("size")
    try:
        return int(v or 0)
    except Exception:
        return 0


def _feerate_atoms_per_vb(fee: float, vbytes: int) -> float:
    if vbytes <= 0:
        return 0.0
    return (fee * 1e8) / float(vbytes)


def _feerate_photons_per_vb(fee: float, vbytes: int) -> float:
    # Radiant uses 1e8 base units per RXD. We expose "photons/vB" as the human-facing
    # name for the same base-unit-per-vbyte calculation.
    return _feerate_atoms_per_vb(fee, vbytes)


@router.get("/mempool/info", tags=["mempool"], summary="Get mempool information")
def mempool_info() -> Dict[str, Any]:
    cached = cache.get("mempool:info")
    if cached is not None:
        return cached
    info = rpc_call("getmempoolinfo")
    cache.set("mempool:info", info, 5)
    return info


@router.get("/mempool/txs", tags=["mempool"], summary="Get mempool transactions")
def mempool_txs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: Literal["feerate_desc", "age_desc", "vsize_desc"] = "feerate_desc",
) -> Dict[str, Any]:
    raw = _get_raw_mempool_verbose()
    now = int(time.time())

    items: List[Dict[str, Any]] = []
    for txid, entry in raw.items():
        entry = entry or {}
        fee = _fee_rxd(entry)
        vbytes = _vsize(entry)
        t = int(entry.get("time") or now)
        feerate = _feerate_photons_per_vb(fee, vbytes)
        items.append(
            {
                "txid": txid,
                "fee_rxd": fee,
                "vsize": vbytes,
                # Backward compatibility: keep atoms/vB and also provide photons/vB
                "feerate_atoms_per_vb": feerate,
                "feerate_photons_per_vb": feerate,
                "time": t,
                "age_seconds": max(0, now - t),
                "depends": entry.get("depends") or [],
            }
        )

    if sort == "age_desc":
        items.sort(key=lambda x: x.get("age_seconds", 0), reverse=True)
    elif sort == "vsize_desc":
        items.sort(key=lambda x: x.get("vsize", 0), reverse=True)
    else:
        items.sort(key=lambda x: x.get("feerate_photons_per_vb", x.get("feerate_atoms_per_vb", 0.0)), reverse=True)

    total = len(items)
    page = items[offset : offset + limit]
    for it in page:
        it["depends_count"] = len(it.get("depends") or [])
        it.pop("depends", None)

    return {"total": total, "limit": limit, "offset": offset, "sort": sort, "txs": page}


@router.get("/mempool/blocks", tags=["mempool"], summary="Get projected mempool blocks")
def mempool_blocks(
    blocks: int = Query(6, ge=1, le=12),
    block_vsize: int = Query(1_000_000, ge=100_000, le=4_000_000),
) -> Dict[str, Any]:
    raw = _get_raw_mempool_verbose()

    txs: List[Dict[str, Any]] = []
    for txid, entry in raw.items():
        entry = entry or {}
        fee = _fee_rxd(entry)
        vbytes = _vsize(entry)
        fr = _feerate_atoms_per_vb(fee, vbytes)
        txs.append({"txid": txid, "fee_rxd": fee, "vsize": vbytes, "feerate": fr})

    txs.sort(key=lambda x: x.get("feerate", 0.0), reverse=True)

    def tier(fr: float) -> str:
        if fr >= 100:
            return "100+"
        if fr >= 50:
            return "50-99"
        if fr >= 20:
            return "20-49"
        if fr >= 5:
            return "5-19"
        return "<5"

    tiers_order = ["100+", "50-99", "20-49", "5-19", "<5"]

    out_blocks: List[Dict[str, Any]] = []
    idx = 0
    for bi in range(blocks):
        used = 0
        chosen: List[Dict[str, Any]] = []
        while idx < len(txs) and used + int(txs[idx].get("vsize") or 0) <= block_vsize:
            chosen.append(txs[idx])
            used += int(txs[idx].get("vsize") or 0)
            idx += 1

        if not chosen:
            out_blocks.append(
                {
                    "index": bi,
                    "tx_count": 0,
                    "vsize": 0,
                    "total_fees_rxd": 0.0,
                    "feerate_min": 0.0,
                    "feerate_median": 0.0,
                    "feerate_max": 0.0,
                    "tiers": [{"tier": t, "vsize": 0} for t in tiers_order],
                }
            )
            continue

        feerates = sorted([float(t["feerate"]) for t in chosen], reverse=False)
        median = feerates[len(feerates) // 2]
        totals = sum(float(t["fee_rxd"]) for t in chosen)
        tier_v: Dict[str, int] = {t: 0 for t in tiers_order}
        for t in chosen:
            tier_v[tier(float(t["feerate"]))] += int(t.get("vsize") or 0)

        out_blocks.append(
            {
                "index": bi,
                "tx_count": len(chosen),
                "vsize": used,
                "total_fees_rxd": totals,
                "feerate_min": float(min(feerates)),
                "feerate_median": float(median),
                "feerate_max": float(max(feerates)),
                "tiers": [{"tier": t, "vsize": tier_v[t]} for t in tiers_order],
            }
        )

    return {"block_vsize": block_vsize, "blocks": out_blocks}
