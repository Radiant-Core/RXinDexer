# Tests of server/env.py

import os
import random
import re

import pytest

from aiorpcx import Service, NetAddress
from electrumx.server.env import Env, ServiceError
import electrumx.lib.coins as lib_coins


BASE_DAEMON_URL = 'http://username:password@hostname:321/'
BASE_DB_DIR = '/some/dir'

base_environ = {
    'DB_DIRECTORY': BASE_DB_DIR,
    'DAEMON_URL': BASE_DAEMON_URL,
    'COIN': 'Radiant',
}


def setup_base_env():
    os.environ.clear()
    os.environ.update(base_environ)


def assert_required(env_var):
    setup_base_env()
    os.environ.pop(env_var, None)
    with pytest.raises(Env.Error):
        Env()


def assert_default(env_var, attr, default):
    setup_base_env()
    e = Env()
    assert getattr(e, attr) == default
    os.environ[env_var] = 'foo'
    e = Env()
    assert getattr(e, attr) == 'foo'


def assert_integer(env_var, attr, default=''):
    setup_base_env()
    if default != '':
        e = Env()
        assert getattr(e, attr) == default
    value = random.randrange(5, 2000)
    os.environ[env_var] = str(value) + '.1'
    with pytest.raises(Env.Error):
        Env()
    os.environ[env_var] = str(value)
    e = Env()
    assert getattr(e, attr) == value


def assert_boolean(env_var, attr, default):
    e = Env()
    assert getattr(e, attr) == default
    os.environ[env_var] = 'foo'
    e = Env()
    assert getattr(e, attr) == True
    os.environ[env_var] = ''
    e = Env()
    assert getattr(e, attr) == False


def test_minimal():
    setup_base_env()
    Env()


def test_DB_DIRECTORY():
    assert_required('DB_DIRECTORY')
    setup_base_env()
    e = Env()
    assert e.db_dir == BASE_DB_DIR


def test_DAEMON_URL():
    assert_required('DAEMON_URL')
    setup_base_env()
    e = Env()
    assert e.daemon_url == BASE_DAEMON_URL


def test_COIN_NET():
    '''COIN/NET selection across the Radiant coin classes.

    All Radiant networks share NAME="Radiant" and are distinguished by NET, per
    the ElectrumX convention. A network is therefore selected by setting NET
    alone (COIN stays "Radiant") — which is how the shipped compose files
    template it: COIN=${COIN:-Radiant} NET=${NET:-mainnet}.
    '''
    setup_base_env()
    e = Env()
    assert e.coin == lib_coins.Radiant  # default NET=mainnet

    os.environ['NET'] = 'mainnet'
    assert Env().coin == lib_coins.Radiant

    # NET redirects to each variant without changing COIN.
    os.environ['NET'] = 'testnet'
    assert Env().coin == lib_coins.RadiantTestnet
    os.environ['NET'] = 'scalingtest'
    assert Env().coin == lib_coins.RadiantScalingTestnet
    os.environ['NET'] = 'regtest'
    assert Env().coin == lib_coins.RadiantRegtest

    # NET is stripped of surrounding whitespace.
    os.environ['NET'] = ' testnet '
    assert Env().coin == lib_coins.RadiantTestnet


def test_CACHE_MB():
    assert_integer('CACHE_MB', 'cache_MB', 1200)


def test_SERVICES():
    setup_base_env()
    e = Env()
    assert e.services == []
    # This has a blank entry between commas
    os.environ['SERVICES'] = 'tcp://foo.bar:1234,,ws://1.2.3.4:567,rpc://[::1]:700'
    e = Env()
    assert e.services == [
        Service('tcp', NetAddress('foo.bar', 1234)),
        Service('ws', NetAddress('1.2.3.4', 567)),
        Service('rpc', NetAddress('::1', 700)),
    ]

def test_SERVICES_default_rpc():
    # Reset to the base env first; this test sets only SERVICES and otherwise
    # relies on the required vars (DB_DIRECTORY etc.) being present. Without
    # this it depends on environment left over from a prior test.
    setup_base_env()
    # This has a blank entry between commas
    os.environ['SERVICES'] = 'rpc://foo.bar'
    e = Env()
    assert e.services[0].host == 'foo.bar'
    # Fork default RPC port (env.py services_to_run): 8001.
    assert e.services[0].port == 8001
    os.environ['SERVICES'] = 'rpc://:800'
    e = Env()
    assert e.services[0].host == 'localhost'
    assert e.services[0].port == 800
    os.environ['SERVICES'] = 'rpc://'
    e = Env()
    assert e.services[0].host == 'localhost'
    assert e.services[0].port == 8001


