"""
Market Data Index for RXinDexer

This module provides market data tracking including:
- Trade history and aggregation
- OHLCV candle generation
- Volume tracking
- Price calculations from swap data

Based on the RXinDexer PostgreSQL implementation.
"""

import struct
import time
from typing import Optional, Dict, Any, List, Tuple
from collections import defaultdict
from dataclasses import dataclass, field

from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash


# Database key prefixes for market data
class MarketDBKeys:
    TRADE = b'MT'        # MT + token_ref + timestamp -> trade info
    OHLCV = b'MO'        # MO + token_ref + interval + candle_time -> OHLCV
    VOLUME = b'MV'       # MV + token_ref + day -> daily volume
    PRICE = b'MP'        # MP + token_ref -> latest price
    SWAP = b'MS'         # MS + swap_id -> swap advertisement


@dataclass
class Trade:
    """Represents a single trade."""
    token_ref: bytes
    txid: bytes
    height: int
    timestamp: int
    amount: int              # Token amount
    price_rxd: int           # Price in photons (1e8)
    side: str                # 'buy' or 'sell'
    maker: bytes             # Maker scripthash
    taker: bytes             # Taker scripthash
    
    def to_bytes(self) -> bytes:
        """Serialize trade to bytes."""
        side_byte = 0 if self.side == 'buy' else 1
        return (
            self.token_ref +
            self.txid +
            struct.pack('<I', self.height) +
            struct.pack('<Q', self.timestamp) +
            struct.pack('<Q', self.amount) +
            struct.pack('<Q', self.price_rxd) +
            struct.pack('<B', side_byte) +
            self.maker +
            self.taker
        )
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'Trade':
        """Deserialize trade from bytes."""
        pos = 0
        token_ref = data[pos:pos+36]; pos += 36
        txid = data[pos:pos+32]; pos += 32
        height = struct.unpack('<I', data[pos:pos+4])[0]; pos += 4
        timestamp = struct.unpack('<Q', data[pos:pos+8])[0]; pos += 8
        amount = struct.unpack('<Q', data[pos:pos+8])[0]; pos += 8
        price_rxd = struct.unpack('<Q', data[pos:pos+8])[0]; pos += 8
        side_byte = data[pos]; pos += 1
        side = 'buy' if side_byte == 0 else 'sell'
        maker = data[pos:pos+32]; pos += 32
        taker = data[pos:pos+32]; pos += 32
        
        return cls(
            token_ref=token_ref,
            txid=txid,
            height=height,
            timestamp=timestamp,
            amount=amount,
            price_rxd=price_rxd,
            side=side,
            maker=maker,
            taker=taker,
        )


@dataclass
class OHLCV:
    """OHLCV candle data."""
    open: int
    high: int
    low: int
    close: int
    volume: int
    trade_count: int
    timestamp: int
    
    def to_bytes(self) -> bytes:
        return struct.pack('<QQQQQQQ',
            self.open, self.high, self.low, self.close,
            self.volume, self.trade_count, self.timestamp
        )
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'OHLCV':
        values = struct.unpack('<QQQQQQQ', data[:56])
        return cls(
            open=values[0],
            high=values[1],
            low=values[2],
            close=values[3],
            volume=values[4],
            trade_count=values[5],
            timestamp=values[6],
        )


@dataclass
class SwapAdvertisement:
    """On-chain swap advertisement (RSWP)."""
    swap_id: bytes           # Unique swap identifier
    token_ref: bytes         # Token being sold
    amount: int              # Amount offered
    price_rxd: int           # Price in photons
    seller: bytes            # Seller scripthash
    expiry: int              # Expiry timestamp
    status: str              # 'open', 'filled', 'cancelled', 'expired'
    txid: bytes              # Creating transaction
    height: int              # Block height
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'swap_id': self.swap_id.hex(),
            'token_ref': self.token_ref.hex(),
            'amount': self.amount,
            'price_rxd': self.price_rxd,
            'seller': self.seller.hex(),
            'expiry': self.expiry,
            'status': self.status,
            'txid': hash_to_hex_str(self.txid),
            'height': self.height,
        }


# Candle intervals in seconds
INTERVALS = {
    '1m': 60,
    '5m': 300,
    '15m': 900,
    '1h': 3600,
    '4h': 14400,
    '1d': 86400,
    '1w': 604800,
}


