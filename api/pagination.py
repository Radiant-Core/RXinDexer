"""
Cursor-based pagination utilities for RXinDexer API.

Provides consistent, performant pagination across all list endpoints.
Cursor-based pagination maintains consistent performance regardless of offset depth.
"""

import base64
import json
from typing import Optional, TypeVar, Generic, List, Any, Callable
from dataclasses import dataclass
from pydantic import BaseModel


T = TypeVar('T')


class CursorInfo(BaseModel):
    """Decoded cursor information."""
    id: int
    direction: str = "after"  # "after" or "before"


class PaginatedResponse(BaseModel):
    """Standard paginated response format."""
    items: List[Any]
    total: Optional[int] = None
    limit: int
    has_next: bool = False
    has_prev: bool = False
    next_cursor: Optional[str] = None
    prev_cursor: Optional[str] = None
    # For backward compatibility with offset pagination
    offset: Optional[int] = None
    page: Optional[int] = None


def encode_cursor(item_id: int, direction: str = "after") -> str:
    """
    Encode an item ID into an opaque cursor string.
    
    Args:
        item_id: The database ID to encode
        direction: "after" or "before"
    
    Returns:
        Base64-encoded cursor string
    """
    data = json.dumps({"id": item_id, "d": direction})
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> Optional[CursorInfo]:
    """
    Decode a cursor string back to its components.
    
    Args:
        cursor: Base64-encoded cursor string
    
    Returns:
        CursorInfo with id and direction, or None if invalid
    """
    if not cursor:
        return None
    
    try:
        # Add padding if needed
        padding = 4 - (len(cursor) % 4)
        if padding != 4:
            cursor += "=" * padding
        
        data = json.loads(base64.urlsafe_b64decode(cursor).decode())
        return CursorInfo(
            id=int(data.get("id", 0)),
            direction=data.get("d", "after")
        )
    except Exception:
        return None


def paginate_query(
    query,
    id_column,
    limit: int,
    cursor: Optional[str] = None,
    offset: Optional[int] = None,
    order_desc: bool = True,
):
    """
    Apply cursor-based or offset pagination to a SQLAlchemy query.
    
    Args:
        query: SQLAlchemy query object
        id_column: The column to use for cursor (usually Model.id)
        limit: Maximum items to return
        cursor: Optional cursor string for cursor-based pagination
        offset: Optional offset for backward-compatible offset pagination
        order_desc: True for descending order (newest first), False for ascending
    
    Returns:
        tuple: (paginated_query, cursor_info)
    """
    cursor_info = decode_cursor(cursor) if cursor else None
    
    if cursor_info:
        # Cursor-based pagination
        if cursor_info.direction == "after":
            if order_desc:
                query = query.filter(id_column < cursor_info.id)
            else:
                query = query.filter(id_column > cursor_info.id)
        else:  # before
            if order_desc:
                query = query.filter(id_column > cursor_info.id)
            else:
                query = query.filter(id_column < cursor_info.id)
    elif offset is not None and offset > 0:
        # Offset-based pagination (backward compatibility)
        query = query.offset(offset)
    
    # Apply ordering
    if order_desc:
        query = query.order_by(id_column.desc())
    else:
        query = query.order_by(id_column.asc())
    
    # Fetch one extra to determine if there are more results
    query = query.limit(limit + 1)
    
    return query, cursor_info


def build_paginated_response(
    items: List[Any],
    limit: int,
    cursor_info: Optional[CursorInfo] = None,
    offset: Optional[int] = None,
    page: Optional[int] = None,
    total: Optional[int] = None,
    id_extractor: Callable[[Any], int] = lambda x: x.id,
) -> dict:
    """
    Build a standardized paginated response.
    
    Args:
        items: List of items (may include one extra for has_next detection)
        limit: Requested limit
        cursor_info: Decoded cursor if cursor-based pagination was used
        offset: Offset if offset-based pagination was used
        page: Page number if page-based pagination was used
        total: Optional total count
        id_extractor: Function to extract ID from an item
    
    Returns:
        dict with pagination metadata
    """
    has_next = len(items) > limit
    if has_next:
        items = items[:limit]  # Remove the extra item
    
    has_prev = False
    if cursor_info:
        has_prev = True  # If we have a cursor, there's previous data
    elif offset and offset > 0:
        has_prev = True
    elif page and page > 1:
        has_prev = True
    
    next_cursor = None
    prev_cursor = None
    
    if items:
        if has_next:
            last_id = id_extractor(items[-1])
            next_cursor = encode_cursor(last_id, "after")
        
        if has_prev or cursor_info:
            first_id = id_extractor(items[0])
            prev_cursor = encode_cursor(first_id, "before")
    
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "has_next": has_next,
        "has_prev": has_prev,
        "next_cursor": next_cursor,
        "prev_cursor": prev_cursor,
        "offset": offset,
        "page": page,
    }


# Convenience function for dict-based results (raw SQL queries)
def paginate_dict_results(
    results: List[dict],
    limit: int,
    id_key: str = "id",
    cursor: Optional[str] = None,
    offset: Optional[int] = None,
    total: Optional[int] = None,
) -> dict:
    """
    Build paginated response for dict-based query results.
    
    Args:
        results: List of dicts (may include one extra for has_next detection)
        limit: Requested limit
        id_key: Key name for the ID field in dicts
        cursor: Original cursor string if used
        offset: Offset if used
        total: Optional total count
    
    Returns:
        dict with items and pagination metadata
    """
    cursor_info = decode_cursor(cursor) if cursor else None
    
    return build_paginated_response(
        items=results,
        limit=limit,
        cursor_info=cursor_info,
        offset=offset,
        total=total,
        id_extractor=lambda x: x.get(id_key, 0),
    )