def test_bad_SERVICES():
    setup_base_env()
    os.environ['SERVICES'] = 'tcp:foo.bar:1234'
    with pytest.raises(ServiceError) as err:
         Env()
    assert 'invalid service string' in str(err.value)
    os.environ['SERVICES'] = 'xxx://foo.com:50001'
    with pytest.raises(ServiceError) as err:
        Env()
    assert 'unknown protocol' in str(err.value)


def test_onion_SERVICES():
    setup_base_env()
    os.environ['SERVICES'] = 'tcp://foo.bar.onion:1234'
    with pytest.raises(ServiceError) as err:
        Env()
    assert 'bad host' in str(err.value)


def test_duplicate_SERVICES():
    setup_base_env()
    os.environ['SERVICES'] = 'tcp://foo.bar:1234,ws://foo.bar:1235'
    e = Env()
    os.environ['SERVICES'] = 'tcp://foo.bar:1234,ws://foo.bar:1234'
    with pytest.raises(ServiceError) as err:
        Env()
    assert 'multiple services' in str(err.value)


@pytest.mark.parametrize("service", (
    'ssl://foo.bar:1234',
    'wss://foo.bar:1234',
))
def test_ssl_SERVICES(service):
    setup_base_env()
    os.environ['SERVICES'] = service
    with pytest.raises(Env.Error) as err:
        Env()
    assert 'SSL_CERTFILE' in str(err.value)
    os.environ['SSL_CERTFILE'] = 'certfile'
    with pytest.raises(Env.Error) as err:
        Env()
    assert 'SSL_KEYFILE' in str(err.value)
    os.environ['SSL_KEYFILE'] = 'keyfile'
    Env()
    setup_base_env()
    os.environ['SERVICES'] = service
    os.environ['SSL_KEYFILE'] = 'keyfile'
    with pytest.raises(Env.Error) as err:
        Env()
    assert 'SSL_CERTFILE' in str(err.value)


def test_REPORT_SERVICES():
    setup_base_env()
    e = Env()
    assert e.report_services == []
    # This has a blank entry between commas
    os.environ['REPORT_SERVICES'] = 'tcp://foo.bar:1234,,ws://1.2.3.4:567'
    e = Env()
    assert e.report_services == [
        Service('tcp', NetAddress('foo.bar', 1234)),
        Service('ws', NetAddress('1.2.3.4', 567)),
    ]


def test_REPORT_SERVICES_rpc():
    setup_base_env()
    os.environ['REPORT_SERVICES'] = 'rpc://foo.bar:1234'
    with pytest.raises(ServiceError) as err:
        Env()
    assert 'bad protocol' in str(err.value)


def test_REPORT_SERVICES_private():
    setup_base_env()
    os.environ['REPORT_SERVICES'] = 'tcp://192.168.0.1:1234'
    with pytest.raises(ServiceError) as err:
        Env()
    assert 'bad IP address' in str(err.value)
    # Accept it not PEER_ANNOUNCE
    os.environ['PEER_ANNOUNCE'] = ''
    Env()


def test_REPORT_SERVICES_localhost():
    setup_base_env()
    os.environ['REPORT_SERVICES'] = 'tcp://localhost:1234'
    with pytest.raises(ServiceError) as err:
        Env()
    assert 'bad host' in str(err.value)


def test_REORG_LIMIT():
    assert_integer('REORG_LIMIT', 'reorg_limit',
                   lib_coins.Radiant.REORG_LIMIT)


def test_COST_HARD_LIMIT():
    # Fork default (env.py): COST_HARD_LIMIT=100000.
    assert_integer('COST_HARD_LIMIT', 'cost_hard_limit', 100000)


def test_COST_SOFT_LIMIT():
    # Fork default (env.py): COST_SOFT_LIMIT=10000.
    assert_integer('COST_SOFT_LIMIT', 'cost_soft_limit', 10000)


def test_INITIAL_CONCURRENT():
    # Fork default (env.py): INITIAL_CONCURRENT=50.
    assert_integer('INITIAL_CONCURRENT', 'initial_concurrent', 50)


def test_REQUEST_SLEEP():
    # Fork default (env.py): REQUEST_SLEEP=500.
    assert_integer('REQUEST_SLEEP', 'request_sleep', 500)


def test_BANDWIDTH_UNIT_COST():
    assert_integer('BANDWIDTH_UNIT_COST', 'bw_unit_cost', 5000)


def test_DONATION_ADDRESS():
    assert_default('DONATION_ADDRESS', 'donation_address', '')


def test_DB_ENGINE():
    # Fork default (env.py): DB_ENGINE=rocksdb.
    assert_default('DB_ENGINE', 'db_engine', 'rocksdb')


def test_MAX_SEND():
    assert_integer('MAX_SEND', 'max_send', 10000000)


def test_LOG_LEVEL():
    setup_base_env()
    e = Env()
    assert e.log_level == 'INFO'
    os.environ['LOG_LEVEL'] = 'warning'
    e = Env()
    assert e.log_level == 'WARNING'


