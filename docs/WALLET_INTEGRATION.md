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
| SLIP-44 coin type | **512** (authoritative) **+ 0** (legacy) | use 512 going forward; **also** derive coin type `0` (Bitcoin's original path) to find legacy funds — see §3.1 |
| Derivation (spending) | `m/44'/512'/0'/0/k` **and** legacy `m/44'/0'/0'/0/k` | external chain; scan **both** coin types for discovery |
| Derivation (swap subaccount) | `m/44'/512'/0'/0/1` | optional, ecosystem convention |
| Derivation (encryption key) | `m/44'/512'/0'/2/0` | optional; only if you use encrypted token payloads |
| Address format | **legacy Base58Check** | no CashAddr; addresses look Bitcoin-style |
| P2PKH version byte (mainnet) | `0x00` | testnet/regtest `0x6f` |
| P2SH version byte (mainnet) | `0x05` | testnet/regtest `0xc4` |
| WIF secret byte | `0x80` mainnet / `0xef` test | standard |
| BIP32 xpub/xprv magic | standard Bitcoin (`0x0488B21E` / `0x0488ADE4`) | server does no key derivation |
| Genesis hash (mainnet) | `0000000065d8ed5d8be28d6876b3ffb660ac2a6c0ca59e437e1f7a6f4e003fb4` | |
| **Block header hash algo** | **double SHA512-256** ⚠️ | **NOT** double SHA256 — see §4.1 |
| Electrum scripthash (plain addr) | standard `sha256(scriptPubKey)`, byte-reversed hex | see §3.2 |
| Electrum protocol version | **1.4 – 1.4.2** | negotiate via `server.version` |
| **Mainnet min fee rate** | **10,000 photons/byte** | ⚠️ the `relayfee` RPC under-reports this — see §4.2 |

---

## 2. Connecting (public WSS endpoint)

You consume public infrastructure rather than self-hosting, and connect over
**WebSocket Electrum (WSS)** — which is exactly the transport the community server
exposes publicly.

**Community endpoint (radiantcore):**

| Service | Endpoint | Notes |
|---|---|---|
| **WSS (WebSocket Electrum, TLS)** | **`wss://electrumx.radiantcore.org`** (port 443) | the Electrum protocol over a secure WebSocket — what you connect to |
| REST (HTTP) | `http://electrumx.radiantcore.org:8000` (`/health`, `/glyphs/{ref}`, …) | optional; token metadata/thumbnails and a health probe |

A connection is exactly an ElectrumX session carried over the WebSocket: open the WSS
socket, send `server.version`, then issue `blockchain.*` calls (newline-delimited
JSON-RPC).

Two operational notes for depending on a public server:

1. **Treat the endpoint as a live dependency.** The server is synced to the chain tip
   and serving. As normal client hygiene, handle reconnects/backoff, and you can
   sanity-check liveness at any time via `GET http://electrumx.radiantcore.org:8000/health`
   (`status: healthy`, with `sync_height` tracking the chain tip).
2. **Coordinate production use with the operator.** A public community server applies
   per-IP rate limits and may change. For production wallet traffic, agree on rate-limit
   headroom (or an allowlisted path) with the radiantcore operator before launch.

(Raw TCP/SSL Electrum ports are firewalled on the public server; **WSS on :443 is the
supported public transport**. Operators who would rather self-host can see
[`docs/ECOSYSTEM_SERVER_SETUP.md`](ECOSYSTEM_SERVER_SETUP.md).)

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

### 3.1 Address derivation — support BOTH coin types (legacy `0` and `512`)

Radiant has two HD derivation lineages, and a wallet should support **both**:

- **Legacy / original — coin type `0`** (`m/44'/0'/0'/0/k`): the **same path as Bitcoin**.
  Early Radiant wallets and some existing user funds live here.
- **Authoritative — coin type `512`** (`m/44'/512'/0'/0/k`): the SLIP-0044-registered ID
  for Radiant; the standard going forward.

How to handle both:

- **Discovery / balance:** scan addresses under **both** coin type `0` **and** `512`
  (external `…/0/k` and change `…/1/k` chains, with your normal gap limit) and aggregate
  the results. A wallet that derives only `512` will **miss** funds a user holds under the
  legacy `0` path — and vice-versa.
- **Receiving:** hand out **`512`** addresses by default (the path going forward).
- **Spending:** inputs from either lineage are ordinary P2PKH UTXOs and can be mixed in
  one transaction; sign each input with the key from its own path.
- **Optional migration:** offer to sweep/consolidate legacy `0` funds onto a `512` address
  so users converge on the authoritative path over time.

The server is **path-agnostic** — it indexes by address/scripthash only — so both
lineages work against the same endpoint with no special server support.

### 3.2 Computing the scripthash

For a normal P2PKH (or P2SH) address the Electrum scripthash is computed the
**standard** way — no Radiant special-casing:

1. Decode the Base58Check address → 20-byte hash160 (version byte `0x00` for mainnet P2PKH).
2. Build the scriptPubKey (`OP_DUP OP_HASH160 <hash160> OP_EQUALVERIFY OP_CHECKSIG`).
3. `sha256(scriptPubKey)` → 32 bytes → **reverse byte order** → hex. That string is the
   `scripthash` parameter for all `blockchain.scripthash.*` calls.

(RXinDexer also keeps an internal 11-byte index key and a token-aware "zeroed-ref"
hash, but those are server internals — clients always use the standard 32-byte
scripthash above. The zeroed-ref behavior only matters for token scripts; see §5.3.)

### 3.3 Building and broadcasting a transaction

Radiant transactions are Bitcoin-style (P2PKH inputs/outputs). Signing is standard ECDSA
over **secp256k1**, DER + 1 sighash-type byte, low-S — same encoding as BCH/BSV. **Two
things differ and you must get both right: the sighash *preimage* (§3.4) and the fee rate
(§4.2).** Then `blockchain.transaction.broadcast` the raw hex.

### 3.4 Signing — the Radiant sighash preimage (REQUIRED for on-device signing)

A hardware wallet builds the signature digest on the secure element, so it must construct
Radiant's preimage **exactly**. Radiant uses a BIP143/FORKID-style preimage (like BCH/BSV)
with **one extra field**: a 32-byte `hashOutputHashes` inserted **immediately before** the
standard `hashOutputs`. A vanilla BIP143 preimage (without it) yields signatures the node
rejects. (Verified against Radiant Core `interpreter.cpp`, `@radiant-core/radiantjs`, and
the C# port — they agree on layout, sizes, FORKID, and digest.)

**Preimage field order** (each input signed independently):

| # | Field | Bytes |
|---|---|---|
| 1 | `nVersion` | 4 (LE) |
| 2 | `hashPrevouts` | 32 |
| 3 | `hashSequence` | 32 |
| 4 | `outpoint` of this input (32B txid + 4B vout LE) | 36 |
| 5 | `scriptCode` (varint length + bytes) | var |
| 6 | `value` of the UTXO being spent | 8 (LE) |
| 7 | `nSequence` of this input | 4 |
| 8 | **`hashOutputHashes`** ← Radiant-specific | 32 |
| 9 | `hashOutputs` (standard BIP143) | 32 |
| 10 | `nLockTime` | 4 |
| 11 | `sighashType` | 4 (LE) |

**Double-SHA256** the buffer → the 32-byte digest to sign. (Double-SHA256 — *not* the
SHA512-256 used for block headers.) Sighash type = `SIGHASH_ALL | SIGHASH_FORKID = 0x41`,
serialized as uint32 LE `0x00000041` (fork value 0).

**Computing `hashOutputHashes`** (field 8): for **each output** build a 4-field summary,
concatenate all summaries, then double-SHA256 the result:

1. `value` — 8 bytes LE
2. `scriptPubKeyHash` — **double-SHA256 of the output's scriptPubKey** (32 bytes)
3. `totalRefs` — 4 bytes LE = count of distinct push-refs in the output
4. `refsHash` — 32 bytes: all-zero if the output has no push-refs; else double-SHA256 of
   the output's push-ref operands (each a 36-byte `uint288`), **deduplicated and sorted as
   little-endian integers**, concatenated.

"Push-refs" = the operands of `OP_PUSHINPUTREF` (`0xd0`) and `OP_PUSHINPUTREFSINGLETON`
(`0xd8`) only (not the `REQUIRE`/`DISALLOW` ref opcodes).

> ⚠️ **Ref sort order is consensus-critical.** Sort the 36-byte refs as **little-endian
> integers** (compare from the last byte down), matching Radiant Core / the C# port — *not*
> radiantjs's hex-string (big-endian) sort, which diverges when one output carries 2+
> distinct refs. Single-ref outputs (the common token case) are unaffected.

The preimage **structure is identical for plain-RXD and token inputs** — the only
input-specific part is `scriptCode` (field 5 = that input's locking script). Tokens change
only field 8's *value* (through outputs that carry refs), never its position or size. So
one signer handles both RXD and token transfers.

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
{ "tx_hash":"…", "tx_pos":0, "height":319, "value":1000, "refs":[] }
{ "tx_hash":"…", "tx_pos":0, "height":319, "value":1,    "refs":[{ "ref":"<txid>i<vout>", "type":"single" }] }
{ "tx_hash":"…", "tx_pos":0, "height":315, "value":300,  "refs":[{ "ref":"<txid>i<vout>", "type":"normal" }] }
```

- **`refs` is empty (`[]`)** → plain RXD. Safe to spend as fee/change.
- **`refs` is non-empty** → the UTXO **carries a token**. **Do not** select it for fees or
  ordinary change — spending it as plain RXD **burns the token**.
- The ref `type` is the kind: **`"single"`** = NFT/singleton (`OP_PUSHINPUTREFSINGLETON`),
  **`"normal"`** = FT (`OP_PUSHINPUTREF`). For an FT the UTXO's **`value` is the token
  amount** (1 token = 1 photon). (Shapes above are from a live regtest run — §7.1.)

> Even if you only support RXD send/receive, honor this rule — a user's address can hold
> token UTXOs. Filter coin selection to `refs == []` outputs.

### 5.2 Finding an address's tokens — the zeroed-ref scripthash (IMPORTANT)

Token UTXOs are **not** indexed under the owner address's plain P2PKH scripthash. The
indexer keys every UTXO by `sha256(zero_refs(scriptPubKey))` — it **zeroes the 36-byte ref
operands** (of `0xd0`/`0xd8`) before hashing. So to find a token at an address, query the
scripthash of the token's locking script **with the ref operand set to all zeros**. One
scripthash per token *kind* per address.

You still use the **same standard Electrum calls** as RXD —
`blockchain.scripthash.listunspent` / `.subscribe` — the only trick is *which* scripthash
you hash:

**NFT scripthash** = `sha256(` these bytes `)`, then byte-reverse:
```
d8 <00 × 36> 75 76a914 <addr_hash160> 88ac
└┘ └zeroed ref┘ └DROP┘ └──── P2PKH owner ────┘
```

**FT scripthash** = `sha256(` these bytes `)`, then byte-reverse:
```
76a914 <addr_hash160> 88ac bd d0 <00 × 36> dec0e9aa76e378e4a269e69d
└──── P2PKH owner ────┘ └SEP┘└┘ └zeroed ref┘ └── value-sum covenant ──┘
```

Each returned UTXO carries `refs[0].ref` (the **real**, non-zeroed ref) and a `type`
(§5.1). Rebuild the real locking script from that ref when you need to spend it (§5.3);
sum FT UTXO `value`s for an FT balance. (Both scripthash recipes are verified end-to-end
in §7.1.)

> **Do not use `glyph.list_tokens` for wallet discovery** — the reference wallet does not,
> and it does not return holdings keyed this way (it returns empty for the scripthashes
> above). Discovery is purely the two zeroed-ref `listunspent`/`subscribe` queries. The
> `glyph.*` RPCs below are for *metadata by ref*, not for enumerating an address.

**Metadata / detail RPCs** (keyed by a token ref — for names, images, supply):

| Method | Returns |
|---|---|
| `glyph.get_by_ref` | `type_name`, `name`, `ticker`, `decimals`, supply, `deploy_txid`, and a resolved `owner.address` |
| `glyph.get_metadata` | parsed CBOR metadata (name / desc / image / attrs) |
| `blockchain.ref.get` | a ref's current location / reveal txid |

REST equivalents (`:8000`, `X-API-Key` if `REST_API_KEY` is set): `GET /glyphs/{ref}`,
`GET /tokens/{ref}/holders`.

A **ref** is accepted as `"<txid>_<vout>"` or `"<txid>i<vout>"` (display) or 72-hex
(internal LE). `glyph.get_by_ref` returns `deploy_txid` — the reveal tx carrying the
token's CBOR metadata.

### 5.3 Transferring tokens — wire-level construction (FT + NFT)

A transfer spends the token UTXO and **re-emits the ref** on the destination output's
locking script. **No Glyph envelope (`gly`+CBOR) is emitted on a transfer** — the
`gly`/CBOR payload exists only at mint; on every move the token survives purely via the
colored-coin ref opcode, and the indexer recovers metadata by walking back to the genesis
reveal.

**Rules common to FT and NFT:**

- **Ref encoding:** the 36-byte ref operand is the outpoint **little-endian** — reverse
  *both* the 32-byte txid and the 4-byte vout from display form. (vout 0 hides byte-order
  bugs — reversed zeros are still zeros — so test with a non-zero vout.)
- **Inputs:** spend the token UTXO(s) with a **normal `<sig> <pubkey>` P2PKH scriptSig** —
  the ref opcodes are in the *locking* script you satisfy, not in the scriptSig. Fund the
  fee from **separate plain-RXD inputs** (`refs == []`); never let a token UTXO pay the fee.
- **Fee / change:** `fee = MIN_RELAY_FEE_RATE (10,000) × tx_size`; RXD change is a plain
  P2PKH output. Size token inputs as P2PKH (~107-byte scriptSig), not by their longer
  locking script.
- **Signing:** the §3.4 preimage, `SIGHASH_ALL|FORKID` (`0x41`), per input.

**NFT (singleton) — destination output script (63 bytes):**
```
d8 <ref:36 LE> 75 76a914 <recipient_hash160> 88ac
```
`OP_PUSHINPUTREFSINGLETON <ref> OP_DROP` + recipient P2PKH. Move the NFT UTXO (typically
1 photon) to one such output. `0xd8` enforces the ref's global uniqueness.

**FT (fungible amount) — destination/change output script (75 bytes):**
```
76a914 <hash160> 88ac bd d0 <ref:36 LE> dec0e9aa76e378e4a269e69d
```
P2PKH(owner) + `OP_STATESEPARATOR` + `OP_PUSHINPUTREF <ref>` + value-sum covenant tail.
- **The amount IS the output's photon `value`** (1 token = 1 photon) — no separate field.
- If the gathered FT inputs exceed the send amount, add a **second FT output back to the
  sender** (same script with the sender's `hash160`) carrying the change — the FT analog of
  UTXO change.
- The covenant tail (bytes `dec0e9aa76e378e4a269e69d`) enforces on-chain that input
  token-photons ≥ output token-photons under the same code-script, so send + change must
  conserve value. ("Melt"/burn = simply omit the FT output: spend the token UTXO and emit
  no `ftScript` output, so the token-photons go to plain RXD/fee.)

**Opcode bytes** you must emit: `OP_STATESEPARATOR = 0xbd`, `OP_PUSHINPUTREF = 0xd0`,
`OP_PUSHINPUTREFSINGLETON = 0xd8`, `OP_DROP = 0x75`.

**Reference implementation (JS/TS):** `@photonic/lib` — `transfer.tsx`
(`transferFungible` / `transferNonFungible`), `script.ts` (`ftScript` / `nftScript` /
`ftScriptHash` / `nftScriptHash`), `coinSelect.ts` (token-safety guard + funding),
`tx.ts` (signing), `feePolicy.ts` (the 10,000 floor). If your companion software is JS/TS,
call these directly; otherwise mirror the byte layouts above.

---

## 6. Suggested phased rollout

1. **Phase 1 — RXD send/receive.** Standard Electrum (Tier 1) + the three §4 rules.
   Apply the §5.1 coin-selection filter so token UTXOs are never spent as fees. This
   alone is a complete, safe RXD wallet.
2. **Phase 2 — Token display (read-only).** Discover holdings via the §5.2 zeroed-ref
   `listunspent` queries (one NFT + one FT scripthash per address), then `glyph.get_by_ref`
   / `glyph.get_metadata` for names/images/supply. No signing changes.
3. **Phase 3 — Token transfer.** Implement the §5.3 wire-level FT/NFT construction (or
   reuse `@photonic/lib`) plus the §3.4 sighash preimage. Test heavily on regtest/testnet
   first — the exact cycle is in §7.1.

---

## 7. Verification / smoke test

```bash
# 1. Handshake — connect to wss://electrumx.radiantcore.org and send each request
#    as a newline-delimited JSON-RPC frame over the WebSocket:
{"id":0,"method":"server.version","params":["mywallet","1.4"]}
# Expect a [server_string, "1.4.x"] result.

# 2. Balance of a known address's scripthash
{"id":1,"method":"blockchain.scripthash.get_balance","params":["<scripthash>"]}

# 3. UTXOs — verify the refs[] field is present (token-safety)
{"id":2,"method":"blockchain.scripthash.listunspent","params":["<scripthash>"]}

# 4. (tokens) find an address's tokens — listunspent on the ZEROED-REF scripthash (§5.2),
#    NOT glyph.list_tokens. Each token UTXO carries refs[] with a type (single=NFT, normal=FT).
{"id":3,"method":"blockchain.scripthash.listunspent","params":["<nft_or_ft_scripthash>"]}

# 5. REST health / sync status
curl http://electrumx.radiantcore.org:8000/health
```

Do a full **regtest/testnet** cycle (derive address → fund → `listunspent` → build →
sign → `broadcast` → confirm via `get_history`) before mainnet. For a local stack, see
[`docs/ECOSYSTEM_SERVER_SETUP.md`](ECOSYSTEM_SERVER_SETUP.md).

### 7.1 Verified end-to-end on regtest (FT + NFT)

Everything in §5 is proven against a local Radiant regtest node + RXinDexer with real,
confirmed transactions (`@photonic/lib`'s `sendFlows.regtest.test.ts`):

- **FT:** mint 1000 → send 300 to B (700 change to A) → melt 700. On-chain assertions pass
  (B = 300, A change = 700, melted output gone).
- **NFT:** mint to A → send to B (at B, gone from A).

The indexer independently confirmed each step:

| Check | Result |
|---|---|
| Indexer logged the mints as it followed the chain | `Indexed Glyph token … type=1 name=E2E FT` and `type=2 name=E2E NFT` |
| `glyph.get_by_ref` (NFT) | `type_name:"NFT"`, `name:"E2E NFT"`, resolved `owner.address` = recipient B |
| **NFT discovery** — `listunspent` on the zeroed-ref `nftScriptHash(B)` | NFT UTXO: `value:1`, `refs:[{type:"single"}]` |
| **FT discovery** — `listunspent` on the zeroed-ref `ftScriptHash(B')` | FT UTXO: `value:300`, `refs:[{type:"normal"}]` |
| `glyph.list_tokens` on those scripthashes | **empty** — confirms discovery must use `listunspent`, not `glyph.list_tokens` |

So the discovery recipe (§5.2), transfer construction (§5.3), and token-safety rule (§5.1)
are confirmed against a live indexer, not merely described.

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
