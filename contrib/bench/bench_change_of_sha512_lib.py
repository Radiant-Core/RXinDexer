import timeit
from Cryptodome.Hash import SHA512
import hashlib
import os

# Data to hash
data = b"The quick brown fox jumps over the lazy dog"

# Old Implementation (with Crypto.Hash.SHA512)
def old_sha512_256(x):
    firsthash = SHA512.new(truncate="256")
    firsthash.update(x)
    return firsthash.digest()

def old_double_sha512_256(x):
    firsthash = SHA512.new(truncate="256")
    firsthash.update(x)
    secondhash = SHA512.new(truncate="256")
    secondhash.update(firsthash.digest())
    return secondhash.digest()

# New Implementation (with hashlib, using sha512_256)
def new_sha512_256(x):
    firsthash = hashlib.new('sha512_256')
    firsthash.update(x)
    return firsthash.digest()

def new_double_sha512_256(x):
    firsthash = hashlib.new('sha512_256')
    firsthash.update(x)
    secondhash = hashlib.new('sha512_256')
    secondhash.update(firsthash.digest())
    return secondhash.digest()

# Function to test correctness of old vs new implementation across random inputs
def check_correctness():
    for _ in range(100):  # Test with 100 random inputs
        random_input = os.urandom(64)  # Generate 64 bytes of random input

        # Compare the results for sha512_256
        assert old_sha512_256(random_input) == new_sha512_256(random_input), \
            f"Mismatch for sha512_256 with input {random_input.hex()}"

        # Compare the results for double_sha512_256
        assert old_double_sha512_256(random_input) == new_double_sha512_256(random_input), \
            f"Mismatch for double_sha512_256 with input {random_input.hex()}"

    print("All correctness tests passed.")

# Benchmarking
def benchmark():
    # Run correctness tests first
    check_correctness()

    # Number of iterations for benchmarking
    iterations = 100000

    # Benchmark old implementation of sha512_256
    old_sha512_256_time = timeit.timeit(lambda: old_sha512_256(data), number=iterations)
    print(f"Old sha512_256 time: {old_sha512_256_time:.6f} seconds for {iterations} iterations")

    # Benchmark new implementation of sha512_256
    new_sha512_256_time = timeit.timeit(lambda: new_sha512_256(data), number=iterations)
    print(f"New sha512_256 time: {new_sha512_256_time:.6f} seconds for {iterations} iterations")

    # Benchmark old implementation of double_sha512_256
    old_double_sha512_256_time = timeit.timeit(lambda: old_double_sha512_256(data), number=iterations)
    print(f"Old double_sha512_256 time: {old_double_sha512_256_time:.6f} seconds for {iterations} iterations")

    # Benchmark new implementation of double_sha512_256
    new_double_sha512_256_time = timeit.timeit(lambda: new_double_sha512_256(data), number=iterations)
    print(f"New double_sha512_256 time: {new_double_sha512_256_time:.6f} seconds for {iterations} iterations")


if __name__ == "__main__":
    benchmark()
