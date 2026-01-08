"""
WebSocket support for real-time updates.

Provides live notifications for:
- New blocks
- New transactions
- Token updates
"""

import asyncio
import json
import logging
from typing import Dict, List, Set, Optional, Any
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect
from dataclasses import dataclass, field

logger = logging.getLogger("rxindexer.websocket")


@dataclass
class Subscription:
    """Represents a client subscription to a specific channel."""
    channel: str
    filters: Dict[str, Any] = field(default_factory=dict)


class ConnectionManager:
    """
    Manages WebSocket connections and message broadcasting.
    
    Supports multiple channels:
    - blocks: New block notifications
    - transactions: New transaction notifications  
    - tokens: Token update notifications
    - mempool: Mempool transaction notifications
    """
    
    def __init__(self):
        # Map of channel -> set of websockets
        self._connections: Dict[str, Set[WebSocket]] = {
            "blocks": set(),
            "transactions": set(),
            "tokens": set(),
            "mempool": set(),
        }
        # Map of websocket -> set of subscribed channels
        self._subscriptions: Dict[WebSocket, Set[str]] = {}
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
        # Stats
        self._total_connections = 0
        self._total_messages_sent = 0
    
    async def connect(self, websocket: WebSocket, channel: str = "blocks") -> bool:
        """
        Accept a new WebSocket connection and subscribe to a channel.
        
        Args:
            websocket: The WebSocket connection
            channel: Channel to subscribe to (blocks, transactions, tokens, mempool)
            
        Returns:
            True if connection was accepted
        """
        if channel not in self._connections:
            return False
            
        await websocket.accept()
        
        async with self._lock:
            self._connections[channel].add(websocket)
            if websocket not in self._subscriptions:
                self._subscriptions[websocket] = set()
            self._subscriptions[websocket].add(channel)
            self._total_connections += 1
            
        logger.info(f"WebSocket connected to channel '{channel}'. Total active: {self.active_connections}")
        return True
    
    async def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket from all subscribed channels."""
        async with self._lock:
            if websocket in self._subscriptions:
                for channel in self._subscriptions[websocket]:
                    self._connections[channel].discard(websocket)
                del self._subscriptions[websocket]
                
        logger.info(f"WebSocket disconnected. Total active: {self.active_connections}")
    
    async def subscribe(self, websocket: WebSocket, channel: str) -> bool:
        """Subscribe an existing connection to an additional channel."""
        if channel not in self._connections:
            return False
            
        async with self._lock:
            self._connections[channel].add(websocket)
            if websocket in self._subscriptions:
                self._subscriptions[websocket].add(channel)
                
        return True
    
    async def unsubscribe(self, websocket: WebSocket, channel: str):
        """Unsubscribe a connection from a channel."""
        async with self._lock:
            self._connections.get(channel, set()).discard(websocket)
            if websocket in self._subscriptions:
                self._subscriptions[websocket].discard(channel)
    
    async def broadcast(self, channel: str, message: Dict[str, Any]):
        """
        Broadcast a message to all connections on a channel.
        
        Args:
            channel: The channel to broadcast to
            message: The message dict to send as JSON
        """
        if channel not in self._connections:
            return
            
        # Add metadata
        message["_channel"] = channel
        message["_timestamp"] = datetime.utcnow().isoformat()
        
        # Get current connections (copy to avoid modification during iteration)
        async with self._lock:
            connections = list(self._connections[channel])
        
        if not connections:
            return
            
        # Broadcast to all connections
        disconnected = []
        for websocket in connections:
            try:
                await websocket.send_json(message)
                self._total_messages_sent += 1
            except Exception as e:
                logger.debug(f"Failed to send to websocket: {e}")
                disconnected.append(websocket)
        
        # Clean up disconnected clients
        for websocket in disconnected:
            await self.disconnect(websocket)
    
    async def send_personal(self, websocket: WebSocket, message: Dict[str, Any]):
        """Send a message to a specific connection."""
        try:
            await websocket.send_json(message)
            self._total_messages_sent += 1
        except Exception as e:
            logger.debug(f"Failed to send personal message: {e}")
            await self.disconnect(websocket)
    
    @property
    def active_connections(self) -> int:
        """Total number of active WebSocket connections."""
        return len(self._subscriptions)
    
    def get_channel_stats(self) -> Dict[str, int]:
        """Get connection count per channel."""
        return {channel: len(conns) for channel, conns in self._connections.items()}
    
    def get_stats(self) -> Dict[str, Any]:
        """Get overall WebSocket statistics."""
        return {
            "active_connections": self.active_connections,
            "total_connections_ever": self._total_connections,
            "total_messages_sent": self._total_messages_sent,
            "channels": self.get_channel_stats(),
        }


# Global connection manager instance
manager = ConnectionManager()


async def broadcast_new_block(block_data: Dict[str, Any]):
    """
    Broadcast a new block to all subscribers.
    
    Args:
        block_data: Block information dict with keys like:
            - height: Block height
            - hash: Block hash
            - tx_count: Number of transactions
            - timestamp: Block timestamp
    """
    message = {
        "type": "new_block",
        "data": block_data,
    }
    await manager.broadcast("blocks", message)
    logger.debug(f"Broadcast new block {block_data.get('height')}")


async def broadcast_new_transaction(tx_data: Dict[str, Any]):
    """
    Broadcast a new transaction to subscribers.
    
    Args:
        tx_data: Transaction information dict with keys like:
            - txid: Transaction ID
            - block_height: Block height (None if mempool)
            - value: Total value
    """
    message = {
        "type": "new_transaction",
        "data": tx_data,
    }
    
    # Broadcast to transactions channel
    await manager.broadcast("transactions", message)
    
    # Also broadcast to mempool if unconfirmed
    if tx_data.get("block_height") is None:
        await manager.broadcast("mempool", message)


async def broadcast_token_update(token_data: Dict[str, Any]):
    """
    Broadcast a token update to subscribers.
    
    Args:
        token_data: Token information dict with keys like:
            - ref: Token reference
            - name: Token name
            - event: Event type (mint, transfer, burn)
    """
    message = {
        "type": "token_update",
        "data": token_data,
    }
    await manager.broadcast("tokens", message)
