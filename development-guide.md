# /Users/radiant/Desktop/RXinDexer/docs/development-guide.md
# This file provides guidelines and best practices for developing and contributing to RXinDexer.
# It includes setup instructions, testing procedures, and architecture overview.

# RXinDexer Development Guide

This guide provides comprehensive instructions for developers working on the RXinDexer codebase, covering local setup, testing, coding standards, and architectural patterns.

## Development Environment Setup

### Prerequisites

- Python 3.11+
- PostgreSQL 16
- Redis 7 (optional for local development)
- Radiant Node 1.2.0 (or access to a testnet node)

### Local Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Radiant-Core/RXinDexer.git
   cd RXinDexer
   ```

2. **Set up a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt  # Development dependencies
   ```

4. **Configure environment**:
   ```bash
   cp .env.example .env.dev
   # Edit .env.dev with your development settings
   ```

5. **Set up local database**:
   ```bash
   # Create a PostgreSQL database
   createdb rxindexer_dev
   
   # Initialize the database schema
   python -m src.db.init_db
   ```

### Development Environment Variables

Key variables for development:

```
# General settings
ENVIRONMENT=development
LOG_LEVEL=DEBUG

# Database settings
DATABASE_URL=postgresql://localhost/rxindexer_dev

# RPC settings (use testnet or local node)
RADIANT_RPC_URL=http://localhost:7332
RADIANT_RPC_USER=dev
RADIANT_RPC_PASSWORD=dev_password

# Sync parameters (smaller values for development)
SYNC_BATCH_SIZE=100
SYNC_MAX_WORKERS=4
UTXO_MAX_WORKERS=2

# Caching (optional for development)
ENABLE_CACHE=false
```

### Running the Development Server

```bash
# Start the FastAPI development server with auto-reload
uvicorn src.main:app --reload --port 8000

# In a separate terminal, run the sync process in development mode
python -m sync.rxindex_sync --initialize --continuous --interval 30
```

## Project Structure

The RXinDexer codebase follows a modular structure:

```
RXinDexer/
├── docs/                  # Documentation files
├── src/                   # Source code
│   ├── api/               # API endpoints and routes
│   │   ├── v1/            # API version 1 endpoints
│   │   └── main.py        # API entry point
│   ├── db/                # Database models and initialization
│   │   ├── functions/     # SQL functions
│   │   ├── models/        # SQLAlchemy models
│   │   └── init_db.py     # Database initialization
│   ├── models/            # Pydantic models
│   ├── sync/              # Blockchain sync components
│   │   ├── parser/        # Transaction and token parsers
│   │   ├── rpc_client.py  # RPC client for Radiant node
│   │   └── rpc_selector.py # RPC client selector
│   ├── utils/             # Utility functions
│   └── main.py            # Application entry point
├── tests/                 # Test suite
│   ├── unit/              # Unit tests
│   ├── integration/       # Integration tests
│   └── fixtures/          # Test fixtures
├── sync/                  # Consolidated sync scripts
├── docker/                # Docker configuration files
├── requirements.txt       # Project dependencies
├── requirements-dev.txt   # Development dependencies
└── .env.example           # Example environment variables
```

### Key Components

1. **API Layer** (`src/api/`):
   - FastAPI routes and endpoint definitions
   - Request validation and response models
   - Error handling and middleware

2. **Database Layer** (`src/db/`):
   - SQLAlchemy models representing database tables
   - Database initialization and migration scripts
   - SQL functions for complex operations

3. **Sync Layer** (`src/sync/`):
   - RPC client for communicating with Radiant node
   - Block and transaction parsers
   - Token metadata extractors
   - Chain reorganization handling

4. **Consolidated Sync** (`sync/`):
   - Efficient sync process for production use
   - Combines all indexing functionalities

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run specific test modules
pytest tests/unit/test_rpc_client.py

# Run with coverage
pytest --cov=src

# Generate coverage report
pytest --cov=src --cov-report=html
```

### Test Structure

1. **Unit Tests**:
   - Test individual functions and classes in isolation
   - Mock external dependencies
   - Located in `tests/unit/`

2. **Integration Tests**:
   - Test interactions between components
   - May require a test database
   - Located in `tests/integration/`

3. **API Tests**:
   - Test API endpoints using FastAPI's TestClient
   - Verify request/response patterns
   - Located in `tests/api/`

### Test Fixtures

Common test fixtures are defined in `tests/fixtures/`:

- Database fixtures with test data
- Mock RPC responses
- Sample transactions and blocks

## Development Workflow

### Feature Development Process

1. **Create a new branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Implement the feature**:
   - Write tests first (TDD approach)
   - Implement the feature
   - Ensure tests pass

3. **Code Review**:
   - Submit a pull request
   - Address review comments
   - Ensure CI pipeline passes

### Coding Standards

RXinDexer follows these coding standards:

1. **PEP 8** for Python code style:
   - Use 4 spaces for indentation
   - Maximum line length of 100 characters
   - Follow naming conventions:
     - `snake_case` for variables and functions
     - `PascalCase` for classes
     - `UPPER_CASE` for constants

2. **Type Annotations**:
   - Use type hints for all function parameters and return values
   - Example:
     ```python
     def get_address_balance(address: str) -> Dict[str, Any]:
         ...
     ```

3. **Docstrings**:
   - Use Google-style docstrings
   - Include parameters, return values, and examples
   - Example:
     ```python
     def get_address_balance(address: str) -> Dict[str, Any]:
         """Get the current balance for a specific address.
         
         Args:
             address: The Radiant address to query
             
         Returns:
             A dictionary containing RXD and token balances
             
         Raises:
             ValueError: If the address is invalid
         """
     ```

4. **Imports**:
   - Group imports in the following order:
     1. Standard library imports
     2. Third-party imports
     3. Local application imports
   - Sort alphabetically within each group

### Linting and Formatting

```bash
# Run black formatter
black src tests

