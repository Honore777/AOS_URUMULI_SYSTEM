#!/bin/bash
# Backup Script for Urumuli Smart System
# This script creates backups of database and application files

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

# Configuration
BACKUP_DIR="/var/backups/urumuli"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=30

# Load environment variables
if [ -f /var/www/urumuli/.env ]; then
    export $(cat /var/www/urumuli/.env | grep -v '^#' | xargs)
else
    print_error ".env file not found!"
    exit 1
fi

echo "=========================================="
echo "Starting Backup - $DATE"
echo "=========================================="

# Create backup directory
mkdir -p $BACKUP_DIR
print_success "Backup directory created"

# Backup PostgreSQL database
echo "Backing up PostgreSQL database..."
docker exec urumuli_db pg_dump -U ${POSTGRES_USER} ${POSTGRES_DB} | gzip > $BACKUP_DIR/db_backup_$DATE.sql.gz
print_success "Database backup completed"

# Backup application files
echo "Backing up application files..."
tar -czf $BACKUP_DIR/app_backup_$DATE.tar.gz -C /var/www urumuli
print_success "Application files backup completed"

# Backup Docker volumes
echo "Backing up Docker volumes..."
docker run --rm -v urumuli_postgres_data:/data -v $BACKUP_DIR:/backup alpine tar -czf /backup/postgres_volume_$DATE.tar.gz -C /data .
docker run --rm -v urumuli_redis_data:/data -v $BACKUP_DIR:/backup alpine tar -czf /backup/redis_volume_$DATE.tar.gz -C /data .
print_success "Docker volumes backup completed"

# Backup Nginx configuration
echo "Backing up Nginx configuration..."
tar -czf $BACKUP_DIR/nginx_config_$DATE.tar.gz -C /etc nginx
print_success "Nginx configuration backup completed"

# Generate backup manifest
echo "Generating backup manifest..."
cat > $BACKUP_DIR/manifest_$DATE.txt << EOF
Backup Date: $DATE
Database: db_backup_$DATE.sql.gz
Application: app_backup_$DATE.tar.gz
PostgreSQL Volume: postgres_volume_$DATE.tar.gz
Redis Volume: redis_volume_$DATE.tar.gz
Nginx Config: nginx_config_$DATE.tar.gz
Database Size: $(du -h $BACKUP_DIR/db_backup_$DATE.sql.gz | cut -f1)
Application Size: $(du -h $BACKUP_DIR/app_backup_$DATE.tar.gz | cut -f1)
Total Size: $(du -sh $BACKUP_DIR | cut -f1)
EOF
print_success "Backup manifest generated"

# Clean up old backups
echo "Cleaning up old backups (older than $RETENTION_DAYS days)..."
find $BACKUP_DIR -name "*.gz" -mtime +$RETENTION_DAYS -delete
find $BACKUP_DIR -name "*.txt" -mtime +$RETENTION_DAYS -delete
print_success "Old backups cleaned up"

# Calculate backup size
BACKUP_SIZE=$(du -sh $BACKUP_DIR | cut -f1)

echo ""
echo "=========================================="
print_success "Backup completed successfully!"
echo "=========================================="
echo "Backup Location: $BACKUP_DIR"
echo "Total Size: $BACKUP_SIZE"
echo "Retention: $RETENTION_DAYS days"
echo ""

# Optional: Upload to cloud storage (configure rclone first)
if command -v rclone &> /dev/null; then
    echo "Uploading backup to cloud storage..."
    rclone copy $BACKUP_DIR remote:urumuli-backups/$DATE
    print_success "Cloud backup completed"
else
    print_warning "rclone not configured for cloud backup"
fi

# Send notification (configure mail or webhook)
# Example: mail -s "Urumuli Backup Completed" admin@yourdomain.com < $BACKUP_DIR/manifest_$DATE.txt

echo "Backup process finished at $(date)"
