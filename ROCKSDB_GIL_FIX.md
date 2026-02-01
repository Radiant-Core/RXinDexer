# RocksDB GIL Fix

## Problem

The CI lint workflow was failing due to Cython compilation errors in the `python-rocksdb` package. The errors were:

```
Converting to Python object not allowed without gil
Accessing Python attribute not allowed without gil
```

These errors occur in `rocksdb/_rocksdb.pyx` around lines 2365-2413 where Python API calls are made without holding the Global Interpreter Lock (GIL).

## Solution

The fix involves multiple approaches:

### 1. CI Workflow Updates

Updated `.github/workflows/ci.yml` to install `python-rocksdb` with GIL workarounds:

```bash
# Install python-rocksdb with GIL workaround
CFLAGS="-DCYTHON_WITHOUT_GIL=0" pip install python-rocksdb || pip install "python-rocksdb<=0.7.0"
```

This tries two approaches:
1. Compile with `CFLAGS="-DCYTHON_WITHOUT_GIL=0"` to disable GIL-free optimizations
2. Fall back to an older version (<=0.7.0) that doesn't have the GIL issues

### 2. Setup Scripts

- `setup-rocksdb.sh`: Comprehensive setup script for local development
- `scripts/fix-rocksdb-gil.sh`: Standalone script for fixing rocksdb installation

### 3. Usage

For local development:
```bash
./setup-rocksdb.sh
```

For CI/CD: The workflow automatically handles the installation.

## Alternative Solutions

If the above solutions don't work, consider:

1. **Use pre-compiled wheels**: Some platforms have pre-compiled wheels that avoid compilation
2. **Use alternative package**: Consider using `plyvel` (LevelDB) instead of RocksDB if suitable
3. **Manual patching**: Download the source and manually add `with gil:` blocks in the Cython code

## Root Cause

The issue is in the `python-rocksdb` package's Cython code where Python objects are accessed in `nogil` contexts. The proper fix would be to patch the upstream package to wrap Python interactions in `with gil:` blocks, but since this is a third-party dependency, we use workarounds during installation.
