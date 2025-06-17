# /Users/radiant/Desktop/RXinDexer/src/api/manage_keys.py
# This file provides utilities for managing API keys for the RXinDexer API.
# It allows creating, listing, and revoking API keys.

import argparse
import os
import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Default keys file location
KEYS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'api_keys.json')

class ApiKeyManager:
    """Manages API keys for the RXinDexer API"""
    
    def __init__(self, keys_file: str = KEYS_FILE):
        self.keys_file = keys_file
        self.keys_data = self._load_keys()
    
    def _load_keys(self) -> Dict:
        """Load API keys from file or create default structure"""
        keys_path = Path(self.keys_file)
        if keys_path.exists():
            with open(keys_path, 'r') as f:
                return json.load(f)
        else:
            # Create default structure
            return {
                "meta": {
                    "created_at": time.time(),
                    "updated_at": time.time()
                },
                "keys": []
            }
    
    def _save_keys(self):
        """Save API keys to file"""
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.keys_file), exist_ok=True)
        
        # Update metadata
        self.keys_data["meta"]["updated_at"] = time.time()
        
        # Write to file with pretty formatting
        with open(self.keys_file, 'w') as f:
            json.dump(self.keys_data, f, indent=2)
        
        # Update environment variable
        self._update_env_var()
    
    def _update_env_var(self):
        """Update API_KEYS environment variable"""
        # Extract just the key values for the environment variable
        key_values = [k["key"] for k in self.keys_data["keys"] if k["status"] == "active"]
        os.environ["API_KEYS"] = ",".join(key_values)
        
        print(f"Environment variable updated with {len(key_values)} active keys")
        print("To make this persistent, add to your .env file or server environment:")
        print(f"API_KEYS={os.environ['API_KEYS']}")
    
    def create_key(self, client_name: str, description: str = "", 
                  expires_days: Optional[int] = 365) -> Dict:
        """Create a new API key"""
        # Generate a secure API key
        api_key = secrets.token_urlsafe(32)
        
        # Calculate expiration if provided
        expires_at = None
        if expires_days is not None:
            expires_at = time.time() + (expires_days * 86400)
        
        # Create key entry
        key_entry = {
            "id": len(self.keys_data["keys"]) + 1,
            "key": api_key,
            "client_name": client_name,
            "description": description,
            "created_at": time.time(),
            "expires_at": expires_at,
            "status": "active",
            "last_used": None
        }
        
        # Add to keys list
        self.keys_data["keys"].append(key_entry)
        
        # Save changes
        self._save_keys()
        
        return key_entry
    
    def list_keys(self, show_key: bool = False, include_inactive: bool = False) -> List[Dict]:
        """List all API keys"""
        keys = self.keys_data["keys"]
        
        # Filter inactive keys if requested
        if not include_inactive:
            keys = [k for k in keys if k["status"] == "active"]
        
        # Mask key values if requested
        if not show_key:
            for k in keys:
                if "key" in k:
                    # Show only first 8 chars
                    k["key"] = k["key"][:8] + "..." if k["key"] else None
        
        return keys
    
    def revoke_key(self, key_id: int) -> bool:
        """Revoke an API key by ID"""
        for key in self.keys_data["keys"]:
            if key["id"] == key_id:
                key["status"] = "revoked"
                self._save_keys()
                return True
        return False
    
    def print_keys_table(self, include_inactive: bool = False):
        """Print a formatted table of API keys"""
        keys = self.list_keys(show_key=False, include_inactive=include_inactive)
        
        if not keys:
            print("No API keys found")
            return
        
        # Format table
        print("\nRXinDexer API Keys")
        print("-" * 80)
        print(f"{'ID':<5} {'Client':<20} {'Status':<10} {'Created':<20} {'Expires':<20}")
        print("-" * 80)
        
        for k in keys:
            created = datetime.fromtimestamp(k["created_at"]).strftime("%Y-%m-%d %H:%M")
            expires = "Never"
            if k["expires_at"]:
                expires = datetime.fromtimestamp(k["expires_at"]).strftime("%Y-%m-%d %H:%M")
            
            print(f"{k['id']:<5} {k['client_name'][:18]:<20} {k['status']:<10} {created:<20} {expires:<20}")
        
        print("-" * 80)

def main():
    """Command line interface for API key management"""
    parser = argparse.ArgumentParser(description="RXinDexer API Key Management")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Create key command
    create_parser = subparsers.add_parser("create", help="Create a new API key")
    create_parser.add_argument("--client", "-c", required=True, help="Client name")
    create_parser.add_argument("--description", "-d", default="", help="Key description")
    create_parser.add_argument("--expires", "-e", type=int, default=365, 
                               help="Days until expiration (0 for no expiration)")
    
    # List keys command
    list_parser = subparsers.add_parser("list", help="List all API keys")
    list_parser.add_argument("--show-key", "-s", action="store_true", help="Show full key values")
    list_parser.add_argument("--all", "-a", action="store_true", help="Include inactive keys")
    
    # Revoke key command
    revoke_parser = subparsers.add_parser("revoke", help="Revoke an API key")
    revoke_parser.add_argument("key_id", type=int, help="ID of the key to revoke")
    
    # Parse args
    args = parser.parse_args()
    
    # Initialize key manager
    manager = ApiKeyManager()
    
    # Execute command
    if args.command == "create":
        expires = None if args.expires == 0 else args.expires
        key = manager.create_key(args.client, args.description, expires)
        print(f"\nCreated new API key for {key['client_name']}")
        print(f"API Key: {key['key']}")
        print("\nIMPORTANT: Store this key securely! It won't be shown again.\n")
    
    elif args.command == "list":
        manager.print_keys_table(include_inactive=args.all)
        
        if args.show_key:
            print("\nFull API Keys (SENSITIVE):")
            for k in manager.list_keys(show_key=True, include_inactive=args.all):
                if k["status"] == "active":
                    print(f"{k['id']:<5} {k['client_name']:<20} {k['key']}")
    
    elif args.command == "revoke":
        if manager.revoke_key(args.key_id):
            print(f"API key {args.key_id} has been revoked")
        else:
            print(f"API key {args.key_id} not found")
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
