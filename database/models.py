# SQLAlchemy models for RXinDexer database
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, JSON, BigInteger, Text, func, Index, UniqueConstraint
from sqlalchemy.orm import relationship

Base = declarative_base()

# Block model
class Block(Base):
    __tablename__ = 'blocks'
    id = Column(Integer, primary_key=True)
    hash = Column(String, unique=True, nullable=False)
    height = Column(Integer, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    reorg = Column(Boolean, default=False)  # Flag for reorg handling
    transactions = relationship('Transaction', back_populates='block')

# Transaction model
class Transaction(Base):
    __tablename__ = 'transactions'
    id = Column(Integer, primary_key=True)
    txid = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    locktime = Column(Integer, nullable=False)
    block_id = Column(Integer, ForeignKey('blocks.id'))
    block_height = Column(Integer, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    block = relationship('Block', back_populates='transactions')
    utxos = relationship('UTXO', back_populates='transaction')
    inputs = relationship('TransactionInput', back_populates='transaction')

# TransactionInput model (for scriptSig and witness data)
class TransactionInput(Base):
    __tablename__ = 'transaction_inputs'
    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey('transactions.id'), nullable=False, index=True)
    input_index = Column(Integer, nullable=False)  # vin index
    spent_txid = Column(String, nullable=True, index=True)     # Null for coinbase
    spent_vout = Column(Integer, nullable=True, index=True)    # Null for coinbase
    script_sig = Column(String, nullable=True)     # Hex string
    sequence = Column(BigInteger, nullable=False)
    coinbase = Column(String, nullable=True)       # Hex string, only for coinbase inputs
    
    transaction = relationship('Transaction', back_populates='inputs')

# ContractType enum values (matching reference)
CONTRACT_TYPE_RXD = 'RXD'
CONTRACT_TYPE_NFT = 'NFT'
CONTRACT_TYPE_FT = 'FT'
CONTRACT_TYPE_CONTAINER = 'CONTAINER'
CONTRACT_TYPE_USER = 'USER'
CONTRACT_TYPE_DELEGATE_BURN = 'DELEGATE_BURN'
CONTRACT_TYPE_DELEGATE_TOKEN = 'DELEGATE_TOKEN'

# UTXO model (TxO in reference)
class UTXO(Base):
    __tablename__ = 'utxos'
    id = Column(Integer, primary_key=True)
    txid = Column(String, nullable=False, index=True)
    vout = Column(Integer, nullable=False)
    address = Column(String, nullable=True, index=True)
    value = Column(Float, nullable=False)  # In satoshis
    spent = Column(Boolean, default=False, index=True)
    spent_in_txid = Column(String, nullable=True)
    transaction_id = Column(Integer, ForeignKey('transactions.id'))
    transaction_block_height = Column(Integer, nullable=False, index=True)
    script_type = Column(String, nullable=True)
    script_hex = Column(String, nullable=True)
    
    # New fields from reference (TxO model)
    date = Column(Integer, nullable=True)  # Unix timestamp
    change = Column(Boolean, nullable=True)  # Is this a change output?
    is_glyph_reveal = Column(Boolean, default=False)  # Is this a glyph reveal transaction?
    glyph_ref = Column(String, nullable=True, index=True)  # Reference to the glyph this UTXO belongs to
    contract_type = Column(String(20), nullable=True, index=True)  # RXD, NFT, FT, CONTAINER, USER, DELEGATE_BURN, DELEGATE_TOKEN
    
    transaction = relationship('Transaction', back_populates='utxos')

# GlyphType enum values (matching reference)
GLYPH_TYPE_NFT = 'NFT'
GLYPH_TYPE_FT = 'FT'
GLYPH_TYPE_DAT = 'DAT'
GLYPH_TYPE_CONTAINER = 'CONTAINER'
GLYPH_TYPE_USER = 'USER'

# Unified Glyph model (combines GlyphToken and NFT, matching reference implementation)
# This is the primary token table - replaces separate glyph_tokens and nfts tables
class Glyph(Base):
    __tablename__ = 'glyphs'
    id = Column(Integer, primary_key=True)
    
    # Core identification (matching reference Glyph model)
    ref = Column(String(72), nullable=False, index=True, unique=True)  # 36-byte ref as hex (primary identifier)
    token_type = Column(String(20), nullable=False, index=True)  # NFT, FT, DAT, CONTAINER, USER
    
    # Protocol information
    p = Column(JSON)  # Array of protocol numbers/strings from payload.p
    
    # Core metadata (from CBOR payload)
    name = Column(String(255), nullable=False, index=True)
    ticker = Column(String(50), nullable=True, index=True)
    type = Column(String(100), nullable=False)  # User-defined type from payload (user/container/object/etc)
    description = Column(Text, nullable=False, default='')
    immutable = Column(Boolean, nullable=True)  # True unless both NFT(2) and MUT(5) protocols
    attrs = Column(JSON, default={})  # Custom attributes from payload.attrs
    
    # Author and container refs
    author = Column(String, nullable=False, index=True, default='')  # Author ref from payload.by
    container = Column(String, nullable=False, index=True, default='')  # Container ref from payload.in
    
    # Container-specific fields
    is_container = Column(Boolean, default=False, index=True)  # Flag if this glyph IS a container
    container_items = Column(JSON, nullable=True)  # Array of glyph refs in this container
    
    # State tracking (from reference)
    spent = Column(Boolean, nullable=False, default=False, index=True)  # Is the current UTXO spent?
    fresh = Column(Boolean, nullable=False, default=True)  # Is this newly created (not yet transferred)?
    melted = Column(Boolean, default=False)  # Has this token been melted/burned?
    sealed = Column(Boolean, default=False)  # Is this token sealed (immutable state)?
    swap_pending = Column(Boolean, default=False)  # Is there a pending swap for this token?
    
    # Value tracking
    value = Column(BigInteger, nullable=True)  # Value in satoshis (for FT amounts)

    burned_supply = Column(BigInteger, default=0)
    
    # Location tracking
    location = Column(String, nullable=True)  # Linked payload ref (when payload.loc is set)
    reveal_outpoint = Column(String, nullable=True)  # txid:vout of reveal transaction
    last_txo_id = Column(Integer, ForeignKey('utxos.id'), nullable=True)  # Reference to current UTXO
    
    # Block info
    height = Column(Integer, nullable=True, index=True)  # Block height of last update
    timestamp = Column(Integer, nullable=True)  # Unix timestamp of last update
    
    # Embedded file data (from reference)
    embed_type = Column(String(100), nullable=True)  # MIME type of embedded file
    embed_data = Column(Text, nullable=True)  # Base64-encoded embedded file data
    
    # Remote file data (from reference)
    remote_type = Column(String(100), nullable=True)  # MIME type of remote file
    remote_url = Column(String(500), nullable=True)  # URL of remote file
    remote_hash = Column(String, nullable=True)  # Hash of remote file
    remote_hash_sig = Column(String, nullable=True)  # Hash signature
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Indexes for common query patterns (matching reference)
    __table_args__ = (
        Index('ix_glyphs_token_type', 'token_type'),
        Index('ix_glyphs_spent_fresh', 'spent', 'fresh'),
        Index('ix_glyphs_container', 'container'),
        Index('ix_glyphs_is_container', 'is_container'),
        Index('ix_glyphs_spent_token_type', 'spent', 'token_type'),
        Index('ix_glyphs_spent_is_container', 'spent', 'is_container'),
        Index('ix_glyphs_author', 'author'),
        Index('ix_glyphs_token_type_id', 'token_type', 'id'),
    )


# Keep GlyphToken as alias for backward compatibility during migration
# TODO: Remove after full migration to unified Glyph model
class GlyphToken(Base):
    __tablename__ = 'glyph_tokens'
    id = Column(Integer, primary_key=True)
    token_id = Column(String, nullable=False, index=True)
    txid = Column(String, nullable=False, index=True)
    type = Column(String, index=True)
    owner = Column(String, index=True)
    token_metadata = Column(JSON)
    
    # Protocol information
    protocols = Column(JSON)
    protocol_type = Column(Integer)
    
    # Core metadata fields
    name = Column(String(255), nullable=True, index=True)
    description = Column(Text, nullable=True)
    token_type_name = Column(String(100), nullable=True, index=True)
    immutable = Column(Boolean, default=True)
    license = Column(String(255), nullable=True)
    attrs = Column(JSON, nullable=True)
    location = Column(String, nullable=True)
    
    # Token supply fields
    max_supply = Column(BigInteger)
    current_supply = Column(BigInteger)
    premine = Column(BigInteger, nullable=True)
    circulating_supply = Column(BigInteger, nullable=True)
    burned_supply = Column(BigInteger, default=0)
    
    # Contract fields (for dMint tokens)
    contract_references = Column(JSON)
    difficulty = Column(Integer)
    max_height = Column(BigInteger, nullable=True)
    reward = Column(BigInteger, nullable=True)
    num_contracts = Column(Integer, nullable=True)
    
    # Author and container
    container = Column(String, index=True, nullable=True)
    author = Column(String, index=True, nullable=True)
    ticker = Column(String(50), nullable=True, index=True)
    
    # Resolved author info
    author_name = Column(String(255), nullable=True)
    author_image_url = Column(String(500), nullable=True)
    author_image_data = Column(Text, nullable=True)
    
    # Icon/image data
    icon_mime_type = Column(String(100), nullable=True)
    icon_url = Column(String(500), nullable=True)
    icon_data = Column(Text, nullable=True)
    
    # Location and history tracking
    genesis_height = Column(Integer, index=True)
    latest_height = Column(Integer, index=True)
    current_txid = Column(String)
    current_vout = Column(Integer)
    reveal_txid = Column(String, nullable=True, index=True)
    reveal_vout = Column(Integer, nullable=True)
    
    # New fields from reference
    spent = Column(Boolean, default=False, index=True)
    fresh = Column(Boolean, default=True)
    melted = Column(Boolean, default=False)
    sealed = Column(Boolean, default=False)
    swap_pending = Column(Boolean, default=False)
    value = Column(BigInteger, nullable=True)  # Satoshi value
    
    # Deploy method
    deploy_method = Column(String(20), nullable=True)
    holder_count = Column(Integer, default=0)
    
    # Timestamps
    created_at = Column(DateTime)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    supply_updated_at = Column(DateTime, nullable=True)

# NFT model (for NFT metadata and collections)
class NFT(Base):
    __tablename__ = 'nfts'
    id = Column(Integer, primary_key=True)
    token_id = Column(String, nullable=False, index=True)  # Relaxed unique constraint
    txid = Column(String, nullable=True, index=True)  # Genesis txid
    type = Column(String(50), index=True)  # Script-derived: nft, mutable_nft, delegate
    
    # Payload type from reveal (user, container, object/null)
    token_type_name = Column(String(100), nullable=True, index=True)
    
    # Core metadata (extracted from CBOR for fast queries)
    name = Column(String(255), nullable=True, index=True)
    ticker = Column(String(50), nullable=True, index=True)  # Token ticker (from 'ticker')
    description = Column(Text, nullable=True)
    nft_metadata = Column(JSON)  # Full CBOR-decoded metadata (raw payload)
    attrs = Column(JSON, nullable=True)  # Custom attributes from payload.attrs
    
    # Author and container refs (from payload.by and payload.in)
    author = Column(String, index=True, nullable=True)
    container = Column(String, index=True, nullable=True)
    
    # Protocol information
    protocols = Column(JSON)  # List of protocol numbers from the 'p' field
    protocol_type = Column(Integer)  # Primary protocol: 2=NFT, 5=MUT
    
    # Mutability (true if NOT both NFT and MUT protocols present)
    immutable = Column(Boolean, default=True)
    
    # Linked payload location (when payload.loc points to another ref)
    location = Column(String, nullable=True)
    
    # Owner and collection
    owner = Column(String, index=True)
    collection = Column(String, index=True)  # Legacy field, use container instead
    
    # Block height tracking
    genesis_height = Column(Integer, index=True)
    latest_height = Column(Integer, index=True)
    
    # Reveal transaction tracking
    reveal_txid = Column(String, nullable=True, index=True)
    reveal_vout = Column(Integer, nullable=True)
    
    # Current location
    current_txid = Column(String, nullable=True)
    current_vout = Column(Integer, nullable=True)
    
    # Icon/image fields
    icon_mime_type = Column(String(100), nullable=True)
    icon_url = Column(String(500), nullable=True)
    icon_data = Column(Text, nullable=True)  # Base64 encoded embedded data
    
    # Holder count (cached, always 1 for NFTs unless fractionalized)
    holder_count = Column(Integer, default=1)
    
    # Timestamps
    created_at = Column(DateTime)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

# UserProfile model
class UserProfile(Base):
    __tablename__ = 'user_profiles'
    id = Column(Integer, primary_key=True)
    address = Column(String, nullable=False, index=True) # Relaxed unique constraint
    containers = Column(JSON)  # container relationships
    created_at = Column(DateTime)

# FailedBlock model for failed_blocks table
class FailedBlock(Base):
    __tablename__ = 'failed_blocks'
    block_height = Column(BigInteger, primary_key=True, nullable=False)
    block_hash = Column(String(80), nullable=True)
    fail_reason = Column(Text, nullable=True)
    fail_count = Column(Integer, nullable=False, default=1)
    last_failed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime)