def test_MAX_SESSIONS():
    # sane_max_sessions() caps MAX_SESSIONS at (RLIMIT_NOFILE - 350). Derive the
    # test value from the actual open-file limit so it always exceeds the cap
    # and must be lowered — RLIMIT_NOFILE varies by host (~1024 on CI Linux,
    # ~1048576 on macOS), which is why a fixed 1_000_000 was host-dependent.
    import resource
    nofile = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if nofile < 0 or nofile > 10 ** 12:
        pytest.skip('open-file limit effectively unbounded; session cap cannot trigger')
    too_big = nofile + 1000
    os.environ['MAX_SESSIONS'] = str(too_big)
    e = Env()
    assert e.max_sessions < too_big
    # Cannot test default as it may be lowered by the open file limit cap


def test_REQUEST_TIMEOUT():
    assert_integer('REQUEST_TIMEOUT', 'request_timeout', 30)


def test_SESSION_TIMEOUT():
    assert_integer('SESSION_TIMEOUT', 'session_timeout', 600)


def test_BANNER_FILE():
    e = Env()
    assert e.banner_file is None
    assert e.tor_banner_file is None
    os.environ['BANNER_FILE'] = 'banner_file'
    e = Env()
    assert e.banner_file == 'banner_file'
    assert e.tor_banner_file == 'banner_file'
    os.environ['TOR_BANNER_FILE'] = 'tor_banner_file'
    e = Env()
    assert e.banner_file == 'banner_file'
    assert e.tor_banner_file == 'tor_banner_file'


def test_EVENT_LOOP_POLICY():
    e = Env()
    assert e.loop_policy is None
    os.environ['EVENT_LOOP_POLICY'] = 'foo'
    with pytest.raises(Env.Error):
        Env()
    os.environ['EVENT_LOOP_POLICY'] = 'uvloop'
    try:
        Env()
    except ImportError:
        pass
    del os.environ['EVENT_LOOP_POLICY']


def test_ANON_LOGS():
    assert_boolean('ANON_LOGS', 'anon_logs', False)


def test_PEER_DISCOVERY():
    e = Env()
    assert e.peer_discovery == Env.PD_ON
    os.environ['PEER_DISCOVERY'] = ' '
    e = Env()
    assert e.peer_discovery == Env.PD_OFF
    os.environ['PEER_DISCOVERY'] = 'ON'
    e = Env()
    assert e.peer_discovery == Env.PD_ON
    os.environ['PEER_DISCOVERY'] = 'self'
    e = Env()
    assert e.peer_discovery == Env.PD_SELF


def test_PEER_ANNOUNCE():
    assert_boolean('PEER_ANNOUNCE', 'peer_announce', True)


def test_FORCE_PROXY():
    assert_boolean('FORCE_PROXY', 'force_proxy', False)


def test_TOR_PROXY_HOST():
    assert_default('TOR_PROXY_HOST', 'tor_proxy_host', 'localhost')


def test_TOR_PROXY_PORT():
    assert_integer('TOR_PROXY_PORT', 'tor_proxy_port', None)


def test_ban_versions():
    e = Env()
    assert e.drop_client is None
    ban_re = r'1\.[0-2]\.\d+?[_\w]*'
    os.environ['DROP_CLIENT'] = ban_re
    e = Env()
    assert e.drop_client == re.compile(ban_re)
    assert e.drop_client.match("1.2.3_buggy_client")
    assert e.drop_client.match("1.3.0_good_client") is None


def test_coin_class_provided():
    e = Env(lib_coins.Radiant)
    assert e.coin == lib_coins.Radiant


# --- IP-persistent, proxy-aware rate limiter knobs (H3 follow-up) ---

def test_RATE_LIMIT_ENABLED():
    setup_base_env()
    assert_boolean('RATE_LIMIT_ENABLED', 'rate_limit_enabled', True)


def test_TRUST_PROXY():
    setup_base_env()
    assert_boolean('TRUST_PROXY', 'trust_proxy', False)


def test_TRUST_PROXY_HOPS():
    setup_base_env()
    assert_integer('TRUST_PROXY_HOPS', 'trust_proxy_hops', 1)


def test_MAX_SUBS_PER_IP():
    setup_base_env()
    assert_integer('MAX_SUBS_PER_IP', 'max_subs_per_ip', 50000)


def test_IP_COST_HARD_LIMIT():
    setup_base_env()
    assert_integer('IP_COST_HARD_LIMIT', 'ip_cost_hard_limit', 1_000_000)


def test_RATE_BLOCK_DURATION():
    setup_base_env()
    assert_integer('RATE_BLOCK_DURATION', 'rate_block_duration', 300)


def test_IP_STATE_TTL():
    setup_base_env()
    assert_integer('IP_STATE_TTL', 'ip_state_ttl', 3600)
