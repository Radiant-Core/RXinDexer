# /Users/radiant/Desktop/RXinDexer/src/utils/pagination.py
# This file implements pagination utilities for API endpoints.
# It provides standardized pagination parameters and result formatting.

from typing import Dict, List, Any, Tuple, TypeVar, Generic
from fastapi import Query, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Query as SQLAlchemyQuery

T = TypeVar('T')

class PaginationParams:
    """
    Pagination parameters for API endpoints.
    Used as a FastAPI dependency to standardize pagination across endpoints.
    """
    def __init__(
        self,
        page: int = Query(1, ge=1, description="Page number, starting from 1"),
        limit: int = Query(20, ge=1, le=100, description="Number of items per page")
    ):
        self.page = page
        self.limit = limit
        self.offset = (page - 1) * limit

def paginate_results(
    query: SQLAlchemyQuery, 
    pagination: PaginationParams
) -> Tuple[List[Any], Dict[str, Any]]:
    """
    Apply pagination to a SQLAlchemy query and return results with pagination data.
    
    Args:
        query: SQLAlchemy query to paginate
        pagination: Pagination parameters
        
    Returns:
        Tuple of (paginated results, pagination metadata)
    """
    # Get total count for pagination
    total_count = query.count()
    
    # Apply pagination
    results = query.offset(pagination.offset).limit(pagination.limit).all()
    
    # Calculate pagination metadata
    total_pages = (total_count + pagination.limit - 1) // pagination.limit
    
    pagination_data = {
        "page": pagination.page,
        "limit": pagination.limit,
        "total_items": total_count,
        "total_pages": total_pages,
        "has_next": pagination.page < total_pages,
        "has_prev": pagination.page > 1
    }
    
    return results, pagination_data
