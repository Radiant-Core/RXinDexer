# /Users/radiant/Desktop/RXinDexer/tests/utils/test_pagination.py
# This file tests the pagination utility for API endpoints.
# It verifies that pagination parameters are correctly applied and results are properly formatted.

import pytest
from unittest.mock import MagicMock

from src.utils.pagination import PaginationParams, paginate_results


class TestPagination:
    """Tests for the pagination utilities."""
    
    def test_pagination_params_default(self):
        """Test that default pagination parameters are set correctly."""
        params = PaginationParams()
        assert params.page == 1
        assert params.limit == 20
        assert params.offset == 0
    
    def test_pagination_params_custom(self):
        """Test that custom pagination parameters are set correctly."""
        params = PaginationParams(page=3, limit=50)
        assert params.page == 3
        assert params.limit == 50
        assert params.offset == 100  # (3-1) * 50
    
    def test_paginate_results_empty(self):
        """Test pagination with empty results."""
        # Create mock query with no results
        query = MagicMock()
        query.count.return_value = 0
        query.offset.return_value.limit.return_value.all.return_value = []
        
        # Apply pagination
        pagination = PaginationParams(page=1, limit=10)
        results, pagination_data = paginate_results(query, pagination)
        
        # Verify results
        assert results == []
        assert pagination_data["page"] == 1
        assert pagination_data["limit"] == 10
        assert pagination_data["total_items"] == 0
        assert pagination_data["total_pages"] == 0
        assert pagination_data["has_next"] is False
        assert pagination_data["has_prev"] is False
    
    def test_paginate_results_with_data(self):
        """Test pagination with actual data."""
        # Create mock query with sample data
        sample_data = ["item1", "item2", "item3", "item4", "item5"]
        
        query = MagicMock()
        query.count.return_value = 25  # Total items
        query.offset.return_value.limit.return_value.all.return_value = sample_data
        
        # Apply pagination
        pagination = PaginationParams(page=2, limit=5)
        results, pagination_data = paginate_results(query, pagination)
        
        # Verify query calls
        query.offset.assert_called_once_with(5)  # Offset is (2-1) * 5
        query.offset.return_value.limit.assert_called_once_with(5)
        
        # Verify results
        assert results == sample_data
        assert pagination_data["page"] == 2
        assert pagination_data["limit"] == 5
        assert pagination_data["total_items"] == 25
        assert pagination_data["total_pages"] == 5  # 25 items / 5 per page
        assert pagination_data["has_next"] is True  # Page 2 of 5
        assert pagination_data["has_prev"] is True  # Not the first page
    
    def test_paginate_results_last_page(self):
        """Test pagination on the last page."""
        # Create mock query with sample data for last page
        sample_data = ["item21", "item22", "item23", "item24", "item25"]
        
        query = MagicMock()
        query.count.return_value = 25  # Total items
        query.offset.return_value.limit.return_value.all.return_value = sample_data
        
        # Apply pagination for last page
        pagination = PaginationParams(page=5, limit=5)
        results, pagination_data = paginate_results(query, pagination)
        
        # Verify results
        assert results == sample_data
        assert pagination_data["page"] == 5
        assert pagination_data["total_pages"] == 5
        assert pagination_data["has_next"] is False  # Last page
        assert pagination_data["has_prev"] is True  # Not the first page
