# /Users/radiant/Desktop/RXinDexer/src/api/common.py
# This file provides common utilities and classes for the API, including pagination parameters
# This file is NOT responsible for business logic or database operations

from typing import Optional
from fastapi import Query


class PaginationParams:
    """
    Standard pagination parameters for API endpoints
    
    This class provides consistent pagination parameters across all API endpoints
    that return lists of items. It supports both page-based and offset-based pagination
    with reasonable defaults and constraints.
    """
    
    def __init__(
        self,
        limit: Optional[int] = Query(default=25, ge=1, le=100, description="Number of items to return per page"),
        offset: Optional[int] = Query(default=0, ge=0, description="Number of items to skip"),
        page: Optional[int] = Query(default=1, ge=1, description="Page number")
    ):
        self.limit = limit
        self.offset = offset
        self.page = page
        
    def apply_to_query(self, query):
        """Apply pagination parameters to a SQLAlchemy query"""
        # If using page-based pagination, calculate the offset
        effective_offset = self.offset
        if self.page > 1:
            effective_offset = (self.page - 1) * self.limit
        
        return query.limit(self.limit).offset(effective_offset)
        
    def get_pagination_dict(self, total_count: int):
        """Generate pagination metadata for response"""
        total_pages = max(1, (total_count + self.limit - 1) // self.limit)
        
        return {
            "limit": self.limit,
            "offset": self.offset,
            "page": self.page,
            "total_items": total_count,
            "total_pages": total_pages,
            "has_next": self.page < total_pages,
            "has_prev": self.page > 1
        }
