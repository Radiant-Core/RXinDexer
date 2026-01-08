from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from sqlalchemy import text

from api.cache import cache, CACHE_TTL_SHORT, CACHE_TTL_MEDIUM
from api.dependencies import get_db, get_current_authenticated_user
from api.utils import rpc_call
from database.models import Block, Transaction, TokenVolumeDaily

router = APIRouter()


def _bits_to_target(bits: int) -> int:
    exp = bits >> 24
    mant = bits & 0xFFFFFF
    if exp <= 3:
        return mant >> (8 * (3 - exp))
    return mant << (8 * (exp - 3))


def _bits_to_difficulty(bits: int) -> float:
    # Bitcoin-style difficulty where diff=1 corresponds to 0x1d00ffff
    diff1_target = _bits_to_target(0x1D00FFFF)
    target = _bits_to_target(bits)
    if target <= 0:
        return 0.0
    return float(diff1_target) / float(target)


def _format_chainwork(chainwork_hex: str) -> int | None:
    if not chainwork_hex:
        return None
    try:
        return int(str(chainwork_hex), 16)
    except Exception:
        return None


@router.get("/stats/overview", tags=["stats"], summary="Explorer overview statistics")
def stats_overview(db: Session = Depends(get_db),
    current_user = Depends(get_current_authenticated_user)):
    cache_key = "stats:overview"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    now = datetime.utcnow()

    try:
        blockchain_info = {}
        try:
            blockchain_info = rpc_call("getblockchaininfo")
        except Exception:
            blockchain_info = {}

        best_height = None
        try:
            best_height = int(blockchain_info.get("blocks") or 0) if isinstance(blockchain_info, dict) else 0
        except Exception:
            best_height = None

        if not best_height:
            try:
                best_height = int(db.query(func.max(Block.height)).scalar() or 0)
            except Exception:
                db.rollback()
                best_height = 0

        # Mining
        hashrate_1h = None
        hashrate_1d = None
        hashrate_1w = None
        try:
            hashrate_1h = float(rpc_call("getnetworkhashps", [12]))
            hashrate_1d = float(rpc_call("getnetworkhashps", [288]))
            hashrate_1w = float(rpc_call("getnetworkhashps", [2016]))
        except Exception:
            pass

        difficulty = None
        try:
            difficulty = float(blockchain_info.get("difficulty")) if blockchain_info.get("difficulty") is not None else float(rpc_call("getdifficulty"))
        except Exception:
            difficulty = None

        chainwork_int = _format_chainwork(blockchain_info.get("chainwork"))

        chain_rewrite_days = None
        try:
            if chainwork_int is not None and hashrate_1d and hashrate_1d > 0:
                chain_rewrite_days = (float(chainwork_int) / float(hashrate_1d)) / 86400.0
        except Exception:
            chain_rewrite_days = None

        last_difficulty = None
        difficulty_change_pct = None
        try:
            # Bitcoin-style difficulty adjustment interval.
            # Radiant inherited 2016-block intervals, but even if it differs, this is a reasonable proxy.
            interval = 2016
            last_retarget_height = best_height - (best_height % interval) - 1
            if last_retarget_height >= 0:
                last_hash = rpc_call("getblockhash", [int(last_retarget_height)])
                last_header = rpc_call("getblockheader", [last_hash])
                bits = int(last_header.get("bits"), 16) if isinstance(last_header.get("bits"), str) else int(last_header.get("bits") or 0)
                last_difficulty = _bits_to_difficulty(bits)
                if difficulty is not None and last_difficulty and last_difficulty > 0:
                    difficulty_change_pct = ((difficulty - last_difficulty) / last_difficulty) * 100.0
        except Exception:
            last_difficulty = None
            difficulty_change_pct = None

        unconfirmed_txns = None
        try:
            mempool_info = rpc_call("getmempoolinfo")
            unconfirmed_txns = int(mempool_info.get("size") or 0)
        except Exception:
            unconfirmed_txns = None

        # Blockchain
        total_txs = None
        try:
            # Approximate count: avoid full table scans on large datasets.
            # reltuples is updated by autovacuum/analyze, so it's an estimate.
            row = db.execute(
                text(
                    """
                    SELECT reltuples::bigint AS estimate
                    FROM pg_class
                    WHERE oid = 'public.transactions'::regclass
                    """
                )
            ).fetchone()
            if row and row[0] is not None:
                # Postgres can return -1 when statistics are missing.
                estimate = int(row[0])
                total_txs = estimate if estimate > 0 else None
            else:
                total_txs = None
        except Exception:
            db.rollback()
            total_txs = None

        if total_txs is None:
            try:
                # Use max(id) as a fast, index-backed approximation (avoids COUNT(*)).
                total_txs = int(db.query(func.max(Transaction.id)).scalar() or 0)
            except Exception:
                db.rollback()
                total_txs = None

        data_size_bytes = None
        try:
            data_size_bytes = int(blockchain_info.get("size_on_disk") or 0)
        except Exception:
            data_size_bytes = None

        utxo_txouts = None
        utxo_disk_size_bytes = None
        total_supply = None
        # gettxoutsetinfo is very slow (scans entire UTXO set), cache separately with longer TTL
        utxo_cache_key = "stats:utxo_set_info"
        utxo_cached = cache.get(utxo_cache_key)
        if utxo_cached is not None:
            utxo_txouts = utxo_cached.get("txouts")
            utxo_disk_size_bytes = utxo_cached.get("disk_size")
            total_supply = utxo_cached.get("total_amount")
        else:
            try:
                # Never let /stats/overview hang on a full UTXO set scan.
                # If the cache is cold and the node is slow, just return nulls and try again later.
                utxo = rpc_call("gettxoutsetinfo", timeout=20)
                utxo_txouts = int(utxo.get("txouts") or 0)
                utxo_disk_size_bytes = int(utxo.get("disk_size") or 0)
                total_supply = float(utxo.get("total_amount")) if utxo.get("total_amount") is not None else None
                cache.set(utxo_cache_key, {
                    "txouts": utxo_txouts,
                    "disk_size": utxo_disk_size_bytes,
                    "total_amount": total_supply,
                }, 300)  # Cache for 5 minutes
            except Exception:
                pass

        # Financials
        cex_volume_24h_rxd = None
        cex_volume_24h_usd = None
        dex_volume_24h_rxd = None
        dex_trades_24h = None
        dex_active_tokens_24h = None
        try:
            # CEX volume (CoinGecko)
            market = cache.get("market:rxd")
            if not isinstance(market, dict):
                market = None

            if market is None or (market.get("volume_24h_rxd") is None and market.get("volume_24h_usd") is None):
                try:
                    # Ensure stats are correct even if /market/rxd hasn't been called yet.
                    from api.endpoints.market import get_rxd_market

                    market = get_rxd_market()
                except Exception:
                    market = market

            if isinstance(market, dict):
                cex_volume_24h_rxd = market.get("volume_24h_rxd")
                cex_volume_24h_usd = market.get("volume_24h_usd")
        except Exception:
            pass

        try:
            # token_volume_daily is day-truncated, so a rolling 24h filter can drop most of yesterday.
            # Use today + yesterday as a reasonable approximation for "24h" DEX volume.
            start_24h = datetime.combine((now - timedelta(days=1)).date(), datetime.min.time())
            row = (
                db.query(
                    func.sum(TokenVolumeDaily.volume_rxd).label("volume_rxd"),
                    func.sum(TokenVolumeDaily.trade_count).label("trades"),
                    func.count(func.distinct(TokenVolumeDaily.token_id)).label("active_tokens"),
                )
                .filter(TokenVolumeDaily.date >= start_24h)
                .one_or_none()
            )

            if row is not None:
                dex_volume_24h_rxd = str(row.volume_rxd) if row.volume_rxd is not None else None
                dex_trades_24h = int(row.trades) if row.trades is not None else None
                dex_active_tokens_24h = int(row.active_tokens) if row.active_tokens is not None else None
        except Exception:
            db.rollback()
            pass

        result = {
            "updated_at": int(now.timestamp()),
            "mining": {
                "hashrate_1h": hashrate_1h,
                "hashrate_1d": hashrate_1d,
                "hashrate_1w": hashrate_1w,
                "chain_rewrite_days": chain_rewrite_days,
                "last_difficulty": last_difficulty,
                "difficulty": difficulty,
                "difficulty_change_pct": difficulty_change_pct,
                "unconfirmed_txns": unconfirmed_txns,
            },
            "blockchain": {
                "total_txs": total_txs,
                "data_size_bytes": data_size_bytes,
                "chainwork": str(chainwork_int) if chainwork_int is not None else None,
                "utxo_txouts": utxo_txouts,
                "utxo_disk_size_bytes": utxo_disk_size_bytes,
                "total_supply": total_supply,
                "height": best_height,
            },
            "financials": {
                "cex_volume_24h_rxd": cex_volume_24h_rxd,
                "cex_volume_24h_usd": cex_volume_24h_usd,
                "dex_volume_24h_rxd": dex_volume_24h_rxd,
                "dex_trades_24h": dex_trades_24h,
                "dex_active_tokens_24h": dex_active_tokens_24h,
            },
        }

        ttl = CACHE_TTL_MEDIUM
        if cex_volume_24h_rxd is None and cex_volume_24h_usd is None:
            ttl = CACHE_TTL_SHORT
        cache.set(cache_key, result, ttl)
        return result
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {
            "updated_at": int(now.timestamp()),
            "mining": {
                "hashrate_1h": None,
                "hashrate_1d": None,
                "hashrate_1w": None,
                "chain_rewrite_days": None,
                "last_difficulty": None,
                "difficulty": None,
                "difficulty_change_pct": None,
                "unconfirmed_txns": None,
            },
            "blockchain": {
                "total_txs": None,
                "data_size_bytes": None,
                "chainwork": None,
                "utxo_txouts": None,
                "utxo_disk_size_bytes": None,
                "total_supply": None,
                "height": 0,
            },
            "financials": {
                "cex_volume_24h_rxd": None,
                "cex_volume_24h_usd": None,
                "dex_volume_24h_rxd": None,
                "dex_trades_24h": None,
                "dex_active_tokens_24h": None,
            },
        }
