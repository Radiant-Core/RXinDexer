"""
WebSocket endpoints for real-time updates.

Channels:
- /ws/blocks - Live block notifications
- /ws/transactions - Live transaction notifications
- /ws/tokens - Token update notifications
- /ws/mempool - Mempool transaction notifications
- /ws/subscribe - Multi-channel subscription endpoint
"""

import asyncio
import json
import logging
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import text

from api.websocket import manager, broadcast_new_block
from database.session import AsyncSessionLocal

logger = logging.getLogger("rxindexer.ws.endpoints")

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/blocks")
async def websocket_blocks(websocket: WebSocket):
    """
    WebSocket endpoint for live block notifications.
    
    Sends JSON messages when new blocks are indexed:
    {
        "type": "new_block",
        "data": {
            "height": 123456,
            "hash": "abc123...",
            "tx_count": 5,
            "timestamp": 1234567890
        },
        "_channel": "blocks",
        "_timestamp": "2026-01-08T12:00:00"
    }
    """
    if not await manager.connect(websocket, "blocks"):
        return
    
    try:
        # Send welcome message
        await manager.send_personal(websocket, {
            "type": "connected",
            "channel": "blocks",
            "message": "Subscribed to block notifications"
        })
        
        # Keep connection alive and handle client messages
        while True:
            try:
                # Wait for client messages (ping/pong or commands)
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=60.0  # Send ping every 60s
                )
                
                # Handle client commands
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await manager.send_personal(websocket, {"type": "pong"})
                except json.JSONDecodeError:
                    pass
                    
            except asyncio.TimeoutError:
                # Send keepalive ping
                try:
                    await manager.send_personal(websocket, {"type": "ping"})
                except Exception:
                    break
                    
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)


@router.websocket("/ws/transactions")
async def websocket_transactions(websocket: WebSocket):
    """
    WebSocket endpoint for live transaction notifications.
    
    Sends JSON messages when new transactions are indexed:
    {
        "type": "new_transaction",
        "data": {
            "txid": "abc123...",
            "block_height": 123456,
            "value": 1000000
        },
        "_channel": "transactions",
        "_timestamp": "2026-01-08T12:00:00"
    }
    """
    if not await manager.connect(websocket, "transactions"):
        return
    
    try:
        await manager.send_personal(websocket, {
            "type": "connected",
            "channel": "transactions",
            "message": "Subscribed to transaction notifications"
        })
        
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=60.0
                )
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await manager.send_personal(websocket, {"type": "pong"})
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                try:
                    await manager.send_personal(websocket, {"type": "ping"})
                except Exception:
                    break
                    
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)


@router.websocket("/ws/tokens")
async def websocket_tokens(websocket: WebSocket):
    """
    WebSocket endpoint for token update notifications.
    
    Sends JSON messages when tokens are minted, transferred, or updated:
    {
        "type": "token_update",
        "data": {
            "ref": "abc123...",
            "name": "MyToken",
            "event": "mint"
        },
        "_channel": "tokens",
        "_timestamp": "2026-01-08T12:00:00"
    }
    """
    if not await manager.connect(websocket, "tokens"):
        return
    
    try:
        await manager.send_personal(websocket, {
            "type": "connected",
            "channel": "tokens",
            "message": "Subscribed to token notifications"
        })
        
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=60.0
                )
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await manager.send_personal(websocket, {"type": "pong"})
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                try:
                    await manager.send_personal(websocket, {"type": "ping"})
                except Exception:
                    break
                    
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)


@router.websocket("/ws/mempool")
async def websocket_mempool(websocket: WebSocket):
    """
    WebSocket endpoint for mempool (unconfirmed) transaction notifications.
    
    Sends JSON messages for transactions in the mempool:
    {
        "type": "new_transaction",
        "data": {
            "txid": "abc123...",
            "block_height": null,
            "value": 1000000
        },
        "_channel": "mempool",
        "_timestamp": "2026-01-08T12:00:00"
    }
    """
    if not await manager.connect(websocket, "mempool"):
        return
    
    try:
        await manager.send_personal(websocket, {
            "type": "connected",
            "channel": "mempool",
            "message": "Subscribed to mempool notifications"
        })
        
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=60.0
                )
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await manager.send_personal(websocket, {"type": "pong"})
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                try:
                    await manager.send_personal(websocket, {"type": "ping"})
                except Exception:
                    break
                    
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)


@router.websocket("/ws/subscribe")
async def websocket_multi_subscribe(
    websocket: WebSocket,
    channels: str = Query("blocks", description="Comma-separated list of channels to subscribe to")
):
    """
    Multi-channel subscription endpoint.
    
    Connect to multiple channels at once by specifying them in the query string:
    ws://host/ws/subscribe?channels=blocks,transactions,tokens
    
    Supports dynamic subscription changes via messages:
    {"type": "subscribe", "channel": "tokens"}
    {"type": "unsubscribe", "channel": "blocks"}
    """
    # Parse initial channels
    channel_list = [c.strip() for c in channels.split(",") if c.strip()]
    if not channel_list:
        channel_list = ["blocks"]
    
    # Connect to first channel
    if not await manager.connect(websocket, channel_list[0]):
        return
    
    # Subscribe to additional channels
    for channel in channel_list[1:]:
        await manager.subscribe(websocket, channel)
    
    try:
        await manager.send_personal(websocket, {
            "type": "connected",
            "channels": channel_list,
            "message": f"Subscribed to: {', '.join(channel_list)}"
        })
        
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=60.0
                )
                
                try:
                    msg = json.loads(data)
                    msg_type = msg.get("type")
                    
                    if msg_type == "ping":
                        await manager.send_personal(websocket, {"type": "pong"})
                        
                    elif msg_type == "subscribe":
                        channel = msg.get("channel")
                        if channel and await manager.subscribe(websocket, channel):
                            await manager.send_personal(websocket, {
                                "type": "subscribed",
                                "channel": channel
                            })
                            
                    elif msg_type == "unsubscribe":
                        channel = msg.get("channel")
                        if channel:
                            await manager.unsubscribe(websocket, channel)
                            await manager.send_personal(websocket, {
                                "type": "unsubscribed",
                                "channel": channel
                            })
                            
                    elif msg_type == "get_stats":
                        await manager.send_personal(websocket, {
                            "type": "stats",
                            "data": manager.get_stats()
                        })
                        
                except json.JSONDecodeError:
                    pass
                    
            except asyncio.TimeoutError:
                try:
                    await manager.send_personal(websocket, {"type": "ping"})
                except Exception:
                    break
                    
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)


@router.get("/ws/stats", summary="WebSocket statistics", tags=["websocket"])
async def get_websocket_stats():
    """Get current WebSocket connection statistics."""
    return manager.get_stats()