# TokenFile model for storing embedded/remote files from tokens
class TokenFile(Base):
    __tablename__ = 'token_files'
    id = Column(Integer, primary_key=True)
    token_id = Column(String, nullable=False, index=True)  # Reference to glyph_tokens or nfts
    token_type = Column(String, nullable=False)  # 'glyph' or 'nft'
    file_key = Column(String, nullable=True)  # Key from metadata (e.g., 'icon', 'image', 'main')
    mime_type = Column(String, nullable=True)  # MIME type (e.g., 'image/png')
    file_data = Column(Text, nullable=True)  # Base64-encoded data for embedded files
    remote_url = Column(String, nullable=True)  # URL for remote files
    file_hash = Column(String, nullable=True)  # Hash of the file content
    file_size = Column(Integer, nullable=True)  # Size in bytes
    created_at = Column(DateTime, server_default=func.now())

# Container model for tracking collections/containers
class Container(Base):
    __tablename__ = 'containers'
    id = Column(Integer, primary_key=True)
    container_id = Column(String, nullable=False, index=True, unique=True)  # The ref/token_id of the container
    name = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    owner = Column(String, nullable=True, index=True)
    token_count = Column(Integer, default=0)  # Number of tokens in this container
    container_metadata = Column(JSON, nullable=True)  # Additional metadata
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