class MarketIndex:
    """
    Market data indexer for token trading activity.
    
    Tracks trades, calculates OHLCV candles, and maintains volume statistics.
    """
    
    def __init__(self, db):
        self.db = db
        self.logger = None
        
        # In-memory caches
        self.price_cache: Dict[bytes, int] = {}  # token_ref -> latest price
        self.volume_cache: Dict[bytes, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.swap_cache: Dict[bytes, SwapAdvertisement] = {}
    
    def set_logger(self, logger):
        self.logger = logger
    
    # =========================================================================
    # TRADE TRACKING
    # =========================================================================
    
    async def record_trade(self, trade: Trade):
        """Record a new trade."""
        # Store trade
        trade_key = (
            MarketDBKeys.TRADE +
            trade.token_ref +
            struct.pack('>Q', trade.timestamp)
        )
        await self.db.put(trade_key, trade.to_bytes())
        
        # Update price cache
        self.price_cache[trade.token_ref] = trade.price_rxd
        
        # Update volume cache (daily)
        day = trade.timestamp // 86400 * 86400
        day_key = f"{day}"
        self.volume_cache[trade.token_ref][day_key] += trade.amount
        
        # Update OHLCV candles
        await self._update_ohlcv(trade)
        
        if self.logger:
            self.logger.info(f"Recorded trade: {trade.amount} @ {trade.price_rxd} RXD")
    
    async def get_trades(self, token_ref: bytes, limit: int = 50, 
                         start_time: int = None, end_time: int = None) -> List[Dict]:
        """Get trades for a token with optional time filtering."""
        trades = []
        prefix = MarketDBKeys.TRADE + token_ref
        
        async for key, value in self.db.iterator(prefix=prefix, reverse=True):
            try:
                trade = Trade.from_bytes(value)
                
                # Time filtering
                if start_time and trade.timestamp < start_time:
                    continue
                if end_time and trade.timestamp > end_time:
                    break
                
                trades.append({
                    'txid': hash_to_hex_str(trade.txid),
                    'height': trade.height,
                    'timestamp': trade.timestamp,
                    'amount': trade.amount,
                    'price_rxd': trade.price_rxd,
                    'side': trade.side,
                })
                
                if len(trades) >= limit:
                    break
            except Exception:
                continue
        
        return trades
    
    # =========================================================================
    # OHLCV CANDLES
    # =========================================================================
    
    async def _update_ohlcv(self, trade: Trade):
        """Update OHLCV candles for all intervals."""
        for interval_name, interval_seconds in INTERVALS.items():
            candle_time = (trade.timestamp // interval_seconds) * interval_seconds
            
            ohlcv_key = (
                MarketDBKeys.OHLCV +
                trade.token_ref +
                interval_name.encode() +
                struct.pack('>Q', candle_time)
            )
            
            # Get existing candle or create new
            existing = await self.db.get(ohlcv_key)
            
            if existing:
                candle = OHLCV.from_bytes(existing)
                candle.high = max(candle.high, trade.price_rxd)
                candle.low = min(candle.low, trade.price_rxd)
                candle.close = trade.price_rxd
                candle.volume += trade.amount
                candle.trade_count += 1
            else:
                candle = OHLCV(
                    open=trade.price_rxd,
                    high=trade.price_rxd,
                    low=trade.price_rxd,
                    close=trade.price_rxd,
                    volume=trade.amount,
                    trade_count=1,
                    timestamp=candle_time,
                )
            
            await self.db.put(ohlcv_key, candle.to_bytes())
    
    async def get_ohlcv(self, token_ref: bytes, interval: str = '1d', 
                        limit: int = 100) -> List[Dict]:
        """Get OHLCV candles for a token."""
        if interval not in INTERVALS:
            interval = '1d'
        
        candles = []
        prefix = MarketDBKeys.OHLCV + token_ref + interval.encode()
        
        async for key, value in self.db.iterator(prefix=prefix, reverse=True):
            try:
                candle = OHLCV.from_bytes(value)
                candles.append({
                    'timestamp': candle.timestamp,
                    'open': candle.open,
                    'high': candle.high,
                    'low': candle.low,
                    'close': candle.close,
                    'volume': candle.volume,
                    'trade_count': candle.trade_count,
                })
                
                if len(candles) >= limit:
                    break
            except Exception:
                continue
        
        # Return in chronological order
        return list(reversed(candles))
    
    # =========================================================================
    # PRICE & VOLUME
    # =========================================================================
    
    async def get_price(self, token_ref: bytes) -> Optional[Dict]:
        """Get current price for a token."""
        # Check cache first
        if token_ref in self.price_cache:
            price = self.price_cache[token_ref]
        else:
            # Get from latest trade
            trades = await self.get_trades(token_ref, limit=1)
            if not trades:
                return None
            price = trades[0]['price_rxd']
            self.price_cache[token_ref] = price
        
        return {
            'price_rxd': price,
            'price_rxd_formatted': price / 1e8,  # Convert from photons
        }
    
    async def get_volume_24h(self, token_ref: bytes) -> Dict:
        """Get 24-hour volume for a token."""
        now = int(time.time())
        start_time = now - 86400
        
        trades = await self.get_trades(token_ref, limit=10000, start_time=start_time)
        
        volume = sum(t['amount'] for t in trades)
        trade_count = len(trades)
        
        return {
            'volume_24h': volume,
            'trade_count_24h': trade_count,
            'start_time': start_time,
            'end_time': now,
        }
    
    async def get_market_summary(self, token_ref: bytes) -> Dict:
        """Get full market summary for a token."""
        price_data = await self.get_price(token_ref)
        volume_data = await self.get_volume_24h(token_ref)
        
        # Get 24h price change
        now = int(time.time())
        trades_24h_ago = await self.get_trades(
            token_ref, limit=1, 
            start_time=now - 86400 - 3600,
            end_time=now - 86400
        )
        
        price_24h_ago = trades_24h_ago[0]['price_rxd'] if trades_24h_ago else None
        current_price = price_data['price_rxd'] if price_data else None
        
        change_24h = None
        change_24h_pct = None
        if price_24h_ago and current_price:
            change_24h = current_price - price_24h_ago
            change_24h_pct = round((change_24h / price_24h_ago) * 100, 2)
        
        return {
            'token_ref': token_ref.hex(),
            'price_rxd': current_price,
            'price_24h_ago': price_24h_ago,
            'change_24h': change_24h,
            'change_24h_pct': change_24h_pct,
            **volume_data,
        }
    
    # =========================================================================
    # SWAP ADVERTISEMENTS
    # =========================================================================
    
    async def record_swap(self, swap: SwapAdvertisement):
        """Record a new swap advertisement."""
        swap_key = MarketDBKeys.SWAP + swap.swap_id
        
        # Serialize swap
        data = {
            'token_ref': swap.token_ref.hex(),
            'amount': swap.amount,
            'price_rxd': swap.price_rxd,
            'seller': swap.seller.hex(),
            'expiry': swap.expiry,
            'status': swap.status,
            'txid': swap.txid.hex(),
            'height': swap.height,
        }
        
        import json
        await self.db.put(swap_key, json.dumps(data).encode())
        
        # Update cache
        self.swap_cache[swap.swap_id] = swap
    
    async def get_open_swaps(self, token_ref: bytes = None, limit: int = 50) -> List[Dict]:
        """Get open swap advertisements."""
        swaps = []
        now = int(time.time())
        
        async for key, value in self.db.iterator(prefix=MarketDBKeys.SWAP):
            try:
                import json
                data = json.loads(value.decode())
                
                # Filter by status
                if data.get('status') != 'open':
                    continue
                
                # Filter expired
                if data.get('expiry', 0) < now:
                    continue
                
                # Filter by token if specified
                if token_ref and data.get('token_ref') != token_ref.hex():
                    continue
                
                swaps.append(data)
                
                if len(swaps) >= limit:
                    break
            except Exception:
                continue
        
        return swaps
    
    async def get_swap_history(self, limit: int = 50) -> List[Dict]:
        """Get recent swap history (all statuses)."""
        swaps = []
        
        async for key, value in self.db.iterator(prefix=MarketDBKeys.SWAP, reverse=True):
            try:
                import json
                data = json.loads(value.decode())
                swaps.append(data)
                
                if len(swaps) >= limit:
                    break
            except Exception:
                continue
        
        return swaps
