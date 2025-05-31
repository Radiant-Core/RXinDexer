# /Users/radiant/Desktop/RXinDexer/src/api/analytics_endpoints.py
# This file defines FastAPI endpoints for blockchain analytics and time-series data.
# It provides historical metrics, rich lists, and token distribution analysis.

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_, and_, text
from datetime import datetime, timedelta

from src.models import (
    TimeSeriesMetric, RichList, TokenDistribution, 
    MarketData, ActivityMetric,
    get_db
)
from src.api.schemas import (
    TimeSeriesResponse, RichListResponse, TokenDistributionResponse
)

router = APIRouter(prefix="/api/v1")

# Time-series metrics endpoint
@router.get("/analytics/metrics", response_model=TimeSeriesResponse, tags=["Analytics"])
async def get_time_series_metrics(
    metric_type: str = Query(..., description="Type of metric to retrieve"),
    token_id: Optional[str] = None,
    interval: str = Query("1d", enum=["1h", "1d", "1w", "1m"]),
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """
    Retrieve time-series metrics for blockchain activity.
    
    Parameters:
    - metric_type: Type of metric (transactions, active_addresses, volume, etc.)
    - token_id: Optional token ID to filter by
    - interval: Time interval for the data points
    - start_time: Optional start time for the data range
    - end_time: Optional end time for the data range
    - limit: Maximum number of data points to return
    
    Returns:
    - Time-series data for the requested metric
    """
    # Build query
    query = db.query(TimeSeriesMetric).filter(
        TimeSeriesMetric.metric_type == metric_type,
        TimeSeriesMetric.interval == interval
    )
    
    # Apply token filter if provided
    metric_scope = None
    if token_id:
        metric_scope = f"token:{token_id}"
        query = query.filter(TimeSeriesMetric.metric_scope == metric_scope)
    else:
        query = query.filter(TimeSeriesMetric.metric_scope == "global")
    
    # Apply time range filters
    if start_time:
        query = query.filter(TimeSeriesMetric.timestamp >= start_time)
    
    if end_time:
        query = query.filter(TimeSeriesMetric.timestamp <= end_time)
    else:
        # Default to last 30 days if no end time specified
        end_time = datetime.utcnow()
        query = query.filter(TimeSeriesMetric.timestamp <= end_time)
    
    if not start_time:
        # Default to appropriate start time based on interval if not specified
        if interval == "1h":
            start_time = end_time - timedelta(days=2)  # 48 hours
        elif interval == "1d":
            start_time = end_time - timedelta(days=30)  # 30 days
        elif interval == "1w":
            start_time = end_time - timedelta(days=180)  # ~6 months
        else:  # 1m
            start_time = end_time - timedelta(days=365)  # 1 year
        
        query = query.filter(TimeSeriesMetric.timestamp >= start_time)
    
    # Order by time and limit results
    query = query.order_by(TimeSeriesMetric.timestamp).limit(limit)
    
    # Execute query
    metrics = query.all()
    
    if not metrics:
        raise HTTPException(
            status_code=404,
            detail=f"No metrics found for type: {metric_type}, interval: {interval}"
        )
    
    # Construct response
    response = {
        "metric_type": metric_type,
        "metric_scope": metric_scope or "global",
        "interval": interval,
        "data": [
            {
                "timestamp": metric.timestamp,
                "value": metric.value,
                "count": metric.count,
                "sum": metric.sum,
                "avg": metric.avg,
                "min": metric.min,
                "max": metric.max
            } for metric in metrics
        ]
    }
    
    return response

# Rich list endpoint
@router.get("/analytics/richlist", response_model=RichListResponse, tags=["Analytics"])
async def get_rich_list(
    token_type: str = Query("rxd", enum=["rxd", "ft", "nft"]),
    token_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Retrieve rich list data showing top holders of a token.
    
    Parameters:
    - token_type: Type of token (rxd, ft, nft)
    - token_id: Optional token ID (required for ft and nft)
    - limit: Maximum number of entries to return
    - offset: Number of entries to skip (for pagination)
    
    Returns:
    - Rich list data with holder rankings and balances
    """
    # Validate token_id if needed
    if token_type in ["ft", "nft"] and not token_id:
        raise HTTPException(
            status_code=400,
            detail=f"token_id is required for token_type: {token_type}"
        )
    
    # Get the latest timestamp for rich list data
    latest_timestamp = db.query(func.max(RichList.timestamp)).filter(
        RichList.token_type == token_type
    )
    
    if token_id:
        latest_timestamp = latest_timestamp.filter(RichList.token_id == token_id)
    
    latest_timestamp = latest_timestamp.scalar()
    
    if not latest_timestamp:
        raise HTTPException(
            status_code=404,
            detail=f"No rich list data found for token_type: {token_type}"
        )
    
    # Build query for rich list entries
    query = db.query(RichList).filter(
        RichList.token_type == token_type,
        RichList.timestamp == latest_timestamp
    )
    
    if token_id:
        query = query.filter(RichList.token_id == token_id)
    
    # Count total entries
    total_entries = query.count()
    
    # Apply pagination and sorting
    entries = query.order_by(RichList.rank).offset(offset).limit(limit).all()
    
    if not entries:
        raise HTTPException(
            status_code=404,
            detail=f"No rich list entries found for token_type: {token_type}"
        )
    
    # Construct response
    response = {
        "token_type": token_type,
        "token_id": token_id,
        "timestamp": latest_timestamp,
        "entries": [
            {
                "address": entry.address,
                "balance": entry.balance,
                "rank": entry.rank,
                "percentage": entry.percentage,
                "balance_change": entry.balance_change,
                "rank_change": entry.rank_change
            } for entry in entries
        ],
        "pagination": {
            "total": total_entries,
            "offset": offset,
            "limit": limit
        }
    }
    
    return response

# Token distribution endpoint
@router.get("/analytics/token/{token_id}/distribution", tags=["Analytics"])
async def get_token_distribution(
    token_id: str,
    token_type: str = Query("ft", enum=["rxd", "ft", "nft"]),
    group_type: str = Query("balance_range", enum=["balance_range", "top_percent", "address_type"]),
    db: Session = Depends(get_db)
):
    """
    Retrieve token distribution data showing how tokens are distributed across holders.
    
    Parameters:
    - token_id: Token ID (or "rxd" for RXD token)
    - token_type: Type of token (rxd, ft, nft)
    - group_type: Type of grouping for distribution analysis
    
    Returns:
    - Token distribution data with holder groups and percentages
    """
    # Get the latest timestamp for distribution data
    latest_timestamp = db.query(func.max(TokenDistribution.timestamp)).filter(
        TokenDistribution.token_type == token_type,
        TokenDistribution.group_type == group_type
    )
    
    if token_id != "rxd":
        latest_timestamp = latest_timestamp.filter(TokenDistribution.token_id == token_id)
    
    latest_timestamp = latest_timestamp.scalar()
    
    if not latest_timestamp:
        raise HTTPException(
            status_code=404,
            detail=f"No distribution data found for token: {token_id}, group_type: {group_type}"
        )
    
    # Build query for distribution groups
    query = db.query(TokenDistribution).filter(
        TokenDistribution.token_type == token_type,
        TokenDistribution.group_type == group_type,
        TokenDistribution.timestamp == latest_timestamp
    )
    
    if token_id != "rxd":
        query = query.filter(TokenDistribution.token_id == token_id)
    
    # Get distribution groups
    groups = query.order_by(TokenDistribution.group_key).all()
    
    if not groups:
        raise HTTPException(
            status_code=404,
            detail=f"No distribution groups found for token: {token_id}, group_type: {group_type}"
        )
    
    # Construct response
    response = {
        "token_type": token_type,
        "token_id": token_id if token_id != "rxd" else None,
        "timestamp": latest_timestamp,
        "group_type": group_type,
        "groups": [
            {
                "group_key": group.group_key,
                "address_count": group.address_count,
                "total_balance": group.total_balance,
                "percentage": group.percentage,
                "address_count_change": group.address_count_change,
                "balance_change": group.balance_change,
                "percentage_change": group.percentage_change
            } for group in groups
        ]
    }
    
    return response

# Activity metrics endpoint
@router.get("/analytics/activity", tags=["Analytics"])
async def get_activity_metrics(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db)
):
    """
    Retrieve daily activity metrics for the blockchain.
    
    Parameters:
    - start_date: Optional start date for the data range
    - end_date: Optional end date for the data range
    - limit: Maximum number of days to return
    
    Returns:
    - Daily activity metrics including transactions, active addresses, etc.
    """
    # Build query
    query = db.query(ActivityMetric)
    
    # Apply date range filters
    if end_date:
        query = query.filter(ActivityMetric.date <= end_date)
    else:
        # Default to current date if no end date specified
        end_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(ActivityMetric.date <= end_date)
    
    if start_date:
        query = query.filter(ActivityMetric.date >= start_date)
    else:
        # Default to limit days before end date if no start date specified
        start_date = end_date - timedelta(days=limit)
        query = query.filter(ActivityMetric.date >= start_date)
    
    # Order by date and limit results
    query = query.order_by(desc(ActivityMetric.date)).limit(limit)
    
    # Execute query
    metrics = query.all()
    
    if not metrics:
        raise HTTPException(
            status_code=404,
            detail="No activity metrics found for the specified date range"
        )
    
    # Construct response
    response = {
        "start_date": start_date,
        "end_date": end_date,
        "metrics": [
            {
                "date": metric.date,
                "active_addresses": metric.active_addresses,
                "new_addresses": metric.new_addresses,
                "transaction_count": metric.transaction_count,
                "transaction_volume": metric.transaction_volume,
                "average_transaction_size": metric.average_transaction_size,
                "average_transaction_fee": metric.average_transaction_fee,
                "tokens_transferred": metric.tokens_transferred,
                "nfts_transferred": metric.nfts_transferred,
                "block_count": metric.block_count,
                "average_block_size": metric.average_block_size,
                "average_block_time": metric.average_block_time
            } for metric in metrics
        ]
    }
    
    return response

# Token price endpoint
@router.get("/analytics/token/{token_id}/price", tags=["Analytics"])
async def get_token_price(
    token_id: str,
    token_type: str = Query("ft", enum=["rxd", "ft", "nft"]),
    pair: Optional[str] = None,
    interval: str = Query("1d", enum=["1h", "1d", "1w", "1m"]),
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """
    Retrieve price history for a token.
    
    Parameters:
    - token_id: Token ID (or "rxd" for RXD token)
    - token_type: Type of token (rxd, ft, nft)
    - pair: Trading pair (e.g. "RXD/USD")
    - interval: Time interval for the data points
    - start_time: Optional start time for the data range
    - end_time: Optional end time for the data range
    - limit: Maximum number of data points to return
    
    Returns:
    - Price history data for the token
    """
    # Build query
    query = db.query(MarketData).filter(
        MarketData.token_type == token_type,
        MarketData.interval == interval
    )
    
    if token_id != "rxd":
        query = query.filter(MarketData.token_id == token_id)
    
    if pair:
        query = query.filter(MarketData.pair == pair)
    
    # Apply time range filters
    if start_time:
        query = query.filter(MarketData.timestamp >= start_time)
    
    if end_time:
        query = query.filter(MarketData.timestamp <= end_time)
    else:
        # Default to current time if no end time specified
        end_time = datetime.utcnow()
        query = query.filter(MarketData.timestamp <= end_time)
    
    if not start_time:
        # Default to appropriate start time based on interval if not specified
        if interval == "1h":
            start_time = end_time - timedelta(days=2)  # 48 hours
        elif interval == "1d":
            start_time = end_time - timedelta(days=30)  # 30 days
        elif interval == "1w":
            start_time = end_time - timedelta(days=180)  # ~6 months
        else:  # 1m
            start_time = end_time - timedelta(days=365)  # 1 year
        
        query = query.filter(MarketData.timestamp >= start_time)
    
    # Order by time and limit results
    query = query.order_by(MarketData.timestamp).limit(limit)
    
    # Execute query
    prices = query.all()
    
    if not prices:
        raise HTTPException(
            status_code=404,
            detail=f"No price data found for token: {token_id}, interval: {interval}"
        )
    
    # Construct response
    response = {
        "token_id": token_id if token_id != "rxd" else None,
        "token_type": token_type,
        "pair": pair or prices[0].pair,
        "interval": interval,
        "price_data": [
            {
                "timestamp": price.timestamp,
                "open": price.open_price,
                "close": price.close_price,
                "high": price.high_price,
                "low": price.low_price,
                "volume": price.volume,
                "volume_quote": price.volume_quote,
                "trades_count": price.trades_count
            } for price in prices
        ]
    }
    
    return response
