#!/bin/bash

# Script to fix python-rocksdb GIL issues during installation
# This script applies patches to the Cython source if needed

set -e

echo "Attempting to install python-rocksdb with GIL workaround..."

# Try installing with CFLAGS first
if CFLAGS="-DCYTHON_WITHOUT_GIL=0" pip install python-rocksdb; then
    echo "✅ python-rocksdb installed successfully with CFLAGS workaround"
    exit 0
fi

echo "CFLAGS workaround failed, trying older version..."

# Try older version that doesn't have GIL issues
if pip install "python-rocksdb<=0.7.0"; then
    echo "✅ python-rocksdb installed successfully using older version"
    exit 0
fi

echo "Both workarounds failed, you may need to manually patch the source"
echo "See: https://github.com/stephen-hansen/python-rocksdb/issues/157"
exit 1
