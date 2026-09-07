"""
`/glyphs/recent` parameter naming — exercised through a real TestClient, not by inspection.

This is the height-descending listing. It already existed, backed by the v4 GLOBAL_RECENT /
BY_TYPE_RECENT indexes (`inv_height = 0xFFFFFFFF - deploy_height`, so a forward scan is
newest-first). The only thing wrong with it was that it took `type_id` while `/glyphs` takes
`token_type`, so a caller had to know which endpoint used which name.

`token_type` is now the parameter, `type_id` a deprecated alias, and both are echoed back.

Run: PYTHONPATH=. python3 -m pytest tests/server/test_glyphs_recent_params.py
"""
import os
import sys
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from electrumx.server.rest_api import app, set_indexer  # noqa: E402

RECENT = {'tokens': [{'ref': 'aa' * 36}], 'next_cursor': 'NEXT'}
BY_TYPE = {'tokens': [{'ref': 'bb' * 36}], 'next_cursor': None}


@pytest.fixture
def client():
    idx = Mock()
    idx.enabled = True
    idx.get_recent_tokens = Mock(return_value=dict(RECENT))
    idx.get_tokens_by_type = Mock(return_value=dict(BY_TYPE))
    # Every index method an exercised route touches must return a real value: FastAPI serialising
    # an auto-created Mock attribute recurses until it blows the stack, which is what the
    # db_engine fix in 55cd96c ran into.
    idx.get_all_tokens_summary = Mock(return_value={'total': 0, 'tokens': []})
    db = Mock()
    db.db_height = 461630
    set_indexer(idx, db, Mock())
    yield TestClient(app), idx
    set_indexer(None, None, None)


def test_unfiltered_reads_the_global_recency_index(client):
    c, idx = client
    resp = c.get('/glyphs/recent')
    assert resp.status_code == 200
    body = resp.json()
    assert body['order'] == 'recent'
    assert body['token_type'] is None and body['type_id'] is None
    assert body['tokens'] == RECENT['tokens']
    idx.get_recent_tokens.assert_called_once()
    idx.get_tokens_by_type.assert_not_called()


def test_token_type_reads_the_per_type_recency_index(client):
    c, idx = client
    resp = c.get('/glyphs/recent', params={'token_type': 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body['token_type'] == 2 and body['type_id'] == 2, 'both names echoed'
    assert body['tokens'] == BY_TYPE['tokens']
    # Must request recency ordering, or it would silently serve ref order.
    kwargs = idx.get_tokens_by_type.call_args.kwargs
    assert kwargs['order'] == 'recent'
    idx.get_recent_tokens.assert_not_called()


def test_deprecated_type_id_still_works(client):
    """Existing callers must not break on the rename."""
    c, idx = client
    resp = c.get('/glyphs/recent', params={'type_id': 4})
    assert resp.status_code == 200
    assert resp.json()['token_type'] == 4
    assert idx.get_tokens_by_type.call_args.args[0] == 4


def test_both_names_agreeing_is_accepted(client):
    c, _idx = client
    resp = c.get('/glyphs/recent', params={'token_type': 2, 'type_id': 2})
    assert resp.status_code == 200
    assert resp.json()['token_type'] == 2


def test_both_names_disagreeing_is_rejected(client):
    """Silently picking one would make the response quietly not match the request."""
    c, idx = client
    resp = c.get('/glyphs/recent', params={'token_type': 2, 'type_id': 4})
    assert resp.status_code == 400
    assert 'disagree' in resp.json()['detail']
    idx.get_tokens_by_type.assert_not_called()


def test_cursor_is_forwarded_and_returned(client):
    c, idx = client
    resp = c.get('/glyphs/recent', params={'cursor': 'ABC', 'limit': 5})
    assert resp.status_code == 200
    assert resp.json()['next_cursor'] == 'NEXT'
    kwargs = idx.get_recent_tokens.call_args.kwargs
    assert kwargs['cursor'] == 'ABC' and kwargs['limit'] == 5


def test_limit_and_type_bounds_are_enforced(client):
    c, _idx = client
    assert c.get('/glyphs/recent', params={'limit': 501}).status_code == 422
    assert c.get('/glyphs/recent', params={'token_type': 8}).status_code == 422
    assert c.get('/glyphs/recent', params={'token_type': -1}).status_code == 422


def test_glyphs_and_glyphs_recent_now_share_the_parameter_name(client):
    """The point of the change: one name works on both listings."""
    c, _idx = client
    assert c.get('/glyphs/recent', params={'token_type': 2}).status_code == 200
    assert c.get('/glyphs', params={'token_type': 2}).status_code == 200


def test_unavailable_index_returns_503(client):
    c, _idx = client
    set_indexer(None, None, None)
    assert c.get('/glyphs/recent').status_code == 503


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
