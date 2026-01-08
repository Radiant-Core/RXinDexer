from fastapi import APIRouter
from api.cache import cache, CACHE_TTL_LONG

import requests

router = APIRouter()


@router.get("/market/rxd", tags=["market"], summary="Get RXD market information")
def get_rxd_market():
    cache_key = "market:rxd"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = "https://api.coingecko.com/api/v3/coins/radiant"
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "false",
        "sparkline": "false",
    }

    try:
        resp = requests.get(url, params=params, timeout=3)
        resp.raise_for_status()
        data = resp.json() or {}

        market = (data.get("market_data") or {})
        current_price = (market.get("current_price") or {})
        market_cap = (market.get("market_cap") or {})
        total_volume = (market.get("total_volume") or {})

        total_supply = market.get("total_supply")
        max_supply = market.get("max_supply")
        circulating_supply = market.get("circulating_supply")

        denom = max_supply if max_supply else total_supply
        percent_mined = None
        try:
            if denom and circulating_supply is not None:
                denom_f = float(denom)
                circ_f = float(circulating_supply)
                if denom_f > 0:
                    percent_mined = (circ_f / denom_f) * 100.0
        except Exception:
            percent_mined = None

        result = {
            "symbol": "RXD",
            "source": "coingecko",
            "price_usd": current_price.get("usd"),
            "market_cap_usd": market_cap.get("usd"),
            "volume_24h_usd": total_volume.get("usd"),
            "volume_24h_rxd": (float(total_volume.get("usd")) / float(current_price.get("usd"))) if total_volume.get("usd") and current_price.get("usd") else None,
            "circulating_supply": circulating_supply,
            "total_supply": total_supply,
            "max_supply": max_supply,
            "percent_mined": percent_mined,
        }

        cache.set(cache_key, result, CACHE_TTL_LONG)  # Cache market data for 5 minutes
        return result
    except Exception as e:
        result = {
            "symbol": "RXD",
            "source": "coingecko",
            "error": str(e),
            "price_usd": None,
            "market_cap_usd": None,
            "volume_24h_usd": None,
            "volume_24h_rxd": None,
            "circulating_supply": None,
            "total_supply": None,
            "max_supply": None,
            "percent_mined": None,
        }
        cache.set(cache_key, result, 10)
        return result
