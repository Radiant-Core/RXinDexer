#!/usr/bin/env python3
"""Script to add authentication dependencies to all API endpoints."""

import os
import re
from pathlib import Path

# List of endpoints to protect (excluding health and auth)
ENDPOINTS_TO_PROTECT = [
    'blocks.py',
    'glyphs.py',
    'market.py',
    'mempool.py',
    'stats.py',
    'tokens.py',
    'transactions.py',
    'users.py',
    'wallets.py'
]

def add_authentication_to_file(file_path):
    """Add authentication imports and dependencies to a file."""
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Skip if already has authentication import
    if 'get_current_authenticated_user' in content:
        print(f"Skipping {file_path} - already has authentication")
        return
    
    # Add authentication import
    old_import = 'from api.dependencies import get_db'
    new_import = 'from api.dependencies import get_db, get_current_authenticated_user'
    
    if old_import in content:
        content = content.replace(old_import, new_import)
    else:
        # Try alternative import patterns
        patterns = [
            'from api.dependencies import',
            'import api.dependencies'
        ]
        
        for pattern in patterns:
            if pattern in content:
                if 'import api.dependencies' in content:
                    # Add new import after existing import
                    content = content.replace(
                        'import api.dependencies',
                        'import api.dependencies\nfrom api.dependencies import get_current_authenticated_user'
                    )
                else:
                    # Add to existing from import
                    content = content.replace(
                        pattern,
                        f'{pattern[:-1]}, get_current_authenticated_user)'
                    )
                break
    
    # Add authentication dependency to all @router endpoints
    # Pattern to find function definitions with @router
    router_pattern = r'(@router\.[^(]+\([^)]*\)\s*\n)(def\s+\w+\([^)]*db:\s+Session\s*=\s*Depends\(get_db\)[^)]*\):)'
    
    def add_auth_dependency(match):
        decorator = match.group(1)
        func_def = match.group(2)
        
        # Add current_user parameter before the closing parenthesis
        if 'current_user' not in func_def:
            func_def = func_def.replace(
                'db: Session = Depends(get_db)',
                'db: Session = Depends(get_db),\n    current_user = Depends(get_current_authenticated_user)'
            )
        
        return decorator + func_def
    
    content = re.sub(router_pattern, add_auth_dependency, content)
    
    # Write back to file
    with open(file_path, 'w') as f:
        f.write(content)
    
    print(f"Updated {file_path}")

def main():
    """Main function to update all endpoint files."""
    endpoints_dir = Path('/Users/rxindexer/Desktop/RXinDexer_1/api/endpoints')
    
    for endpoint_file in ENDPOINTS_TO_PROTECT:
        file_path = endpoints_dir / endpoint_file
        if file_path.exists():
            add_authentication_to_file(file_path)
        else:
            print(f"File not found: {file_path}")

if __name__ == "__main__":
    main()
