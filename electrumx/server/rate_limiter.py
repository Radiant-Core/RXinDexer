"""IP-persistent, proxy-aware rate limiting for RXinDexer (H3 follow-up).

The per-*session* subscription cap in :mod:`electrumx.server.glyph_subscriptions`
(``GlyphSubscriptionManager._check_can_subscribe`` / ``MAX_SUBS_PER_CLIENT``)
bounds a single live connection.  It does NOT bound an attacker who opens many
connections, or who reconnects after disconnect cleanup, because each new
session gets a fresh budget and the aiorpcx per-session cost resets to its
connection base.  Closing that bypass is what this module does: it holds
**per-IP** state that PERSISTS across reconnects, so accumulated cost,
violations and the aggregate subscription count are shared across all of an
IP's concurrent and sequential sessions.

Design:

* ``IPState`` — one record per client IP.  Cost decays over time (same decay
  model as aiorpcx).  It tracks a violation count, a ``blocked_until`` wall
  clock, the set of active session ids, and the aggregate live subscription
  count.
* ``IPRateLimiter`` — owns the ``ip -> IPState`` map.  Entries are evicted only
  after ``ip_state_ttl`` seconds of inactivity (last-seen), so a dropped+
  reopened connection finds its prior state intact, while idle IPs are reaped
  so memory stays bounded.
* Proxy trust — when ``TRUST_PROXY`` is on AND the socket peer is itself a
  configured trusted proxy (``TRUSTED_PROXIES``, a CIDR/IP allowlist that
  defaults to loopback + the RFC1918 docker ranges), the real client IP is
  taken from the proxy-supplied forwarded chain (``TRUST_PROXY_HOPS`` from the
  right); otherwise the raw socket peer is used.  This peer check is what stops
  a client that connects DIRECTLY to a published listener (e.g. ``wss
  :50011``/``ssl :50012``) from spoofing ``X-Forwarded-For`` to poison another
  IP's persisted cost bucket — its forwarded header is ignored because its peer
  is not in the allowlist.  When a trusted proxy is configured the socket peer
  is the proxy (typically loopback / the bridge subnet), so private/loopback
  peers are NOT exempted — we throttle on the forwarded client IP instead.

The limiter is intentionally dependency-free and synchronous; callers are
single-threaded asyncio sessions, so a plain dict + monotonic clock is enough.
"""

import os
import time
from ipaddress import ip_address, ip_network
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Defaults.  Chosen high enough that a normal Electrum/Photonic client never
# trips them; only abusive aggregate behaviour from one IP does.
# ---------------------------------------------------------------------------

# Aggregate subscription cap across ALL of one IP's sessions.  Larger than the
# per-session DEFAULT_MAX_SUBS_PER_CLIENT (10000) so a single well-behaved
# session is governed by the per-session cap, while the per-IP layer only
# catches the many-connection / reconnect-loop bypass.
DEFAULT_MAX_SUBS_PER_IP = 50000

# Per-IP concurrent CONNECTION cap.  The global MAX_SESSIONS alone cannot stop
# one host from filling every slot, so this bounds how many simultaneous
# sessions a single client IP may hold.  Chosen generous so a shared NAT or a
# power user (wallet + miner + a few tabs) is never affected — only a
# single-host socket flood is.  SAFETY: never applied to a trusted-proxy /
# private address (see check_can_register), so a missing or garbled
# X-Forwarded-For — which collapses every real client onto the proxy's bridge
# IP — can never lock everyone out at once.  0 disables the cap.
DEFAULT_MAX_SESSIONS_PER_IP = 200

# Hard cost ceiling per IP.  Mirrors the aiorpcx per-session cost scale
# (cost_hard_limit defaults to 100000 in env.py); the per-IP ceiling is the
# aggregate budget an IP may accumulate across reconnects before it is blocked.
DEFAULT_IP_COST_HARD_LIMIT = 1_000_000.0

# How long an IP stays blocked once it trips the hard limit (seconds).
DEFAULT_RATE_BLOCK_DURATION = 300

# Idle TTL: evict an IP's state this many seconds after its last activity.
# Long enough that a reconnecting client keeps its throttle, short enough that
# memory is bounded.
DEFAULT_IP_STATE_TTL = 3600

# Cost decay per second.  Matches the order of magnitude aiorpcx uses
# (cost_hard_limit / 10000); applied lazily on each touch.
_DEFAULT_COST_DECAY_PER_SEC = DEFAULT_IP_COST_HARD_LIMIT / 10000.0

