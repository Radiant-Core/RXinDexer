"""
GraphQL API for RXinDexer.

Provides flexible querying for:
- Blocks and transactions
- Tokens (Glyphs - NFTs, FTs, Containers)
- Wallets and addresses
"""

from api.graphql.schema import schema, graphql_router

__all__ = ["schema", "graphql_router"]
