# /Users/radiant/Desktop/RXinDexer/src/db/seed_test_data.py
# This file seeds the database with test data for development and testing purposes.
# It creates sample NFTs, collections, user profiles, and containers.

import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
import random
import uuid
import json

# Add parent directory to path for imports
parent_dir = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(parent_dir))

from sqlalchemy.orm import Session
from src.models.database import engine
from src.models import (
    NFTMetadata, NFTCollection, NFTTransfer,
    UserProfile, Container, ContainerHistory, 
    user_addresses, container_contents
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def generate_random_address():
    """Generate a random Radiant blockchain address."""
    return f"rx1{uuid.uuid4().hex[:34]}"

def generate_random_txid():
    """Generate a random transaction ID."""
    return uuid.uuid4().hex

def seed_nft_collections(db: Session, num_collections=5):
    """Seed NFT collections."""
    logger.info(f"Creating {num_collections} NFT collections...")
    collections = []
    
    for i in range(num_collections):
        collection_id = f"collection-{i+1}"
        creator_address = generate_random_address()
        
        collection = NFTCollection(
            collection_id=collection_id,
            name=f"Test Collection {i+1}",
            description=f"This is a test collection #{i+1} for development",
            creator_address=creator_address,
            banner_image_url=f"https://example.com/collections/{i+1}/banner.jpg",
            external_url=f"https://example.com/collections/{i+1}",
            floor_price=str(random.randint(10, 1000)),
            total_volume=str(random.randint(1000, 10000)),
            metadata={
                "category": random.choice(["art", "gaming", "collectibles", "photography"]),
                "verified": random.choice([True, False]),
                "created_at": (datetime.now() - timedelta(days=random.randint(1, 100))).isoformat()
            }
        )
        
        db.add(collection)
        collections.append(collection)
    
    db.commit()
    logger.info(f"Created {len(collections)} collections successfully!")
    return collections

def seed_nfts(db: Session, collections, num_nfts=20):
    """Seed NFT metadata."""
    logger.info(f"Creating {num_nfts} NFTs...")
    nfts = []
    
    for i in range(num_nfts):
        token_id = f"token-{i+1}"
        collection = random.choice(collections) if collections else None
        creator_address = generate_random_address()
        owner_address = generate_random_address()
        
        creation_txid = generate_random_txid()
        last_transfer_txid = generate_random_txid()
        
        nft = NFTMetadata(
            token_id=token_id,
            name=f"Test NFT #{i+1}",
            description=f"This is test NFT #{i+1} for development and testing",
            image_url=f"https://example.com/nfts/{i+1}.jpg",
            animation_url=f"https://example.com/nfts/{i+1}.mp4" if random.random() > 0.7 else None,
            external_url=f"https://example.com/nfts/{i+1}",
            attributes={
                "trait_type": random.choice(["common", "rare", "epic", "legendary"]),
                "color": random.choice(["red", "blue", "green", "yellow", "purple"]),
                "level": random.randint(1, 100)
            },
            owner_address=owner_address,
            creator_address=creator_address,
            creation_height=random.randint(1000, 100000),
            creation_txid=creation_txid,
            last_transfer_height=random.randint(1000, 100000),
            last_transfer_txid=last_transfer_txid,
            collection_id=collection.collection_id if collection else None,
            media_metadata={
                "mime_type": "image/jpeg",
                "size": random.randint(100000, 5000000),
                "dimensions": f"{random.randint(500, 2000)}x{random.randint(500, 2000)}"
            }
        )
        
        db.add(nft)
        nfts.append(nft)
        
        # Add transfer history
        transfer = NFTTransfer(
            token_id=token_id,
            transaction_id=last_transfer_txid,
            from_address=creator_address,
            to_address=owner_address,
            timestamp=datetime.now() - timedelta(days=random.randint(1, 30)),
            block_height=random.randint(1000, 100000),
            block_hash=generate_random_txid(),
            value="1"
        )
        
        db.add(transfer)
    
    db.commit()
    logger.info(f"Created {len(nfts)} NFTs successfully!")
    return nfts

def seed_user_profiles(db: Session, num_users=10):
    """Seed user profiles."""
    logger.info(f"Creating {num_users} user profiles...")
    users = []
    
    for i in range(num_users):
        user_id = f"user-{i+1}"
        
        # Create a list of addresses to store in profile metadata
        user_addresses_list = []
        for j in range(random.randint(1, 3)):
            address = generate_random_address()
            user_addresses_list.append({
                "address": address,
                "linked_at": (datetime.now() - timedelta(days=random.randint(0, 30))).isoformat(),
                "is_primary": (j == 0)  # First address is primary
            })
        
        # Create social and preferences metadata
        social_data = {
            "twitter": f"@testuser{i+1}",
            "discord": f"testuser{i+1}#1234"
        }
        
        preferences_data = {
            "theme": random.choice(["light", "dark", "system"]),
            "notifications": random.choice([True, False])
        }
        
        # Create user profile with addresses in metadata
        user = UserProfile(
            user_id=user_id,
            username=f"testuser{i+1}",
            display_name=f"Test User {i+1}",
            bio=f"This is a test user profile #{i+1} for development",
            avatar_url=f"https://example.com/avatars/{i+1}.jpg",
            nft_count=random.randint(0, 10),
            token_count=random.randint(0, 50),
            container_count=random.randint(0, 5),
            first_activity=datetime.now() - timedelta(days=random.randint(10, 100)),
            last_activity=datetime.now() - timedelta(days=random.randint(0, 10)),
            is_verified=random.choice([True, False]),
            status=random.choice(["active", "inactive"]),
            # Store addresses directly in profile data instead of using the association table
            profile_data={
                "addresses": user_addresses_list,
                "social": social_data,
                "preferences": preferences_data
            }
        )
        
        db.add(user)
        users.append(user)
    
    db.commit()
    logger.info(f"Created {len(users)} user profiles successfully!")
    return users

def seed_containers(db: Session, users, nfts, num_containers=15):
    """Seed containers and container contents."""
    logger.info(f"Creating {num_containers} containers...")
    containers = []
    
    container_types = ["collection", "album", "folder", "playlist", "showcase"]
    
    for i in range(num_containers):
        container_id = f"container-{i+1}"
        user = random.choice(users) if users else None
        
        # Prepare container contents
        num_contents = random.randint(0, min(5, len(nfts)))
        selected_nfts = random.sample(nfts, num_contents) if num_contents > 0 else []
        
        # Create container history and content metadata
        history_entries = []
        content_entries = []
        
        for j, nft in enumerate(selected_nfts):
            # Add to content entries
            content_entries.append({
                "content_id": nft.token_id,
                "content_type": "nft",
                "position": j,
                "name": nft.name,
                "image_url": nft.image_url,
                "added_at": (datetime.now() - timedelta(days=random.randint(0, 20))).isoformat()
            })
            
            # Add to history entries
            history_entries.append({
                "action_type": "add",
                "content_id": nft.token_id,
                "content_type": "nft",
                "timestamp": (datetime.now() - timedelta(days=random.randint(0, 20))).isoformat(),
                "actor_address": user.user_id if user else generate_random_address(),
                "txid": generate_random_txid()
            })
        
        # Create container with content data in metadata
        container = Container(
            container_id=container_id,
            name=f"Test Container {i+1}",
            description=f"This is a test container #{i+1} for development",
            container_type=random.choice(container_types),
            owner_id=user.id if user else None,
            is_public=random.choice([True, False]),
            created_at=datetime.now() - timedelta(days=random.randint(1, 50)),
            updated_at=datetime.now() - timedelta(days=random.randint(0, 30)),
            content_count=len(content_entries),
            metadata={
                "category": random.choice(["personal", "shared", "curated"]),
                "tags": random.sample(["art", "gaming", "favorite", "rare", "investment"], random.randint(1, 3)),
                "contents": content_entries,
                "history": history_entries
            }
        )
        
        db.add(container)
        containers.append(container)
        
        # Also create one history entry in the actual database table for each container
        if content_entries:
            # Just add the first content item to history for demonstration
            first_content = content_entries[0]
            history_entry = ContainerHistory(
                container_id=container_id,  # Use string ID instead of numeric ID
                action_type="create",
                content_id=first_content["content_id"],
                content_type=first_content["content_type"],
                timestamp=datetime.now() - timedelta(days=random.randint(0, 20)),
                actor_address=user.user_id if user else generate_random_address(),
                txid=generate_random_txid()
            )
            db.add(history_entry)
    
    db.commit()
    logger.info(f"Created {len(containers)} containers with contents successfully!")
    return containers

def seed_test_data():
    """Main function to seed all test data."""
    logger.info("Starting database seeding...")
    
    with Session(engine) as db:
        try:
            # Check if we already have data
            existing_nfts = db.query(NFTMetadata).count()
            existing_users = db.query(UserProfile).count()
            
            if existing_nfts > 0 or existing_users > 0:
                logger.info(f"Database already has data: {existing_nfts} NFTs, {existing_users} users")
                response = input("Do you want to add more test data? (y/n): ")
                if response.lower() != 'y':
                    logger.info("Seeding cancelled by user.")
                    return
            
            # Seed data in order of dependencies
            collections = seed_nft_collections(db)
            nfts = seed_nfts(db, collections)
            users = seed_user_profiles(db)
            containers = seed_containers(db, users, nfts)
            
            logger.info("Database seeding completed successfully!")
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error seeding database: {str(e)}")
            raise
    
if __name__ == "__main__":
    seed_test_data()
