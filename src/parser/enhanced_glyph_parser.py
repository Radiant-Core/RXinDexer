# /Users/radiant/Desktop/RXinDexer/src/parser/enhanced_glyph_parser.py
# This file extends the base GlyphParser to support advanced features like NFT metadata extraction,
# collection detection, and container relationship tracking.

import logging
import json
import os
import re
import hashlib
from typing import Dict, List, Any, Optional, Tuple, Set
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import cbor2
import requests
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_
from sqlalchemy.exc import IntegrityError

from src.sync.rpc_client import RadiantRPC
from src.models import (
    GlyphToken, UTXO, Holder, 
    NFTMetadata, NFTCollection, NFTTransfer,
    UserProfile, Container, ContainerHistory,
    user_addresses, container_contents
)
from src.parser.glyph_parser import GlyphParser

logger = logging.getLogger(__name__)

# Get environment variables for configuration
GLYPH_DEEP_INDEXING = os.getenv("GLYPH_DEEP_INDEXING", "true").lower() == "true"
GLYPH_MEDIA_FETCH = os.getenv("GLYPH_MEDIA_FETCH", "true").lower() == "true"
GLYPH_COLLECTION_TRACKING = os.getenv("GLYPH_COLLECTION_TRACKING", "true").lower() == "true"
GLYPH_CONTAINER_DEPTH = int(os.getenv("GLYPH_CONTAINER_DEPTH", "3"))

# Maximum number of concurrent media fetching tasks
MAX_MEDIA_WORKERS = int(os.getenv("MEDIA_FETCH_WORKERS", "4"))

# Media fetch timeout in seconds
MEDIA_FETCH_TIMEOUT = int(os.getenv("MEDIA_FETCH_TIMEOUT", "10"))

# Maximum content size to fetch (5MB)
MAX_MEDIA_SIZE = int(os.getenv("MAX_MEDIA_SIZE", "5242880"))


