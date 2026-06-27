#!/bin/bash
# Database Setup Script for Urumuli Smart System
# This script sets up PostgreSQL database and runs migrations

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
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

echo "=========================================="
echo "Database Setup for Urumuli Smart System"
echo "=========================================="

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
    print_success "Environment variables loaded"
else
    print_error ".env file not found!"
    exit 1
fi

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    print_error "Docker is not running. Please start Docker first."
    exit 1
fi

# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    print_error "docker-compose is not installed"
    exit 1
fi

# Use docker compose if available, otherwise docker-compose
if docker compose version &> /dev/null; then
    DOCKER_COMPOSE="docker compose"
else
    DOCKER_COMPOSE="docker-compose"
fi

# Start PostgreSQL container
echo "Starting PostgreSQL container..."
$DOCKER_COMPOSE up -d db
print_success "PostgreSQL container started"

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL to be ready..."
MAX_ATTEMPTS=30
ATTEMPT=0

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    if $DOCKER_COMPOSE exec -T db pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB} > /dev/null 2>&1; then
        print_success "PostgreSQL is ready"
        break
    fi
    ATTEMPT=$((ATTEMPT + 1))
    echo "Attempt $ATTEMPT/$MAX_ATTEMPTS..."
    sleep 2
done

if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
    print_error "PostgreSQL did not become ready in time"
    exit 1
fi

# Run database migrations
echo "Running database migrations..."
$DOCKER_COMPOSE run --rm web flask db upgrade
print_success "Database migrations completed"

# Create initial admin user if it doesn't exist
echo "Checking for admin user..."
ADMIN_EXISTS=$($DOCKER_COMPOSE exec -T db psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tAc "SELECT 1 FROM users WHERE username='admin'")

if [ -z "$ADMIN_EXISTS" ]; then
    echo "Creating admin user..."
    $DOCKER_COMPOSE run --rm web python -c "
from app import app, db
from core.models import User
import os

with app.app_context():
    admin_password = os.getenv('ADMIN_PASSWORD', 'admin123')
    admin = User(username='admin', email='admin@urumuli.rw', role='admin', is_active=True)
    admin.set_password(admin_password)
    db.session.add(admin)
    db.session.commit()
    print('Admin user created successfully')
    print(f'Username: admin')
    print(f'Password: {admin_password}')
    print('IMPORTANT: Change this password immediately after first login!')
"
    print_success "Admin user created"
else
    print_warning "Admin user already exists"
fi

# Verify database connection
echo "Verifying database connection..."
$DOCKER_COMPOSE exec -T db psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "SELECT version();"
print_success "Database connection verified"

# Show database statistics
echo "Database statistics:"
$DOCKER_COMPOSE exec -T db psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
"

echo ""
echo "=========================================="
print_success "Database setup completed successfully!"
echo "=========================================="
echo ""
print_warning "IMPORTANT:"
echo "  1. Change the default admin password immediately"
echo "  2. Configure regular database backups"
echo "  3. Monitor database performance"
echo ""
