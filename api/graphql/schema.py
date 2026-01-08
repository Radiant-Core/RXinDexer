"""
GraphQL schema and resolvers for RXinDexer API.
"""

import strawberry
from strawberry.fastapi import GraphQLRouter
from typing import Optional, List
from sqlalchemy import select, func, text
from datetime import datetime

from api.graphql.types import (
    BlockType, TransactionType, GlyphType, UTXOType,
    TokenStatsType, BlockchainStatsType, PaginationInfo,
    GlyphConnection, BlockConnection, TransactionConnection
)
from database.session import AsyncSessionLocal
from database.models import Block, Transaction, Glyph, UTXO


def block_to_graphql(block: Block, tx_count: int = 0) -> BlockType:
    """Convert database Block to GraphQL BlockType."""
    return BlockType(
        id=block.id,
        hash=block.hash,
        height=block.height,
        timestamp=block.timestamp,
        tx_count=tx_count
    )


def transaction_to_graphql(tx: Transaction) -> TransactionType:
    """Convert database Transaction to GraphQL TransactionType."""
    return TransactionType(
        id=tx.id,
        txid=tx.txid,
        version=tx.version,
        locktime=tx.locktime,
        block_id=tx.block_id,
        block_height=tx.block_height,
        created_at=tx.created_at
    )


def glyph_to_graphql(glyph: Glyph) -> GlyphType:
    """Convert database Glyph to GraphQL GlyphType."""
    return GlyphType(
        id=glyph.id,
        ref=glyph.ref,
        token_type=glyph.token_type,
        name=glyph.name or "",
        ticker=glyph.ticker,
        type=glyph.type or "",
        description=glyph.description or "",
        immutable=glyph.immutable,
        author=glyph.author or "",
        container=glyph.container or "",
        is_container=glyph.is_container or False,
        spent=glyph.spent or False,
        fresh=glyph.fresh or True,
        melted=glyph.melted or False,
        sealed=glyph.sealed or False,
        value=glyph.value,
        burned_supply=glyph.burned_supply or 0,
        location=glyph.location,
        height=glyph.height,
        timestamp=glyph.timestamp,
        embed_type=glyph.embed_type,
        remote_url=glyph.remote_url,
        created_at=glyph.created_at,
        updated_at=glyph.updated_at
    )


def utxo_to_graphql(utxo: UTXO) -> UTXOType:
    """Convert database UTXO to GraphQL UTXOType."""
    return UTXOType(
        id=utxo.id,
        txid=utxo.txid,
        vout=utxo.vout,
        address=utxo.address,
        value=utxo.value,
        spent=utxo.spent or False,
        spent_in_txid=utxo.spent_in_txid,
        script_type=utxo.script_type,
        contract_type=utxo.contract_type,
        glyph_ref=utxo.glyph_ref
    )


