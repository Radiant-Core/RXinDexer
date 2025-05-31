# /Users/radiant/Desktop/RXinDexer/src/api/user_container_endpoints.py
# This file defines FastAPI endpoints for user profiles and containers.
# It provides query capabilities for profile management and container content retrieval.

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_, and_, text
from datetime import datetime, timedelta
import logging

from src.models import (
    UserProfile, Container, ContainerHistory, NFTMetadata,
    user_addresses, container_contents,
    get_db
)
from src.api.schemas import (
    UserProfileResponse, UserProfilesListResponse,
    ContainerResponse, ContainersListResponse, ContainerContentsResponse
)
from src.utils.cache import cache_decorator, CACHE_TTL
from src.utils.pagination import PaginationParams, paginate_results

# Remove the prefix here as it's already defined in main.py
router = APIRouter()
logger = logging.getLogger(__name__)

# User profile retrieval
@router.get("/users/{user_id}", response_model=UserProfileResponse, tags=["Users"])
@cache_decorator(ttl=CACHE_TTL)
async def get_user_profile(
    user_id: str,
    db: Session = Depends(get_db)
):
    """
    Retrieve detailed information about a specific user profile.
    
    Parameters:
    - user_id: The unique identifier of the user profile
    
    Returns:
    - Detailed user profile information including linked addresses and owned containers
    """
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail=f"User profile with ID {user_id} not found")
    
    # Get linked addresses
    addresses = db.query(
        user_addresses
    ).filter(
        user_addresses.c.user_id == profile.id
    ).all()
    
    address_links = [
        {
            "address": addr.address,
            "linked_at": addr.linked_at,
            "is_primary": addr.is_primary
        } for addr in addresses
    ]
    
    # Get owned containers
    containers = db.query(Container).filter(Container.owner_id == profile.id).limit(10).all()
    
    container_summaries = [
        {
            "container_id": container.container_id,
            "name": container.name,
            "container_type": container.container_type,
            "content_count": container.content_count,
            "created_at": container.created_at
        } for container in containers
    ]
    
    # Get recent activity (container history and NFT transfers)
    # This is a simplified version that only looks at container history
    # A full implementation would combine multiple activity types
    activity = db.query(ContainerHistory).filter(
        ContainerHistory.actor_address.in_([addr.address for addr in addresses])
    ).order_by(
        desc(ContainerHistory.timestamp)
    ).limit(20).all()
    
    activity_items = [
        {
            "type": "container_" + history.action_type,
            "timestamp": history.timestamp,
            "container_id": db.query(Container.container_id).filter(
                Container.id == history.container_id
            ).scalar(),
            "content_id": history.content_id,
            "content_type": history.content_type,
            "txid": history.txid
        } for history in activity
    ]
    
    # Construct response
    response = {
        "user_id": profile.user_id,
        "username": profile.username,
        "display_name": profile.display_name,
        "bio": profile.bio,
        "avatar_url": profile.avatar_url,
        "nft_count": profile.nft_count,
        "token_count": profile.token_count,
        "container_count": profile.container_count,
        "first_activity": profile.first_activity,
        "last_activity": profile.last_activity,
        "is_verified": profile.is_verified,
        "status": profile.status,
        "addresses": address_links,
        "profile_metadata": profile.profile_metadata,
        "owned_containers": container_summaries,
        "recent_activity": activity_items
    }
    
    return response

