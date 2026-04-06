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
    ALGORITHM_NAMES = {
        ALGO_SHA256D: "sha256d",
        ALGO_BLAKE3: "blake3",
        ALGO_K12: "k12",
        ALGO_ARGON2ID_LIGHT: "argon2id-light",
        ALGO_RANDOMX_LIGHT: "randomx-light",
    }
    DAA_MODE_NAMES = {
        0: "fixed",
        1: "epoch",
        2: "asert",
        3: "lwma",
        4: "schedule",
    }
    
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
            'daa_mode': 0,
            'daa_mode_name': 'Fixed',
            'icon_type': None,
            'icon_data': None,
            'icon_url': None,
            'icon_ref': None,
            'total_supply': 0,
            'mined_supply': 0,
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
                    c[key] = value
                return True
        return False
    
    def deactivate_contract(self, ref: str) -> bool:
        """Mark a contract as inactive (100% mined)."""
        return self.update_contract(ref, active=False, percent_mined=100)

    @staticmethod
    def _normalize_icon_data(value: Any) -> Optional[str]:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).hex()
        if isinstance(value, str) and value:
            return value
        return None

    def _extract_icon_fields(self, token: Dict[str, Any]) -> Dict[str, Optional[str]]:
        embed = token.get('embed') if isinstance(token.get('embed'), dict) else None
        remote = token.get('remote') if isinstance(token.get('remote'), dict) else None

        icon_type = None
        icon_data = None
        icon_url = None

        if embed:
            icon_type = embed.get('type') or embed.get('t')
            icon_data = self._normalize_icon_data(embed.get('data') or embed.get('b'))

        if remote:
            icon_type = icon_type or remote.get('type') or remote.get('t')
            icon_url = remote.get('url') or remote.get('u')

        token_icon_type = token.get('icon_type')
        if not icon_type and isinstance(token_icon_type, str):
            icon_type = token_icon_type

        token_icon_ref = token.get('icon_ref')
        if not icon_url and isinstance(token_icon_ref, str) and token_icon_ref and token_icon_ref != 'embedded':
            icon_url = token_icon_ref

        return {
            'icon_type': icon_type,
            'icon_data': icon_data,
            'icon_url': icon_url,
            'icon_ref': token_icon_ref if isinstance(token_icon_ref, str) else None,
        }
    
    def sync_from_index(self, height: int) -> int:
        """
        Sync contracts from GlyphIndex.
        
        Scans for new dMint tokens and updates existing ones.
        Deactivates contracts whose tokens are burned (is_spent=True).
        Removes orphaned contracts that no longer appear in the index.
        Returns number of contracts added/updated.
        """
        if not self.glyph_index:
            return 0
        
        from electrumx.lib.glyph import GlyphProtocol
        
        updated = 0
        # Track which refs the index knows about so we can detect orphans
        index_refs = set()
        
        # Get all dMint tokens from index
        dmint_tokens = self.glyph_index.get_tokens_by_type(
            token_type=4,  # GlyphTokenType.DMINT
            limit=10000
        )
        
        for token in dmint_tokens:
            ref = token.get('ref', '').replace('_', '')
            if not ref:
                continue
            
            index_refs.add(ref)
            
            # Check if new
            existing = next((c for c in self.contracts if c['ref'] == ref), None)
            
            if existing:
                # Update existing contract with latest data
                changed = False
                dmint = token.get('dmint', {})
                icon_fields = self._extract_icon_fields(token)
                sync_fields = {
                    'difficulty': dmint.get('current_difficulty', existing.get('difficulty', 0)),
                    'reward': dmint.get('reward', existing.get('reward', 0)),
                    'percent_mined': token.get('percent_mined', existing.get('percent_mined', 0)),
                    'outputs': dmint.get('num_contracts') or existing.get('outputs', 1),
                    'daa_mode': dmint.get('daa_mode', existing.get('daa_mode', 0)),
                    'daa_mode_name': dmint.get('daa_mode_name', existing.get('daa_mode_name', 'Fixed')),
                    'total_supply': token.get('total_supply', existing.get('total_supply', 0)),
                    'mined_supply': token.get('mined_supply', existing.get('mined_supply', 0)),
                    'icon_type': icon_fields.get('icon_type'),
                    'icon_data': icon_fields.get('icon_data'),
                    'icon_url': icon_fields.get('icon_url'),
                    'icon_ref': icon_fields.get('icon_ref'),
                }
                for key, value in sync_fields.items():
                    if value and value != existing.get(key):
                        existing[key] = value
                        changed = True
                
                # Check if fully mined
                if token.get('percent_mined', 0) >= 100:
                    if existing.get('active', True):
                        existing['active'] = False
                        changed = True
                
                # Check if burned (contract singleton destroyed)
                if token.get('is_spent') and existing.get('active', True):
                    existing['active'] = False
                    existing['burned'] = True
                    changed = True
                    self.logger.info(
                        f'Deactivated burned contract: {ref[:16]}... '
                        f'({existing.get("ticker") or "unnamed"})'
                    )
                
                if changed:
                    updated += 1
            else:
                # Skip burned tokens — don't add them to listings
                if token.get('is_spent'):
                    continue
                
                # Add new contract
                # Need to get outputs count from contract data
                outputs = token.get('dmint', {}).get('num_contracts', 1) or 1
                
                dmint = token.get('dmint', {})
                icon_fields = self._extract_icon_fields(token)
                
                added = self.add_contract(
                    ref=ref,
                    outputs=outputs,
                    ticker=token.get('ticker'),
                    name=token.get('name'),
                    algorithm=dmint.get('algorithm', self.ALGO_SHA256D),
                    difficulty=dmint.get('current_difficulty', 0),
                    reward=dmint.get('reward', 0),
                    deploy_height=token.get('deploy_height', 0),
                )
                if added:
                    # Set extra fields on the newly added contract
                    self.update_contract(
                        ref,
                        daa_mode=dmint.get('daa_mode', 0),
                        daa_mode_name=dmint.get('daa_mode_name', 'Fixed'),
                        icon_type=icon_fields.get('icon_type'),
                        icon_data=icon_fields.get('icon_data'),
                        icon_url=icon_fields.get('icon_url'),
                        icon_ref=icon_fields.get('icon_ref'),
                        total_supply=token.get('total_supply', 0),
                        mined_supply=token.get('mined_supply', 0),
                        percent_mined=token.get('percent_mined', 0),
                    )
                    updated += 1
        
        # Deactivate orphaned contracts not found in the index (e.g. after
        # a reorg or if the token record was purged).
        for contract in self.contracts:
            cref = contract.get('ref', '')
            if cref and cref not in index_refs and contract.get('active', True):
                contract['active'] = False
                contract['orphaned'] = True
                updated += 1
                self.logger.info(
                    f'Deactivated orphaned contract: {cref[:16]}... '
                    f'({contract.get("ticker") or "unnamed"})'
                )
        
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

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _to_token_summary_item(self, contract: Dict[str, Any]) -> Dict[str, Any]:
        total_contracts = max(self._to_int(contract.get('outputs'), 0), 0)
        total_supply = max(self._to_int(contract.get('total_supply'), 0), 0)
        mined_supply = max(self._to_int(contract.get('mined_supply'), 0), 0)
        remaining_supply = max(total_supply - mined_supply, 0)

        percent_mined = self._to_float(contract.get('percent_mined'), 0.0)
        if total_supply > 0:
            percent_mined = (mined_supply / total_supply) * 100.0

        active = bool(contract.get('active', True))
        fully_mined = (not active) or percent_mined >= 100.0
        mineable_contracts_remaining = 0 if fully_mined else None

        algorithm_id = self._to_int(contract.get('algorithm'), self.ALGO_SHA256D)
        daa_mode_id = self._to_int(contract.get('daa_mode'), 0)

        return {
            'token_ref': contract.get('ref'),
            'ticker': contract.get('ticker') or '???',
            'name': contract.get('name') or '',
            'algorithm': {
                'id': algorithm_id,
                'name': self.ALGORITHM_NAMES.get(algorithm_id, f'unknown({algorithm_id})'),
            },
            'daa_mode': {
                'id': daa_mode_id,
                'name': self.DAA_MODE_NAMES.get(daa_mode_id, contract.get('daa_mode_name') or f'unknown({daa_mode_id})'),
            },
            'contracts': {
                'total': total_contracts,
                'mineable_remaining': mineable_contracts_remaining,
                'fully_mined': total_contracts if fully_mined else None,
            },
            'supply': {
                'total': str(total_supply),
                'minted': str(mined_supply),
                'remaining': str(remaining_supply),
                'unit': 'photons',
            },
            'reward_per_mint': str(max(self._to_int(contract.get('reward'), 0), 0)),
            'target': str(max(self._to_int(contract.get('difficulty'), 0), 0)),
            'percent_mined': round(percent_mined, 8),
            'deploy_height': max(self._to_int(contract.get('deploy_height'), 0), 0),
            'active': active,
            'is_fully_mined': fully_mined,
            'icon': {
                'type': contract.get('icon_type') or None,
                'url': contract.get('icon_url') or contract.get('icon_ref') or None,
                'data_hex': contract.get('icon_data') or None,
            },
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }

    def get_contracts_v2(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get contracts using the v2 token summary schema."""
        params = params or {}
        if not isinstance(params, dict):
            raise ValueError('params must be an object')

        version = self._to_int(params.get('version', 2), 2)
        if version != 2:
            raise ValueError('unsupported version')

        view = params.get('view', 'token_summary')
        if view != 'token_summary':
            raise ValueError('unsupported view')

        filters = params.get('filters') or {}
        sort = params.get('sort') or {}
        pagination = params.get('pagination') or {}

        status = (filters.get('status') or 'mineable').lower()
        algorithm_ids = filters.get('algorithm_ids') or []
        algorithm_ids = {
            self._to_int(algo)
            for algo in algorithm_ids
            if isinstance(algo, (int, float, str))
        }

        items = [self._to_token_summary_item(c) for c in self.contracts]

        if status == 'mineable':
            items = [i for i in items if not i.get('is_fully_mined')]
        elif status == 'finished':
            items = [i for i in items if i.get('is_fully_mined')]
        elif status != 'all':
            raise ValueError('invalid status filter')

        if algorithm_ids:
            items = [
                i for i in items
                if self._to_int(i.get('algorithm', {}).get('id')) in algorithm_ids
            ]

        sort_field = sort.get('field', 'deploy_height')
        sort_dir = (sort.get('dir') or 'desc').lower()
        reverse = sort_dir != 'asc'

        def sort_key(item: Dict[str, Any]):
            if sort_field == 'ticker':
                return (item.get('ticker') or '').lower()
            if sort_field == 'reward_per_mint':
                return self._to_int(item.get('reward_per_mint'))
            if sort_field == 'percent_mined':
                return self._to_float(item.get('percent_mined'))
            if sort_field == 'mineable_contracts_remaining':
                value = item.get('contracts', {}).get('mineable_remaining')
                return self._to_int(value, -1)
            if sort_field == 'total_contracts':
                return self._to_int(item.get('contracts', {}).get('total'))
            return self._to_int(item.get('deploy_height'))

        items.sort(key=sort_key, reverse=reverse)

        limit = max(1, min(self._to_int(pagination.get('limit'), 1000), 5000))
        offset = max(0, self._to_int(pagination.get('cursor'), 0))
        paged_items = items[offset:offset + limit]
        next_offset = offset + len(paged_items)
        cursor_next = str(next_offset) if next_offset < len(items) else None

        return {
            'version': 2,
            'view': 'token_summary',
            'schema': 'dmint.get_contracts.v2',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'network': 'mainnet',
            'indexed_height': self.last_updated_height,
            'reorg_safe_depth': 0,
            'cursor_next': cursor_next,
            'count': len(paged_items),
            'total_estimate': len(items),
            'items': paged_items,
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