@strawberry.type
class Query:
    """GraphQL queries for RXinDexer."""
    
    @strawberry.field
    async def block(self, height: Optional[int] = None, hash: Optional[str] = None) -> Optional[BlockType]:
        """Get a block by height or hash."""
        async with AsyncSessionLocal() as db:
            if height is not None:
                result = await db.execute(select(Block).where(Block.height == height))
            elif hash is not None:
                result = await db.execute(select(Block).where(Block.hash == hash))
            else:
                return None
            
            block = result.scalar_one_or_none()
            if not block:
                return None
            
            # Get tx count
            count_result = await db.execute(
                select(func.count(Transaction.id)).where(Transaction.block_id == block.id)
            )
            tx_count = count_result.scalar() or 0
            
            return block_to_graphql(block, tx_count)
    
    @strawberry.field
    async def blocks(
        self,
        limit: int = 20,
        offset: int = 0,
        min_height: Optional[int] = None,
        max_height: Optional[int] = None
    ) -> BlockConnection:
        """Get paginated list of blocks."""
        async with AsyncSessionLocal() as db:
            stmt = select(Block)
            count_stmt = select(func.count(Block.id))
            
            if min_height is not None:
                stmt = stmt.where(Block.height >= min_height)
                count_stmt = count_stmt.where(Block.height >= min_height)
            if max_height is not None:
                stmt = stmt.where(Block.height <= max_height)
                count_stmt = count_stmt.where(Block.height <= max_height)
            
            # Get total count
            total_result = await db.execute(count_stmt)
            total = total_result.scalar() or 0
            
            # Get blocks
            stmt = stmt.order_by(Block.height.desc()).limit(limit).offset(offset)
            result = await db.execute(stmt)
            blocks = result.scalars().all()
            
            # Get tx counts
            block_ids = [b.id for b in blocks]
            tx_counts = {}
            if block_ids:
                counts_result = await db.execute(
                    select(Transaction.block_id, func.count(Transaction.id))
                    .where(Transaction.block_id.in_(block_ids))
                    .group_by(Transaction.block_id)
                )
                tx_counts = {bid: cnt for bid, cnt in counts_result.all()}
            
            items = [block_to_graphql(b, tx_counts.get(b.id, 0)) for b in blocks]
            
            return BlockConnection(
                items=items,
                pagination=PaginationInfo(
                    total=total,
                    limit=limit,
                    offset=offset,
                    has_next=offset + len(items) < total,
                    has_prev=offset > 0
                )
            )
    
    @strawberry.field
    async def transaction(self, txid: str) -> Optional[TransactionType]:
        """Get a transaction by txid."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Transaction).where(Transaction.txid == txid))
            tx = result.scalar_one_or_none()
            return transaction_to_graphql(tx) if tx else None
    
    @strawberry.field
    async def transactions(
        self,
        limit: int = 20,
        offset: int = 0,
        block_height: Optional[int] = None
    ) -> TransactionConnection:
        """Get paginated list of transactions."""
        async with AsyncSessionLocal() as db:
            stmt = select(Transaction)
            count_stmt = select(func.count(Transaction.id))
            
            if block_height is not None:
                stmt = stmt.where(Transaction.block_height == block_height)
                count_stmt = count_stmt.where(Transaction.block_height == block_height)
            
            # Get total count
            total_result = await db.execute(count_stmt)
            total = total_result.scalar() or 0
            
            # Get transactions
            stmt = stmt.order_by(Transaction.id.desc()).limit(limit).offset(offset)
            result = await db.execute(stmt)
            txs = result.scalars().all()
            
            items = [transaction_to_graphql(tx) for tx in txs]
            
            return TransactionConnection(
                items=items,
                pagination=PaginationInfo(
                    total=total,
                    limit=limit,
                    offset=offset,
                    has_next=offset + len(items) < total,
                    has_prev=offset > 0
                )
            )
    
    @strawberry.field
    async def glyph(self, ref: str) -> Optional[GlyphType]:
        """Get a glyph (token) by ref."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Glyph).where(Glyph.ref == ref))
            glyph = result.scalar_one_or_none()
            return glyph_to_graphql(glyph) if glyph else None
    
    @strawberry.field
    async def glyphs(
        self,
        limit: int = 20,
        offset: int = 0,
        token_type: Optional[str] = None,
        name: Optional[str] = None,
        ticker: Optional[str] = None,
        author: Optional[str] = None,
        container: Optional[str] = None,
        is_container: Optional[bool] = None,
        spent: Optional[bool] = None
    ) -> GlyphConnection:
        """Get paginated list of glyphs with optional filters."""
        async with AsyncSessionLocal() as db:
            stmt = select(Glyph)
            count_stmt = select(func.count(Glyph.id))
            
            # Apply filters
            if token_type:
                stmt = stmt.where(Glyph.token_type == token_type)
                count_stmt = count_stmt.where(Glyph.token_type == token_type)
            if name:
                stmt = stmt.where(Glyph.name.ilike(f"%{name}%"))
                count_stmt = count_stmt.where(Glyph.name.ilike(f"%{name}%"))
            if ticker:
                stmt = stmt.where(Glyph.ticker.ilike(f"%{ticker}%"))
                count_stmt = count_stmt.where(Glyph.ticker.ilike(f"%{ticker}%"))
            if author:
                stmt = stmt.where(Glyph.author == author)
                count_stmt = count_stmt.where(Glyph.author == author)
            if container:
                stmt = stmt.where(Glyph.container == container)
                count_stmt = count_stmt.where(Glyph.container == container)
            if is_container is not None:
                stmt = stmt.where(Glyph.is_container == is_container)
                count_stmt = count_stmt.where(Glyph.is_container == is_container)
            if spent is not None:
                stmt = stmt.where(Glyph.spent == spent)
                count_stmt = count_stmt.where(Glyph.spent == spent)
            
            # Get total count
            total_result = await db.execute(count_stmt)
            total = total_result.scalar() or 0
            
            # Get glyphs
            stmt = stmt.order_by(Glyph.id.desc()).limit(limit).offset(offset)
            result = await db.execute(stmt)
            glyphs = result.scalars().all()
            
            items = [glyph_to_graphql(g) for g in glyphs]
            
            return GlyphConnection(
                items=items,
                pagination=PaginationInfo(
                    total=total,
                    limit=limit,
                    offset=offset,
                    has_next=offset + len(items) < total,
                    has_prev=offset > 0
                )
            )
    
    @strawberry.field
    async def nfts(self, limit: int = 20, offset: int = 0) -> GlyphConnection:
        """Get paginated list of NFTs."""
        return await self.glyphs(limit=limit, offset=offset, token_type="NFT")
    
    @strawberry.field
    async def fts(self, limit: int = 20, offset: int = 0) -> GlyphConnection:
        """Get paginated list of FTs (fungible tokens)."""
        return await self.glyphs(limit=limit, offset=offset, token_type="FT")
    
    @strawberry.field
    async def containers(self, limit: int = 20, offset: int = 0) -> GlyphConnection:
        """Get paginated list of containers."""
        return await self.glyphs(limit=limit, offset=offset, is_container=True)
    
    @strawberry.field
    async def token_stats(self) -> TokenStatsType:
        """Get token statistics."""
        async with AsyncSessionLocal() as db:
            # Total tokens
            total_result = await db.execute(select(func.count(Glyph.id)))
            total = total_result.scalar() or 0
            
            # By type
            nft_result = await db.execute(
                select(func.count(Glyph.id)).where(Glyph.token_type == "NFT")
            )
            nfts = nft_result.scalar() or 0
            
            ft_result = await db.execute(
                select(func.count(Glyph.id)).where(Glyph.token_type == "FT")
            )
            fts = ft_result.scalar() or 0
            
            container_result = await db.execute(
                select(func.count(Glyph.id)).where(Glyph.is_container == True)
            )
            containers = container_result.scalar() or 0
            
            user_result = await db.execute(
                select(func.count(Glyph.id)).where(Glyph.token_type == "USER")
            )
            users = user_result.scalar() or 0
            
            return TokenStatsType(
                total_tokens=total,
                total_nfts=nfts,
                total_fts=fts,
                total_containers=containers,
                total_users=users
            )
    
    @strawberry.field
    async def blockchain_stats(self) -> BlockchainStatsType:
        """Get blockchain statistics."""
        async with AsyncSessionLocal() as db:
            # Latest block
            height_result = await db.execute(select(func.max(Block.height)))
            latest_height = height_result.scalar() or 0
            
            # Total transactions
            tx_result = await db.execute(select(func.count(Transaction.id)))
            total_txs = tx_result.scalar() or 0
            
            # Total UTXOs
            utxo_result = await db.execute(select(func.count(UTXO.id)))
            total_utxos = utxo_result.scalar() or 0
            
            # Total tokens
            token_result = await db.execute(select(func.count(Glyph.id)))
            total_tokens = token_result.scalar() or 0
            
            return BlockchainStatsType(
                latest_block_height=latest_height,
                total_transactions=total_txs,
                total_utxos=total_utxos,
                total_tokens=total_tokens,
                sync_status="synced"
            )
    
    @strawberry.field
    async def search_tokens(
        self,
        query: str,
        limit: int = 20
    ) -> List[GlyphType]:
        """Search tokens by name, ticker, or ref."""
        async with AsyncSessionLocal() as db:
            stmt = (
                select(Glyph)
                .where(
                    (Glyph.name.ilike(f"%{query}%")) |
                    (Glyph.ticker.ilike(f"%{query}%")) |
                    (Glyph.ref.ilike(f"%{query}%"))
                )
                .limit(limit)
            )
            result = await db.execute(stmt)
            glyphs = result.scalars().all()
            return [glyph_to_graphql(g) for g in glyphs]
    
    @strawberry.field
    async def address_utxos(
        self,
        address: str,
        spent: Optional[bool] = False,
        limit: int = 50
    ) -> List[UTXOType]:
        """Get UTXOs for an address."""
        async with AsyncSessionLocal() as db:
            stmt = select(UTXO).where(UTXO.address == address)
            if spent is not None:
                stmt = stmt.where(UTXO.spent == spent)
            stmt = stmt.limit(limit)
            
            result = await db.execute(stmt)
            utxos = result.scalars().all()
            return [utxo_to_graphql(u) for u in utxos]


# Create the schema
schema = strawberry.Schema(query=Query)

# Create the GraphQL router for FastAPI
graphql_router = GraphQLRouter(schema, path="/graphql")
