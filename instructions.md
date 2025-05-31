RXinDexer Development Plan
This plan outlines the development of RXinDexer, a lightweight, scalable indexer for the Radiant (RXD) blockchain, similar to Electrum, with support for Glyph tokens and wallet holder counts. It is designed for a coding AI to follow, using information from Glyph-Protocol-Tech-Guide and Radiant-Node.
1. Project Overview
RXinDexer will:
Index RXD transactions and balances using Radiant’s UTXO model.
Track Glyph tokens (fungible, non-fungible, dmint) via their CBOR-encoded payloads.
Count unique wallet holders for RXD and Glyph tokens.
Expose REST APIs for querying balances, transaction history, token metadata, and holder counts.
Ensure scalability and compatibility with Radiant’s induction proof system.
2. Technical Requirements
Radiant Node: Version 1.2.0 (latest stable as of May 2025).
Database: PostgreSQL 16 for relational queries.
Language: Python 3.11 for rapid development and library support.
Libraries:
python-bitcoinrpc (v1.0) for Radiant Node RPC.
cbor2 (v5.4.6) for parsing Glyph token payloads.
fastapi (v0.115.0) for REST APIs.
sqlalchemy (v2.0.35) for database ORM.
redis-py (v5.0.8) for caching.
Tools: Docker (v27.0) for deployment, pytest (v8.3.3) for testing.
3. Architecture
RXinDexer consists of:
Sync Module: Fetches blocks and transactions from Radiant Node.
Parser: Extracts UTXOs, Glyph tokens, and wallet addresses.
Database: Stores indexed data for fast querying.
API Layer: Serves data to clients via REST endpoints.
Cache: Improves query performance with Redis.
4. Development Steps
4.1. Set Up Radiant Node
Task: Clone and configure Radiant-Node.
Code:
bash
git clone https://github.com/Radiant-Core/Radiant-Node.git
cd Radiant-Node
./autogen.sh
./configure
make && make install
Config: Edit radiant.conf:
conf
rpcuser=rxin
rpcpassword=securepassword
rpcport=7332
server=1
txindex=1
Run: radiantd -daemon and wait for blockchain sync.
4.2. Implement Sync Module
Task: Fetch blocks and transactions via RPC, handle reorgs.
Code:
python
from bitcoinrpc.authproxy import AuthServiceProxy
import time

rpc = AuthServiceProxy("http://rxin:securepassword@localhost:7332")
def sync_blocks(start_height):
    latest_height = rpc.getblockcount()
    for height in range(start_height, latest_height + 1):
        block_hash = rpc.getblockhash(height)
        block = rpc.getblock(block_hash, 2)  # Verbosity 2 for full tx data
        yield block
Reorg Handling: Check chainwork to detect reorgs and rollback affected blocks.
Output: Store latest synced height in database.
4.3. Build Parser
Task: Parse UTXOs and Glyph tokens.
UTXO Parsing:
python
def parse_utxo(tx, vout_idx):
    vout = tx["vout"][vout_idx]
    if vout["scriptPubKey"]["type"] == "pubkeyhash":
        return {
            "txid": tx["txid"],
            "vout": vout_idx,
            "address": vout["scriptPubKey"]["addresses"][0],
            "amount": vout["value"]
        }
    return None
Glyph Token Parsing:
python
import cbor2

def parse_glyph_token(tx):
    for vin in tx["vin"]:
        if "gly" in vin.get("scriptSig", {}).get("asm", ""):
            raw_tx = rpc.getrawtransaction(tx["txid"], True)
            reveal_script = raw_tx["vin"][0]["scriptSig"]["hex"]
            if reveal_script.startswith("gly"):
                cbor_data = cbor2.loads(bytes.fromhex(reveal_script[6:]))
                return {
                    "ref": cbor_data.get("ref"),
                    "type": cbor_data.get("type"),  # "fungible", "non-fungible", "dmint"
                    "metadata": cbor_data.get("metadata")
                }
    return None
Output: Store UTXOs and tokens in database.
4.4. Track Wallet Holders
Task: Count unique addresses with non-zero balances.
Code:
python
def update_holders(utxos, tokens):
    holders = {}
    for utxo in utxos:
        addr = utxo["address"]
        holders[addr] = holders.get(addr, {"rxd": 0, "tokens": {}})
        holders[addr]["rxd"] += utxo["amount"]
    for token in tokens:
        addr = token["address"]
        holders[addr]["tokens"][token["ref"]] = token["balance"]
    return len([addr for addr, data in holders.items() if data["rxd"] > 0 or data["tokens"]])