# Run isort to sort imports
isort src tests

# Run flake8 linter
flake8 src tests

# Run mypy for type checking
mypy src
```

## Database Migrations

RXinDexer uses Alembic for database migrations:

```bash
# Create a new migration
alembic revision --autogenerate -m "Add new table"

# Apply migrations
alembic upgrade head

# Revert to a specific version
alembic downgrade <revision>
```

### Migration Best Practices

1. **Keep migrations small and focused**
2. **Test migrations on a copy of production data**
3. **Ensure backward compatibility where possible**
4. **Include both upgrade and downgrade paths**
5. **Add comments explaining complex migrations**

## Architecture and Design Patterns

### Core Design Principles

1. **Separation of Concerns**:
   - Clear boundaries between API, database, and sync components
   - Each module has a single responsibility

2. **Dependency Injection**:
   - Dependencies are explicitly passed to functions and classes
   - Makes testing easier by allowing components to be mocked

3. **Repository Pattern**:
   - Database access is abstracted through repository classes
   - Simplifies testing and ensures consistent data access patterns

4. **Service Layer**:
   - Business logic is contained in service classes
   - Services coordinate between repositories and external systems

### Database Design

1. **Table Structure**:
   - Normalized schema for efficiency
   - Use of indexes for query performance
   - JSONB for flexible metadata storage

2. **Database Functions**:
   - Complex operations implemented as PostgreSQL functions
   - Balances and aggregations done at the database level for efficiency

3. **Performance Considerations**:
   - Use of batch operations for bulk inserts/updates
   - Strategic use of materialized views for heavy analytics
   - Efficient query patterns to minimize database load

## API Design Patterns

1. **RESTful Principles**:
   - Resource-oriented endpoints
   - Appropriate HTTP methods (GET, POST, etc.)
   - Consistent response formats

2. **Versioning**:
   - All endpoints versioned under `/api/v1/`
   - Ensures backward compatibility

3. **Response Patterns**:
   - Consistent pagination structure
   - Standard error response format
   - Use of Pydantic models for validation

4. **Performance**:
   - Caching strategies for common queries
   - Asynchronous request handling
   - Response compression

## Performance Tuning for Development

1. **Database Indexing**:
   - Review explain plans for slow queries
   - Add indexes for common query patterns
   - Use partial indexes for specific query conditions

2. **Caching Strategies**:
   - Identify cacheable resources
   - Set appropriate TTL values
   - Implement cache invalidation patterns

3. **Query Optimization**:
   - Use database-specific features for optimization
   - Implement pagination for large result sets
   - Consider denormalization for read-heavy workloads

## Troubleshooting Development Issues

### Common Issues and Solutions

1. **Database Connection Problems**:
   - Check connection string in `.env.dev`
   - Verify PostgreSQL is running
   - Check database user permissions

2. **RPC Connection Issues**:
   - Verify Radiant node is running
   - Check RPC credentials
   - Ensure network connectivity

3. **Sync Process Errors**:
   - Check log files for specific errors
   - Verify database schema is up to date
   - Ensure adequate disk space

### Debugging Tips

1. **Enhanced Logging**:
   - Set `LOG_LEVEL=DEBUG` in `.env.dev`
   - Add strategic log statements to problematic code
   - Review logs for error patterns

2. **Database Debugging**:
   - Use `pgAdmin` or `psql` to inspect database state
   - Run queries directly to verify results
   - Check for database locks or conflicts

3. **API Debugging**:
   - Use Swagger UI at `/docs` to test endpoints
   - Examine request/response patterns
   - Check API logs for error details

## Contributing

### Pull Request Process

1. **Fork the repository** and create your feature branch
2. **Ensure all tests pass** (`pytest`)
3. **Update documentation** as needed
4. **Submit a pull request** with a clear description

### Code Review Guidelines

1. **Review Criteria**:
   - Code adheres to style guidelines
   - Tests are comprehensive
   - Documentation is updated
   - Performance considerations addressed

2. **Review Process**:
   - At least one approval required
   - CI pipeline must pass
   - No merge conflicts

## Resource Management

### Memory Considerations

1. **Connection Pooling**:
   - Use connection pools for database and Redis
   - Configure appropriate pool sizes
   - Monitor connection usage

2. **Large Dataset Handling**:
   - Use pagination for large result sets
   - Implement streaming responses where appropriate
   - Consider chunked processing for large sync operations

### CPU Optimization

1. **Parallelization**:
   - Use worker threads for CPU-bound tasks
   - Configure worker counts based on available cores
   - Monitor CPU usage during sync

## Documentation

### API Documentation

1. **OpenAPI Specification**:
   - Available at `/docs` (Swagger UI)
   - Comprehensive endpoint documentation
   - Request/response examples

2. **Code Documentation**:
   - Docstrings for all public functions and classes
   - Comments for complex logic
   - Architecture overview documents

## Support and Resources

- **GitHub Issues**: For bug reports and feature requests
- **Development Chat**: Available on Discord
- **Documentation**: Available in the `docs/` directory and online
