# /Users/radiant/Desktop/RXinDexer/src/models/analytics.py
# This file defines models for blockchain analytics and time-series data tracking.
# It enables rich historical data analysis for transactions, addresses, and tokens.

from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, BigInteger, ForeignKey, DateTime, Index, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.orm import relationship

from .database import Base, JSONType, ArrayType

# Use custom types for cross-database compatibility
JsonColumn = JSONType
StringArrayColumn = ArrayType(String)


class TimeSeriesMetric(Base):
    """
    Stores time-series data for various blockchain metrics.
    Enables historical tracking of network activity and performance.
    """
    __tablename__ = "time_series_metrics"
    
    # Primary key and identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Time and interval information
    timestamp = Column(DateTime, nullable=False, index=True,
                      doc="Start timestamp for this data point")
    interval = Column(String(8), nullable=False, index=True,
                     doc="Interval size: 1h, 1d, 1w, 1m")
    
    # Metric identification
    metric_type = Column(String(32), nullable=False, index=True,
                        doc="Type of metric: transactions, active_addresses, volume, etc.")
    metric_scope = Column(String(64), nullable=True, index=True,
                         doc="Scope of the metric: global, token:<id>, nft_collection:<id>, etc.")
    
    # Metric values
    value = Column(Float, nullable=False,
                  doc="Primary numeric value of the metric")
    count = Column(BigInteger, nullable=True,
                  doc="Count value if applicable (e.g., number of transactions)")
    sum = Column(Float, nullable=True,
                doc="Sum value if applicable (e.g., total transaction volume)")
    avg = Column(Float, nullable=True,
                doc="Average value if applicable")
    min = Column(Float, nullable=True,
                doc="Minimum value if applicable")
    max = Column(Float, nullable=True,
                doc="Maximum value if applicable")
    
    # Additional data
    metric_data = Column(JsonColumn, default={}, nullable=False,
                       doc="Additional metric data and dimensions as JSONB")
    
    # Unique constraint to prevent duplicate entries
    __table_args__ = (
        UniqueConstraint('timestamp', 'interval', 'metric_type', 'metric_scope', 
                         name='uq_time_series_metric'),
        Index('idx_metric_lookup', metric_type, metric_scope, timestamp),
    )
    
    def __repr__(self):
        """String representation of the time series metric"""
        return f"<TimeSeriesMetric(type='{self.metric_type}', scope='{self.metric_scope}', time='{self.timestamp}', value={self.value})>"


class RichList(Base):
    """
    Tracks address rankings by balance for RXD and tokens.
    Enables richlist views and wealth distribution analysis.
    """
    __tablename__ = "rich_lists"
    
    # Primary key and identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Snapshot information
    timestamp = Column(DateTime, nullable=False, index=True,
                      doc="Timestamp when this snapshot was taken")
    token_type = Column(String(16), nullable=False, index=True,
                       doc="Type of token: rxd, ft, nft")
    token_id = Column(String(64), nullable=True, index=True,
                     doc="Token ID if applicable, null for RXD")
    
    # Ranking information
    address = Column(String(64), nullable=False, index=True,
                    doc="Wallet address")
    balance = Column(String(64), nullable=False,
                    doc="Balance amount stored as string to preserve precision")
    rank = Column(Integer, nullable=False,
                 doc="Rank position (1-based)")
    
    # Distribution metrics
    percentage = Column(Float, nullable=True,
                       doc="Percentage of total supply held")
    percentile = Column(Float, nullable=True,
                       doc="Percentile of holder distribution")
    
    # Changes since last snapshot
    balance_change = Column(String(64), nullable=True,
                           doc="Change in balance since previous snapshot")
    rank_change = Column(Integer, nullable=True,
                        doc="Change in rank since previous snapshot")
    
    # Unique constraint to prevent duplicate entries
    __table_args__ = (
        UniqueConstraint('timestamp', 'token_type', 'token_id', 'address', 
                         name='uq_rich_list_entry'),
        Index('idx_rich_list_rank', token_type, token_id, timestamp, rank),
    )
    
    def __repr__(self):
        """String representation of the rich list entry"""
        return f"<RichList(token='{self.token_id or 'RXD'}', address='{self.address}', rank={self.rank}, balance={self.balance})>"


class TokenDistribution(Base):
    """
    Tracks the distribution of token holdings across address groups.
    Enables analysis of wealth concentration and holder demographics.
    """
    __tablename__ = "token_distributions"
    
    # Primary key and identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Snapshot information
    timestamp = Column(DateTime, nullable=False, index=True,
                      doc="Timestamp when this snapshot was taken")
    token_type = Column(String(16), nullable=False, index=True,
                       doc="Type of token: rxd, ft, nft")
    token_id = Column(String(64), nullable=True, index=True,
                     doc="Token ID if applicable, null for RXD")
    
    # Distribution group
    group_type = Column(String(32), nullable=False,
                       doc="Type of grouping: balance_range, top_percent, address_type")
    group_key = Column(String(64), nullable=False,
                      doc="Key identifying this group: '0-1', '1-10', '10-100', 'top_1_percent', etc.")
    
    # Group metrics
    address_count = Column(Integer, nullable=False,
                          doc="Number of addresses in this group")
    total_balance = Column(String(64), nullable=False,
                          doc="Total balance held by this group")
    percentage = Column(Float, nullable=False,
                       doc="Percentage of total supply held by this group")
    
    # Changes since last snapshot
    address_count_change = Column(Integer, nullable=True,
                                 doc="Change in address count since previous snapshot")
    balance_change = Column(String(64), nullable=True,
                           doc="Change in total balance since previous snapshot")
    percentage_change = Column(Float, nullable=True,
                              doc="Change in percentage since previous snapshot")
    
    # Unique constraint to prevent duplicate entries
    __table_args__ = (
        UniqueConstraint('timestamp', 'token_type', 'token_id', 'group_type', 'group_key', 
                         name='uq_token_distribution'),
        Index('idx_distribution_lookup', token_type, token_id, timestamp, group_type),
    )
    
    def __repr__(self):
        """String representation of the token distribution entry"""
        return f"<TokenDistribution(token='{self.token_id or 'RXD'}', group='{self.group_key}', addresses={self.address_count}, percentage={self.percentage})>"


