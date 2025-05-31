#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/run_optimized.sh
# This script deploys the optimized RXinDexer stack on any test machine.
# It handles setup, configuration, and deployment with all optimizations.

set -e

# Set working directory to the project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to check if Docker and Docker Compose are installed
check_prerequisites() {
    echo -e "${YELLOW}Checking prerequisites...${NC}"
    
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}Error: Docker is required but not installed.${NC}"
        echo "Please install Docker: https://docs.docker.com/get-docker/"
        exit 1
    fi
    
    if ! docker compose version &> /dev/null; then
        echo -e "${RED}Error: Docker Compose is required but not installed.${NC}"
        echo "Please install Docker Compose: https://docs.docker.com/compose/install/"
        exit 1
    fi
    
    echo -e "${GREEN}Prerequisites satisfied.${NC}"
}

# Function to create necessary directories
setup_directories() {
    echo -e "${YELLOW}Setting up directories...${NC}"
    
    # Create logs directory if it doesn't exist
    mkdir -p logs
    
    # Create docker directory if it doesn't exist
    mkdir -p docker/grafana/provisioning/datasources
    mkdir -p docker/grafana/provisioning/dashboards
    mkdir -p docker/prometheus
    
    echo -e "${GREEN}Directories set up successfully.${NC}"
}

# Function to ensure all configuration files are in place
setup_config_files() {
    echo -e "${YELLOW}Setting up configuration files...${NC}"
    
    # Check if prometheus.yml exists, create if not
    if [ ! -f docker/prometheus/prometheus.yml ]; then
        cat > docker/prometheus/prometheus.yml << EOF
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
  
  - job_name: 'rxindexer'
    static_configs:
      - targets: ['api:8000']
EOF
    fi
    
    # Check if Grafana datasource config exists, create if not
    if [ ! -f docker/grafana/provisioning/datasources/datasource.yml ]; then
        cat > docker/grafana/provisioning/datasources/datasource.yml << EOF
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
EOF
    fi
    
    echo -e "${GREEN}Configuration files are ready.${NC}"
}

# Function to apply database optimizations
apply_db_optimizations() {
    echo -e "${YELLOW}Applying database optimizations...${NC}"
    
    # Check if the database is running
    if ! docker ps | grep -q "rxindexer-db"; then
        echo -e "${RED}Error: Database container is not running.${NC}"
        echo "Start the stack first with: $0 start"
        return 1
    fi
    
    # Run the optimizations SQL file
    echo -e "${YELLOW}Running complete_db_optimization.sql...${NC}"
    docker exec rxindexer-db psql -U postgres -d rxindexer -f /docker-entrypoint-initdb.d/01-init-optimizations.sql
    
    # Verify optimizations were applied
    echo -e "${YELLOW}Verifying optimizations...${NC}"
    docker exec rxindexer-db psql -U postgres -d rxindexer -c "SHOW work_mem;"
    docker exec rxindexer-db psql -U postgres -d rxindexer -c "SHOW maintenance_work_mem;"
    
    echo -e "${GREEN}Database optimizations applied successfully.${NC}"
    return 0
}

# Function to run the optimized stack
run_stack() {
    echo -e "${YELLOW}Starting the optimized RXinDexer stack...${NC}"
    
    # Ensure any old containers are stopped and removed first
    echo -e "${YELLOW}Stopping any existing containers...${NC}"
    docker compose -f optimized-docker-compose.yml down --remove-orphans
    
    # Remove any old container images to ensure fresh build
    echo -e "${YELLOW}Cleaning up old images...${NC}"
    docker compose -f optimized-docker-compose.yml rm -f
    
    # Pull latest images
    echo -e "${YELLOW}Pulling latest images...${NC}"
    docker compose -f optimized-docker-compose.yml pull
    
    # Copy the database optimization SQL to the right location
    echo -e "${YELLOW}Preparing database optimization files...${NC}"
    cp complete_db_optimization.sql docker/postgresql-init.sql
    
    # Build and start the stack with fresh images
    echo -e "${YELLOW}Building and starting services...${NC}"
    docker compose -f optimized-docker-compose.yml up -d --build
    
    echo -e "${GREEN}RXinDexer stack is now running!${NC}"
    echo -e "${GREEN}API is available at: http://localhost:8000${NC}"
    echo -e "${GREEN}Monitoring dashboard is available at: http://localhost:3000${NC}"
    echo -e "${GREEN}(Default Grafana login: admin/admin)${NC}"
}

