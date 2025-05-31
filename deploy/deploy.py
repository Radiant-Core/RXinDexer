# /Users/radiant/Desktop/RXinDexer/deploy/deploy.py
# This file is a deployment script for RXinDexer that handles environment configuration and deployment.
# It generates environment-specific .env files and manages Docker containers based on configuration.

import os
import sys
import argparse
import subprocess
import shutil
import logging
from string import Template
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"deploy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger("rxindexer-deploy")

# Define paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY_DIR = os.path.join(BASE_DIR, "deploy")
ENV_TEMPLATE_PATH = os.path.join(DEPLOY_DIR, "env.template")
DOCKER_COMPOSE_PATH = os.path.join(BASE_DIR, "docker-compose.yml")

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Deploy RXinDexer to the specified environment")
    parser.add_argument(
        "environment", 
        choices=["development", "staging", "production"],
        help="Deployment environment"
    )
    parser.add_argument(
        "--build", 
        action="store_true", 
        help="Force rebuild of Docker images"
    )
    parser.add_argument(
        "--no-cache", 
        action="store_true", 
        help="Build without using cache"
    )
    parser.add_argument(
        "--config-only", 
        action="store_true", 
        help="Generate configuration files only without deploying"
    )
    parser.add_argument(
        "--stop", 
        action="store_true", 
        help="Stop the deployment instead of starting it"
    )
    parser.add_argument(
        "--backup-db", 
        action="store_true", 
        help="Backup database before deployment"
    )
    return parser.parse_args()

def read_env_file(file_path):
    """Read environment file and return a dictionary of key-value pairs."""
    env_vars = {}
    try:
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                key, value = line.split('=', 1)
                env_vars[key] = value
        return env_vars
    except Exception as e:
        logger.error(f"Error reading environment file {file_path}: {str(e)}")
        return {}

def generate_env_file(env_config, template_path, output_path):
    """Generate .env file from template and configuration."""
    try:
        with open(template_path, 'r') as f:
            template = Template(f.read())
        
        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Generate the .env file
        with open(output_path, 'w') as f:
            f.write(template.safe_substitute(env_config))
        
        logger.info(f"Generated environment file: {output_path}")
        return True
    except Exception as e:
        logger.error(f"Error generating environment file: {str(e)}")
        return False

def backup_database(env_config):
    """Backup the database if it exists."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"rxindexer_db_backup_{timestamp}.sql"
        backup_cmd = [
            "docker", "exec", "rxindexer_db",
            "pg_dump", "-U", env_config.get("DB_USER", "postgres"),
            "-d", env_config.get("DB_NAME", "rxindexer"),
            "-f", f"/var/lib/postgresql/data/backups/{backup_file}"
        ]
        
        logger.info(f"Backing up database to {backup_file}...")
        subprocess.run(backup_cmd, check=True)
        logger.info("Database backup completed successfully")
        return True
    except Exception as e:
        logger.error(f"Database backup failed: {str(e)}")
        return False

def run_docker_compose(env_config, args):
    """Run docker-compose with the appropriate commands."""
    try:
        # Set environment variables for docker-compose
        env = os.environ.copy()
        for key, value in env_config.items():
            env[key] = value
        
        # Determine docker-compose command
        cmd = ["docker-compose", "-f", DOCKER_COMPOSE_PATH]
        
        if args.stop:
            cmd.extend(["down"])
            logger.info("Stopping containers...")
        else:
            if args.build:
                build_cmd = cmd.copy()
                build_cmd.extend(["build"])
                if args.no_cache:
                    build_cmd.extend(["--no-cache"])
                logger.info("Building Docker images...")
                subprocess.run(build_cmd, env=env, check=True)
            
            cmd.extend(["up", "-d"])
            logger.info("Starting containers...")
        
        # Run the docker-compose command
        subprocess.run(cmd, env=env, check=True)
        
        if args.stop:
            logger.info("Containers stopped successfully")
        else:
            logger.info("Deployment completed successfully")
        
        return True
    except Exception as e:
        logger.error(f"Docker deployment failed: {str(e)}")
        return False

def main():
    """Main deployment function."""
    args = parse_args()
    
    # Get environment configuration
    env_config_path = os.path.join(DEPLOY_DIR, f"config.{args.environment}.env")
    if not os.path.exists(env_config_path):
        logger.error(f"Environment configuration file not found: {env_config_path}")
        return 1
    
    logger.info(f"Deploying RXinDexer to {args.environment} environment")
    
    # Read environment configuration
    env_config = read_env_file(env_config_path)
    if not env_config:
        logger.error("Failed to read environment configuration")
        return 1
    
    # Generate .env file
    env_output_path = os.path.join(BASE_DIR, ".env")
    if not generate_env_file(env_config, ENV_TEMPLATE_PATH, env_output_path):
        logger.error("Failed to generate .env file")
        return 1
    
    # Stop here if config-only option is set
    if args.config_only:
        logger.info("Configuration generated successfully. Exiting as requested.")
        return 0
    
    # Backup database if requested
    if args.backup_db and not args.stop:
        backup_result = backup_database(env_config)
        if not backup_result and not args.stop:
            logger.warning("Database backup failed, but continuing with deployment")
    
    # Deploy with docker-compose
    if not run_docker_compose(env_config, args):
        logger.error("Deployment failed")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
