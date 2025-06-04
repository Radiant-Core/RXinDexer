#!/bin/bash
# This script is designed to directly debug the security issues within the Docker container itself

echo "===== ENVIRONMENT VERIFICATION ====="
docker-compose exec rxindexer-api env | grep -i api
echo ""

echo "===== CHECKING CODE FRESHNESS ====="
docker-compose exec rxindexer-api ls -la /app/src/api/ | grep -E "main.py|security.py|security_patch.py"
echo ""

echo "===== API KEY TEST INSIDE CONTAINER ====="
docker-compose exec rxindexer-api curl -s -i http://localhost:8000/api/v1/tokens/
echo ""

echo "===== API KEY TEST WITH VALID KEY INSIDE CONTAINER ====="
docker-compose exec rxindexer-api curl -s -i -H "X-API-Key: test-api-key-1" http://localhost:8000/api/v1/tokens/
echo ""

echo "===== SECURITY HEADERS TEST INSIDE CONTAINER ====="
docker-compose exec rxindexer-api curl -s -i http://localhost:8000/health
echo ""

echo "===== CHECKING LOADED MODULES ====="
docker-compose exec rxindexer-api python3 -c "
import sys
print('Python path:')
print('\\n'.join(sys.path))
print('\\nImported modules:')
from src.api import main, security
print('main.py security components:', [attr for attr in dir(main) if 'secur' in attr.lower()])
print('security.py components:', [attr for attr in dir(security) if not attr.startswith('__')])
try:
    from src.api import security_patch
    print('security_patch.py loaded successfully')
except ImportError as e:
    print('Failed to import security_patch:', str(e))
"
