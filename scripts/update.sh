#!/bin/bash
# Update Script for Urumuli Smart System
# This script handles application updates with zero-downtime deployment

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

echo "=========================================="
echo "Urumuli Smart System Update"
echo "=========================================="
echo ""

# Check if running as non-root
if [ "$EUID" -eq 0 ]; then 
    print_error "Please run as non-root user"
    exit 1
fi

# Check if in correct directory
if [ ! -f "app.py" ]; then
    print_error "Please run this script from the project root directory"
    exit 1
fi

# Step 1: Create backup before update
print_info "Step 1: Creating pre-update backup..."
./scripts/backup.sh
print_success "Pre-update backup completed"

# Step 2: Pull latest changes
print_info "Step 2: Pulling latest changes from repository..."
git fetch origin
git status

# Ask for confirmation
print_warning "This will update your application to the latest version"
read -p "Do you want to continue? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_info "Update cancelled"
    exit 0
fi

git pull origin main
print_success "Latest changes pulled"

# Step 3: Check for database migrations
print_info "Step 3: Checking for database migrations..."
if [ -f "alembic.ini" ]; then
    print_info "Running database migrations..."
    if docker compose version &> /dev/null; then
        DOCKER_COMPOSE="docker compose"
    else
        DOCKER_COMPOSE="docker-compose"
    fi
    $DOCKER_COMPOSE exec -T web flask db upgrade
    print_success "Database migrations completed"
else
    print_info "No database migrations needed"
fi

# Step 4: Build new Docker image
print_info "Step 4: Building new Docker image..."
$DOCKER_COMPOSE build web
print_success "New Docker image built"

# Step 5: Zero-downtime deployment
print_info "Step 5: Performing zero-downtime deployment..."

# Start new container alongside old one
$DOCKER_COMPOSE up -d --no-deps --scale web=2 web

# Wait for new container to be healthy
sleep 10

# Check if new container is healthy
NEW_CONTAINER=$($DOCKER_COMPOSE ps -q web | tail -n 1)
if docker inspect $NEW_CONTAINER | grep -q '"Status": "running"'; then
    print_success "New container is running"
    
    # Stop old container
    $DOCKER_COMPOSE up -d --no-deps --scale web=1 web
    print_success "Old container stopped"
else
    print_error "New container failed to start. Rolling back..."
    $DOCKER_COMPOSE up -d --no-deps --scale web=1 web
    exit 1
fi

# Step 6: Update other services if needed
print_info "Step 6: Checking for other service updates..."
$DOCKER_COMPOSE pull
$DOCKER_COMPOSE up -d
print_success "All services updated"

# Step 7: Cleanup
print_info "Step 7: Cleaning up old Docker images..."
docker image prune -f
print_success "Cleanup completed"

# Step 8: Verify deployment
print_info "Step 8: Verifying deployment..."
$DOCKER_COMPOSE ps
$DOCKER_COMPOSE exec -T web python -c "from app import app; print('Application is healthy')"
print_success "Deployment verification completed"

echo ""
echo "=========================================="
print_success "Update completed successfully!"
echo "=========================================="
echo ""
print_info "What was updated:"
echo "  - Application code"
echo "  - Docker images"
echo "  - Database schema (if migrations existed)"
echo ""
print_warning "If you encounter any issues:"
echo "  - Check logs: docker compose logs -f"
echo "  - Rollback: git reset --hard HEAD~1 && ./scripts/update.sh"
echo ""
