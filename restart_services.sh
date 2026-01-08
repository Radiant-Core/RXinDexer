#!/bin/bash
echo "🔄 RXinDexer Service Restart Script"
echo "=================================="

# Set environment for OrbStack
export POSTGRES_HOST=localhost

echo "1. Clearing Python caches..."
python3 -c "
from api.endpoints.tokens import cached_token_holder_count
cached_token_holder_count.cache_clear()
print('✅ Token holder cache cleared')
"

echo "2. Refreshing materialized views..."
python3 -c "
from database.session import SessionLocal
from sqlalchemy import text
db = SessionLocal()
try:
    db.execute(text('REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_holder_stats'))
    db.execute(text('REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_burn_stats'))
    db.commit()
    print('✅ Materialized views refreshed')
except Exception as e:
    print(f'⚠️  {e}')
    db.rollback()
finally:
    db.close()
"

echo "3. Restarting services..."
# Add your restart commands here
# docker-compose restart api
# orbstack restart rxindexer-api
# systemctl restart rxindexer

echo "✅ Restart complete!"
echo "📊 Check your explorer for updated holder counts"
