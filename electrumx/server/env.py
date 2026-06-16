# Copyright (c) 2016, Neil Booth
#
# All rights reserved.
#
# See the file "LICENCE" for information about the copyright
# and warranty status of this software.

'''Class for handling environment configuration and defaults.'''


import logging
import re
from ipaddress import IPv4Address, IPv6Address

_logger = logging.getLogger('electrumx.server.env')

from aiorpcx import Service, ServicePart
from electrumx.lib.coins import Coin
from electrumx.lib.env_base import EnvBase


class ServiceError(Exception):
    pass


class Env(EnvBase):
    '''Wraps environment configuration. Optionally, accepts a Coin class
       as first argument to have ElectrumX serve custom coins not part of
       the standard distribution.
    '''

    # Peer discovery
    PD_OFF, PD_SELF, PD_ON = ('OFF', 'SELF', 'ON')
    SSL_PROTOCOLS = {'ssl', 'wss'}
    KNOWN_PROTOCOLS = {'ssl', 'tcp', 'ws', 'wss', 'rpc'}

    def __init__(self, coin=None):
        super().__init__()
        self.obsolete(["MAX_SUBSCRIPTIONS", "MAX_SUBS", "MAX_SESSION_SUBS", "BANDWIDTH_LIMIT",
                       "HOST", "TCP_PORT", "SSL_PORT", "RPC_HOST", "RPC_PORT", "REPORT_HOST",
                       "REPORT_TCP_PORT", "REPORT_SSL_PORT", "REPORT_HOST_TOR",
                       "REPORT_TCP_PORT_TOR", "REPORT_SSL_PORT_TOR"])

        # Core items

        self.db_dir = self.required('DB_DIRECTORY')
        self.daemon_url = self.required('DAEMON_URL')
        if coin is not None:
            assert issubclass(coin, Coin)
            self.coin = coin
        else:
            coin_name = self.required('COIN').strip()
            network = self.default('NET', 'mainnet').strip()
            self.coin = Coin.lookup_coin_class(coin_name, network)

        # Peer discovery

        self.peer_discovery = self.peer_discovery_enum()
        self.peer_announce = self.boolean('PEER_ANNOUNCE', True)
        self.force_proxy = self.boolean('FORCE_PROXY', False)
        self.tor_proxy_host = self.default('TOR_PROXY_HOST', 'localhost')
        self.tor_proxy_port = self.integer('TOR_PROXY_PORT', None)

        # Misc

        self.db_engine = self.default('DB_ENGINE', 'rocksdb')
        self.banner_file = self.default('BANNER_FILE', None)
        self.tor_banner_file = self.default('TOR_BANNER_FILE',
                                            self.banner_file)
        self.anon_logs = self.boolean('ANON_LOGS', False)
        self.log_sessions = self.integer('LOG_SESSIONS', 3600)
        self.log_level = self.default('LOG_LEVEL', 'info').upper()
        self.donation_address = self.default('DONATION_ADDRESS', '')
        self.drop_client = self.custom("DROP_CLIENT", None, re.compile)
        self.cache_MB = self.integer('CACHE_MB', 1200)

        # R26: use coin.REORG_LIMIT as default (Radiant coin sets 69 = node max reorg depth)
        # Warn if set to an unreasonably low value
        self.reorg_limit = self.integer('REORG_LIMIT', self.coin.REORG_LIMIT)
        if self.reorg_limit < 2:
            _logger.warning(
                f'REORG_LIMIT={self.reorg_limit} is dangerously low — reorgs deeper than '
                f'{self.reorg_limit} blocks cannot be handled safely'
            )

        # R25: MINIMAL_MODE=1 disables all optional indexers and REST API
        minimal = self.boolean('MINIMAL_MODE', False)

        # RXinDexer: Glyph/WAVE/Swap indexing configuration
        self.glyph_index = False if minimal else self.boolean('GLYPH_INDEX', True)
        self.wave_index = False if minimal else self.boolean('WAVE_INDEX', True)
        self.realm_index = False if minimal else self.boolean('REALM_INDEX', True)
        self.swap_index = False if minimal else self.boolean('SWAP_INDEX', True)
        self.predict_index = False if minimal else self.boolean('PREDICT_INDEX', True)
        # Royalty-listing (RRYL beacon) discovery — default OFF: enabling it
        # requires a reindex/backfill so historic listings populate (see
        # docs / RoyaltyIndex). Turn on with ROYALTY_INDEX=1 after deploy.
        self.royalty_index = False if minimal else self.boolean('ROYALTY_INDEX', False)
        self.analytics_index = False if minimal else self.boolean('ANALYTICS_INDEX', True)
        self.glyph_subscriptions = False if minimal else self.boolean('GLYPH_SUBSCRIPTIONS', True)
        self.mempool_glyph_index = False if minimal else self.boolean('MEMPOOL_GLYPH_INDEX', True)
        self.mempool_swap_index = False if minimal else self.boolean('MEMPOOL_SWAP_INDEX', True)
        self.rest_api = False if minimal else self.boolean('REST_API', True)
        if minimal:
            _logger.info('MINIMAL_MODE enabled: all optional indexers and REST API disabled')
        
        # WAVE naming system configuration
        self.wave_genesis_ref = self.default('WAVE_GENESIS_REF', None)
        self.wave_hot_names = self.integer('WAVE_HOT_NAMES', 10000)
        
        # dMint contracts configuration
        self.dmint_contracts_file = self.default('DMINT_CONTRACTS_FILE', 'data/contracts.json')

        # dMint spam denylist — comma-separated token refs in txid_vout form.
        # Mint events for these tokens are indexed for supply tracking but
        # history entries and per-address balance writes are silently skipped,
        # and any stored CBOR metadata blobs (GM keys) are scrubbed on startup.
        # Does not affect the token's GT record (it remains queryable).
        # Example: DMINT_DENYLIST=abc123_0,def456_1
        raw_denylist = self.default('DMINT_DENYLIST', '')
        self.dmint_denylist: set = set()
        if raw_denylist.strip():
            for entry in raw_denylist.split(','):
                entry = entry.strip()
                if entry:
                    self.dmint_denylist.add(entry)

        # Server limits to help prevent DoS

        self.max_send = self.integer('MAX_SEND', self.coin.DEFAULT_MAX_SEND)
        self.max_recv = self.integer('MAX_RECV', 5_000_000)
        self.max_sessions = self.sane_max_sessions()
        self.cost_soft_limit = self.integer('COST_SOFT_LIMIT', 10000)
        self.cost_hard_limit = self.integer('COST_HARD_LIMIT', 100000)
        self.bw_unit_cost = self.integer('BANDWIDTH_UNIT_COST', 5000)
        self.initial_concurrent = self.integer('INITIAL_CONCURRENT', 50)
        self.request_sleep = self.integer('REQUEST_SLEEP', 500)
        self.request_timeout = self.integer('REQUEST_TIMEOUT', 30)
        self.session_timeout = self.integer('SESSION_TIMEOUT', 600)

        # IP-persistent, proxy-aware rate limiting (H3 follow-up).  These bound
        # an attacker who opens many connections or reconnect-loops to bypass
        # the per-session subscription cap.  Defaults are high enough that a
        # normal client never trips them; the feature degrades gracefully (with
        # no trusted proxy the socket peer is used).
        self.rate_limit_enabled = self.boolean('RATE_LIMIT_ENABLED', True)
        # Proxy trust model (matches REST API: honour X-Forwarded-For ONLY when
        # explicitly placed behind a trusted reverse proxy).
        self.trust_proxy = self.boolean('TRUST_PROXY', False)
        self.trust_proxy_hops = self.integer('TRUST_PROXY_HOPS', 1)
        # Socket-peer allowlist (comma/space-separated CIDRs or IPs).  Only a
        # peer in this list is treated as the reverse proxy whose
        # X-Forwarded-For is honoured; a directly-connected client cannot spoof
        # a victim IP.  Empty -> the rate limiter's safe default (loopback +
        # RFC1918 docker ranges).
        self.trusted_proxies = self.default('TRUSTED_PROXIES', '')
        # Aggregate subscription cap across all of one IP's sessions.  Reuses
        # MAX_SUBS_PER_CLIENT semantics but at the IP layer; defaults larger
        # than the per-session cap so the per-session cap governs single
        # connections and this only catches the many-connection bypass.
        self.max_subs_per_ip = self.integer('MAX_SUBS_PER_IP', 50000)
        # Per-IP concurrent CONNECTION cap: bounds how many simultaneous
        # sessions one client IP may hold (the global MAX_SESSIONS can't stop a
        # single host filling every slot).  Generous by default so shared NAT /
        # a power user is unaffected; never applied to a trusted-proxy address
        # by the limiter, so a missing X-Forwarded-For can't lock everyone out.
        # 0 disables.
        self.max_sessions_per_ip = self.integer('MAX_SESSIONS_PER_IP', 200)
        # Per-IP cost ceiling and block window.
        self.ip_cost_hard_limit = self.integer('IP_COST_HARD_LIMIT', 1_000_000)
        self.rate_block_duration = self.integer('RATE_BLOCK_DURATION', 300)
        # Idle TTL after which a per-IP state is evicted (memory bound).
        self.ip_state_ttl = self.integer('IP_STATE_TTL', 3600)

        # Services last - uses some env vars above

        self.services = self.services_to_run()
        if {service.protocol for service in self.services}.intersection(self.SSL_PROTOCOLS):
            self.ssl_certfile = self.required('SSL_CERTFILE')
            self.ssl_keyfile = self.required('SSL_KEYFILE')
        self.report_services = self.services_to_report()

    def sane_max_sessions(self):
        '''Return the maximum number of sessions to permit.  Normally this
        is MAX_SESSIONS.  However, to prevent open file exhaustion, ajdust
        downwards if running with a small open file rlimit.'''
        env_value = self.integer('MAX_SESSIONS', 1000)
        # No resource module on Windows
        try:
            import resource
            nofile_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
            # We give the DB 250 files; allow ElectrumX 100 for itself
            value = max(0, min(env_value, nofile_limit - 350))
            if value < env_value:
                self.logger.warning('lowered maximum sessions from {:,d} to {:,d} '
                                    'because your open file limit is {:,d}'
                                    .format(env_value, value, nofile_limit))
        except ImportError:
            value = 512  # that is what returned by stdio's _getmaxstdio()
        return value

    def _parse_services(self, services_str, default_func):
        result = []
        for service_str in services_str.split(','):
            if not service_str:
                continue
            try:
                service = Service.from_string(service_str, default_func=default_func)
            except Exception as e:
                raise ServiceError(f'"{service_str}" invalid: {e}') from None
            if service.protocol not in self.KNOWN_PROTOCOLS:
                raise ServiceError(f'"{service_str}" invalid: unknown protocol')
            result.append(service)

        # Find duplicate addresses
        service_map = {service.address: [] for service in result}
        for service in result:
            service_map[service.address].append(service)
        for address, services in service_map.items():
            if len(services) > 1:
                raise ServiceError(f'address {address} has multiple services')

        return result

    def services_to_run(self):
        def default_part(protocol, part):
            return default_services.get(protocol, {}).get(part)

        default_services = {protocol: {ServicePart.HOST: 'all_interfaces'}
                            for protocol in self.KNOWN_PROTOCOLS}
        default_services['rpc'] = {ServicePart.HOST: 'localhost', ServicePart.PORT: 8001}
        services = self._parse_services(self.default('SERVICES', ''), default_part)

        # Find onion hosts
        for service in services:
            if str(service.host).endswith('.onion'):
                raise ServiceError(f'bad host for SERVICES: {service}')

        return services

    def services_to_report(self):
        services = self._parse_services(self.default('REPORT_SERVICES', ''), None)

        for service in services:
            if service.protocol == 'rpc':
                raise ServiceError(f'bad protocol for REPORT_SERVICES: {service.protocol}')
            if isinstance(service.host, (IPv4Address, IPv6Address)):
                ip_addr = service.host
                if (ip_addr.is_multicast or ip_addr.is_unspecified or
                        (ip_addr.is_private and self.peer_announce)):
                    raise ServiceError(f'bad IP address for REPORT_SERVICES: {ip_addr}')
            elif service.host.lower() == 'localhost':
                raise ServiceError(f'bad host for REPORT_SERVICES: {service.host}')

        return services

    def peer_discovery_enum(self):
        pd = self.default('PEER_DISCOVERY', 'on').strip().lower()
        if pd in ('off', ''):
            return self.PD_OFF
        elif pd == 'self':
            return self.PD_SELF
        else:
            return self.PD_ON