# Trusted-proxy allowlist used when TRUST_PROXY is on but TRUSTED_PROXIES is
# not explicitly set.  Only a socket peer inside one of these networks is
# treated as the reverse proxy whose X-Forwarded-For we honour.  The default
# covers loopback and the RFC1918 ranges (the docker bridge Caddy connects
# from), so a directly-connected public client cannot spoof a victim's IP.
# Operators behind a proxy on a known address SHOULD narrow this to the exact
# proxy IP / bridge subnet via the TRUSTED_PROXIES env var.
DEFAULT_TRUSTED_PROXIES = (
    '127.0.0.0/8',     # IPv4 loopback
    '10.0.0.0/8',      # RFC1918
    '172.16.0.0/12',   # RFC1918 (docker default bridge networks live here)
    '192.168.0.0/16',  # RFC1918
    '::1/128',         # IPv6 loopback
    'fc00::/7',        # IPv6 unique-local
)


def _coerce_int(value, default: int) -> int:
    """Best-effort int coercion that never raises (Mock-safe)."""
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value, default: float) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def peer_in_networks(host: Optional[str], networks: List) -> bool:
    """Whether a peer host string falls inside any of the given networks.

    Shared by the per-IP limiter's trusted-proxy check
    (:meth:`IPRateLimiter._peer_is_trusted_proxy`) and the REST API's
    ``_get_client_ip`` so both gate ``X-Forwarded-For`` trust on the SAME
    allowlist semantics.  Returns ``False`` — i.e. "not a trusted proxy", the
    safe default — for an empty allowlist, a missing host, or an unparseable
    address, so the forwarded chain is ignored and the raw socket peer is used.
    """
    if not networks or host is None:
        return False
    try:
        addr = ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in networks)


class IPState:
    """Per-IP throttle state that survives individual connections."""

    __slots__ = (
        'ip', 'cost', 'violations', 'blocked_until', 'sessions',
        'sub_count', 'last_seen', '_cost_time',
    )

    def __init__(self, ip: str, now: float):
        self.ip = ip
        self.cost = 0.0
        self.violations = 0
        self.blocked_until = 0.0
        # Live session ids currently attached to this IP.
        self.sessions: Set[int] = set()
        # Aggregate live subscription count across all of this IP's sessions.
        self.sub_count = 0
        self.last_seen = now
        self._cost_time = now

    def decay(self, now: float, decay_per_sec: float) -> None:
        """Decay accumulated cost proportionally to elapsed time."""
        if now > self._cost_time and decay_per_sec > 0:
            self.cost = max(0.0, self.cost - (now - self._cost_time) * decay_per_sec)
        self._cost_time = now

    def touch(self, now: float) -> None:
        self.last_seen = now


