# RXinDexer — Wallet Integration Guide (RXD + Tokens)

This guide is for wallet engineering teams (including hardware-wallet backends) who
want to serve **Radiant (RXD)** wallets and, optionally, **Radiant tokens (Glyph
protocol)** using RXinDexer.

**The short version:** RXinDexer is an **ElectrumX-compatible** server. It speaks the
standard Electrum JSON-RPC protocol (`blockchain.scripthash.*`,
`blockchain.transaction.*`, `server.version`, …). If your wallet already talks to a
Radiant ElectrumX server, **switching to RXinDexer is primarily a connection change**.
Token support is an **additive layer** over the same connection — you can ship RXD
support first and add tokens later, without re-architecting.

Two integration tiers:

- **Tier 1 — Serve RXD wallets** (send/receive/balance/history/broadcast): standard
  Electrum protocol. Most wallets already implement this; you mainly supply Radiant's
  network parameters and a couple of Radiant-specific values (below).
- **Tier 2 — Token support** (display and/or transfer Glyph tokens): a small set of
  `glyph.*` RPC methods + REST endpoints, plus **one coin-selection rule** that keeps
  you from accidentally burning a token.

---

## 1. Radiant parameter cheat-sheet

Everything a wallet needs that differs from Bitcoin. (Server-verified values are from
the RXinDexer `Radiant` coin class; wallet-side derivation values are the ecosystem
standard used by the reference wallet, Photonic.)

| Parameter | Value | Notes |
|---|---|---|
| Coin name / ticker | `Radiant` / **RXD** | `COIN=Radiant`, `NET=mainnet\|testnet\|regtest` |
| Base unit | **1 RXD = 100,000,000 photons** | "photon" = satoshi-equivalent; 8 decimals |
| SLIP-44 coin type | **512** | legacy coin type `0` exists but is opt-in; use 512 |
| Derivation (spending) | `m/44'/512'/0'/0/k` | standard BIP44 external chain |
| Derivation (swap subaccount) | `m/44'/512'/0'/0/1` | optional, ecosystem convention |
| Derivation (encryption key) | `m/44'/512'/0'/2/0` | optional; only if you use encrypted token payloads |
| Address format | **legacy Base58Check** | no CashAddr; addresses look Bitcoin-style |
| P2PKH version byte (mainnet) | `0x00` | testnet/regtest `0x6f` |
| P2SH version byte (mainnet) | `0x05` | testnet/regtest `0xc4` |
| WIF secret byte | `0x80` mainnet / `0xef` test | standard |
| BIP32 xpub/xprv magic | standard Bitcoin (`0x0488B21E` / `0x0488ADE4`) | server does no key derivation |
| Genesis hash (mainnet) | `0000000065d8ed5d8be28d6876b3ffb660ac2a6c0ca59e437e1f7a6f4e003fb4` | |
| **Block header hash algo** | **double SHA512-256** ⚠️ | **NOT** double SHA256 — see §4.1 |
| Electrum scripthash (plain addr) | standard `sha256(scriptPubKey)`, byte-reversed hex | see §3.1 |
| Electrum protocol version | **1.4 – 1.4.2** | negotiate via `server.version` |
| **Mainnet min fee rate** | **10,000 photons/byte** | ⚠️ the `relayfee` RPC under-reports this — see §4.2 |

---

## 2. Connecting

