#!/bin/bash
# Automated Deployment Script for Urumuli Smart System
# This script handles the complete deployment process

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
echo "Urumuli Smart System Deployment"
echo "=========================================="
echo ""

# Check if running as non-root
if [ "$EUID" -eq 0 ]; then 
    print_error "Please run as non-root user (use sudo only when needed)"
    exit 1
fi

# Check if in correct directory
if [ ! -f "app.py" ]; then
    print_error "Please run this script from the project root directory"
    exit 1
fi

# Step 1: Environment Setup
print_info "Step 1: Setting up environment..."
if [ ! -f .env ]; then
    print_warning ".env file not found. Creating from template..."
    cat > .env << EOF
# Application Configuration
SECRET_KEY=$(openssl rand -hex 32)
FLASK_ENV=production
LOG_LEVEL=INFO

# Database Configuration
POSTGRES_USER=urumuli_user
POSTGRES_PASSWORD=$(openssl rand -base64 32)
POSTGRES_DB=urumuli_prod
DATABASE_URL=postgresql://urumuli_user:\${POSTGRES_PASSWORD}@db:5432/urumuli_prod

# Redis Configuration
REDIS_PASSWORD=$(openssl rand -base64 32)

# Brevo Email Configuration
BREVO_API_KEY=your-brevo-api-key
BREVO_SENDER_EMAIL=noreply@yourdomain.com
BREVO_SENDER_NAME=Urumuli Smart System

# Admin User
ADMIN_PASSWORD=$(openssl rand -base64 16)

# Database Pool Configuration
SQLALCHEMY_POOL_SIZE=7
SQLALCHEMY_MAX_OVERFLOW=10
SQLALCHEMY_POOL_TIMEOUT=35
SQLALCHEMY_POOL_RECYCLE=400
EOF
    print_success ".env file created with secure defaults"
    print_warning "Please update .env with your actual configuration values"
    print_warning "IMPORTANT: Save these credentials securely!"
    exit 1
else
    print_success ".env file found"
fi

# Step 2: Create necessary directories
print_info "Step 2: Creating necessary directories..."
mkdir -p logs backups nginx/ssl static
print_success "Directories created"

# Step 3: Build Docker images
print_info "Step 3: Building Docker images..."
if docker compose version &> /dev/null; then
    DOCKER_COMPOSE="docker compose"
else
    DOCKER_COMPOSE="docker-compose"
fi

$DOCKER_COMPOSE build
print_success "Docker images built"

# Step 4: Start services
print_info "Step 4: Starting services..."
$DOCKER_COMPOSE up -d
print_success "Services started"

# Step 5: Wait for database to be ready
print_info "Step 5: Waiting for database to be ready..."
MAX_ATTEMPTS=30
ATTEMPT=0

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    if $DOCKER_COMPOSE exec -T db pg_isready -U urumuli_user > /dev/null 2>&1; then
        print_success "Database is ready"
        break
    fi
    ATTEMPT=$((ATTEMPT + 1))
    echo "Attempt $ATTEMPT/$MAX_ATTEMPTS..."
    sleep 2
done

if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
    print_error "Database did not become ready in time"
    exit 1
fi

# Step 6: Run database migrations
print_info "Step 6: Running database migrations..."
$DOCKER_COMPOSE exec -T web flask db upgrade
print_success "Database migrations completed"

# Step 7: Create admin user
print_info "Step 7: Creating admin user..."
$DOCKER_COMPOSE exec -T web python -c "
from app import app, db
from core.models import User
import os

with app.app_context():
    admin_exists = User.query.filter_by(username='admin').first()
    if not admin_exists:
        admin_password = os.getenv('ADMIN_PASSWORD', 'admin123')
        admin = User(username='admin', email='admin@urumuli.rw', role='admin', is_active=True)
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()
        print('Admin user created')
        print(f'Username: admin')
        print(f'Password: {admin_password}')
    else:
        print('Admin user already exists')
"
print_success "Admin user setup completed"

# Step 8: Setup SSL certificates (if domain is configured)
print_info "Step 8: Checking SSL configuration..."
if [ -f "nginx/ssl/fullchain.pem" ] && [ -f "nginx/ssl/privkey.pem" ]; then
    print_success "SSL certificates found"
else
    print_warning "SSL certificates not found"
    print_info "To set up SSL:"
    print_info "1. Point your domain to this server"
    print_info "2. Run: sudo certbot certonly --standalone -d yourdomain.com"
    print_info "3. Copy certificates to nginx/ssl/"
    print_info "4. Restart nginx container"
fi

# Step 9: Setup log rotation
print_info "Step 9: Setting up log rotation..."
if [ -w "/etc/logrotate.d" ]; then
    sudo tee /etc/logrotate.d/urumuli > /dev/null << EOF
/var/www/urumuli/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 urumuli urumuli
    sharedscripts
}
EOF
    print_success "Log rotation configured"
else
    print_warning "Could not configure log rotation (requires sudo)"
fi

# Step 10: Setup backup cron job
print_info "Step 10: Setting up backup cron job..."
(crontab -l 2>/dev/null; echo "0 2 * * * /var/www/urumuli/scripts/backup.sh >> /var/www/urumuli/logs/backup.log 2>&1") | crontab -
print_success "Backup cron job scheduled (daily at 2 AM)"

# Step 11: Verify deployment
print_info "Step 11: Verifying deployment..."
$DOCKER_COMPOSE ps
print_success "Deployment verification completed"

echo ""
echo "=========================================="
print_success "Deployment completed successfully!"
echo "=========================================="
echo ""
print_info "Application Status:"
echo "  - Web Application: Running on port 10000"
echo "  - Database: PostgreSQL container running"
echo "  - Redis: Redis container running"
echo "  - Nginx: Reverse proxy configured"
echo ""
print_warning "Next Steps:"
echo "  1. Configure SSL certificates for HTTPS"
echo "  2. Update admin password (check .env for ADMIN_PASSWORD)"
echo "  3. Configure your domain name in Nginx"
echo "  4. Set up monitoring and alerting"
echo "  5. Test backup and restore procedures"
echo ""
print_info "Useful Commands:"
echo "  - View logs: docker compose logs -f"
echo "  - Restart services: docker compose restart"
echo "  - Stop services: docker compose down"
echo "  - Update application: ./scripts/update.sh"
echo "  - Create backup: ./scripts/backup.sh"
echo ""
