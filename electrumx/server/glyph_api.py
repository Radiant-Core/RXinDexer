"""
Glyph v2 Token API Extensions for RXinDexer

This module provides RPC API methods for querying Glyph v1/v2 tokens.
These methods can be added to the ElectrumX session handler.

Reference: https://github.com/Radiant-Core/Glyph-Token-Standards
"""

from electrumx.lib.glyph import (
    GLYPH_MAGIC,
    GlyphProtocol,
    GlyphVersion,
    GlyphTokenType,
    parse_glyph_envelope,
    get_token_type,
    get_protocol_name,
    validate_protocols,
    is_fungible,
    is_nft,
    is_dmint,
    format_glyph_id,
    parse_glyph_id,
    parse_ref,
    format_ref,
)
from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash


class GlyphAPIMixin:
    """
    Mixin class providing Glyph v2 token API methods.
    
    Add this to the ElectrumX session class to enable Glyph queries.
    
    Example:
        class ElectrumX(GlyphAPIMixin, SessionBase):
            ...
    """

    async def glyph_get_token(self, glyph_id: str):
        """
        Get token information by Glyph ID.
        
        Args:
            glyph_id: Token ID in format "txid:vout"
            
        Returns:
            Token information dict or None if not found
        """
        self.bump_cost(1.0)
        
        try:
            txid, vout = parse_glyph_id(glyph_id)
        except (ValueError, IndexError):
            return {'error': 'Invalid glyph_id format. Expected txid:vout'}
        
        # Fetch the transaction
        try:
            raw_tx = await self.daemon_request('getrawtransaction', txid, True)
        except Exception:
            return None
        
        if not raw_tx or 'vout' not in raw_tx:
            return None
        
        if vout >= len(raw_tx['vout']):
            return None
        
        output = raw_tx['vout'][vout]
        script_hex = output.get('scriptPubKey', {}).get('hex', '')
        
        if not script_hex:
            return None
        
        script_bytes = bytes.fromhex(script_hex)
        envelope = parse_glyph_envelope(script_bytes)
        
        if not envelope:
            return None
        
        result = {
            'glyph_id': glyph_id,
            'txid': txid,
            'vout': vout,
            'value': int(output.get('value', 0) * 100_000_000),
            'version': envelope.get('version'),
            'is_reveal': envelope.get('is_reveal', False),
        }
        
        if envelope.get('commit_hash'):
            result['commit_hash'] = envelope['commit_hash']
        
        if envelope.get('content_root'):
            result['content_root'] = envelope['content_root']
        
        return result

    async def glyph_get_by_ref(self, ref: str):
        """
        Get all UTXOs containing a specific reference.
        
        Args:
            ref: 36-byte reference in hex (72 characters)
            
        Returns:
            List of UTXOs with the reference
        """
        self.bump_cost(2.0)
        
        if len(ref) != 72:
            return {'error': 'Invalid ref format. Expected 72 hex characters'}
        
        try:
            ref_bytes = bytes.fromhex(ref)
        except ValueError:
            return {'error': 'Invalid hex in ref'}
        
        # Query the database for UTXOs with this reference
        utxos = await self.db.get_utxos_by_ref(ref_bytes)
        
        result = []
        for utxo in utxos:
            result.append({
                'tx_hash': hash_to_hex_str(utxo.tx_hash),
                'tx_pos': utxo.tx_pos,
                'height': utxo.height,
                'value': utxo.value,
            })
        
        return result

    async def glyph_validate_protocols(self, protocols: list):
        """
        Validate a protocol combination per Glyph v2 rules.
        
        Args:
            protocols: List of protocol IDs
            
        Returns:
            Validation result with any errors
        """
        self.bump_cost(0.1)
        
        if not isinstance(protocols, list):
            return {'valid': False, 'error': 'protocols must be a list'}
        
        valid, error = validate_protocols(protocols)
        
        result = {'valid': valid}
        if error:
            result['error'] = error
        
        # Add protocol names for convenience
        result['protocol_names'] = [get_protocol_name(p) for p in protocols]
        result['token_type'] = get_token_type(protocols)
        
        return result

    async def glyph_get_protocol_info(self):
        """
        Get information about all Glyph v2 protocols.
        
        Returns:
            Dict with protocol definitions
        """
        self.bump_cost(0.1)
        
        return {
            'version': GlyphVersion.V2,
            'magic': GLYPH_MAGIC.hex(),
            'protocols': {
                'GLYPH_FT': {
                    'id': GlyphProtocol.GLYPH_FT,
                    'name': 'Fungible Token',
                    'description': 'Standard fungible token',
                },
                'GLYPH_NFT': {
                    'id': GlyphProtocol.GLYPH_NFT,
                    'name': 'Non-Fungible Token',
                    'description': 'Unique digital asset',
                },
                'GLYPH_DAT': {
                    'id': GlyphProtocol.GLYPH_DAT,
                    'name': 'Data Storage',
                    'description': 'On-chain data storage',
                },
                'GLYPH_DMINT': {
                    'id': GlyphProtocol.GLYPH_DMINT,
                    'name': 'Decentralized Minting',
                    'description': 'Proof-of-work token distribution',
                    'requires': ['GLYPH_FT'],
                },
                'GLYPH_MUT': {
                    'id': GlyphProtocol.GLYPH_MUT,
                    'name': 'Mutable State',
                    'description': 'Updateable token metadata',
                    'requires': ['GLYPH_NFT'],
                },
                'GLYPH_BURN': {
                    'id': GlyphProtocol.GLYPH_BURN,
                    'name': 'Explicit Burn',
                    'description': 'Verifiable token destruction',
                },
                'GLYPH_CONTAINER': {
                    'id': GlyphProtocol.GLYPH_CONTAINER,
                    'name': 'Container',
                    'description': 'Collection or grouping of tokens',
                    'requires': ['GLYPH_NFT'],
                },
                'GLYPH_ENCRYPTED': {
                    'id': GlyphProtocol.GLYPH_ENCRYPTED,
                    'name': 'Encrypted Content',
                    'description': 'Private token content',
                    'requires': ['GLYPH_NFT'],
                },
                'GLYPH_TIMELOCK': {
                    'id': GlyphProtocol.GLYPH_TIMELOCK,
                    'name': 'Timelocked Reveal',
                    'description': 'Time-delayed content reveal',
                },
                'GLYPH_AUTHORITY': {
                    'id': GlyphProtocol.GLYPH_AUTHORITY,
                    'name': 'Authority Token',
                    'description': 'Delegated minting/management rights',
                    'requires': ['GLYPH_NFT'],
                },
                'GLYPH_WAVE': {
                    'id': GlyphProtocol.GLYPH_WAVE,
                    'name': 'WAVE Name',
                    'description': 'Human-readable naming',
                    'requires': ['GLYPH_NFT', 'GLYPH_MUT'],
                },
            },
        }

    async def glyph_parse_envelope(self, script_hex: str):
        """
        Parse a Glyph envelope from script hex.
        
        Args:
            script_hex: Script in hexadecimal
            
        Returns:
            Parsed envelope or None
        """
        self.bump_cost(0.5)
        
        try:
            script_bytes = bytes.fromhex(script_hex)
        except ValueError:
            return {'error': 'Invalid hex string'}
        
        envelope = parse_glyph_envelope(script_bytes)
        
        if not envelope:
            return None
        
        return envelope


    # ========================================================================
    # New RXinDexer Glyph Index Methods
    # ========================================================================

    async def glyph_get_token_info(self, ref: str):
        """
        Get full token information from the Glyph index.
        
        Args:
            ref: Token ref in format "txid_vout"
            
        Returns:
            Full token information dict or None
        """
        self.bump_cost(1.0)
        
        if not hasattr(self, 'glyph_index') or not self.glyph_index:
            return {'error': 'Glyph indexing not enabled'}
        
        return self.glyph_index.get_token_by_ref_str(ref)

    async def glyph_stats(self):
        """
        Get Glyph token indexing statistics.
        
        Returns:
            Dict with token counts by type and version:
            - total_tokens: Total number of indexed tokens
            - by_type: {FT, NFT, DAT, dMint, unknown}
            - by_version: {v1, v2}
            - enabled: Whether indexing is enabled
        """
        self.bump_cost(2.0)  # Slightly higher cost as it scans DB
        
        if not hasattr(self, 'glyph_index') or not self.glyph_index:
            return {'enabled': False, 'error': 'Glyph indexing not enabled'}
        
        return self.glyph_index.get_stats()

    async def glyph_get_balance(self, scripthash: str, ref: str):
        """
        Get token balance for a scripthash.
        
        Args:
            scripthash: Address scripthash (64 hex chars)
            ref: Token ref in format "txid_vout"
            
        Returns:
            Balance dict with confirmed amount
        """
        self.bump_cost(1.0)
        
        if not hasattr(self, 'glyph_index') or not self.glyph_index:
            return {'error': 'Glyph indexing not enabled'}
        
        try:
            scripthash_bytes = bytes.fromhex(scripthash)
            txid, vout = parse_ref(ref)
            from electrumx.server.glyph_index import pack_ref
            ref_bytes = pack_ref(hex_str_to_hash(txid), vout)
            
            balance = self.glyph_index.get_balance(scripthash_bytes, ref_bytes)
            return {'confirmed': balance, 'unconfirmed': 0}
        except Exception as e:
            return {'error': str(e)}

    async def glyph_list_tokens(self, scripthash: str, limit: int = 100):
        """
        List all tokens held by a scripthash.
        
        Args:
            scripthash: Address scripthash (64 hex chars)
            limit: Maximum results (default 100)
            
        Returns:
            List of token balances
        """
        self.bump_cost(2.0)
        
        if not hasattr(self, 'glyph_index') or not self.glyph_index:
            return {'error': 'Glyph indexing not enabled'}
        
        try:
            scripthash_bytes = bytes.fromhex(scripthash)
            return self.glyph_index.get_balances_for_scripthash(
                scripthash_bytes, limit=limit
            )
        except Exception as e:
            return {'error': str(e)}

    async def glyph_get_history(self, ref: str, limit: int = 100, offset: int = 0):
        """
        Get transaction history for a token.
        
        Args:
            ref: Token ref in format "txid_vout"
            limit: Maximum results
            offset: Pagination offset
            
        Returns:
            List of history events
        """
        self.bump_cost(2.0)
        
        if not hasattr(self, 'glyph_index') or not self.glyph_index:
            return {'error': 'Glyph indexing not enabled'}
        
        try:
            txid, vout = parse_ref(ref)
            from electrumx.server.glyph_index import pack_ref
            ref_bytes = pack_ref(hex_str_to_hash(txid), vout)
            
            return self.glyph_index.get_token_history(
                ref_bytes, limit=limit, offset=offset
            )
        except Exception as e:
            return {'error': str(e)}

    async def glyph_search_tokens(self, query: str, protocols: list = None, 
                                   limit: int = 50):
        """
        Search tokens by name or ticker.
        
        Args:
            query: Search query string
            protocols: Optional list of protocol IDs to filter
            limit: Maximum results
            
        Returns:
            List of matching tokens
        """
        self.bump_cost(3.0)
        
        if not hasattr(self, 'glyph_index') or not self.glyph_index:
            return {'error': 'Glyph indexing not enabled'}
        
        return self.glyph_index.search_tokens(
            query, protocols=protocols, limit=limit
        )

    async def glyph_get_tokens_by_type(self, token_type: int, limit: int = 100,
                                        offset: int = 0):
        """
        Get tokens by type.
        
        Args:
            token_type: GlyphTokenType ID (1=FT, 2=NFT, 3=DAT, 4=DMINT, etc.)
            limit: Maximum results
            offset: Pagination offset
            
        Returns:
            List of tokens of the specified type
        """
        self.bump_cost(2.0)
        
        if not hasattr(self, 'glyph_index') or not self.glyph_index:
            return {'error': 'Glyph indexing not enabled'}
        
        return self.glyph_index.get_tokens_by_type(
            token_type, limit=limit, offset=offset
        )

    async def glyph_get_metadata(self, ref: str):
        """
        Get full CBOR metadata for a token.
        
        Args:
            ref: Token ref in format "txid_vout"
            
        Returns:
            Parsed metadata dict
        """
        self.bump_cost(1.5)
        
        if not hasattr(self, 'glyph_index') or not self.glyph_index:
            return {'error': 'Glyph indexing not enabled'}
        
        try:
            token = self.glyph_index.get_token_by_ref_str(ref)
            if not token:
                return None
            
            metadata_hash = token.get('metadata_hash')
            if metadata_hash:
                return self.glyph_index.get_metadata(bytes.fromhex(metadata_hash))
            return None
        except Exception as e:
            return {'error': str(e)}

    # ========================================================================
    # dMint Contracts API (for Glyph Miner)
    # ========================================================================

    async def dmint_get_contracts(self, format: str = 'simple'):
        """
        Get list of mineable dMint contracts.
        
        Args:
            format: 'simple' for [[ref, outputs], ...] or 'extended' for full details
            
        Returns:
            List of contracts in requested format
        """
        self.bump_cost(1.0)
        
        if not hasattr(self, 'dmint_contracts') or not self.dmint_contracts:
            return {'error': 'dMint contracts manager not initialized'}
        
        if format == 'extended':
            return self.dmint_contracts.get_contracts_extended(active_only=True)
        else:
            return self.dmint_contracts.get_contracts_simple()

    async def dmint_get_contract(self, ref: str):
        """
        Get details for a specific dMint contract.
        
        Args:
            ref: Contract reference (72 hex chars: txid + vout)
            
        Returns:
            Contract details dict
        """
        self.bump_cost(1.0)
        
        if not hasattr(self, 'dmint_contracts') or not self.dmint_contracts:
            return {'error': 'dMint contracts manager not initialized'}
        
        return self.dmint_contracts.get_contract(ref)

    async def dmint_get_by_algorithm(self, algorithm: int):
        """
        Get contracts filtered by mining algorithm.
        
        Args:
            algorithm: Algorithm ID (0=SHA256D, 1=Blake3, 2=K12)
            
        Returns:
            List of contracts using that algorithm
        """
        self.bump_cost(1.5)
        
        if not hasattr(self, 'dmint_contracts') or not self.dmint_contracts:
            return {'error': 'dMint contracts manager not initialized'}
        
        return self.dmint_contracts.get_contracts_by_algorithm(algorithm)

    async def dmint_get_most_profitable(self, limit: int = 10):
        """
        Get contracts sorted by estimated profitability.
        
        Args:
            limit: Maximum contracts to return (default 10)
            
        Returns:
            List of contracts sorted by reward/difficulty ratio
        """
        self.bump_cost(2.0)
        
        if not hasattr(self, 'dmint_contracts') or not self.dmint_contracts:
            return {'error': 'dMint contracts manager not initialized'}
        
        return self.dmint_contracts.get_most_profitable(limit=min(limit, 100))

    async def dmint_get_stats(self):
        """
        Get aggregate dMint statistics.
        
        Returns:
            Dict with total/active/completed counts, breakdown by algorithm and DAA mode
        """
        self.bump_cost(2.0)
        
        if not hasattr(self, 'dmint_contracts') or not self.dmint_contracts:
            return {'error': 'dMint contracts manager not initialized'}
        
        contracts = self.dmint_contracts.contracts
        active = [c for c in contracts if c.get('active', True)]
        inactive = [c for c in contracts if not c.get('active', True)]
        
        algo_names = {0: 'SHA256D', 1: 'BLAKE3', 2: 'K12', 3: 'Argon2id-Light', 4: 'RandomX-Light'}
        by_algorithm = {}
        for c in active:
            algo_id = c.get('algorithm', 0)
            name = algo_names.get(algo_id, f'unknown({algo_id})')
            by_algorithm[name] = by_algorithm.get(name, 0) + 1
        
        daa_names = {0: 'fixed', 1: 'epoch', 2: 'asert', 3: 'lwma', 4: 'schedule'}
        by_daa = {}
        for c in active:
            daa_id = c.get('daa_mode', 0)
            name = daa_names.get(daa_id, f'unknown({daa_id})')
            by_daa[name] = by_daa.get(name, 0) + 1
        
        return {
            'total_contracts': len(contracts),
            'active': len(active),
            'completed': len(inactive),
            'by_algorithm': by_algorithm,
            'by_daa_mode': by_daa,
            'total_active_reward': sum(c.get('reward', 0) for c in active),
            'updated_height': self.dmint_contracts.last_updated_height,
        }

    async def dmint_get_contract_daa(self, ref: str):
        """
        Get DAA configuration for a specific dMint contract.
        
        Args:
            ref: Contract reference (72 hex chars)
            
        Returns:
            Dict with algorithm, DAA mode, current difficulty, and mode-specific params
        """
        self.bump_cost(1.0)
        
        if not hasattr(self, 'dmint_contracts') or not self.dmint_contracts:
            return {'error': 'dMint contracts manager not initialized'}
        
        contract = self.dmint_contracts.get_contract(ref)
        if not contract:
            return {'error': 'Contract not found'}
        
        daa_names = {0: 'fixed', 1: 'epoch', 2: 'asert', 3: 'lwma', 4: 'schedule'}
        algo_names = {0: 'SHA256D', 1: 'BLAKE3', 2: 'K12', 3: 'Argon2id-Light', 4: 'RandomX-Light'}
        daa_id = contract.get('daa_mode', 0)
        algo_id = contract.get('algorithm', 0)
        
        result = {
            'ref': ref,
            'algorithm': {'id': algo_id, 'name': algo_names.get(algo_id, 'unknown')},
            'daa_mode': {'id': daa_id, 'name': daa_names.get(daa_id, 'unknown')},
            'current_difficulty': contract.get('difficulty', 0),
            'reward': contract.get('reward', 0),
        }
        
        daa_params = contract.get('daa_params', {})
        if daa_params:
            result['daa_params'] = daa_params
        elif daa_id == 2:
            result['daa_params'] = {
                'target_block_time': contract.get('target_block_time', 60),
                'half_life': contract.get('half_life', 1000),
            }
        elif daa_id == 3:
            result['daa_params'] = {
                'target_block_time': contract.get('target_block_time', 60),
                'window_size': contract.get('window_size', 144),
            }
        elif daa_id == 1:
            result['daa_params'] = {
                'target_block_time': contract.get('target_block_time', 60),
                'epoch_length': contract.get('epoch_length', 2016),
                'max_adjustment': contract.get('max_adjustment', 4),
            }
        
        return result

    # ========================================================================
    # Mempool Glyph/Swap API
    # ========================================================================

    async def glyph_get_unconfirmed_balance(self, scripthash: str, ref: str):
        """
        Get unconfirmed (mempool) balance delta for a token.
        
        Args:
            scripthash: Address scripthash (hex)
            ref: Token ref in format "txid_vout"
            
        Returns:
            Unconfirmed balance delta (positive=incoming, negative=outgoing)
        """
        self.bump_cost(1.0)
        
        if not hasattr(self, 'mempool') or not self.mempool:
            return {'error': 'Mempool not available'}
        
        if not hasattr(self.mempool, 'glyph_mempool') or not self.mempool.glyph_mempool:
            return 0  # Mempool Glyph indexing not enabled
        
        try:
            scripthash_bytes = bytes.fromhex(scripthash)
            ref_bytes = self._parse_ref(ref)
            return self.mempool.glyph_mempool.get_unconfirmed_glyph_balance(
                scripthash_bytes, ref_bytes
            )
        except Exception as e:
            return {'error': str(e)}

    async def glyph_get_unconfirmed_txs(self, scripthash: str):
        """
        Get unconfirmed Glyph transactions for an address.
        
        Args:
            scripthash: Address scripthash (hex)
            
        Returns:
            List of unconfirmed Glyph transactions
        """
        self.bump_cost(1.5)
        
        if not hasattr(self, 'mempool') or not self.mempool:
            return {'error': 'Mempool not available'}
        
        if not hasattr(self.mempool, 'glyph_mempool') or not self.mempool.glyph_mempool:
            return []
        
        try:
            scripthash_bytes = bytes.fromhex(scripthash)
            return self.mempool.glyph_mempool.get_unconfirmed_glyph_txs(scripthash_bytes)
        except Exception as e:
            return {'error': str(e)}

    async def glyph_get_token_unconfirmed(self, ref: str):
        """
        Get unconfirmed transactions for a specific token.
        
        Args:
            ref: Token ref in format "txid_vout"
            
        Returns:
            List of unconfirmed transactions for the token
        """
        self.bump_cost(1.5)
        
        if not hasattr(self, 'mempool') or not self.mempool:
            return {'error': 'Mempool not available'}
        
        if not hasattr(self.mempool, 'glyph_mempool') or not self.mempool.glyph_mempool:
            return []
        
        try:
            ref_bytes = self._parse_ref(ref)
            return self.mempool.glyph_mempool.get_unconfirmed_token_txs(ref_bytes)
        except Exception as e:
            return {'error': str(e)}

    async def swap_get_unconfirmed_orders(self, base_ref: str = None, quote_ref: str = None):
        """
        Get unconfirmed swap orders from mempool.
        
        Args:
            base_ref: Optional base token ref filter
            quote_ref: Optional quote token ref filter
            
        Returns:
            List of unconfirmed swap orders
        """
        self.bump_cost(2.0)
        
        if not hasattr(self, 'mempool') or not self.mempool:
            return {'error': 'Mempool not available'}
        
        if not hasattr(self.mempool, 'glyph_mempool') or not self.mempool.glyph_mempool:
            return []
        
        try:
            base_bytes = self._parse_ref(base_ref) if base_ref else None
            quote_bytes = self._parse_ref(quote_ref) if quote_ref else None
            return self.mempool.glyph_mempool.get_unconfirmed_swap_orders(
                base_bytes, quote_bytes
            )
        except Exception as e:
            return {'error': str(e)}

    async def swap_get_user_unconfirmed(self, scripthash: str):
        """
        Get unconfirmed swap orders for a user.
        
        Args:
            scripthash: User's scripthash (hex)
            
        Returns:
            List of user's unconfirmed orders
        """
        self.bump_cost(1.5)
        
        if not hasattr(self, 'mempool') or not self.mempool:
            return {'error': 'Mempool not available'}
        
        if not hasattr(self.mempool, 'glyph_mempool') or not self.mempool.glyph_mempool:
            return []
        
        try:
            scripthash_bytes = bytes.fromhex(scripthash)
            return self.mempool.glyph_mempool.get_user_unconfirmed_orders(scripthash_bytes)
        except Exception as e:
            return {'error': str(e)}

    async def mempool_glyph_stats(self):
        """
        Get mempool Glyph/Swap indexing statistics.
        
        Returns:
            Stats dict with counts of indexed items
        """
        self.bump_cost(0.5)
        
        if not hasattr(self, 'mempool') or not self.mempool:
            return {'error': 'Mempool not available'}
        
        if not hasattr(self.mempool, 'glyph_mempool') or not self.mempool.glyph_mempool:
            return {'enabled': False}
        
        stats = self.mempool.glyph_mempool.stats()
        stats['enabled'] = True
        return stats

    def _parse_ref(self, ref: str) -> bytes:
        """Parse a ref string to bytes."""
        if '_' in ref:
            txid_hex, vout_str = ref.split('_')
            txid = bytes.fromhex(txid_hex)[::-1]  # Reverse for internal format
            vout = int(vout_str)
            return txid + vout.to_bytes(4, 'little')
        else:
            # Assume it's already in hex format
            return bytes.fromhex(ref)

    # ========================================================================
    # WebSocket Subscription API
    # ========================================================================

    async def glyph_subscribe_balance(self, scripthash: str, ref: str):
        """Subscribe to token balance changes for an address."""
        self.bump_cost(0.5)
        
        if not hasattr(self, 'glyph_subscriptions') or not self.glyph_subscriptions:
            return {'error': 'Subscriptions not enabled'}
        
        try:
            scripthash_bytes = bytes.fromhex(scripthash)
            ref_bytes = self._parse_ref(ref)
            session_id = id(self)
            return self.glyph_subscriptions.subscribe_balance(session_id, scripthash_bytes, ref_bytes)
        except Exception as e:
            return {'error': str(e)}

    async def glyph_unsubscribe_balance(self, scripthash: str, ref: str):
        """Unsubscribe from token balance changes."""
        self.bump_cost(0.1)
        
        if not hasattr(self, 'glyph_subscriptions') or not self.glyph_subscriptions:
            return False
        
        try:
            scripthash_bytes = bytes.fromhex(scripthash)
            ref_bytes = self._parse_ref(ref)
            session_id = id(self)
            return self.glyph_subscriptions.unsubscribe_balance(session_id, scripthash_bytes, ref_bytes)
        except Exception:
            return False

    async def glyph_subscribe_token(self, ref: str):
        """Subscribe to token state changes."""
        self.bump_cost(0.5)
        
        if not hasattr(self, 'glyph_subscriptions') or not self.glyph_subscriptions:
            return {'error': 'Subscriptions not enabled'}
        
        try:
            ref_bytes = self._parse_ref(ref)
            session_id = id(self)
            return self.glyph_subscriptions.subscribe_token(session_id, ref_bytes)
        except Exception as e:
            return {'error': str(e)}

    async def glyph_unsubscribe_token(self, ref: str):
        """Unsubscribe from token state changes."""
        self.bump_cost(0.1)
        
        if not hasattr(self, 'glyph_subscriptions') or not self.glyph_subscriptions:
            return False
        
        try:
            ref_bytes = self._parse_ref(ref)
            session_id = id(self)
            return self.glyph_subscriptions.unsubscribe_token(session_id, ref_bytes)
        except Exception:
            return False

    async def glyph_subscribe_transfers(self, ref: str):
        """Subscribe to token transfer events."""
        self.bump_cost(0.5)
        
        if not hasattr(self, 'glyph_subscriptions') or not self.glyph_subscriptions:
            return {'error': 'Subscriptions not enabled'}
        
        try:
            ref_bytes = self._parse_ref(ref)
            session_id = id(self)
            return self.glyph_subscriptions.subscribe_transfers(session_id, ref_bytes)
        except Exception as e:
            return {'error': str(e)}

    async def swap_subscribe_orderbook(self, base_ref: str, quote_ref: str):
        """Subscribe to orderbook updates for a trading pair."""
        self.bump_cost(0.5)
        
        if not hasattr(self, 'glyph_subscriptions') or not self.glyph_subscriptions:
            return {'error': 'Subscriptions not enabled'}
        
        try:
            base_bytes = self._parse_ref(base_ref)
            quote_bytes = self._parse_ref(quote_ref)
            session_id = id(self)
            return self.glyph_subscriptions.subscribe_orderbook(session_id, base_bytes, quote_bytes)
        except Exception as e:
            return {'error': str(e)}

    async def swap_unsubscribe_orderbook(self, base_ref: str, quote_ref: str):
        """Unsubscribe from orderbook updates."""
        self.bump_cost(0.1)
        
        if not hasattr(self, 'glyph_subscriptions') or not self.glyph_subscriptions:
            return False
        
        try:
            base_bytes = self._parse_ref(base_ref)
            quote_bytes = self._parse_ref(quote_ref)
            session_id = id(self)
            return self.glyph_subscriptions.unsubscribe_orderbook(session_id, base_bytes, quote_bytes)
        except Exception:
            return False

    async def swap_subscribe_fills(self, base_ref: str, quote_ref: str):
        """Subscribe to trade fill notifications."""
        self.bump_cost(0.5)
        
        if not hasattr(self, 'glyph_subscriptions') or not self.glyph_subscriptions:
            return {'error': 'Subscriptions not enabled'}
        
        try:
            base_bytes = self._parse_ref(base_ref)
            quote_bytes = self._parse_ref(quote_ref)
            session_id = id(self)
            return self.glyph_subscriptions.subscribe_fills(session_id, base_bytes, quote_bytes)
        except Exception as e:
            return {'error': str(e)}

    async def swap_subscribe_user_orders(self, scripthash: str):
        """Subscribe to user's order updates."""
        self.bump_cost(0.5)
        
        if not hasattr(self, 'glyph_subscriptions') or not self.glyph_subscriptions:
            return {'error': 'Subscriptions not enabled'}
        
        try:
            scripthash_bytes = bytes.fromhex(scripthash)
            session_id = id(self)
            return self.glyph_subscriptions.subscribe_user_orders(session_id, scripthash_bytes)
        except Exception as e:
            return {'error': str(e)}

    async def wave_subscribe_name(self, name: str):
        """Subscribe to WAVE name ownership changes."""
        self.bump_cost(0.5)
        
        if not hasattr(self, 'glyph_subscriptions') or not self.glyph_subscriptions:
            return {'error': 'Subscriptions not enabled'}
        
        try:
            session_id = id(self)
            return self.glyph_subscriptions.subscribe_wave_name(session_id, name)
        except Exception as e:
            return {'error': str(e)}

    async def dmint_subscribe_token(self, ref: str):
        """Subscribe to dMint token mining stats updates."""
        self.bump_cost(0.5)
        
        if not hasattr(self, 'glyph_subscriptions') or not self.glyph_subscriptions:
            return {'error': 'Subscriptions not enabled'}
        
        try:
            ref_bytes = self._parse_ref(ref)
            session_id = id(self)
            return self.glyph_subscriptions.subscribe_dmint(session_id, ref_bytes)
        except Exception as e:
            return {'error': str(e)}

    # ========================================================================
    # WAVE Naming System API
    # ========================================================================

    async def wave_resolve(self, name: str):
        """
        Resolve a WAVE name to its zone records and owner.
        
        Args:
            name: WAVE name to resolve (e.g., "alice", "mail.alice")
            
        Returns:
            {name, ref, zone, owner, available} or None if not registered
        """
        self.bump_cost(1.0)
        
        if not hasattr(self, 'wave_index') or not self.wave_index:
            return {'error': 'WAVE indexing not enabled'}
        
        return self.wave_index.resolve(name)

    async def wave_check_available(self, name: str):
        """
        Check if a WAVE name is available for registration.
        
        Args:
            name: WAVE name to check
            
        Returns:
            {available: bool, name, ref?, error?}
        """
        self.bump_cost(0.5)
        
        if not hasattr(self, 'wave_index') or not self.wave_index:
            return {'error': 'WAVE indexing not enabled'}
        
        return self.wave_index.check_available(name)

    async def wave_get_subdomains(self, parent_name: str, limit: int = 100, 
                                   offset: int = 0):
        """
        Get subdomains of a parent WAVE name.
        
        Args:
            parent_name: Parent name to query
            limit: Maximum results (default 100)
            offset: Pagination offset
            
        Returns:
            List of {char, ref} for registered subdomains
        """
        self.bump_cost(2.0)
        
        if not hasattr(self, 'wave_index') or not self.wave_index:
            return {'error': 'WAVE indexing not enabled'}
        
        return self.wave_index.get_subdomains(
            parent_name, limit=min(limit, 1000), offset=offset
        )

    async def wave_reverse_lookup(self, scripthash: str, limit: int = 100):
        """
        Find WAVE names owned by an address.
        
        Args:
            scripthash: Owner's scripthash (hex)
            limit: Maximum results (default 100)
            
        Returns:
            List of {ref} for owned names
        """
        self.bump_cost(3.0)
        
        if not hasattr(self, 'wave_index') or not self.wave_index:
            return {'error': 'WAVE indexing not enabled'}
        
        try:
            scripthash_bytes = bytes.fromhex(scripthash)
            return self.wave_index.reverse_lookup(scripthash_bytes, limit=min(limit, 1000))
        except Exception as e:
            return {'error': str(e)}

    async def wave_stats(self):
        """Get WAVE indexing statistics."""
        self.bump_cost(0.5)
        
        if not hasattr(self, 'wave_index') or not self.wave_index:
            return {'enabled': False}
        
        return self.wave_index.stats()


