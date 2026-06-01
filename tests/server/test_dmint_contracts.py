import pytest
from unittest.mock import Mock

from electrumx.server.dmint_contracts import DMintContractsManager


def _make_contract(
    ref: str,
    ticker: str,
    algorithm: int,
    reward: int,
    deploy_height: int,
    *,
    active: bool = True,
    outputs: int = 1,
    total_supply: int = 1000,
    mined_supply: int = 100,
    daa_mode: int = 0,
):
    return {
        "ref": ref,
        "ticker": ticker,
        "name": f"{ticker} Token",
        "algorithm": algorithm,
        "difficulty": 123,
        "reward": reward,
        "outputs": outputs,
        "deploy_height": deploy_height,
        "active": active,
        "percent_mined": 0,
        "daa_mode": daa_mode,
        "daa_mode_name": "Fixed",
        "icon_type": None,
        "icon_data": None,
        "icon_url": None,
        "total_supply": total_supply,
        "mined_supply": mined_supply,
    }


@pytest.fixture
def dmint_manager(tmp_path):
    mgr = DMintContractsManager(str(tmp_path))
    mgr.last_updated_height = 123456
    mgr.contracts = [
        _make_contract("a" * 72, "ALFA", algorithm=1, reward=300, deploy_height=10, outputs=4),
        _make_contract(
            "b" * 72,
            "BETA",
            algorithm=1,
            reward=400,
            deploy_height=12,
            active=False,
            total_supply=1000,
            mined_supply=1000,
            outputs=2,
        ),
        _make_contract("c" * 72, "GAMMA", algorithm=2, reward=500, deploy_height=20, outputs=3),
        _make_contract("d" * 72, "DELTA", algorithm=1, reward=100, deploy_height=15, outputs=1),
    ]
    return mgr


def test_get_contracts_v2_filters_sort_and_pagination(dmint_manager):
    params_page_1 = {
        "version": 2,
        "view": "token_summary",
        "filters": {
            "status": "mineable",
            "algorithm_ids": [1],
        },
        "sort": {
            "field": "reward_per_mint",
            "dir": "desc",
        },
        "pagination": {
            "limit": 1,
            "cursor": "0",
        },
    }

    page_1 = dmint_manager.get_contracts_v2(params_page_1)

    assert page_1["version"] == 2
    assert page_1["view"] == "token_summary"
    assert page_1["indexed_height"] == 123456
    assert page_1["total_estimate"] == 2
    assert page_1["count"] == 1
    assert page_1["cursor_next"] == "1"
    assert page_1["items"][0]["token_ref"] == "a" * 72
    assert page_1["items"][0]["reward_per_mint"] == "300"

    params_page_2 = {
        **params_page_1,
        "pagination": {
            "limit": 1,
            "cursor": page_1["cursor_next"],
        },
    }

    page_2 = dmint_manager.get_contracts_v2(params_page_2)

    assert page_2["total_estimate"] == 2
    assert page_2["count"] == 1
    assert page_2["cursor_next"] is None
    assert page_2["items"][0]["token_ref"] == "d" * 72
    assert page_2["items"][0]["reward_per_mint"] == "100"


def test_get_contracts_v2_finished_status_filter(dmint_manager):
    response = dmint_manager.get_contracts_v2(
        {
            "version": 2,
            "view": "token_summary",
            "filters": {"status": "finished"},
            "pagination": {"limit": 10},
        }
    )

    assert response["count"] == 1
    assert response["items"][0]["token_ref"] == "b" * 72
    assert response["items"][0]["is_fully_mined"] is True


def test_get_contracts_v2_rejects_unsupported_version(dmint_manager):
    with pytest.raises(ValueError, match="unsupported version"):
        dmint_manager.get_contracts_v2(
            {
                "version": 1,
                "view": "token_summary",
            }
        )


def test_get_contracts_v2_rejects_unsupported_view(dmint_manager):
    with pytest.raises(ValueError, match="unsupported view"):
        dmint_manager.get_contracts_v2(
            {
                "version": 2,
                "view": "raw_contracts",
            }
        )


def test_get_contracts_v2_rejects_invalid_status_filter(dmint_manager):
    with pytest.raises(ValueError, match="invalid status filter"):
        dmint_manager.get_contracts_v2(
            {
                "version": 2,
                "view": "token_summary",
                "filters": {"status": "invalid"},
            }
        )


def _index_token(ref_internal: str, *, total_supply: int, mined_supply: int,
                 percent_mined, is_spent: bool = False):
    """Build a GlyphIndex dMint token as returned by get_tokens_by_type."""
    return {
        "ref": ref_internal,
        "ticker": "LIVE",
        "name": "Live Token",
        "deploy_height": 100,
        "total_supply": total_supply,
        "mined_supply": mined_supply,
        "percent_mined": percent_mined,
        "is_spent": is_spent,
        "dmint": {
            "algorithm": 1,
            "current_difficulty": 123,
            "reward": 50,
            "num_contracts": 1,
            "daa_mode": 0,
            "daa_mode_name": "Fixed",
        },
    }