class EnhancedGlyphParser(GlyphParser):
    """
    Enhanced parser for Glyph tokens with advanced features.
    
    Extends the base GlyphParser to provide:
    - Detailed NFT metadata extraction and processing
    - Collection detection and grouping
    - Container relationship tracking
    - User profile association
    """
    
    def __init__(self, rpc: RadiantRPC, db: Session):
        """
        Initialize the enhanced Glyph token parser.
        
        Args:
            rpc: RPC client for Radiant Node
            db: Database session
        """
        super().__init__(rpc, db)
        self.collections_cache = {}  # Cache to avoid repeat DB lookups
        self.media_fetch_session = requests.Session()
    
    def parse_transaction(self, tx: Dict[str, Any], height: int, block_hash: str) -> List[Dict[str, Any]]:
        """
        Parse a transaction to extract enhanced Glyph token data.
        
        Args:
            tx: Transaction data from Radiant Node
            height: Block height
            block_hash: Block hash
            
        Returns:
            List of extracted token data
        """
        # First, use the base parser to extract the basic token data
        tokens_found = super().parse_transaction(tx, height, block_hash)
        
        # If deep indexing is disabled, return basic results
        if not GLYPH_DEEP_INDEXING:
            return tokens_found
        
        # Process each token for enhanced metadata
        for token_data in tokens_found:
            if not token_data:
                continue
                
            # Determine token type and process accordingly
            token_type = token_data.get("type", "unknown")
            token_ref = token_data.get("ref")
            metadata = token_data.get("metadata", {})
            
            if token_type == "non-fungible":
                # Process as NFT
                self._process_nft(tx, token_ref, metadata, token_data.get("vout", 0), height, block_hash)
            elif token_type == "container":
                # Process as Container
                self._process_container(tx, token_ref, metadata, token_data.get("vout", 0), height, block_hash)
            elif token_type == "profile":
                # Process as User Profile
                self._process_user_profile(tx, token_ref, metadata, token_data.get("vout", 0), height, block_hash)
            elif token_type == "fungible":
                # Process as Fungible Token (basic support for now)
                pass
        
        return tokens_found
    
    def _process_nft(self, tx: Dict[str, Any], token_ref: str, metadata: Dict[str, Any], vout: int, 
                    height: int, block_hash: str) -> None:
        """
        Process a non-fungible token, extracting and storing detailed metadata.
        
        Args:
            tx: Transaction data
            token_ref: Token reference
            metadata: Token metadata
            vout: Output index
            height: Block height
            block_hash: Block hash
        """
        txid = tx.get("txid")
        
        # Check if NFT already exists in database
        nft = self.db.query(NFTMetadata).filter(NFTMetadata.token_id == token_ref).first()
        
        # Extract media URLs from metadata
        media_urls = self._extract_media_urls(metadata)
        
        # Extract attributes from metadata
        attributes = metadata.get("attributes", {})
        if isinstance(attributes, list):
            # Convert attribute list to dictionary
            attr_dict = {}
            for attr in attributes:
                if isinstance(attr, dict) and "trait_type" in attr and "value" in attr:
                    attr_dict[attr["trait_type"]] = attr["value"]
            attributes = attr_dict
        
        # Determine creator address from input
        creator_address = self._get_input_address(tx)
        
        # Determine current owner address from output
        owner_address = self._get_output_address(tx, vout)
        
        if nft:
            # Update existing NFT
            nft.name = metadata.get("name", nft.name)
            nft.description = metadata.get("description", nft.description)
            nft.image_url = media_urls.get("image", nft.image_url)
            nft.animation_url = media_urls.get("animation", nft.animation_url)
            nft.external_url = media_urls.get("external", nft.external_url)
            nft.attributes = attributes or nft.attributes
            nft.owner_address = owner_address
            nft.last_transfer_height = height
            nft.last_transfer_txid = txid
        else:
            # Create new NFT record
            nft = NFTMetadata(
                token_id=token_ref,
                name=metadata.get("name", "Unnamed NFT"),
                description=metadata.get("description", ""),
                image_url=media_urls.get("image", ""),
                animation_url=media_urls.get("animation", ""),
                external_url=media_urls.get("external", ""),
                attributes=attributes,
                creator_address=creator_address,
                owner_address=owner_address,
                creation_height=height,
                creation_txid=txid,
                last_transfer_height=height,
                last_transfer_txid=txid
            )
            self.db.add(nft)
        
        # Detect and link to collection if collection tracking is enabled
        if GLYPH_COLLECTION_TRACKING:
            collection_id = self._detect_collection(metadata, token_ref)
            if collection_id:
                nft.collection_id = collection_id
        
        # Commit NFT to database
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.error(f"Failed to save NFT metadata for token {token_ref}")
        
        # Create transfer record if this isn't the creation transaction
        if nft.creation_txid != txid:
            self._create_nft_transfer(tx, token_ref, creator_address, owner_address, height, block_hash)
        
        # Fetch media if configured to do so
        if GLYPH_MEDIA_FETCH and media_urls.get("image"):
            self._fetch_media_async(token_ref, media_urls.get("image"), "image")
    
    def _process_container(self, tx: Dict[str, Any], token_ref: str, metadata: Dict[str, Any], vout: int, 
                          height: int, block_hash: str) -> None:
        """
        Process a container token, tracking contents and relationships.
        
        Args:
            tx: Transaction data
            token_ref: Token reference
            metadata: Token metadata
            vout: Output index
            height: Block height
            block_hash: Block hash
        """
        txid = tx.get("txid")
        
        # Check if container already exists
        container = self.db.query(Container).filter(Container.container_id == token_ref).first()
        
        # Determine owner address from output
        owner_address = self._get_output_address(tx, vout)
        
        # Get container type from metadata
        container_type = metadata.get("type", "collection")
        
        # Get content IDs if available
        content_ids = metadata.get("contents", [])
        if isinstance(content_ids, dict):
            content_ids = list(content_ids.values())
        
        content_types = metadata.get("content_types", [])
        
        # Find owner user profile if it exists
        owner_profile = None
        if owner_address:
            owner_profile = self.db.query(UserProfile).join(
                user_addresses, 
                user_addresses.c.user_id == UserProfile.id
            ).filter(
                user_addresses.c.address == owner_address
            ).first()
        
        if container:
            # Update existing container
            container.name = metadata.get("name", container.name)
            container.description = metadata.get("description", container.description)
            container.container_type = container_type
            container.content_count = len(content_ids)
            container.content_types = content_types
            container.owner_address = owner_address
            container.owner_id = owner_profile.id if owner_profile else None
            container.updated_at = datetime.utcnow()
            container.metadata = metadata
        else:
            # Create new container
            container = Container(
                container_id=token_ref,
                name=metadata.get("name", "Unnamed Container"),
                description=metadata.get("description", ""),
                container_type=container_type,
                content_count=len(content_ids),
                content_types=content_types,
                owner_address=owner_address,
                owner_id=owner_profile.id if owner_profile else None,
                is_public=metadata.get("public", True),
                creation_txid=txid,
                metadata=metadata
            )
            self.db.add(container)
        
        # Commit container to database
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.error(f"Failed to save container for token {token_ref}")
            return
        
        # Record container history
        history_entry = ContainerHistory(
            container_id=container.id,
            action_type="create" if container.creation_txid == txid else "update",
            txid=txid,
            block_height=height,
            timestamp=datetime.utcnow(),
            actor_address=owner_address,
            metadata={"content_count": len(content_ids)}
        )
        self.db.add(history_entry)
        
        # Process container contents if available and container depth allows
        self._process_container_contents(container, content_ids, owner_address, txid, height)
    
    def _process_user_profile(self, tx: Dict[str, Any], token_ref: str, metadata: Dict[str, Any], vout: int, 
                             height: int, block_hash: str) -> None:
        """
        Process a user profile token, tracking user identity and linked addresses.
        
        Args:
            tx: Transaction data
            token_ref: Token reference
            metadata: Token metadata
            vout: Output index
            height: Block height
            block_hash: Block hash
        """
        txid = tx.get("txid")
        
        # Check if profile already exists
        profile = self.db.query(UserProfile).filter(UserProfile.user_id == token_ref).first()
        
        # Determine owner address from output
        owner_address = self._get_output_address(tx, vout)
        
        # Extract profile information
        username = metadata.get("username", "")
        display_name = metadata.get("display_name", "")
        bio = metadata.get("bio", "")
        avatar_url = metadata.get("avatar_url", "")
        
        if profile:
            # Update existing profile
            profile.username = username or profile.username
            profile.display_name = display_name or profile.display_name
            profile.bio = bio or profile.bio
            profile.avatar_url = avatar_url or profile.avatar_url
            profile.profile_metadata = metadata
            profile.updated_at = datetime.utcnow()
        else:
            # Create new profile
            profile = UserProfile(
                user_id=token_ref,
                username=username,
                display_name=display_name,
                bio=bio,
                avatar_url=avatar_url,
                profile_metadata=metadata,
                first_activity=datetime.utcnow(),
                last_activity=datetime.utcnow()
            )
            self.db.add(profile)
        
        # Commit profile to database
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.error(f"Failed to save user profile for token {token_ref}")
            return
        
        # Associate address with profile
        if owner_address:
            # Check if address is already linked
            address_link = self.db.query(user_addresses).filter(
                user_addresses.c.user_id == profile.id,
                user_addresses.c.address == owner_address
            ).first()
            
            if not address_link:
                # Add new address link
                self.db.execute(
                    user_addresses.insert().values(
                        user_id=profile.id,
                        address=owner_address,
                        linked_at=datetime.utcnow(),
                        is_primary=True if not profile.addresses else False
                    )
                )
        
        # Update activity timestamps
        profile.last_activity = datetime.utcnow()
        
        self.db.commit()
    
    def _extract_media_urls(self, metadata: Dict[str, Any]) -> Dict[str, str]:
        """
        Extract media URLs from token metadata.
        
        Args:
            metadata: Token metadata dictionary
            
        Returns:
            Dictionary with media URLs by type
        """
        urls = {}
        
        # Extract image URL
        if "image" in metadata:
            urls["image"] = metadata["image"]
        elif "image_url" in metadata:
            urls["image"] = metadata["image_url"]
        elif "thumbnail" in metadata:
            urls["image"] = metadata["thumbnail"]
        
        # Extract animation URL
        if "animation_url" in metadata:
            urls["animation"] = metadata["animation_url"]
        elif "animation" in metadata:
            urls["animation"] = metadata["animation"]
        elif "video" in metadata:
            urls["animation"] = metadata["video"]
        
        # Extract external URL
        if "external_url" in metadata:
            urls["external"] = metadata["external_url"]
        elif "external_link" in metadata:
            urls["external"] = metadata["external_link"]
        
        # Normalize URLs - ensure they start with http/https/ipfs
        for key, url in urls.items():
            if url and isinstance(url, str):
                # Handle IPFS URLs
                if url.startswith("ipfs://"):
                    urls[key] = f"https://ipfs.io/ipfs/{url[7:]}"
                # Ensure URLs have protocol
                elif not url.startswith("http"):
                    urls[key] = f"https://{url}"
        
        return urls
    
    def _get_input_address(self, tx: Dict[str, Any]) -> Optional[str]:
        """
        Get the first input address of a transaction.
        
        Args:
            tx: Transaction data
            
        Returns:
            Address string or None if not found
        """
        if not tx.get("vin"):
            return None
            
        for vin in tx.get("vin", []):
            if "txid" in vin and "vout" in vin:
                # Look up the previous transaction output
                prev_txid = vin["txid"]
                prev_vout = vin["vout"]
                
                try:
                    # Query the UTXO table for the address
                    prev_utxo = self.db.query(UTXO).filter(
                        UTXO.txid == prev_txid,
                        UTXO.vout == prev_vout
                    ).first()
                    
                    if prev_utxo and prev_utxo.address:
                        return prev_utxo.address
                except Exception as e:
                    logger.debug(f"Error finding input address: {str(e)}")
        
        return None
    
    def _get_output_address(self, tx: Dict[str, Any], vout_idx: int) -> Optional[str]:
        """
        Get the address for a specific output of a transaction.
        
        Args:
            tx: Transaction data
            vout_idx: Output index
            
        Returns:
            Address string or None if not found
        """
        vouts = tx.get("vout", [])
        if not vouts or vout_idx >= len(vouts):
            return None
        
        vout = vouts[vout_idx]
        if "scriptPubKey" in vout and "addresses" in vout["scriptPubKey"]:
            addresses = vout["scriptPubKey"]["addresses"]
            if addresses and len(addresses) > 0:
                return addresses[0]
        
        return None
    
    def _detect_collection(self, metadata: Dict[str, Any], token_ref: str) -> Optional[str]:
        """
        Detect which collection an NFT belongs to based on its metadata.
        
        Args:
            metadata: Token metadata
            token_ref: Token reference
            
        Returns:
            Collection ID or None if not detected
        """
        # First check if collection ID is explicitly specified
        if "collection" in metadata:
            collection_data = metadata["collection"]
            if isinstance(collection_data, str):
                # Direct collection ID reference
                return collection_data
            elif isinstance(collection_data, dict) and "id" in collection_data:
                # Collection object with ID
                return collection_data["id"]
        
        # Check for collection name
        collection_name = None
        if "collection_name" in metadata:
            collection_name = metadata["collection_name"]
        elif "collection" in metadata and isinstance(metadata["collection"], dict):
            collection_name = metadata["collection"].get("name")
        
        if collection_name:
            # Look for existing collection with this name
            collection = self.db.query(NFTCollection).filter(
                NFTCollection.name == collection_name
            ).first()
            
            if collection:
                return collection.collection_id
            
            # No existing collection, create a new one
            collection_id = f"collection:{hashlib.sha256(collection_name.encode()).hexdigest()[:16]}"
            
            # Create new collection
            collection = NFTCollection(
                collection_id=collection_id,
                name=collection_name,
                description=metadata.get("collection", {}).get("description", ""),
                creator_address=None,  # Will be updated when we know more
                banner_image_url=metadata.get("collection", {}).get("image", ""),
                external_url=metadata.get("collection", {}).get("external_url", ""),
                created_at=datetime.utcnow(),
                metadata=metadata.get("collection", {})
            )
            
            try:
                self.db.add(collection)
                self.db.commit()
                return collection_id
            except IntegrityError:
                self.db.rollback()
                logger.error(f"Failed to create collection for {collection_name}")
        
        # Try to detect collection from token prefix pattern
        # Many NFT projects use a common prefix in token IDs
        if ":" in token_ref:
            prefix = token_ref.split(":")[0]
            if len(prefix) > 3:
                # Check if we have other NFTs with the same prefix
                count = self.db.query(NFTMetadata).filter(
                    NFTMetadata.token_id.like(f"{prefix}:%")
                ).count()
                
                if count > 0:
                    # Look for existing collection with this prefix
                    collection = self.db.query(NFTCollection).filter(
                        NFTCollection.token_prefix == prefix
                    ).first()
                    
                    if collection:
                        return collection.collection_id
                    
                    # Create a new collection based on the prefix
                    collection_id = f"collection:{prefix}"
                    
                    # Try to infer a name from the prefix
                    inferred_name = prefix.replace("_", " ").title()
                    
                    collection = NFTCollection(
                        collection_id=collection_id,
                        name=inferred_name,
                        token_prefix=prefix,
                        created_at=datetime.utcnow()
                    )
                    
                    try:
                        self.db.add(collection)
                        self.db.commit()
                        return collection_id
                    except IntegrityError:
                        self.db.rollback()
                        logger.error(f"Failed to create collection for prefix {prefix}")
        
        return None
    
    def _create_nft_transfer(self, tx: Dict[str, Any], token_id: str, from_address: Optional[str], 
                           to_address: Optional[str], height: int, block_hash: str) -> None:
        """
        Create an NFT transfer record.
        
        Args:
            tx: Transaction data
            token_id: NFT token ID
            from_address: Sender address (None for mints)
            to_address: Recipient address
            height: Block height
            block_hash: Block hash
        """
        txid = tx.get("txid")
        timestamp = datetime.utcnow()
        
        # Get the value/price if available (for sales)
        value = 0
        try:
            for vout in tx.get("vout", []):
                if vout.get("value") and "scriptPubKey" in vout:
                    if "addresses" in vout["scriptPubKey"] and from_address in vout["scriptPubKey"]["addresses"]:
                        value += float(vout["value"])
        except Exception as e:
            logger.debug(f"Error calculating transfer value: {str(e)}")
        
        # Create transfer record
        transfer = NFTTransfer(
            token_id=token_id,
            transaction_id=txid,
            block_height=height,
            block_hash=block_hash,
            from_address=from_address,
            to_address=to_address,
            timestamp=timestamp,
            value=str(value) if value > 0 else None
        )
        
        try:
            self.db.add(transfer)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.error(f"Failed to create transfer record for token {token_id} in tx {txid}")
    
    def _fetch_media_async(self, token_id: str, url: str, media_type: str) -> None:
        """
        Asynchronously fetch media from URL and store metadata.
        
        Args:
            token_id: NFT token ID
            url: Media URL to fetch
            media_type: Type of media (image, animation, etc.)
        """
        # Run in a thread pool to avoid blocking
        with ThreadPoolExecutor(max_workers=MAX_MEDIA_WORKERS) as executor:
            executor.submit(self._fetch_media, token_id, url, media_type)
    
    def _fetch_media(self, token_id: str, url: str, media_type: str) -> None:
        """
        Fetch media from URL and store metadata.
        
        Args:
            token_id: NFT token ID
            url: Media URL to fetch
            media_type: Type of media (image, animation, etc.)
        """
        try:
            # Fetch with timeout and size limit
            response = self.media_fetch_session.get(
                url, 
                timeout=MEDIA_FETCH_TIMEOUT,
                stream=True,
                headers={"User-Agent": "RXinDexer/1.0"}
            )
            
            if response.status_code != 200:
                logger.warning(f"Failed to fetch media for {token_id}: HTTP {response.status_code}")
                return
            
            # Check content type
            content_type = response.headers.get("Content-Type", "")
            
            # Get content length if available
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_MEDIA_SIZE:
                logger.warning(f"Media too large for {token_id}: {content_length} bytes")
                response.close()
                return
                
            # Read the first chunk to determine size and metadata
            # We're not storing the media content itself, just metadata
            chunk = next(response.iter_content(4096), None)
            response.close()
            
            if not chunk:
                logger.warning(f"Empty media content for {token_id}")
                return
            
            # Update NFT with media metadata
            nft = self.db.query(NFTMetadata).filter(NFTMetadata.token_id == token_id).first()
            if nft:
                media_metadata = nft.media_metadata or {}
                media_metadata[media_type] = {
                    "content_type": content_type,
                    "size": content_length,
                    "last_checked": datetime.utcnow().isoformat(),
                    "status": "verified"
                }
                nft.media_metadata = media_metadata
                self.db.commit()
            
        except Exception as e:
            logger.error(f"Error fetching media for {token_id}: {str(e)}")
    
    def _process_container_contents(self, container: Container, content_ids: List[str], 
                                   owner_address: str, txid: str, height: int, depth: int = 0) -> None:
        """
        Process the contents of a container, creating relationships between containers and their contents.
        
        Args:
            container: Container object
            content_ids: List of content IDs in the container
            owner_address: Address of the container owner
            txid: Transaction ID
            height: Block height
            depth: Current depth of container processing (for nested containers)
        """
        # Stop if we've reached the maximum depth
        if depth >= GLYPH_CONTAINER_DEPTH:
            return
        
        # Process each content item
        for position, content_id in enumerate(content_ids):
            # Check if the content ID refers to an NFT
            nft = self.db.query(NFTMetadata).filter(NFTMetadata.token_id == content_id).first()
            if nft:
                # Create history entry for adding NFT to container
                history_entry = ContainerHistory(
                    container_id=container.id,
                    action_type="add_content",
                    content_id=content_id,
                    content_type="nft",
                    txid=txid,
                    block_height=height,
                    timestamp=datetime.utcnow(),
                    actor_address=owner_address
                )
                self.db.add(history_entry)
                continue
            
            # Check if the content ID refers to another container
            sub_container = self.db.query(Container).filter(Container.container_id == content_id).first()
            if sub_container:
                # Add relationship between containers
                self.db.execute(
                    container_contents.insert().values(
                        container_id=container.id,
                        content_id=sub_container.id,
                        position=position,
                        added_at=datetime.utcnow()
                    )
                )
                
                # Create history entry for adding container to container
                history_entry = ContainerHistory(
                    container_id=container.id,
                    action_type="add_content",
                    content_id=content_id,
                    content_type="container",
                    txid=txid,
                    block_height=height,
                    timestamp=datetime.utcnow(),
                    actor_address=owner_address
                )
                self.db.add(history_entry)
                
                # Recursively process nested container (if depth allows)
                if depth < GLYPH_CONTAINER_DEPTH - 1:
                    sub_metadata = sub_container.metadata or {}
                    sub_content_ids = sub_metadata.get("contents", [])
                    if isinstance(sub_content_ids, dict):
                        sub_content_ids = list(sub_content_ids.values())
                    
                    self._process_container_contents(
                        sub_container, sub_content_ids, owner_address, txid, height, depth + 1
                    )
        
        self.db.commit()