# Function to display logs of a specific service
show_logs() {
    if [ -z "$1" ]; then
        echo -e "${RED}Error: Please specify a service (api, indexer, db, redis, radiant)${NC}"
        exit 1
    fi
    
    echo -e "${YELLOW}Showing logs for rxindexer-$1...${NC}"
    docker logs -f "rxindexer-$1"
}

# Function to stop the stack
stop_stack() {
    echo -e "${YELLOW}Stopping the RXinDexer stack...${NC}"
    docker compose -f optimized-docker-compose.yml down
    echo -e "${GREEN}RXinDexer stack stopped.${NC}"
}

# Function to stop and remove all data
clean_stack() {
    echo -e "${YELLOW}Cleaning up the RXinDexer stack (this will remove all data)...${NC}"
    docker compose -f optimized-docker-compose.yml down -v
    echo -e "${GREEN}RXinDexer stack cleaned.${NC}"
}

# Function to display monitoring metrics
show_metrics() {
    echo -e "${YELLOW}Database metrics:${NC}"
    docker exec rxindexer-db psql -U postgres -d rxindexer -c "
        SELECT datname as database, numbackends as connections, 
               pg_size_pretty(pg_database_size(datname)) as size
        FROM pg_stat_database 
        WHERE datname = 'rxindexer'
    "
    
    echo -e "${YELLOW}Cache metrics:${NC}"
    docker exec rxindexer-redis redis-cli info | grep -E 'used_memory|connected_clients|keyspace'
    
    echo -e "${YELLOW}Sync performance metrics:${NC}"
    docker exec rxindexer-db psql -U postgres -d rxindexer -c "
        SELECT to_char(NOW() - query_start, 'HH24:MI:SS') as runtime, 
               state, query 
        FROM pg_stat_activity 
        WHERE query NOT LIKE '%pg_stat_activity%' 
        ORDER BY query_start ASC
    "
    
    echo -e "${YELLOW}Container resource usage:${NC}"
    docker stats --no-stream rxindexer-api rxindexer-indexer rxindexer-db rxindexer-redis
}

# Function to run the optimized sync module directly
run_optimized_sync() {
    echo -e "${YELLOW}Running optimized sync module...${NC}"
    
    # Check if the indexer container is running
    if ! docker ps | grep -q "rxindexer-indexer"; then
        echo -e "${RED}Error: Indexer container is not running.${NC}"
        echo "Start the stack first with: $0 start"
        return 1
    fi
    
    # Set the optimized environment variables
    docker exec -it rxindexer-indexer bash -c "export SYNC_BATCH_SIZE=5000 && \
        export SYNC_MAX_WORKERS=32 && \
        export UTXO_MAX_WORKERS=8 && \
        export BLOCK_PARALLEL_THRESHOLD=100 && \
        export PROGRESSIVE_SYNC=true && \
        export INITIAL_SYNC_MINIMAL=true && \
        export USE_REDIS_CACHE=true && \
        python -m src.sync.optimized_sync"
    
    echo -e "${GREEN}Optimized sync completed.${NC}"
}

# Main execution
case "$1" in
    start|up)
        check_prerequisites
        setup_directories
        setup_config_files
        run_stack
        ;;
    stop|down)
        stop_stack
        ;;
    logs)
        show_logs "$2"
        ;;
    clean)
        clean_stack
        ;;
    metrics)
        show_metrics
        ;;
    optimize-db)
        apply_db_optimizations
        ;;
    sync)
        run_optimized_sync
        ;;
    *)
        echo "RXinDexer Optimized Deployment"
        echo "=============================="
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  start, up     Start the optimized RXinDexer stack"
        echo "  stop, down    Stop the stack"
        echo "  logs SERVICE  Show logs for a specific service (api, indexer, db, redis, radiant)"
        echo "  clean         Stop and remove all containers and data"
        echo "  metrics       Show monitoring metrics"
        echo ""
        echo "Example:"
        echo "  $0 start      # Start the optimized stack"
        echo "  $0 logs db    # Show database logs"
        ;;
esac

exit 0