# Method registration for ElectrumX
GLYPH_METHODS = {
    # Original methods
    'glyph.get_token': 'glyph_get_token',
    'glyph.get_by_ref': 'glyph_get_by_ref',
    'glyph.validate_protocols': 'glyph_validate_protocols',
    'glyph.get_protocol_info': 'glyph_get_protocol_info',
    'glyph.parse_envelope': 'glyph_parse_envelope',
    # New RXinDexer methods
    'glyph.stats': 'glyph_stats',
    'glyph.get_token_info': 'glyph_get_token_info',
    'glyph.get_balance': 'glyph_get_balance',
    'glyph.list_tokens': 'glyph_list_tokens',
    'glyph.get_history': 'glyph_get_history',
    'glyph.search_tokens': 'glyph_search_tokens',
    'glyph.get_tokens_by_type': 'glyph_get_tokens_by_type',
    'glyph.get_metadata': 'glyph_get_metadata',
    # dMint contracts (for Glyph Miner)
    'dmint.get_contracts': 'dmint_get_contracts',
    'dmint.get_contract': 'dmint_get_contract',
    'dmint.get_by_algorithm': 'dmint_get_by_algorithm',
    'dmint.get_most_profitable': 'dmint_get_most_profitable',
    # Mempool Glyph/Swap
    'glyph.get_unconfirmed_balance': 'glyph_get_unconfirmed_balance',
    'glyph.get_unconfirmed_txs': 'glyph_get_unconfirmed_txs',
    'glyph.get_token_unconfirmed': 'glyph_get_token_unconfirmed',
    'swap.get_unconfirmed_orders': 'swap_get_unconfirmed_orders',
    'swap.get_user_unconfirmed': 'swap_get_user_unconfirmed',
    'mempool.glyph_stats': 'mempool_glyph_stats',
    # WebSocket Subscriptions
    'glyph.subscribe.balance': 'glyph_subscribe_balance',
    'glyph.unsubscribe.balance': 'glyph_unsubscribe_balance',
    'glyph.subscribe.token': 'glyph_subscribe_token',
    'glyph.unsubscribe.token': 'glyph_unsubscribe_token',
    'glyph.subscribe.transfers': 'glyph_subscribe_transfers',
    'swap.subscribe.orderbook': 'swap_subscribe_orderbook',
    'swap.unsubscribe.orderbook': 'swap_unsubscribe_orderbook',
    'swap.subscribe.fills': 'swap_subscribe_fills',
    'swap.subscribe.user_orders': 'swap_subscribe_user_orders',
    'wave.subscribe.name': 'wave_subscribe_name',
    'dmint.subscribe.token': 'dmint_subscribe_token',
    # WAVE Naming System
    'wave.resolve': 'wave_resolve',
    'wave.check_available': 'wave_check_available',
    'wave.get_subdomains': 'wave_get_subdomains',
    'wave.reverse_lookup': 'wave_reverse_lookup',
    'wave.stats': 'wave_stats',
}
