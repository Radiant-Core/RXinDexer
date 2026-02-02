"""
Radiant-specific ElectrumX session class with token support.

This module extends the base ElectrumX session with Glyph, WAVE, and Swap
token tracking capabilities for the Radiant blockchain.
"""

from electrumx.server.session import ElectrumX
from electrumx.server.glyph_api import GlyphAPIMixin, GLYPH_METHODS


class RadiantElectrumX(GlyphAPIMixin, ElectrumX):
    """
    Radiant ElectrumX server session with full token support.
    
    This class combines:
    - Base ElectrumX functionality (blocks, transactions, addresses)
    - Glyph v2 token tracking (FT, NFT, dMint, Mutable, Containers)
    - WAVE naming system indexing
    - Swap DEX order book tracking
    - Real-time WebSocket subscriptions
    - Mempool indexing for unconfirmed transactions
    
    Usage:
        This session class is automatically used by the Radiant coin class
        to provide complete blockchain indexing with token support.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize the Radiant ElectrumX session."""
        super().__init__(*args, **kwargs)
    
    @property
    def glyph_index(self):
        """Access the Glyph token index from block processor."""
        if hasattr(self, 'session_mgr') and hasattr(self.session_mgr, 'bp'):
            return getattr(self.session_mgr.bp, 'glyph_index', None)
        return None
    
    @property
    def wave_index(self):
        """Access the WAVE naming index from block processor."""
        if hasattr(self, 'session_mgr') and hasattr(self.session_mgr, 'bp'):
            return getattr(self.session_mgr.bp, 'wave_index', None)
        return None
    
    @property
    def swap_index(self):
        """Access the Swap DEX index from block processor."""
        if hasattr(self, 'session_mgr') and hasattr(self.session_mgr, 'bp'):
            return getattr(self.session_mgr.bp, 'swap_index', None)
        return None
    
    @property
    def mempool_glyph(self):
        """Access the mempool Glyph tracker."""
        if hasattr(self, 'session_mgr') and hasattr(self.session_mgr, 'mempool'):
            return getattr(self.session_mgr.mempool, 'glyph_mempool', None)
        return None
    
    @property
    def glyph_subscriptions(self):
        """Access the Glyph subscription manager from block processor."""
        if hasattr(self, 'session_mgr') and hasattr(self.session_mgr, 'bp'):
            return getattr(self.session_mgr.bp, 'subscriptions', None)
        return None
    
    @property
    def dmint_contracts(self):
        """Access the dMint contracts manager from block processor."""
        if hasattr(self, 'session_mgr') and hasattr(self.session_mgr, 'bp'):
            return getattr(self.session_mgr.bp, 'dmint_contracts', None)
        return None
    
    def set_request_handlers(self, ptuple):
        """Register protocol request handlers including token methods."""
        # Call parent to register standard ElectrumX methods
        super().set_request_handlers(ptuple)
        
        # Add token tracking methods from GLYPH_METHODS
        for method_name, handler_name in GLYPH_METHODS.items():
            if hasattr(self, handler_name):
                self.request_handlers[method_name] = getattr(self, handler_name)
            else:
                # Log warning if handler is missing
                self.logger.warning(f"Token method {method_name} handler {handler_name} not found")