# User profile search/list endpoint
@router.get("/users", response_model=UserProfilesListResponse, tags=["Users"])
@cache_decorator(ttl=CACHE_TTL)
async def list_user_profiles(
    username: Optional[str] = None,
    address: Optional[str] = None,
    status: Optional[str] = Query(None, enum=["active", "inactive", "suspended"]),
    sort_by: str = Query("last_activity", enum=["last_activity", "username", "nft_count"]),
    sort_dir: str = Query("desc", enum=["asc", "desc"]),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Search and list user profiles with various filtering options.
    
    Parameters:
    - username: Filter by username (partial match)
    - address: Filter by linked address
    - status: Filter by profile status
    - sort_by: Field to sort by
    - sort_dir: Sort direction
    - limit: Maximum number of profiles to return
    - offset: Number of profiles to skip (for pagination)
    
    Returns:
    - List of user profiles matching the filters with pagination info
    """
    # Build base query
    query = db.query(UserProfile)
    
    # Apply filters
    if username:
        query = query.filter(UserProfile.username.ilike(f"%{username}%"))
    
    if status:
        query = query.filter(UserProfile.status == status)
    
    if address:
        # Join with addresses table
        query = query.join(
            user_addresses,
            user_addresses.c.user_id == UserProfile.id
        ).filter(
            user_addresses.c.address == address
        )
    
    # Count total items
    total_items = query.count()
    
    # Apply sorting
    if sort_by == "username":
        order_col = UserProfile.username
    elif sort_by == "nft_count":
        order_col = UserProfile.nft_count
    else:  # last_activity
        order_col = UserProfile.last_activity
    
    if sort_dir == "asc":
        query = query.order_by(order_col)
    else:
        query = query.order_by(desc(order_col))
    
    # Apply pagination
    profiles = query.offset(offset).limit(limit).all()
    
    # Construct response
    response = {
        "items": [
            {
                "user_id": profile.user_id,
                "username": profile.username,
                "display_name": profile.display_name,
                "avatar_url": profile.avatar_url,
                "nft_count": profile.nft_count,
                "status": profile.status
            } for profile in profiles
        ],
        "pagination": {
            "total": total_items,
            "offset": offset,
            "limit": limit
        }
    }
    
    return response

# Container retrieval endpoint
@router.get("/containers/{container_id}", response_model=ContainerResponse, tags=["Containers"])
@cache_decorator(ttl=CACHE_TTL)
async def get_container(
    container_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    include_history: bool = Query(False),
    history_limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db)
):
    """
    Retrieve detailed information about a container and its contents.
    
    Parameters:
    - container_id: The unique identifier of the container
    - limit: Maximum number of content items to return
    - offset: Number of content items to skip (for pagination)
    - include_history: Whether to include container history
    - history_limit: Maximum number of history entries to return
    
    Returns:
    - Detailed container information including contents and optional history
    """
    container = db.query(Container).filter(Container.container_id == container_id).first()
    if not container:
        raise HTTPException(status_code=404, detail=f"Container {container_id} not found")
    
    # Check if container is public or user has access
    if not container.is_public:
        # In a real implementation, we would check user access here
        # For now, we'll just allow access to all containers
        pass
    
    # Get owner profile if available
    owner_profile = None
    if container.owner_id:
        owner_profile = db.query(UserProfile).filter(UserProfile.id == container.owner_id).first()
    
    # Get container contents with pagination
    # This joins the container_contents table with both containers and NFTs
    
    # First get the count of all contents
    content_count = db.query(func.count(container_contents.c.content_id)).filter(
        container_contents.c.container_id == container.id
    ).scalar()
    
    # Get the content IDs with pagination
    content_items = db.query(
        container_contents.c.content_id,
        container_contents.c.position
    ).filter(
        container_contents.c.container_id == container.id
    ).order_by(
        container_contents.c.position
    ).offset(offset).limit(limit).all()
    
    # Now get the actual content data for each content ID
    contents = []
    for content_item in content_items:
        content_id = content_item.content_id
        
        # Check if it's a container
        sub_container = db.query(Container).filter(Container.id == content_id).first()
        if sub_container:
            contents.append({
                "content_id": sub_container.container_id,
                "content_type": "container",
                "name": sub_container.name,
                "container_type": sub_container.container_type,
                "image_url": None,  # Containers don't have images directly
                "content_count": sub_container.content_count
            })
            continue
        
        # If not a container, check if it's an NFT
        # Note: We need to look up the NFT by token_id, which is in container_history
        nft_token_id = db.query(ContainerHistory.content_id).filter(
            ContainerHistory.container_id == container.id,
            ContainerHistory.content_type == "nft"
        ).first()
        
        if nft_token_id:
            nft = db.query(NFTMetadata).filter(NFTMetadata.token_id == nft_token_id[0]).first()
            if nft:
                contents.append({
                    "content_id": nft.token_id,
                    "content_type": "nft",
                    "name": nft.name,
                    "image_url": nft.image_url
                })
    
    # Get container history if requested
    history = []
    if include_history:
        history_items = db.query(ContainerHistory).filter(
            ContainerHistory.container_id == container.id
        ).order_by(
            desc(ContainerHistory.timestamp)
        ).limit(history_limit).all()
        
        history = [
            {
                "action_type": item.action_type,
                "content_id": item.content_id,
                "content_type": item.content_type,
                "timestamp": item.timestamp,
                "actor_address": item.actor_address,
                "txid": item.txid
            } for item in history_items
        ]
    
    # Construct response
    response = {
        "container_id": container.container_id,
        "name": container.name,
        "description": container.description,
        "container_type": container.container_type,
        "content_count": container.content_count,
        "content_types": container.content_types,
        "owner_address": container.owner_address,
        "owner_profile": {
            "user_id": owner_profile.user_id,
            "username": owner_profile.username,
            "display_name": owner_profile.display_name,
            "avatar_url": owner_profile.avatar_url,
            "nft_count": owner_profile.nft_count,
            "status": owner_profile.status
        } if owner_profile else None,
        "is_public": container.is_public,
        "created_at": container.created_at,
        "updated_at": container.updated_at,
        "creation_txid": container.creation_txid,
        "metadata": container.metadata,
        "contents": contents,
        "history": history,
        "pagination": {
            "total": content_count,
            "offset": offset,
            "limit": limit
        }
    }
    
    return response

# Container contents endpoint
@router.get("/containers/{container_id}/contents", response_model=ContainerContentsResponse, tags=["Containers"])
@cache_decorator(ttl=CACHE_TTL)
async def get_container_contents(
    container_id: str,
    content_type: Optional[str] = Query(None, enum=["nft", "container", "all"]),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Get the contents of a container with filtering and pagination options.
    
    Parameters:
    - container_id: The unique identifier of the container
    - content_type: Type of content to filter by
    - limit: Maximum number of content items to return
    - offset: Number of content items to skip (for pagination)
    
    Returns:
    - List of container contents with pagination info
    """
    container = db.query(Container).filter(Container.container_id == container_id).first()
    if not container:
        raise HTTPException(status_code=404, detail=f"Container {container_id} not found")
    
    # Check if container is public or user has access
    if not container.is_public:
        # In a real implementation, we would check user access here
        # For now, we'll just allow access to all containers
        pass
    
    # Build query for container contents
    # Similar to the previous endpoint but with content type filtering
    
    # First get filtered count
    query = db.query(
        container_contents.c.content_id,
        ContainerHistory.content_type
    ).filter(
        container_contents.c.container_id == container.id
    ).join(
        ContainerHistory,
        and_(
            ContainerHistory.container_id == container.id,
            ContainerHistory.content_id == container_contents.c.content_id
        )
    )
    
    if content_type and content_type != "all":
        query = query.filter(ContainerHistory.content_type == content_type)
    
    total_count = query.count()
    
    # Get content items with pagination
    content_items = query.order_by(
        container_contents.c.position
    ).offset(offset).limit(limit).all()
    
    # Process content items
    contents = []
    for content_item in content_items:
        content_id = content_item.content_id
        content_type = content_item.content_type
        
        if content_type == "container":
            sub_container = db.query(Container).filter(Container.id == content_id).first()
            if sub_container:
                contents.append({
                    "content_id": sub_container.container_id,
                    "content_type": "container",
                    "name": sub_container.name,
                    "container_type": sub_container.container_type
                })
        elif content_type == "nft":
            # Look up NFT by token_id from container history
            nft_token_id = db.query(ContainerHistory.content_id).filter(
                ContainerHistory.container_id == container.id,
                ContainerHistory.content_type == "nft",
                ContainerHistory.content_id == content_id
            ).first()
            
            if nft_token_id:
                nft = db.query(NFTMetadata).filter(NFTMetadata.token_id == nft_token_id[0]).first()
                if nft:
                    contents.append({
                        "content_id": nft.token_id,
                        "content_type": "nft",
                        "name": nft.name,
                        "image_url": nft.image_url
                    })
    
    # Construct response
    response = {
        "container_id": container.container_id,
        "name": container.name,
        "container_type": container.container_type,
        "total_contents": total_count,
        "contents": contents,
        "pagination": {
            "total": total_count,
            "offset": offset,
            "limit": limit
        }
    }
    
    return response

# Container search/list endpoint
@router.get("/containers", response_model=ContainersListResponse, tags=["Containers"])
@cache_decorator(ttl=CACHE_TTL)
async def list_containers(
    owner_address: Optional[str] = None,
    container_type: Optional[str] = None,
    is_public: Optional[bool] = None,
    search: Optional[str] = None,
    sort_by: str = Query("updated_at", enum=["updated_at", "name", "content_count"]),
    sort_dir: str = Query("desc", enum=["asc", "desc"]),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Search and list containers with various filtering options.
    
    Parameters:
    - owner_address: Filter by owner address
    - container_type: Filter by container type
    - is_public: Whether to include only public containers
    - search: Search in name and description
    - sort_by: Field to sort by
    - sort_dir: Sort direction
    - limit: Maximum number of containers to return
    - offset: Number of containers to skip (for pagination)
    
    Returns:
    - List of containers matching the filters with pagination info
    """
    # Build query
    query = db.query(Container)
    
    # Apply filters
    if owner_address:
        query = query.filter(Container.owner_address == owner_address)
    
    if container_type:
        query = query.filter(Container.container_type == container_type)
    
    if is_public:
        query = query.filter(Container.is_public == True)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Container.name.ilike(search_term),
                Container.description.ilike(search_term)
            )
        )
    
    # Count total items
    total_items = query.count()
    
    # Apply sorting
    if sort_by == "created_at":
        order_col = Container.created_at
    elif sort_by == "name":
        order_col = Container.name
    elif sort_by == "content_count":
        order_col = Container.content_count
    else:  # updated_at
        order_col = Container.updated_at
    
    if sort_dir == "asc":
        query = query.order_by(order_col)
    else:
        query = query.order_by(desc(order_col))
    
    # Apply pagination
    containers = query.offset(offset).limit(limit).all()
    
    # Construct response
    response = {
        "items": [
            {
                "container_id": container.container_id,
                "name": container.name,
                "container_type": container.container_type,
                "owner_address": container.owner_address,
                "content_count": container.content_count,
                "is_public": container.is_public
            } for container in containers
        ],
        "pagination": {
            "total": total_items,
            "offset": offset,
            "limit": limit
        }
    }
    
    return response