# BackfillStatus model for tracking backfill progress
class BackfillStatus(Base):
    __tablename__ = 'backfill_status'
    id = Column(Integer, primary_key=True)
    backfill_type = Column(String, nullable=False, unique=True)  # 'spent', 'tokens', 'files'
    is_complete = Column(Boolean, default=False)
    last_processed_id = Column(BigInteger, nullable=True)  # For resumable backfills
    total_processed = Column(BigInteger, default=0)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AddressCluster(Base):
    __tablename__ = 'address_clusters'
    address = Column(Text, primary_key=True)
    cluster_id = Column(BigInteger, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ============================================================================
# TOKEN HOLDER TRACKING
# ============================================================================

class TokenHolder(Base):
    """Tracks token balances per address for each token"""
    __tablename__ = 'token_holders'
    id = Column(Integer, primary_key=True)
    token_id = Column(String, nullable=False, index=True)  # Reference to glyph_tokens.token_id
    address = Column(String, nullable=False, index=True)  # Holder's address
    balance = Column(BigInteger, nullable=False, default=0)  # Token balance
    percentage = Column(Float, nullable=True)  # Percentage of circulating supply
    first_acquired_at = Column(DateTime, nullable=True)  # When first acquired
    last_updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Composite indexes for efficient lookups
    __table_args__ = (
        Index('ix_token_holders_token_balance', 'token_id', 'balance'),  # Top holders per token
        Index('ix_token_holders_address_token', 'address', 'token_id'),  # All tokens for address
        UniqueConstraint('token_id', 'address', name='uq_token_holder'),  # One entry per token+address
    )


# ============================================================================
# SWAP/TRADE TRACKING
# ============================================================================

class TokenSwap(Base):
    """Tracks swap offers and completed trades"""
    __tablename__ = 'token_swaps'
    id = Column(Integer, primary_key=True)
    
    # Transaction info
    txid = Column(String, nullable=False, index=True)  # Transaction ID
    psrt_hex = Column(Text, nullable=True)  # Raw PSRT hex (for pending swaps)
    
    # What's being sold
    from_token_id = Column(String, nullable=True, index=True)  # NULL for RXD
    from_amount = Column(BigInteger, nullable=False)  # Amount in photons or token units
    from_is_rxd = Column(Boolean, default=False)  # True if selling RXD
    
    # What's being bought
    to_token_id = Column(String, nullable=True, index=True)  # NULL for RXD
    to_amount = Column(BigInteger, nullable=False)  # Amount in photons or token units
    to_is_rxd = Column(Boolean, default=False)  # True if buying RXD
    
    # Parties
    seller_address = Column(String, nullable=True, index=True)
    buyer_address = Column(String, nullable=True, index=True)  # NULL until completed
    
    # Status
    status = Column(String(20), nullable=False, default='pending')  # pending, completed, cancelled
    
    # Price calculation (for RXD pairs)
    price_per_token = Column(Float, nullable=True)  # Price in RXD per token unit
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    block_height = Column(Integer, nullable=True, index=True)  # Block when completed


# ============================================================================
# BURN/MELT TRACKING
# ============================================================================

class TokenBurn(Base):
    """Tracks token burn (melt) events"""
    __tablename__ = 'token_burns'
    id = Column(Integer, primary_key=True)
    token_id = Column(String, nullable=False, index=True)
    txid = Column(String, nullable=False, index=True)  # Burn transaction
    amount = Column(BigInteger, nullable=False)  # Amount burned
    burner_address = Column(String, nullable=True, index=True)  # Who burned
    block_height = Column(Integer, nullable=True, index=True)
    burned_at = Column(DateTime, server_default=func.now())


# ============================================================================
# HISTORICAL DATA (for future charts/analytics)
# ============================================================================

class TokenSupplyHistory(Base):
    """Historical supply snapshots for tokens"""
    __tablename__ = 'token_supply_history'
    id = Column(Integer, primary_key=True)
    token_id = Column(String, nullable=False, index=True)
    circulating_supply = Column(BigInteger, nullable=False)
    burned_supply = Column(BigInteger, default=0)
    holder_count = Column(Integer, default=0)
    block_height = Column(Integer, nullable=False, index=True)
    recorded_at = Column(DateTime, server_default=func.now())
    
    # Composite index for time-series queries
    __table_args__ = (
        Index('ix_token_supply_history_token_block', 'token_id', 'block_height'),
    )


class TokenPriceHistory(Base):
    """Historical price data from completed trades"""
    __tablename__ = 'token_price_history'
    id = Column(Integer, primary_key=True)
    token_id = Column(String, nullable=False, index=True)
    
    # Price in RXD
    price_rxd = Column(Float, nullable=False)  # Price per token unit in RXD
    
    # Trade info
    swap_id = Column(Integer, ForeignKey('token_swaps.id'), nullable=True)
    txid = Column(String, nullable=True)
    volume = Column(BigInteger, nullable=False)  # Amount traded
    
    # Time info
    block_height = Column(Integer, nullable=True, index=True)
    recorded_at = Column(DateTime, server_default=func.now(), index=True)


class TokenVolumeDaily(Base):
    """Daily aggregated trading volume per token"""
    __tablename__ = 'token_volume_daily'
    id = Column(Integer, primary_key=True)
    token_id = Column(String, nullable=False, index=True)
    date = Column(DateTime, nullable=False, index=True)  # Date (truncated to day)
    
    # Volume metrics
    volume_tokens = Column(BigInteger, default=0)  # Total tokens traded
    volume_rxd = Column(BigInteger, default=0)  # Total RXD volume
    trade_count = Column(Integer, default=0)  # Number of trades
    
    # OHLCV data
    open_price = Column(Float, nullable=True)
    high_price = Column(Float, nullable=True)
    low_price = Column(Float, nullable=True)
    close_price = Column(Float, nullable=True)
    
    # Unique constraint on token + date
    __table_args__ = (
        UniqueConstraint('token_id', 'date', name='uq_token_volume_daily'),
        Index('ix_token_volume_daily_token_date', 'token_id', 'date'),
    )


# ============================================================================
# MINT EVENT TRACKING (for DMINT tokens)
# ============================================================================

class TokenMintEvent(Base):
    """Tracks individual mint events for DMINT tokens"""
    __tablename__ = 'token_mint_events'
    id = Column(Integer, primary_key=True)
    token_id = Column(String, nullable=False, index=True)
    txid = Column(String, nullable=False, index=True)
    minter_address = Column(String, nullable=True, index=True)
    amount = Column(BigInteger, nullable=False)  # Amount minted
    block_height = Column(Integer, nullable=True, index=True)
    minted_at = Column(DateTime, server_default=func.now())


# ============================================================================
# GLYPH ACTION TRACKING (from reference glyph_action.model.ts)
# Tracks all token actions: mint, transfer, melt, swap, update, etc.
# ============================================================================

# Action type constants
ACTION_TYPE_MINT = 'mint'
ACTION_TYPE_TRANSFER = 'transfer'
ACTION_TYPE_MELT = 'melt'
ACTION_TYPE_SWAP = 'swap'
ACTION_TYPE_UPDATE = 'update'
ACTION_TYPE_DELEGATE_BASE = 'delegate_base'
ACTION_TYPE_DELEGATE_TOKEN = 'delegate_token'
ACTION_TYPE_DELEGATE_BURN = 'delegate_burn'
ACTION_TYPE_PARTIAL_MELT = 'partial_melt'
ACTION_TYPE_CREATE_CONTRACT = 'create_contract'
ACTION_TYPE_DEPLOY_CONTRACT = 'deploy_contract'

class GlyphAction(Base):
    """Tracks all glyph/token actions for history and audit trails"""
    __tablename__ = 'glyph_actions'
    id = Column(Integer, primary_key=True)
    
    # Core fields (matching reference)
    ref = Column(String(72), nullable=False, index=True)  # Glyph ref this action relates to
    type = Column(String(30), nullable=False, index=True)  # Action type (mint, transfer, melt, swap, update, etc.)
    txid = Column(String, nullable=False, index=True)  # Transaction ID where action occurred
    height = Column(Integer, nullable=False, index=True)  # Block height
    timestamp = Column(DateTime, nullable=False, server_default=func.now())  # When action occurred
    
    # Additional metadata (flexible JSON for action-specific data)
    action_metadata = Column('metadata', JSON, nullable=True)  # Action-specific data (from_address, to_address, amount, etc.)
    
    # Indexes for common queries
    __table_args__ = (
        Index('ix_glyph_actions_ref_type', 'ref', 'type'),
        Index('ix_glyph_actions_ref_height', 'ref', 'height'),
        Index('ix_glyph_actions_type_height', 'type', 'height'),
    )


# ============================================================================
# DMINT CONTRACT TRACKING (from reference contract.model.ts)
# For mineable tokens (DMINT protocol)
# ============================================================================

class ContractGroup(Base):
    """Groups contracts for a DMINT token (from reference ContractGroup)"""
    __tablename__ = 'contract_groups'
    id = Column(Integer, primary_key=True)
    
    # Core identification
    first_ref = Column(String(72), nullable=False, unique=True, index=True)  # First contract ref (token identifier)
    
    # Token metadata
    name = Column(String(255), nullable=False)
    ticker = Column(String(50), default='')
    token_type = Column(String(20), default='FT')  # Usually FT for DMINT
    description = Column(Text, default='')
    
    # Supply tracking
    num_contracts = Column(Integer, default=0)  # Number of mining contracts
    total_supply = Column(BigInteger, default=0)  # Maximum supply
    minted_supply = Column(BigInteger, default=0)  # Currently minted
    
    # Full glyph data and files (JSON)
    glyph_data = Column(JSON, nullable=True)  # Full decoded glyph payload
    files = Column(JSON, nullable=True)  # Embedded/remote files
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationship to contracts
    contracts = relationship('Contract', back_populates='group')


class Contract(Base):
    """Individual DMINT mining contract (from reference Contract)"""
    __tablename__ = 'contracts'
    id = Column(Integer, primary_key=True)
    
    # Core identification
    contract_ref = Column(String(72), nullable=False, unique=True, index=True)  # This contract's ref
    token_ref = Column(String(72), nullable=False, index=True)  # Token this contract mints
    
    # Location
    location = Column(String, nullable=False)  # Current UTXO location (txid:vout)
    output_index = Column(Integer, nullable=False)  # Output index in transaction
    
    # Mining parameters
    height = Column(Integer, default=0)  # Current block height
    max_height = Column(BigInteger, default=0)  # Maximum block height for mining
    reward = Column(BigInteger, default=0)  # Tokens per successful mint
    target = Column(BigInteger, default=0)  # Mining difficulty target
    
    # Contract script
    script = Column(Text, default='')  # Contract script hex
    message = Column(String(255), default='')  # Optional message
    
    # Group relationship
    group_id = Column(Integer, ForeignKey('contract_groups.id'), nullable=True, index=True)
    group = relationship('ContractGroup', back_populates='contracts')
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ContractList(Base):
    """Quick lookup for contract refs (from reference ContractList)"""
    __tablename__ = 'contract_list'
    id = Column(Integer, primary_key=True)
    base_ref = Column(String(72), nullable=False, index=True)  # Base token ref
    count = Column(Integer, default=0)  # Number of contracts for this token
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ============================================================================
# GLOBAL STATISTICS (from reference stats.model.ts)
# Cached statistics for dashboard/overview queries
# ============================================================================

class Stats(Base):
    """Global statistics cache (from reference Stats)"""
    __tablename__ = 'stats'
    id = Column(Integer, primary_key=True)
    
    # Glyph counts
    glyphs_total = Column(Integer, default=0)
    glyphs_nft = Column(Integer, default=0)
    glyphs_ft = Column(Integer, default=0)
    glyphs_dat = Column(Integer, default=0)
    glyphs_containers = Column(Integer, default=0)
    glyphs_contained_items = Column(Integer, default=0)
    glyphs_users = Column(Integer, default=0)
    
    # TxO counts
    txos_total = Column(Integer, default=0)
    txos_rxd = Column(Integer, default=0)
    txos_nft = Column(Integer, default=0)
    txos_ft = Column(Integer, default=0)
    
    # Block info
    blocks_count = Column(Integer, default=0)
    latest_block_hash = Column(String, nullable=True)
    latest_block_height = Column(Integer, nullable=True)
    latest_block_timestamp = Column(DateTime, nullable=True)
    
    # Last update
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ============================================================================
# USER ENGAGEMENT - LIKES (from reference like.model.ts)
# ============================================================================

class GlyphLike(Base):
    """User likes on glyphs (from reference Like)"""
    __tablename__ = 'glyph_likes'
    id = Column(Integer, primary_key=True)
    
    glyph_ref = Column(String(72), nullable=False, index=True)  # Glyph being liked
    user_address = Column(String, nullable=False, index=True)  # User who liked
    
    created_at = Column(DateTime, server_default=func.now())
    
    # Ensure a user can only like a glyph once
    __table_args__ = (
        UniqueConstraint('glyph_ref', 'user_address', name='uq_glyph_like'),
        Index('ix_glyph_likes_ref_user', 'glyph_ref', 'user_address'),
    )


# ============================================================================
# IMPORT STATE TRACKING (from reference import-state.model.ts)
# ============================================================================

class ImportState(Base):
    """Tracks indexer sync state (from reference ImportState)"""
    __tablename__ = 'import_state'
    id = Column(Integer, primary_key=True)
    
    last_block_height = Column(Integer, nullable=False, default=0)
    last_block_hash = Column(String, nullable=False, default='')
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())
    is_importing = Column(Boolean, default=False)  # Lock flag to prevent concurrent imports
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
