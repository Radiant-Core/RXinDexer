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
import re
import struct
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set, Tuple

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str


_NON_HEX = re.compile(r'[^0-9a-f]')


def _is_hex(s: str) -> bool:
    return bool(s) and _NON_HEX.search(s) is None


def _canonical_ref(ref: Any) -> str:
    """Normalize a Glyph ref to canonical 72-char hex (txid_BE + 8-char hex vout).

    Accepts the common input forms:
      - 72-char hex (already canonical)
      - "<txid_hex>_<decimal_vout>"  (the form GlyphIndex stores)
      - "<txid_hex>:<decimal_vout>"  (the form humans copy from explorers)
      - "<txid_hex><decimal_vout>"   (concatenated, vout 0-99 — 65-66 chars)

    Returns lowercase canonical form, or '' if the input can't be parsed.
    """
    if not isinstance(ref, str):
        return ''
    s = ref.strip().lower()
    if not s:
        return ''

    # Form: txid_vout or txid:vout
    for sep in ('_', ':'):
        if sep in s:
            txid, _, vout_str = s.partition(sep)
            if len(txid) == 64 and _is_hex(txid):
                try:
                    return txid + format(int(vout_str, 10), '08x')
                except (ValueError, TypeError):
                    return ''
            return ''

    # Form: pure 72-char hex
    if len(s) == 72 and _is_hex(s):
        return s

    # Form: txid + decimal vout (e.g. df185c…798e0 for vout 0). Strip leading
    # hex txid (64 chars) and treat the remainder as decimal vout.
    if 64 < len(s) <= 74 and _is_hex(s[:64]):
        tail = s[64:]
        if tail.isdigit():
            try:
                return s[:64] + format(int(tail, 10), '08x')
            except (ValueError, TypeError):
                return ''

    return ''


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
        self.denylist_path = os.path.join(data_dir, 'dmint_denylist.json')

        # In-memory cache
        self.contracts: List[Dict[str, Any]] = []
        self.last_updated_height = 0

        # Denylist: canonical 72-char hex refs hidden from all API responses
        # and skipped during sync. Hot-reloaded on file mtime change.
        self._denylist: Set[str] = set()
        self._denylist_mtime: float = -1.0

        # Load existing contracts, then strip anything already on the denylist
        self._load_contracts()
        self._load_denylist()
        self._purge_denied()

    def _load_denylist(self) -> bool:
        """Reload denylist if the file's mtime has changed. Returns True if reloaded."""
        try:
            mtime = os.path.getmtime(self.denylist_path)
        except OSError:
            # File missing — clear cached denylist if it was previously populated
            if self._denylist:
                self.logger.info('dMint denylist file removed; clearing %d entries',
                                 len(self._denylist))
                self._denylist = set()
                self._denylist_mtime = -1.0
                return True
            return False

        if mtime == self._denylist_mtime:
            return False

        try:
            with open(self.denylist_path, 'r') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            self.logger.error('Failed to read dMint denylist: %s', e)
            return False

        # Accept either {"refs": [...]} or a bare list. Entries can be plain
        # strings or {"ref": "...", "reason": "..."} objects.
        raw_entries = data.get('refs', []) if isinstance(data, dict) else data
        if not isinstance(raw_entries, list):
            self.logger.error('dMint denylist must be a list of refs')
            return False

        new_denylist: Set[str] = set()
        for entry in raw_entries:
            ref_str = entry.get('ref') if isinstance(entry, dict) else entry
            canonical = _canonical_ref(ref_str)
            if canonical:
                new_denylist.add(canonical)
            else:
                self.logger.warning('Skipping unparseable denylist entry: %r', ref_str)

        self._denylist = new_denylist
        self._denylist_mtime = mtime
        self.logger.info('Loaded dMint denylist: %d entries', len(new_denylist))
        return True

    def _is_denied(self, ref: Any) -> bool:
        """Check if a contract ref (any form) is on the denylist."""
        if not self._denylist:
            return False
        canonical = _canonical_ref(ref)
        return bool(canonical) and canonical in self._denylist

    def _purge_denied(self) -> int:
        """Drop any in-memory contracts that match the denylist; persist if changed.
        Returns the number of contracts removed."""
        if not self._denylist or not self.contracts:
            return 0
        kept = [c for c in self.contracts if not self._is_denied(c.get('ref'))]
        removed = len(self.contracts) - len(kept)
        if removed:
            self.contracts = kept
            self._save_contracts()
            self.logger.info('Purged %d denied dMint contract(s) from listings', removed)
        return removed
    
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

        # Hot-reload denylist before sync so newly-added entries take effect on
        # this tick. If the denylist changed, purge in-memory matches up front.
        if self._load_denylist():
            self._purge_denied()

        updated = 0
        # Track which refs the index knows about so we can detect orphans
        index_refs = set()
        
        # Get all dMint tokens from index
        dmint_result = self.glyph_index.get_tokens_by_type(
            token_type=4,  # GlyphTokenType.DMINT
            limit=10000
        )
        dmint_tokens = dmint_result.get('tokens', []) if isinstance(dmint_result, dict) else dmint_result
        
        for token in dmint_tokens:
            ref = token.get('ref', '').replace('_', '')
            if not ref:
                continue

            # Silently skip refs on the operator denylist (abandoned / illegal
            # / takedown content). Do NOT add to index_refs — we want any stale
            # in-memory entry to fall through the orphan sweep below.
            if self._is_denied(ref):
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
                
                # Recompute liveness from ground truth each sync. `active`,
                # `orphaned` and `burned` must NOT be one-way latches.
                #
                # Mineability of a dMint contract is decided by SUPPLY, never
                # by the glyph token ref's `is_spent` flag. For dMint the
                # immutable token ref (vout 0) is normally spent when the mining
                # contracts are deployed from the genesis output, so `is_spent`
                # is true for legitimately-mineable tokens (e.g. GRASS — 75.9%
                # mined, genesis spent, still mineable). Treating is_spent as a
                # burn wrongly hid every such token from the miner.
                #
                # Use `or 0` rather than the dict.get default — when the key
                # exists but is None (a freshly-deployed contract with no mints)
                # get() returns None, which can't be compared to int and would
                # crash the indexer.
                pct = token.get('percent_mined')
                pct = pct if pct is not None else existing.get('percent_mined', 0)
                total_supply = token.get('total_supply', existing.get('total_supply', 0)) or 0
                mined_supply = token.get('mined_supply', existing.get('mined_supply', 0)) or 0
                supply_exhausted = (
                    (pct or 0) >= 100
                    or (total_supply > 0 and mined_supply >= total_supply)
                )

                # Track per-contract liveness for the API (mineable_remaining).
                live = token.get('live_contracts')
                if live is not None and live != existing.get('live_contracts'):
                    existing['live_contracts'] = live
                    changed = True

                # Prefer the indexer's authoritative mineability signal (v3:
                # supply remaining AND >=1 live contract singleton). Fall back to
                # supply-only when it's None (records predating the v3 reindex)
                # so pre-v3 data never regresses. This is what re-hides genuinely
                # burned tokens (all contracts gone, supply remaining) that the
                # supply-only rule would wrongly show as mineable.
                mineable = token.get('mineable')
                desired_active = (not supply_exhausted) if mineable is None else bool(mineable)
                reactivated = False
                if bool(existing.get('active', True)) != desired_active:
                    existing['active'] = desired_active
                    changed = True
                    reactivated = desired_active
                # Seen in the index with supply remaining: clear stale orphan /
                # burned flags left by earlier (incorrect) is_spent-based
                # deactivation so the contract self-heals.
                if desired_active and existing.get('orphaned'):
                    existing['orphaned'] = False
                    changed = True
                    reactivated = True
                if desired_active and existing.get('burned'):
                    existing['burned'] = False
                    changed = True
                    reactivated = True
                if reactivated:
                    self.logger.info(
                        f'Reactivated mineable contract: {ref[:16]}... '
                        f'({existing.get("ticker") or "unnamed"})'
                    )

                if changed:
                    updated += 1
            else:
                # Add new contract. Do NOT skip on `is_spent`: for dMint the
                # genesis/token ref is normally spent once the mining contracts
                # are deployed, so is_spent is true for still-mineable tokens
                # (e.g. GRASS). Inclusion is supply-based; fully-mined tokens are
                # still added (they surface under status=finished) but marked
                # inactive below.
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
                    # Initial active state: prefer the indexer's authoritative
                    # mineability (per-contract liveness); fall back to supply
                    # when untracked (None), mirroring the existing-contract path.
                    n_pct = token.get('percent_mined') or 0
                    n_total = token.get('total_supply') or 0
                    n_mined = token.get('mined_supply') or 0
                    n_exhausted = n_pct >= 100 or (n_total > 0 and n_mined >= n_total)
                    mineable = token.get('mineable')
                    n_active = (not n_exhausted) if mineable is None else bool(mineable)
                    # Set extra fields on the newly added contract
                    self.update_contract(
                        ref,
                        active=n_active,
                        live_contracts=token.get('live_contracts'),
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
        
        # Deactivate orphaned contracts not found in the index (e.g. after a
        # reorg or if the token record was purged).
        #
        # Guard: only run the sweep when the index actually returned dMint
        # tokens this pass. During an initial sync / resync the GlyphIndex can
        # be mid-rebuild and return an empty set; treating that as "every
        # contract is orphaned" permanently deactivated the entire listing in
        # production (orphaned refs had no reactivation path). Requiring a
        # non-empty index_refs keeps a lagging index from nuking live
        # contracts; any transiently missing during a partial resync self-heal
        # via the reactivation logic above once they reappear.
        if index_refs:
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
        if self._load_denylist():
            self._purge_denied()
        return [[c['ref'], c['outputs']] for c in self.contracts
                if c.get('active', True) and not self._is_denied(c.get('ref'))]

    def get_contracts_extended(self, active_only: bool = True) -> Dict[str, Any]:
        """Get contracts in extended format."""
        if self._load_denylist():
            self._purge_denied()

        contracts = [c for c in self.contracts if not self._is_denied(c.get('ref'))]
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

    @staticmethod
    def _normalize_ref(ref: str) -> str:
        """Convert stored ref (txid_BE 64 hex + decimal vout) to 72-char hex ref
        (txid_BE 64 hex + zero-padded 8-char hex vout) expected by the frontend."""
        if not ref or len(ref) < 64:
            return ref or ''
        txid = ref[:64]
        vout_str = ref[64:]
        try:
            vout_int = int(vout_str, 10)
            return txid + format(vout_int, '08x')
        except (ValueError, TypeError):
            return ref

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
        # Authoritative live-contract count from the indexer (v3+). Surfaces a
        # real `mineable_remaining` instead of the legacy null; falls back to
        # null when untracked (pre-v3 records).
        live_contracts = contract.get('live_contracts')
        if fully_mined:
            mineable_contracts_remaining = 0
        elif isinstance(live_contracts, int):
            mineable_contracts_remaining = live_contracts
        else:
            mineable_contracts_remaining = None

        algorithm_id = self._to_int(contract.get('algorithm'), self.ALGO_SHA256D)
        daa_mode_id = self._to_int(contract.get('daa_mode'), 0)

        return {
            'token_ref': self._normalize_ref(contract.get('ref') or ''),
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
            'burned': bool(contract.get('burned', False)),
            'icon': {
                'type': contract.get('icon_type') or None,
                'url': contract.get('icon_url') or (contract.get('icon_ref') if contract.get('icon_ref') not in (None, 'embedded') else None),
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

        if self._load_denylist():
            self._purge_denied()

        items = [self._to_token_summary_item(c) for c in self.contracts
                 if not self._is_denied(c.get('ref'))]

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
        """Get a single contract by ref.

        Stored refs use the internal format `64-hex-txid + decimal-vout`
        (e.g. `bee9...0` for vout 0). Callers from the REST API pass the
        external format `64-hex-txid + 8-hex-vout` (e.g. `bee9...00000000`).
        Normalize both sides to the external format for comparison so the
        endpoint works regardless of which form the caller sends.
        """
        normalized = self._normalize_ref(ref)
        for c in self.contracts:
            if self._normalize_ref(c['ref']) == normalized:
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