RXinDexer can serve any combination of transports (set by the operator's `SERVICES`):

| Transport | Default port | Typical consumer |
|---|---|---|
| TCP Electrum | `50010` | desktop/embedded Electrum clients, hardware-wallet backends |
| SSL Electrum | `50012` | encrypted Electrum clients |
| WSS (WebSocket TLS) | `50011` | browser wallets |
| WS (plain, behind a proxy) | `50013` | reverse-proxied WebSocket |
| REST (HTTP) | `8000` | metadata/explorer queries (optional) |

You have two options:

1. **Use an existing public Radiant indexer.** The community server is
   `electrumx.radiantcore.org`, exposed as **WSS on :443** (TLS-terminated by a reverse
   proxy). If your wallet backend speaks **TCP/SSL Electrum** rather than WebSocket,
   you'll want option 2, because the public endpoint only fronts WSS.
2. **Run your own RXinDexer node** (recommended for a wallet vendor — you control
   uptime, transports, and rate limits). One-page setup:
   [`docs/ECOSYSTEM_SERVER_SETUP.md`](ECOSYSTEM_SERVER_SETUP.md). Expose whichever
   transport your wallet uses (SSL `50012` is the usual choice for hardware wallets),
   and front it with TLS.

A connection is exactly an ElectrumX session: open the socket, send `server.version`,
then issue `blockchain.*` calls.

---

## 3. Tier 1 — Serve RXD wallets (standard Electrum)

These standard Electrum methods are implemented and behave as a wallet expects:

| Purpose | Method |
|---|---|
| Handshake / version negotiation | `server.version`, `server.features`, `server.ping`, `server.banner` |
| Address balance | `blockchain.scripthash.get_balance` |
| Spendable outputs | `blockchain.scripthash.listunspent` |
| Address history | `blockchain.scripthash.get_history`, `blockchain.scripthash.get_mempool` |
| Address change notifications | `blockchain.scripthash.subscribe` / `.unsubscribe` |
| Fetch / broadcast tx | `blockchain.transaction.get`, `blockchain.transaction.broadcast` |
| Merkle proof / SPV | `blockchain.transaction.get_merkle`, `blockchain.transaction.id_from_pos` |
| Headers / chain tip | `blockchain.headers.subscribe`, `blockchain.block.header`, `blockchain.block.headers` |
| Fees | `blockchain.estimatefee`, `blockchain.relayfee` (⚠️ see §4.2) |

That is the full send/receive/balance/history loop. If your firmware already supports
an Electrum-backed coin (BTC/BCH/etc.), this tier is mostly configuration.

### 3.1 Computing the scripthash

For a normal P2PKH (or P2SH) address the Electrum scripthash is computed the
**standard** way — no Radiant special-casing:

1. Decode the Base58Check address → 20-byte hash160 (version byte `0x00` for mainnet P2PKH).
2. Build the scriptPubKey (`OP_DUP OP_HASH160 <hash160> OP_EQUALVERIFY OP_CHECKSIG`).
3. `sha256(scriptPubKey)` → 32 bytes → **reverse byte order** → hex. That string is the
   `scripthash` parameter for all `blockchain.scripthash.*` calls.

(RXinDexer also keeps an internal 11-byte index key and a token-aware "zeroed-ref"
hash, but those are server internals — clients always use the standard 32-byte
scripthash above. The zeroed-ref behavior only matters for token scripts; see §5.3.)

### 3.2 Building and broadcasting a transaction

Radiant transactions are Bitcoin-style (P2PKH inputs/outputs, standard signing). Sign
as you would for a BCH/BSV-lineage chain, then `blockchain.transaction.broadcast` the
raw hex. **Fee rate is the one thing you must get right — see §4.2.**

---

## 4. Critical differences from vanilla Bitcoin / ElectrumX

These are the only places Radiant deviates from a stock Electrum integration. Get these
three right and Tier 1 "just works."

### 4.1 Block headers hash with double SHA512-256 (only matters for SPV)

Radiant block headers are hashed with **double SHA512-256**
(`sha512_256(sha512_256(header))`), **not** double SHA256. If your wallet does **SPV
header-chain / merkle-proof verification**, you must implement this hash; otherwise
header verification fails. If your wallet trusts the server for confirmations (no local
SPV), you can ignore this. Before implementing full merkle-proof verification, confirm
the **txid / merkle-leaf** hashing with the Radiant Core team, as SPV proofs depend on
it.

### 4.2 Fee rate: use 10,000 photons/byte on mainnet — do NOT trust `relayfee`

- The canonical **mainnet minimum relay fee is `MIN_RELAY_FEE_RATE = 10,000` photons/byte**
  (Radiant Core "V2"). The reference wallet clamps every fee to this floor.
- ⚠️ The Electrum `blockchain.relayfee` RPC returns the stock ElectrumX default
  (`0.000001 RXD` = **100 photons/byte**), which is **100× too low** — a tx built at that
  rate will be **rejected by the node**. Do not derive your fee from `relayfee`.
- `blockchain.estimatefee` and `mempool.get_fee_histogram` are **not useful** for fee
  estimation here (the histogram returns `[]`). Use a fixed rate.
- **Recommendation:** fee = `max(10,000, your_rate) × tx_size_bytes` photons. Legacy/testnet
  floor is `1,000` photons/byte. Treat anything above ~`20,000` photons/byte as a
  sanity-check failure.

### 4.3 Token-safety: never spend a token UTXO as fee/change

This is the one rule that makes the difference between a token-safe wallet and one that
silently destroys user assets. It applies even to RXD-only wallets — see §5.1.

---

## 5. Tier 2 — Token support (Glyph protocol)

Radiant tokens (fungible tokens, NFTs, dMint, containers, etc.) live **on UTXOs** via
the Glyph protocol — a token rides "on top of" an ordinary output. This has one
critical consequence for coin selection, and a small API surface for display.

### 5.1 The coin-selection rule (REQUIRED, even for RXD-only wallets)

`blockchain.scripthash.listunspent` returns a **`refs` array** on every UTXO:

```json
{ "tx_hash": "…", "tx_pos": 0, "height": 812345, "value": 1000, "refs": [] }
{ "tx_hash": "…", "tx_pos": 1, "height": 812345, "value": 1, "refs": [ { "ref": "<txid>_<vout>", "type": "normal" } ] }
```

- **`refs` is empty (`[]`)** → plain RXD. Safe to spend as fee/change.
- **`refs` is non-empty** → the UTXO **carries one or more tokens**. **Do not** select it
  for fees or ordinary change — spending it as plain RXD **burns the token**.

> Even if you only support RXD send/receive, you should still honor this rule, because a
> user's address can hold token UTXOs. Filter coin selection to `refs == []` outputs.

### 5.2 Token query API (display)

RPC methods (same Electrum session as Tier 1):

| Method | Use | Notes |
|---|---|---|
| `glyph.list_tokens` | tokens held by an address | params: `scripthash` (the standard 32-byte scripthash from §3.1), optional `limit`/`cursor`; returns `[{ref, name, balance}, …]` |
| `glyph.get_by_ref` | token detail by ref | returns `protocols`, `type`, `name`, `ticker`, `decimals`, supply, `holder_count`, and **`deploy_txid`** |
| `glyph.get_metadata` | parsed CBOR metadata | name/desc/image/attrs — no need to re-decode the reveal tx |
| `glyph.get_balance` | balance for a token/address | fungible amounts |

REST equivalents (HTTP `:8000`; send `X-API-Key` if the operator set `REST_API_KEY`):

| Endpoint | Use |
|---|---|
| `GET /glyphs/{ref}` | token detail (good for thumbnails/metadata) |
| `GET /addresses/{ident}/glyphs` | all tokens at an address |
| `GET /tokens/{ref}/holders` | holder list with balances |

A **ref** is `"<txid>_<vout>"` (display form) or 72-hex (internal form); both are
accepted. `glyph.get_by_ref` returns `deploy_txid`, the reveal tx that carries the
token's CBOR envelope.

### 5.3 Transferring tokens (advanced)

Displaying tokens is read-only and easy (§5.2). **Transferring** a token requires
building a Glyph-aware transaction that carries the token's input-ref through to the
correct output — this is more than a standard P2PKH spend. We strongly recommend
reusing the reference implementation rather than re-deriving it:

- **`@photonic/lib`** (in the Photonic-Wallet repo) implements mint/transfer/coin-select
  with the token-safety and ref-handling logic already correct (`coinSelect.ts`,
  `tx.ts`, `feePolicy.ts`).
- The **Glyph Token Standard** specifies the on-chain envelope/ref format.

(The "zeroed-ref" detail: token-bearing scripts contain 36-byte ref operands that the
indexer zeroes before hashing, so a token UTXO and a plain-RXD UTXO at the same address
still index under the same Electrum scripthash. You don't need to handle this for
display or for standard sends — only when constructing token-transfer scripts, which
`@photonic/lib` handles for you.)

---

## 6. Suggested phased rollout

1. **Phase 1 — RXD send/receive.** Standard Electrum (Tier 1) + the three §4 rules.
   Apply the §5.1 coin-selection filter so token UTXOs are never spent as fees. This
   alone is a complete, safe RXD wallet.
2. **Phase 2 — Token display (read-only).** Add `glyph.list_tokens` / `glyph.get_by_ref`
   / `glyph.get_metadata` (or the REST endpoints) to show balances, names, and images.
   No signing changes.
3. **Phase 3 — Token transfer.** Integrate Glyph-aware tx construction (reuse
   `@photonic/lib`). Test heavily on regtest/testnet first.

---

## 7. Verification / smoke test

```bash
# 1. Handshake (replace host/port with your transport)
#    Over TCP: e.g. `openssl s_client -connect host:50012` for SSL, then send JSON lines.
{"id":0,"method":"server.version","params":["mywallet","1.4"]}
# Expect a [server_string, "1.4.x"] result.

# 2. Balance of a known address's scripthash
{"id":1,"method":"blockchain.scripthash.get_balance","params":["<scripthash>"]}

# 3. UTXOs — verify the refs[] field is present (token-safety)
{"id":2,"method":"blockchain.scripthash.listunspent","params":["<scripthash>"]}

# 4. (tokens) list tokens at the address
{"id":3,"method":"glyph.list_tokens","params":["<scripthash>"]}

# 5. REST health (if you exposed :8000)
curl http://<host>:8000/health
```

Do a full **regtest/testnet** cycle (derive address → fund → `listunspent` → build →
sign → `broadcast` → confirm via `get_history`) before mainnet. For a local stack, see
[`docs/ECOSYSTEM_SERVER_SETUP.md`](ECOSYSTEM_SERVER_SETUP.md).

---

## 8. References

- Electrum protocol (baseline): the standard ElectrumX protocol 1.4 methods.
- RXinDexer Glyph API: [`docs/GLYPH_API.md`](GLYPH_API.md)
- RXinDexer REST API: [`docs/REST_API.md`](REST_API.md)
- Run your own node: [`docs/ECOSYSTEM_SERVER_SETUP.md`](ECOSYSTEM_SERVER_SETUP.md)
- Reference wallet implementation (HD derivation, fee policy, token-safe coin select,
  Glyph transfer): [Photonic-Wallet](https://github.com/Radiant-Core/Photonic-Wallet)
  `@photonic/lib`
- Glyph Token Standard: Radiant-Core Glyph Token Standards.

---

*Questions on the Radiant-specific values (SLIP-44 512, the SHA512-256 header hash, the
10,000-photon/byte fee floor) can be confirmed against `@photonic/lib` and the Radiant
Core node, which are the canonical sources.*
