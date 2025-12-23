"""
Author Resolution Module

Resolves author references to get author name and image from the referenced token.
"""

import logging
from typing import Dict, Optional, Tuple
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def resolve_author(db: Session, author_ref: str) -> Dict:
    """
    Resolve an author reference to get the author's name and image.
    
    The author ref points to another token (usually an NFT profile) that contains
    the author's name and image.
    
    Args:
        db: Database session
        author_ref: The author's token reference (from 'by' field)
        
    Returns:
        dict with 'name', 'image_url', 'image_data' or empty dict if not found
    """
    if not author_ref:
        return {}
    
    # First, check if we already have this token in our database
    result = db.execute(text("""
        SELECT name, ticker, icon_url, icon_data, icon_mime_type, token_metadata
        FROM glyph_tokens
        WHERE token_id = :ref
        LIMIT 1
    """), {'ref': author_ref})
    
    row = result.fetchone()
    if row:
        return {
            'name': row.name or row.ticker,
            'image_url': row.icon_url,
            'image_data': row.icon_data,
            'image_mime_type': row.icon_mime_type,
        }
    
    # Also check NFTs table
    result = db.execute(text("""
        SELECT nft_metadata
        FROM nfts
        WHERE token_id = :ref
        LIMIT 1
    """), {'ref': author_ref})
    
    row = result.fetchone()
    if row and row.nft_metadata:
        metadata = row.nft_metadata
        if isinstance(metadata, dict):
            return {
                'name': metadata.get('name'),
                'image_url': metadata.get('image') or metadata.get('icon_url'),
                'image_data': metadata.get('icon_data'),
            }
    
    # Token not found in database
    logger.debug(f"Author ref {author_ref[:16]}... not found in database")
    return {}


def resolve_container(db: Session, container_ref: str) -> Dict:
    """
    Resolve a container reference to get the container's name and metadata.
    
    Args:
        db: Database session
        container_ref: The container's token reference (from 'in' field)
        
    Returns:
        dict with 'name', 'description' or empty dict if not found
    """
    if not container_ref:
        return {}
    
    # Check containers table first
    result = db.execute(text("""
        SELECT name, description, owner, token_count
        FROM containers
        WHERE container_id = :ref
        LIMIT 1
    """), {'ref': container_ref})
    
    row = result.fetchone()
    if row:
        return {
            'name': row.name,
            'description': row.description,
            'owner': row.owner,
            'token_count': row.token_count,
        }
    
    # Check glyph_tokens
    result = db.execute(text("""
        SELECT name, description, owner
        FROM glyph_tokens
        WHERE token_id = :ref
        LIMIT 1
    """), {'ref': container_ref})
    
    row = result.fetchone()
    if row:
        return {
            'name': row.name,
            'description': row.description,
            'owner': row.owner,
        }
    
    logger.debug(f"Container ref {container_ref[:16]}... not found in database")
    return {}


def update_token_author_info(db: Session, token_id: str, author_ref: str) -> bool:
    """
    Resolve author ref and update the token's author info cache.
    
    Args:
        db: Database session
        token_id: Token to update
        author_ref: Author reference to resolve
        
    Returns:
        True if updated
    """
    author_info = resolve_author(db, author_ref)
    
    if not author_info:
        return False
    
    db.execute(text("""
        UPDATE glyph_tokens
        SET author_name = :name,
            author_image_url = :image_url,
            author_image_data = :image_data
        WHERE token_id = :token_id
    """), {
        'token_id': token_id,
        'name': author_info.get('name'),
        'image_url': author_info.get('image_url'),
        'image_data': author_info.get('image_data'),
    })
    
    return True


def batch_resolve_authors(db: Session, batch_size: int = 100) -> int:
    """
    Resolve authors for all tokens that have an author ref but no author_name.
    
    Args:
        db: Database session
        batch_size: Number of tokens to process per batch
        
    Returns:
        Number of tokens updated
    """
    updated = 0
    offset = 0
    
    while True:
        # Find tokens with author ref but no resolved name
        result = db.execute(text("""
            SELECT token_id, author
            FROM glyph_tokens
            WHERE author IS NOT NULL 
            AND author != ''
            AND (author_name IS NULL OR author_name = '')
            ORDER BY token_id
            LIMIT :limit OFFSET :offset
        """), {'limit': batch_size, 'offset': offset})
        
        tokens = result.fetchall()
        if not tokens:
            break
        
        for row in tokens:
            if update_token_author_info(db, row.token_id, row.author):
                updated += 1
        
        offset += batch_size
        db.commit()
        
        if updated > 0 and updated % 500 == 0:
            logger.info(f"Resolved authors for {updated} tokens...")
    
    logger.info(f"Author resolution complete. Updated {updated} tokens.")
    return updated


def batch_resolve_containers(db: Session, batch_size: int = 100) -> int:
    """
    Ensure all container refs are in the containers table.
    
    Args:
        db: Database session
        batch_size: Number to process per batch
        
    Returns:
        Number of containers added/updated
    """
    updated = 0
    
    # Find all unique container refs
    result = db.execute(text("""
        SELECT DISTINCT container
        FROM glyph_tokens
        WHERE container IS NOT NULL AND container != ''
    """))
    
    container_refs = [row.container for row in result.fetchall()]
    
    for container_ref in container_refs:
        # Check if already in containers table
        exists = db.execute(text("""
            SELECT 1 FROM containers WHERE container_id = :ref
        """), {'ref': container_ref}).fetchone()
        
        if exists:
            continue
        
        # Try to get container info from glyph_tokens
        container_info = resolve_container(db, container_ref)
        
        # Count tokens in this container
        count_result = db.execute(text("""
            SELECT COUNT(*) FROM glyph_tokens WHERE container = :ref
        """), {'ref': container_ref})
        token_count = count_result.scalar() or 0
        
        # Insert into containers table
        db.execute(text("""
            INSERT INTO containers (container_id, name, description, owner, token_count)
            VALUES (:ref, :name, :desc, :owner, :count)
            ON CONFLICT (container_id) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, containers.name),
                token_count = :count
        """), {
            'ref': container_ref,
            'name': container_info.get('name'),
            'desc': container_info.get('description'),
            'owner': container_info.get('owner'),
            'count': token_count,
        })
        
        updated += 1
        
        if updated % 100 == 0:
            db.commit()
            logger.info(f"Processed {updated} containers...")
    
    db.commit()
    logger.info(f"Container resolution complete. Added/updated {updated} containers.")
    return updated