def test_sync_reactivates_orphaned_contract_still_in_index(tmp_path):
    """Regression: a live contract wrongly orphaned by a past resync sweep
    must be reactivated (and de-orphaned) when it reappears in the index, so
    it is no longer hidden from the miner's `mineable` listing."""
    glyph_index = Mock()
    ref_internal = "a" * 64 + "_0"
    ref_stored = "a" * 64 + "0"  # internal stored form (decimal vout)

    # Simulate the production state: contract is only 5% mined but was latched
    # inactive/orphaned by a previous orphan sweep, with no reactivation path.
    mgr = DMintContractsManager(str(tmp_path), glyph_index=glyph_index)
    mgr.contracts = [{
        "ref": ref_stored,
        "outputs": 1,
        "ticker": "LIVE",
        "name": "Live Token",
        "algorithm": 1,
        "difficulty": 123,
        "reward": 50,
        "percent_mined": 5,
        "active": False,
        "orphaned": True,
        "deploy_height": 100,
        "daa_mode": 0,
        "daa_mode_name": "Fixed",
        "total_supply": 1000,
        "mined_supply": 50,
    }]

    glyph_index.get_tokens_by_type.return_value = [
        _index_token(ref_internal, total_supply=1000, mined_supply=50, percent_mined=5)
    ]

    mgr.sync_from_index(500)

    c = mgr.contracts[0]
    assert c["active"] is True
    assert c.get("orphaned") is False

    # It must now appear in the default `mineable` listing the miner uses.
    response = mgr.get_contracts_v2({"version": 2, "view": "token_summary"})
    assert response["count"] == 1
    assert response["items"][0]["is_fully_mined"] is False


def test_sync_does_not_orphan_when_index_returns_empty(tmp_path):
    """Regression: an empty/lagging index pass (mid-resync) must not orphan
    every live contract — that is the failure that blanked the miner."""
    glyph_index = Mock()
    glyph_index.get_tokens_by_type.return_value = []

    mgr = DMintContractsManager(str(tmp_path), glyph_index=glyph_index)
    mgr.contracts = [_make_contract("a" * 72, "ALFA", algorithm=1, reward=300, deploy_height=10)]

    mgr.sync_from_index(500)

    c = mgr.contracts[0]
    assert c.get("active", True) is True
    assert c.get("orphaned") is not True


def test_sync_still_orphans_missing_contract_when_index_populated(tmp_path):
    """A contract genuinely absent from a populated index is still swept."""
    glyph_index = Mock()
    present_internal = "a" * 64 + "_0"
    glyph_index.get_tokens_by_type.return_value = [
        _index_token(present_internal, total_supply=1000, mined_supply=50, percent_mined=5)
    ]

    mgr = DMintContractsManager(str(tmp_path), glyph_index=glyph_index)
    mgr.contracts = [
        {
            "ref": "a" * 64 + "0",
            "outputs": 1, "ticker": "LIVE", "name": "", "algorithm": 1,
            "difficulty": 1, "reward": 1, "percent_mined": 5, "active": True,
            "deploy_height": 100, "total_supply": 1000, "mined_supply": 50,
        },
        _make_contract("f" * 72, "GONE", algorithm=1, reward=1, deploy_height=11),
    ]

    mgr.sync_from_index(500)

    gone = next(c for c in mgr.contracts if c["ticker"] == "GONE")
    assert gone["active"] is False
    assert gone["orphaned"] is True


def test_sync_from_index_uses_icon_ref_when_remote_embed_absent(tmp_path):
    glyph_index = Mock()
    glyph_index.get_tokens_by_type.return_value = [
        {
            "ref": "a" * 64 + "_0",
            "ticker": "ICON",
            "name": "Icon Token",
            "deploy_height": 100,
            "total_supply": 1000,
            "mined_supply": 100,
            "percent_mined": 10,
            "icon_type": "image/png",
            "icon_ref": "ipfs://bafybeigdyrzt",
            "dmint": {
                "algorithm": 1,
                "current_difficulty": 123,
                "reward": 50,
                "num_contracts": 4,
                "daa_mode": 0,
                "daa_mode_name": "Fixed",
            },
        }
    ]

    mgr = DMintContractsManager(str(tmp_path), glyph_index=glyph_index)
    updated = mgr.sync_from_index(500)

    assert updated == 1
    assert len(mgr.contracts) == 1
    c = mgr.contracts[0]
    assert c["icon_type"] == "image/png"
    assert c["icon_url"] == "ipfs://bafybeigdyrzt"
    assert c["icon_ref"] == "ipfs://bafybeigdyrzt"

    response = mgr.get_contracts_v2({"version": 2, "view": "token_summary", "filters": {"status": "all"}})
    assert response["items"][0]["icon"]["url"] == "ipfs://bafybeigdyrzt"


def test_sync_from_index_normalizes_embedded_icon_data(tmp_path):
    glyph_index = Mock()
    glyph_index.get_tokens_by_type.return_value = [
        {
            "ref": "b" * 64 + "_0",
            "ticker": "EMBD",
            "name": "Embedded Icon",
            "deploy_height": 101,
            "total_supply": 1000,
            "mined_supply": 50,
            "percent_mined": 5,
            "embed": {
                "type": "image/webp",
                "data": bytes.fromhex("aabbccdd"),
            },
            "dmint": {
                "algorithm": 1,
                "current_difficulty": 456,
                "reward": 25,
                "num_contracts": 2,
                "daa_mode": 0,
                "daa_mode_name": "Fixed",
            },
        }
    ]

    mgr = DMintContractsManager(str(tmp_path), glyph_index=glyph_index)
    updated = mgr.sync_from_index(501)

    assert updated == 1
    c = mgr.contracts[0]
    assert c["icon_type"] == "image/webp"
    assert c["icon_data"] == "aabbccdd"

    response = mgr.get_contracts_v2({"version": 2, "view": "token_summary", "filters": {"status": "all"}})
    assert response["items"][0]["icon"]["data_hex"] == "aabbccdd"
