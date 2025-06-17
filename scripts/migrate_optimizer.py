#!/usr/bin/env python3
# /Users/radiant/Desktop/RXinDexer/scripts/migrate_optimizer.py
# Script to migrate from RXinDexerOptimizer to the new modular structure

import os
import re
import sys
from pathlib import Path

# Files to search and update
TARGET_DIRS = [
    "/Users/radiant/Desktop/RXinDexer/src",
    "/Users/radiant/Desktop/RXinDexer/scripts",
    "/Users/radiant/Desktop/RXinDexer/sync"
]

# Patterns to search for
OLD_IMPORT_PATTERN = re.compile(r'^from\s+targeted_optimization\s+import\s+RXinDexerOptimizer', re.MULTILINE)
OLD_CLASS_PATTERN = re.compile(r'RXinDexerOptimizer\(')

# New imports
NEW_IMPORTS = """from rxindexer.optimization import (
    DatabaseOptimizer,
    BulkProcessor,
    CacheManager,
    PerformanceMonitor
)"""

def find_files_with_pattern(directory, pattern):
    """Find files containing a specific pattern."""
    matched_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                        if pattern.search(content):
                            matched_files.append(filepath)
                except Exception as e:
                    print(f"Error reading {filepath}: {e}")
    return matched_files

def update_file(filepath):
    """Update a single file to use the new imports and structure."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        
        # Replace imports
        content = OLD_IMPORT_PATTERN.sub(NEW_IMPORTS, content)
        
        # Replace class instantiation
        content = content.replace(
            'RXinDexerOptimizer()',
            'DatabaseOptimizer(), BulkProcessor(), CacheManager(), PerformanceMonitor()'
        )
        
        # If the file was modified, write the changes
        if content != original_content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
            
    except Exception as e:
        print(f"Error updating {filepath}: {e}")
    
    return False

def main():
    """Main function to run the migration."""
    print("Starting migration from RXinDexerOptimizer to new modular structure...")
    
    # Find all files that need updating
    files_to_update = []
    for directory in TARGET_DIRS:
        if os.path.exists(directory):
            files_to_update.extend(find_files_with_pattern(directory, OLD_IMPORT_PATTERN))
    
    if not files_to_update:
        print("No files found that need updating.")
        return
    
    print(f"Found {len(files_to_update)} files that need updating:")
    for filepath in files_to_update:
        print(f"- {filepath}")
    
    # Ask for confirmation
    response = input("\nDo you want to update these files? (y/n): ")
    if response.lower() != 'y':
        print("Migration cancelled.")
        return
    
    # Update files
    updated_count = 0
    for filepath in files_to_update:
        print(f"Updating {filepath}...")
        if update_file(filepath):
            updated_count += 1
    
    print(f"\nMigration complete. Updated {updated_count} files.")
    print("\nPlease review the changes and test your application thoroughly.")
    print("See MIGRATION_GUIDE.md for more information on the new structure.")

if __name__ == "__main__":
    main()