class MarketData(Base):
    """
    Tracks market data for tokens including prices, volumes, and liquidity.
    Enables price discovery and trading analytics.
    """
    __tablename__ = "market_data"
    
    # Primary key and identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Time and interval information
    timestamp = Column(DateTime, nullable=False, index=True,
                      doc="Timestamp for this data point")
    interval = Column(String(8), nullable=False, index=True,
                     doc="Interval size: 1h, 1d, 1w, 1m")
    
    # Token identification
    token_type = Column(String(16), nullable=False, index=True,
                       doc="Type of token: rxd, ft, nft")
    token_id = Column(String(64), nullable=True, index=True,
                     doc="Token ID")
    pair = Column(String(32), nullable=True, index=True,
                 doc="Trading pair if applicable, e.g. 'RXD/USD'")
    
    # Price data
    open_price = Column(String(32), nullable=True,
                       doc="Opening price in the interval")
    close_price = Column(String(32), nullable=True,
                        doc="Closing price in the interval")
    high_price = Column(String(32), nullable=True,
                       doc="Highest price in the interval")
    low_price = Column(String(32), nullable=True,
                      doc="Lowest price in the interval")
    
    # Volume data
    volume = Column(String(64), nullable=True,
                   doc="Trading volume in the interval")
    volume_quote = Column(String(64), nullable=True,
                         doc="Trading volume in quote currency")
    trades_count = Column(Integer, nullable=True,
                         doc="Number of trades in the interval")
    
    # Liquidity data
    liquidity = Column(String(64), nullable=True,
                      doc="Liquidity available")
    liquidity_change = Column(String(64), nullable=True,
                             doc="Change in liquidity during interval")
    
    # Market source
    source = Column(String(32), nullable=True,
                   doc="Source of market data: on-chain, dex:name, etc.")
    
    # Additional data
    market_data_json = Column(JsonColumn, default={}, nullable=False,
                           doc="Additional market metadata as JSONB")
    
    # Unique constraint to prevent duplicate entries
    __table_args__ = (
        UniqueConstraint('timestamp', 'interval', 'token_type', 'token_id', 'pair', 'source',
                         name='uq_market_data'),
        Index('idx_market_lookup', token_type, token_id, timestamp, interval),
    )
    
    def __repr__(self):
        """String representation of the market data entry"""
        return f"<MarketData(token='{self.token_id}', pair='{self.pair}', time='{self.timestamp}', close={self.close_price})>"


class ActivityMetric(Base):
    """
    Tracks blockchain activity metrics like active addresses and transaction counts.
    Enables analysis of network health and usage patterns.
    """
    __tablename__ = "activity_metrics"
    
    # Primary key and identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Time and interval information
    date = Column(DateTime, nullable=False, index=True,
                 doc="Date for this activity metric")
    
    # Activity metrics
    active_addresses = Column(Integer, nullable=False, default=0,
                             doc="Number of addresses active on this date")
    new_addresses = Column(Integer, nullable=False, default=0,
                          doc="Number of new addresses created on this date")
    transaction_count = Column(Integer, nullable=False, default=0,
                              doc="Number of transactions on this date")
    transaction_volume = Column(String(64), nullable=False, default="0",
                               doc="Total transaction volume on this date")
    average_transaction_size = Column(String(32), nullable=True,
                                     doc="Average transaction size on this date")
    average_transaction_fee = Column(String(32), nullable=True,
                                    doc="Average transaction fee on this date")
    
    # Token metrics
    tokens_transferred = Column(Integer, nullable=False, default=0,
                               doc="Number of token transfers on this date")
    nfts_transferred = Column(Integer, nullable=False, default=0,
                             doc="Number of NFT transfers on this date")
    
    # Network metrics
    block_count = Column(Integer, nullable=False, default=0,
                        doc="Number of blocks produced on this date")
    average_block_size = Column(Integer, nullable=True,
                               doc="Average block size in bytes")
    average_block_time = Column(Float, nullable=True,
                               doc="Average block time in seconds")
    
    # Unique constraint to prevent duplicate entries
    __table_args__ = (
        UniqueConstraint('date', name='uq_activity_metric_date'),
    )
    
    def __repr__(self):
        """String representation of the activity metric"""
        return f"<ActivityMetric(date='{self.date}', tx_count={self.transaction_count}, active_addresses={self.active_addresses})>"