class IPRateLimiter:
    """Holds per-IP state shared across an IP's sessions and reconnects."""

    def __init__(self, env=None):
        self._states: Dict[str, IPState] = {}
        # ---- config (resolved from env attrs, then process env, then default)
        self.enabled = self._resolve_bool(
            env, 'rate_limit_enabled', 'RATE_LIMIT_ENABLED', True)
        self.trust_proxy = self._resolve_bool(
            env, 'trust_proxy', 'TRUST_PROXY', False)
        self.trust_proxy_hops = max(1, self._resolve_int(
            env, 'trust_proxy_hops', 'TRUST_PROXY_HOPS', 1))
        # Socket-peer allowlist: only these peers are treated as the reverse
        # proxy whose forwarded chain we trust (see _peer_is_trusted_proxy).
        self.trusted_proxies = self._resolve_trusted_proxies(env)
        self.max_subs_per_ip = self._resolve_int(
            env, 'max_subs_per_ip', 'MAX_SUBS_PER_IP', DEFAULT_MAX_SUBS_PER_IP)
        self.max_sessions_per_ip = self._resolve_int(
            env, 'max_sessions_per_ip', 'MAX_SESSIONS_PER_IP',
            DEFAULT_MAX_SESSIONS_PER_IP)
        self.ip_cost_hard_limit = self._resolve_float(
            env, 'ip_cost_hard_limit', 'IP_COST_HARD_LIMIT',
            DEFAULT_IP_COST_HARD_LIMIT)
        self.block_duration = self._resolve_int(
            env, 'rate_block_duration', 'RATE_BLOCK_DURATION',
            DEFAULT_RATE_BLOCK_DURATION)
        self.ip_state_ttl = self._resolve_int(
            env, 'ip_state_ttl', 'IP_STATE_TTL', DEFAULT_IP_STATE_TTL)
        self.cost_decay_per_sec = self._resolve_float(
            env, 'ip_cost_decay_per_sec', 'IP_COST_DECAY_PER_SEC',
            self.ip_cost_hard_limit / 10000.0 if self.ip_cost_hard_limit > 0
            else _DEFAULT_COST_DECAY_PER_SEC)
        # Cheap incremental eviction trigger.
        self._ops_since_evict = 0
        self._evict_every = 256

    # -- config resolution ---------------------------------------------------

    @staticmethod
    def _resolve_bool(env, attr, envvar, default) -> bool:
        val = getattr(env, attr, None) if env is not None else None
        if isinstance(val, bool):
            return val
        raw = os.environ.get(envvar)
        if raw is None:
            return default
        return raw.strip().lower() not in ('0', 'false', 'no', '')

    @staticmethod
    def _resolve_int(env, attr, envvar, default) -> int:
        val = getattr(env, attr, None) if env is not None else None
        out = _coerce_int(val, None) if val is not None else None
        if out is None:
            out = _coerce_int(os.environ.get(envvar), None)
        return default if out is None else out

    @staticmethod
    def _resolve_float(env, attr, envvar, default) -> float:
        val = getattr(env, attr, None) if env is not None else None
        out = _coerce_float(val, None) if val is not None else None
        if out is None:
            out = _coerce_float(os.environ.get(envvar), None)
        return default if out is None else out

    @classmethod
    def _resolve_trusted_proxies(cls, env) -> List:
        """Resolve the trusted-proxy allowlist into a list of ip_network objs.

        Source order: ``env.trusted_proxies`` attr, then the ``TRUSTED_PROXIES``
        process env, then :data:`DEFAULT_TRUSTED_PROXIES`.  The value may be a
        comma/space-separated string of CIDRs or bare IPs, or an iterable of
        the same.  Unparseable tokens are skipped.
        """
        raw = getattr(env, 'trusted_proxies', None) if env is not None else None
        if isinstance(raw, str):
            raw = raw.strip() or None
        if not raw:
            raw = os.environ.get('TRUSTED_PROXIES')
            if raw is not None:
                raw = raw.strip() or None
        if not raw:
            raw = DEFAULT_TRUSTED_PROXIES
        return cls._parse_networks(raw)

    @staticmethod
    def _parse_networks(spec) -> List:
        if isinstance(spec, str):
            tokens = [t for t in spec.replace(',', ' ').split() if t]
        else:
            tokens = [str(t).strip() for t in spec if str(t).strip()]
        nets = []
        for tok in tokens:
            try:
                # strict=False so a host address with a prefix (or a bare IP,
                # which becomes a /32 //128) is accepted.
                nets.append(ip_network(tok, strict=False))
            except ValueError:
                continue
        return nets

    # -- IP derivation -------------------------------------------------------

    def client_ip(self, session) -> Optional[str]:
        """Resolve a session's real client IP, proxy-aware.

        When ``trust_proxy`` is on AND the socket peer is itself a configured
        trusted proxy (see :meth:`_peer_is_trusted_proxy`), and the
        session/transport exposes a forwarded chain (``X-Forwarded-For`` from
        the WS handshake, or an ``x-real-ip`` header), the client IP is taken
        from that chain at ``trust_proxy_hops`` from the right (the trusted hop
        the proxy added).  In that trusted-proxy mode the socket peer is the
        proxy itself (often loopback), so we do NOT exempt private/loopback
        peers here.

        Crucially, a client that connects DIRECTLY to a published listener
        (its peer is not in the trusted-proxy allowlist) cannot have its
        ``X-Forwarded-For`` honoured — we fall back to its raw socket peer, so
        it can only accrue cost against its own IP, never poison a victim's.

        When ``trust_proxy`` is off, the socket peer host is used and private
        peers behave exactly as before (this layer simply governs them too;
        the caller decides whether to exempt — see :meth:`is_exempt_peer`).
        """
        if self.trust_proxy and self._peer_is_trusted_proxy(session):
            forwarded = self._forwarded_for(session)
            if forwarded:
                return forwarded
            real = self._header(session, 'x-real-ip')
            if real:
                return real.strip()
        host = self._peer_host(session)
        return host

    def _peer_is_trusted_proxy(self, session) -> bool:
        """Whether the session's socket peer is a configured trusted proxy.

        Only a peer inside the ``TRUSTED_PROXIES`` allowlist is permitted to
        set the forwarded client IP.  Returns False (so the forwarded chain is
        ignored and the raw peer is used) when the allowlist is empty, the peer
        is unknown, or the peer is not in any allowlisted network.
        """
        return peer_in_networks(self._peer_host(session), self.trusted_proxies)

    @staticmethod
    def _peer_host(session) -> Optional[str]:
        try:
            addr = session.remote_address()
        except Exception:
            return None
        if addr is None:
            return None
        host = getattr(addr, 'host', None)
        return str(host) if host is not None else None

    def _forwarded_for(self, session) -> Optional[str]:
        raw = self._header(session, 'x-forwarded-for')
        if not raw:
            return None
        parts = [p.strip() for p in raw.split(',') if p.strip()]
        if not parts:
            return None
        idx = max(0, len(parts) - self.trust_proxy_hops)
        return parts[idx]

    @staticmethod
    def _header(session, name: str) -> Optional[str]:
        """Best-effort read of a request header from a (WS) session/transport.

        Supports several shapes so tests and the live WS transport both work:
        ``session.request_headers``, ``session.transport.websocket.request_headers``,
        ``…websocket.request.headers`` and a plain ``session.headers`` mapping.
        Returns None if unavailable.

        The two websocket shapes are a library-version split, and getting it
        wrong fails *silently* rather than loudly — which is why both are
        covered here. ``websockets.serve`` resolves to the legacy asyncio
        server below 14 and to ``websockets.asyncio.server`` from 14 on, and
        the new server does not carry ``request_headers`` at all; the handshake
        request moved to ``websocket.request``. Since this helper falls back to
        None on a missing attribute, a version bump alone would leave
        X-Forwarded-For permanently unresolved: with TRUST_PROXY on, every
        client would collapse onto the proxy's own address and share one
        rate-limit bucket. Verified against 13.1 (``request_headers`` present,
        ``request`` absent) and 16.1 (the reverse).
        """
        candidates = []
        for obj in (session, getattr(session, 'transport', None)):
            if obj is None:
                continue
            ws = getattr(obj, 'websocket', None)
            if ws is not None:
                candidates.append(getattr(ws, 'request_headers', None))
                candidates.append(
                    getattr(getattr(ws, 'request', None), 'headers', None))
            candidates.append(getattr(obj, 'request_headers', None))
            candidates.append(getattr(obj, 'headers', None))
        for headers in candidates:
            if not headers:
                continue
            try:
                # websockets Headers / dict both support .get (case-insensitive
                # for websockets.Headers; we lower the key for plain dicts too).
                val = headers.get(name)
                if val is None:
                    val = headers.get(name.title())
                if val is None and hasattr(headers, 'get'):
                    # Try a case-insensitive scan as a last resort.
                    for k in getattr(headers, 'keys', lambda: [])():
                        if str(k).lower() == name:
                            val = headers.get(k)
                            break
                if val:
                    return val
            except Exception:
                continue
        return None

    def is_exempt_peer(self, session) -> bool:
        """Whether the socket peer should be exempted from grouping.

        Mirrors the legacy ``_ip_addr_group_name`` behaviour: a private/loopback
        peer is exempt — BUT ONLY when no trusted proxy is configured.  When a
        trusted proxy IS configured the peer is the proxy (loopback) and the
        real client IP comes from the forwarded chain, so nothing is exempted.
        """
        if self.trust_proxy:
            return False
        host = self._peer_host(session)
        if host is None:
            return False
        try:
            return ip_address(host).is_private
        except ValueError:
            return False

    # -- state access --------------------------------------------------------

    def _state(self, ip: str, now: float) -> IPState:
        st = self._states.get(ip)
        if st is None:
            st = IPState(ip, now)
            self._states[ip] = st
        return st

    def get_state(self, ip: str) -> Optional[IPState]:
        """Return the existing state for an IP, or None.  No side effects."""
        return self._states.get(ip)

    def _maybe_evict(self, now: float) -> None:
        self._ops_since_evict += 1
        if self._ops_since_evict < self._evict_every:
            return
        self._ops_since_evict = 0
        self.evict_stale(now)

    def evict_stale(self, now: Optional[float] = None) -> int:
        """Evict IP states idle for longer than the TTL.  Returns count removed.

        An IP with live sessions is never evicted regardless of last-seen.
        """
        if now is None:
            now = time.time()
        ttl = self.ip_state_ttl
        if ttl <= 0:
            return 0
        stale = [
            ip for ip, st in self._states.items()
            if not st.sessions and (now - st.last_seen) > ttl
        ]
        for ip in stale:
            del self._states[ip]
        return len(stale)

    # -- lifecycle hooks (called from session.py) ----------------------------

    def register_session(self, ip: Optional[str], session_id: int,
                          now: Optional[float] = None) -> float:
        """Register a new session for an IP and return the PERSISTED cost.

        The returned cost should seed the session's starting aiorpcx ``cost``
        so a reconnect does NOT reset the throttle.  Idempotent per session_id.
        """
        if not self.enabled or ip is None or session_id is None:
            return 0.0
        if now is None:
            now = time.time()
        st = self._state(ip, now)
        st.decay(now, self.cost_decay_per_sec)
        st.sessions.add(session_id)
        st.touch(now)
        self._maybe_evict(now)
        return st.cost

    def release_session(self, ip: Optional[str], session_id: int,
                        session_cost: float = 0.0,
                        sub_count: int = 0,
                        now: Optional[float] = None) -> None:
        """Detach a session on disconnect WITHOUT dropping the IP state.

        ``session_cost`` is the session's final aiorpcx cost; the portion above
        what was seeded is written back so sequential abuse accumulates.  The
        last-seen timestamp is updated so the TTL clock starts now (state is
        kept for reconnects until the TTL elapses).
        """
        if not self.enabled or ip is None:
            return
        if now is None:
            now = time.time()
        st = self._states.get(ip)
        if st is None:
            return
        st.sessions.discard(session_id)
        st.decay(now, self.cost_decay_per_sec)
        # Persist the session's accumulated cost into the IP aggregate.  We add
        # the (decayed) session cost so repeated short connections still build
        # up an IP cost that survives reconnect.
        if session_cost and session_cost > 0:
            st.cost = min(st.cost + float(session_cost),
                          self.ip_cost_hard_limit if self.ip_cost_hard_limit > 0
                          else st.cost + float(session_cost))
        # Release this session's contribution to the aggregate sub count.
        if sub_count:
            st.sub_count = max(0, st.sub_count - int(sub_count))
        st.touch(now)

    # -- cost accounting -----------------------------------------------------

    def add_cost(self, ip: Optional[str], delta: float,
                 now: Optional[float] = None) -> None:
        """Add cost to an IP's running total (decaying first)."""
        if not self.enabled or ip is None or not delta:
            return
        if now is None:
            now = time.time()
        st = self._state(ip, now)
        st.decay(now, self.cost_decay_per_sec)
        st.cost = max(0.0, st.cost + float(delta))
        st.touch(now)

    # -- checks --------------------------------------------------------------

    def connection_count(self, ip: Optional[str]) -> int:
        """Number of live sessions currently attached to ``ip`` (0 if unknown)."""
        if ip is None:
            return 0
        st = self._states.get(ip)
        return len(st.sessions) if st is not None else 0

    def check_can_register(self, ip: Optional[str],
                           now: Optional[float] = None) -> Tuple[bool, str]:
        """Return (allowed, reason) for opening a NEW session from ``ip``.

        Enforces the per-IP concurrent CONNECTION cap — the protection the
        global ``MAX_SESSIONS`` cannot give, since one host can otherwise fill
        every slot.  Call this BEFORE :meth:`register_session`; a refused
        connection must NOT be registered (so it never counts toward the cap)
        and should be closed by the caller.

        SAFETY VALVE: the cap is NEVER applied to an IP inside the
        trusted-proxy allowlist.  Behind a reverse proxy, :meth:`client_ip`
        returns the proxy's OWN address whenever ``X-Forwarded-For`` is missing
        or garbled — collapsing every real client onto one IP.  Capping that
        would lock out ALL clients simultaneously, so we decline to cap it
        (better a no-op than a self-inflicted outage; the per-session caps and
        the per-IP cost limit still apply).
        """
        if not self.enabled or ip is None or self.max_sessions_per_ip <= 0:
            return True, ''
        # Never cap a proxy / private address — see docstring.
        if peer_in_networks(ip, self.trusted_proxies):
            return True, ''
        if now is None:
            now = time.time()
        st = self._states.get(ip)
        if st is None:
            return True, ''
        if st.blocked_until > now:
            return False, (f'rate limited: IP blocked for '
                           f'{int(st.blocked_until - now)}s')
        if len(st.sessions) >= self.max_sessions_per_ip:
            return False, (f'connection limit reached '
                           f'({self.max_sessions_per_ip} per IP)')
        return True, ''

    def check_cost(self, ip: Optional[str],
                   now: Optional[float] = None) -> Tuple[bool, str]:
        """Return (allowed, reason) for the per-IP cost hard limit.

        When the limit is exceeded the IP is marked blocked for
        ``block_duration`` seconds; the block (and the underlying cost) persist
        across reconnects until they decay/expire.
        """
        if not self.enabled or ip is None or self.ip_cost_hard_limit <= 0:
            return True, ''
        if now is None:
            now = time.time()
        st = self._states.get(ip)
        if st is None:
            return True, ''
        if st.blocked_until > now:
            return False, (f'rate limited: IP blocked for '
                           f'{int(st.blocked_until - now)}s')
        st.decay(now, self.cost_decay_per_sec)
        if st.cost >= self.ip_cost_hard_limit:
            st.violations += 1
            st.blocked_until = now + self.block_duration
            # The timed block IS the penalty; reset the accumulated cost so the
            # IP gets a clean slate once the block expires (otherwise a
            # not-yet-decayed cost would re-block it on the very next request).
            st.cost = 0.0
            st.touch(now)
            return False, (f'rate limited: per-IP cost limit '
                           f'({self.ip_cost_hard_limit:g}) exceeded')
        return True, ''

    def check_can_subscribe(self, ip: Optional[str], count: int = 1,
                            now: Optional[float] = None,
                            current: Optional[int] = None) -> Tuple[bool, str]:
        """Return (allowed, reason) for adding ``count`` subscriptions on IP.

        Enforces the AGGREGATE cap across all of the IP's sessions.  When
        ``current`` is given it is used as the authoritative live aggregate
        count (e.g. summed from the GlyphSubscriptionManager across the IP's
        live sessions); otherwise the limiter's own ``sub_count`` bookkeeping is
        used.  Does not mutate state; call :meth:`note_subscribed` to update the
        limiter's bookkeeping when relying on it.
        """
        if not self.enabled or ip is None or self.max_subs_per_ip <= 0:
            return True, ''
        if now is None:
            now = time.time()
        st = self._states.get(ip)
        base = current if current is not None else (st.sub_count if st else 0)
        if st is not None and st.blocked_until > now:
            return False, (f'rate limited: IP blocked for '
                           f'{int(st.blocked_until - now)}s')
        if base + count > self.max_subs_per_ip:
            return False, (f'subscription limit reached '
                           f'({self.max_subs_per_ip} per IP)')
        return True, ''

    def note_subscribed(self, ip: Optional[str], count: int = 1,
                        now: Optional[float] = None) -> None:
        """Record that ``count`` subscriptions were accepted for the IP."""
        if not self.enabled or ip is None or count == 0:
            return
        if now is None:
            now = time.time()
        st = self._state(ip, now)
        st.sub_count = max(0, st.sub_count + int(count))
        st.touch(now)

    def note_unsubscribed(self, ip: Optional[str], count: int = 1,
                          now: Optional[float] = None) -> None:
        if not self.enabled or ip is None or count == 0:
            return
        st = self._states.get(ip)
        if st is None:
            return
        st.sub_count = max(0, st.sub_count - int(count))

    def stats(self) -> dict:
        return {
            'tracked_ips': len(self._states),
            'blocked_ips': sum(1 for st in self._states.values()
                               if st.blocked_until > time.time()),
            'total_subscriptions': sum(st.sub_count
                                       for st in self._states.values()),
        }


# ---------------------------------------------------------------------------
# Process-global instance, installed at startup via init_rate_limiters().
# ---------------------------------------------------------------------------

_ip_rate_limiter: Optional[IPRateLimiter] = None


def init_rate_limiters(env=None) -> IPRateLimiter:
    """Create and install the process-global IP rate limiter.

    Called once from the SessionManager/Controller startup.  Safe to call
    multiple times (idempotent-ish: re-creates from the given env).
    """
    global _ip_rate_limiter
    _ip_rate_limiter = IPRateLimiter(env)
    return _ip_rate_limiter


def get_ip_rate_limiter() -> Optional[IPRateLimiter]:
    """Return the installed limiter, or None if init was never called.

    Callers MUST degrade gracefully when this is None (feature disabled / not
    yet wired), so the live session path never breaks if startup skipped init.
    """
    return _ip_rate_limiter
