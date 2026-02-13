"""
dMint Contracts Manager for RXinDexer

Manages the contracts.json file that Glyph miners use to discover
mineable dMint tokens. Provides both simple and extended formats.

Simple format (for compatibility):
  [["ref", outputs], ...]

Extended format (for enhanced miners):
  {"version": 1, "contracts": [{ref, outputs, ticker, difficulty, ...}]}
"""

import json
import os
import struct
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str


class DMintContractsManager:
    """
    Manages dMint contracts list for miners.
    
    - Reads/writes contracts.json
    - Updates from GlyphIndex on new dMint discoveries
    - Provides API methods for miners
    """
    
    # Algorithm IDs (per Glyph v2 spec Section 11.2)
    ALGO_SHA256D = 0x00
    ALGO_BLAKE3 = 0x01
    ALGO_K12 = 0x02
    ALGO_ARGON2ID_LIGHT = 0x03
    ALGO_RANDOMX_LIGHT = 0x04
    
    def __init__(self, data_dir: str, glyph_index=None):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.data_dir = data_dir
        self.glyph_index = glyph_index
        
        # File paths
        self.simple_path = os.path.join(data_dir, 'contracts.json')
        self.extended_path = os.path.join(data_dir, 'contracts_extended.json')
        
        # In-memory cache
        self.contracts: List[Dict[str, Any]] = []
        self.last_updated_height = 0
        
        # Load existing contracts
        self._load_contracts()
    
    def _load_contracts(self):
        """Load contracts from extended JSON file."""
        if os.path.exists(self.extended_path):
            try:
                with open(self.extended_path, 'r') as f:
                    data = json.load(f)
                    self.contracts = data.get('contracts', [])
                    self.last_updated_height = data.get('updated_height', 0)
                    self.logger.info(f'Loaded {len(self.contracts)} dMint contracts')
            except Exception as e:
                self.logger.error(f'Error loading contracts: {e}')
                self.contracts = []
        elif os.path.exists(self.simple_path):
            # Migrate from simple format
            try:
                with open(self.simple_path, 'r') as f:
                    simple_data = json.load(f)
                    self.contracts = [
                        {'ref': ref, 'outputs': outputs}
                        for ref, outputs in simple_data
                    ]
                    self.logger.info(f'Migrated {len(self.contracts)} contracts from simple format')
                    self._save_contracts()
            except Exception as e:
                self.logger.error(f'Error loading simple contracts: {e}')
                self.contracts = []
    
    def _save_contracts(self):
        """Save contracts to both simple and extended formats."""
        os.makedirs(self.data_dir, exist_ok=True)
        
        # Save extended format
        extended_data = {
            'version': 1,
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'updated_height': self.last_updated_height,
            'contracts': self.contracts
        }
        
        try:
            with open(self.extended_path, 'w') as f:
                json.dump(extended_data, f, indent=2)
        except Exception as e:
            self.logger.error(f'Error saving extended contracts: {e}')
        
        # Save simple format (for backward compatibility)
        simple_data = [
            [c['ref'], c['outputs']] 
            for c in self.contracts
        ]
        
        try:
            with open(self.simple_path, 'w') as f:
                json.dump(simple_data, f, indent=2)
        except Exception as e:
            self.logger.error(f'Error saving simple contracts: {e}')
    
    def add_contract(self, ref: str, outputs: int, ticker: str = None,
                     name: str = None, algorithm: int = ALGO_SHA256D,
                     difficulty: int = 0, reward: int = 0,
                     deploy_height: int = 0) -> bool:
        """
        Add a new dMint contract.
        
        Maintains order by deploy_height (oldest first).
        Returns True if contract was added, False if already exists.
        """
        # Check if already exists
        for c in self.contracts:
            if c['ref'] == ref:
                return False
        
        contract = {
            'ref': ref,
            'outputs': outputs,
            'ticker': ticker,
            'name': name,
            'algorithm': algorithm,
            'difficulty': difficulty,
            'reward': reward,
            'percent_mined': 0,
            'active': True,
            'deploy_height': deploy_height,
            'version': 2  # v2 contract version
        }
        
        # Insert in order by deploy_height
        inserted = False
        for i, c in enumerate(self.contracts):
            c_height = c.get('deploy_height', 0)
            if deploy_height < c_height:
                self.contracts.insert(i, contract)
                inserted = True
                break
        
        if not inserted:
            self.contracts.append(contract)
        
        self.logger.info(f'Added dMint contract: {ref[:16]}... ({ticker or "unnamed"})')
        return True
    
    def update_contract(self, ref: str, **kwargs) -> bool:
        """Update an existing contract's fields."""
        for c in self.contracts:
            if c['ref'] == ref:
                for key, value in kwargs.items():
                    if key in c:
                        c[key] = value
                return True
        return False
    
    def deactivate_contract(self, ref: str) -> bool:
        """Mark a contract as inactive (100% mined)."""
        return self.update_contract(ref, active=False, percent_mined=100)
    
    def sync_from_index(self, height: int) -> int:
        """
        Sync contracts from GlyphIndex.
        
        Scans for new dMint tokens and updates existing ones.
        Returns number of contracts added/updated.
        """
        if not self.glyph_index:
            return 0
        
        from electrumx.lib.glyph import GlyphProtocol
        
        updated = 0
        
        # Get all dMint tokens from index
        dmint_tokens = self.glyph_index.get_tokens_by_type(
            token_type=4,  # GlyphTokenType.DMINT
            limit=10000
        )
        
        for token in dmint_tokens:
            ref = token.get('ref', '').replace('_', '')
            if not ref:
                continue
            
            # Check if new
            existing = next((c for c in self.contracts if c['ref'] == ref), None)
            
            if existing:
                # Update existing
                changed = False
                for key in ['difficulty', 'reward', 'percent_mined']:
                    if key in token and token[key] != existing.get(key):
                        existing[key] = token[key]
                        changed = True
                
                # Check if fully mined
                if token.get('percent_mined', 0) >= 100:
                    existing['active'] = False
                    changed = True
                
                if changed:
                    updated += 1
            else:
                # Add new contract
                # Need to get outputs count from contract data
                outputs = token.get('dmint', {}).get('outputs', 1)
                
                if self.add_contract(
                    ref=ref,
                    outputs=outputs,
                    ticker=token.get('ticker'),
                    name=token.get('name'),
                    algorithm=token.get('dmint', {}).get('algorithm', self.ALGO_SHA256D),
                    difficulty=token.get('dmint', {}).get('current_difficulty', 0),
                    reward=token.get('dmint', {}).get('reward', 0),
                    deploy_height=token.get('deploy_height', 0)
                ):
                    updated += 1
        
        if updated > 0:
            self.last_updated_height = height
            self._save_contracts()
            self.logger.info(f'Synced {updated} dMint contracts at height {height}')
        
        return updated
    
    # ========================================================================
    # API Methods
    # ========================================================================
    
    def get_contracts_simple(self) -> List[List]:
        """Get contracts in simple format for basic miners."""
        return [[c['ref'], c['outputs']] for c in self.contracts if c.get('active', True)]
    
    def get_contracts_extended(self, active_only: bool = True) -> Dict[str, Any]:
        """Get contracts in extended format."""
        contracts = self.contracts
        if active_only:
            contracts = [c for c in contracts if c.get('active', True)]
        
        return {
            'version': 1,
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'updated_height': self.last_updated_height,
            'count': len(contracts),
            'contracts': contracts
        }
    
    def get_contract(self, ref: str) -> Optional[Dict[str, Any]]:
        """Get a single contract by ref."""
        for c in self.contracts:
            if c['ref'] == ref:
                return c
        return None
    
    def get_contracts_by_algorithm(self, algorithm: int) -> List[Dict[str, Any]]:
        """Get contracts filtered by mining algorithm."""
        return [
            c for c in self.contracts 
            if c.get('algorithm') == algorithm and c.get('active', True)
        ]
    
    def get_most_profitable(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get contracts sorted by estimated profitability.
        
        Lower difficulty + higher reward = more profitable.
        """
        active = [c for c in self.contracts if c.get('active', True)]
        
        def profitability_score(c):
            difficulty = c.get('difficulty', 1) or 1
            reward = c.get('reward', 0) or 0
            return reward / difficulty if difficulty > 0 else 0
        
        return sorted(active, key=profitability_score, reverse=True)[:limit]


# API method registration for RPC
DMINT_CONTRACTS_METHODS = {
    'dmint.get_contracts': 'dmint_get_contracts',
    'dmint.get_contracts_extended': 'dmint_get_contracts_extended',
    'dmint.get_contract': 'dmint_get_contract',
    'dmint.get_by_algorithm': 'dmint_get_by_algorithm',
    'dmint.get_most_profitable': 'dmint_get_most_profitable',
    'dmint.get_stats': 'dmint_get_stats',
    'dmint.get_contract_daa': 'dmint_get_contract_daa',
}