Output: Store holder counts in database.
4.5. Set Up Database
Schema (PostgreSQL):
sql
CREATE TABLE utxos (
    txid VARCHAR(64) NOT NULL,
    vout INTEGER NOT NULL,
    address VARCHAR(64) NOT NULL,
    amount DECIMAL(16, 8) NOT NULL,
    ref VARCHAR(64),
    PRIMARY KEY (txid, vout)
);
CREATE TABLE glyph_tokens (
    ref VARCHAR(64) PRIMARY KEY,
    type VARCHAR(20) NOT NULL,
    metadata JSONB,
    current_txid VARCHAR(64),
    current_vout INTEGER
);
CREATE TABLE holders (
    address VARCHAR(64) PRIMARY KEY,
    rxd_balance DECIMAL(16, 8) DEFAULT 0,
    token_balances JSONB DEFAULT '{}'
);
CREATE INDEX idx_utxo_address ON utxos(address);
CREATE INDEX idx_token_ref ON glyph_tokens(ref);
ORM (SQLAlchemy):
python
from sqlalchemy import create_engine, Column, String, Integer, Numeric, JSON
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()
class UTXO(Base):
    __tablename__ = "utxos"
    txid = Column(String, primary_key=True)
    vout = Column(Integer, primary_key=True)
    address = Column(String, nullable=False)
    amount = Column(Numeric(16, 8), nullable=False)
    ref = Column(String)

engine = create_engine("postgresql://user:pass@localhost/rxindexer")
Base.metadata.create_all(engine)
4.6. Develop APIs
Framework: FastAPI.
Endpoints:
python
from fastapi import FastAPI, HTTPException
from sqlalchemy.orm import Session

app = FastAPI()
@app.get("/address/{address}/balance")
async def get_balance(address: str, db: Session = Depends(get_db)):
    utxo = db.query(UTXO).filter(UTXO.address == address).all()
    holder = db.query(Holder).filter(Holder.address == address).first()
    if not utxo and not holder:
        raise HTTPException(404, "Address not found")
    return {
        "address": address,
        "rxd_balance": sum(u.amount for u in utxo),
        "glyph_tokens": holder.token_balances if holder else {}
    }

@app.get("/holders/{asset}")
async def get_holders(asset: str, db: Session = Depends(get_db)):
    if asset == "RXD":
        count = db.query(Holder).filter(Holder.rxd_balance > 0).count()
    else:
        count = db.query(Holder).filter(Holder.token_balances[asset].astext.cast(Integer) > 0).count()
    return {"asset": asset, "holder_count": count}
Features:
Pagination: Add limit and offset query params.
Authentication: Use OAuth2 with API keys.
Error Handling: Return 404 for unknown addresses, 429 for rate limits.
4.7. Implement Caching
Task: Cache frequent queries with Redis.
Code:
python
import redis
import json

r = redis.Redis(host="localhost", port=6379, db=0)
def cache_balance(address):
    key = f"balance:{address}"
    cached = r.get(key)
    if cached:
        return json.loads(cached)
    balance = compute_balance(address)  # From database
    r.setex(key, 300, json.dumps(balance))  # Cache for 5 minutes
    return balance
4.8. Test and Optimize
Unit Tests (pytest):
python
def test_parse_glyph_token():
    tx = {"vin": [{"scriptSig": {"asm": "gly [cbor_data]"}}]}
    token = parse_glyph_token(tx)
    assert token["ref"] == "glyph:1234"
Test Cases:
Parse RXD UTXOs from a block.
Validate Glyph token CBOR payload.
Count holders for a fungible token.
Query balance for an address with mixed assets.
Optimization:
Use batch inserts for database writes.
Index frequently queried fields (e.g., address, ref).
Parallelize block processing with multiprocessing.
5. Deployment
Dockerfile:
dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
docker-compose.yml:
yaml
version: "3.9"
services:
  rxindexer:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://user:pass@db/rxindexer
      - RPC_URL=http://rxin:securepassword@radiant:7332
    depends_on:
      - db
      - redis
      - radiant
  db:
    image: postgres:16
    environment:
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
      - POSTGRES_DB=rxindexer
  redis:
    image: redis:7
  radiant:
    image: radiant-node:1.2.0
    volumes:
      - ./radiant.conf:/root/.radiant/radiant.conf
Monitoring: Use Prometheus for metrics and Grafana for visualization.
6. Error Handling
Reorgs: Rollback database to last stable block if chainwork decreases.
RPC Failures: Retry failed RPC calls with exponential backoff.
Database Consistency: Use transactions for atomic updates.
7. Timeline
Week 1–2: Set up Radiant Node, implement sync module.
Week 3–4: Build parser and wallet tracker.
Week 5–6: Set up database and APIs.
Week 7: Test on testnet, optimize performance.
Week 8: Deploy to mainnet, set up monitoring.
8. Future Enhancements
Support RadiantScript for smart contract indexing.
Add WebSocket for real-time updates.
Integrate with Radiant testnet for development.
9. Notes
Ensure Radiant Node is fully synced before indexing.
Validate Glyph token refs using ref.get RPC method.
Monitor database growth and adjust sharding if needed.

Conclusion
This plan outlines a scalable, Electrum-like indexer for Radiant (RXD) that supports Glyph tokens and tracks wallet holder counts. By leveraging Radiant’s UTXO model, induction proof system, and minimal indexing requirements, the indexer ensures efficiency and compatibility with the Glyph Protocol and Radiant-Node. The provided APIs enable seamless integration with wallets and dApps, fostering adoption within the Radiant ecosystem.
For further details, refer to:

Glyph Protocol Tech Guide: https://github.com/Radiant-Core/Glyph-Protocol-Tech-Guide

Radiant Node: https://github.com/Radiant-Core/Radiant-Node

