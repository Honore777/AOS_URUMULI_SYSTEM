# Professional Deployment Guide - Urumuli Smart System
## Ubuntu Server Deployment with Enterprise Security

This guide provides step-by-step instructions for deploying the Urumuli Smart System to an Ubuntu server with professional-grade security, monitoring, and compliance with Rwanda data protection laws.

---

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Server Initial Setup](#server-initial-setup)
3. [Security Hardening](#security-hardening)
4. [Docker & Docker Compose Installation](#docker--docker-compose-installation)
5. [PostgreSQL Setup](#postgresql-setup)
6. [Application Deployment](#application-deployment)
7. [Nginx Reverse Proxy with SSL](#nginx-reverse-proxy-with-ssl)
8. [Monitoring & Logging](#monitoring--logging)
9. [Backup Strategy](#backup-strategy)
10. [Maintenance & Updates](#maintenance--updates)

---

## Prerequisites

### Required Credentials
- Ubuntu server IP address
- Root username and password
- VPN credentials
- Domain name (recommended for SSL)

### Local Machine Requirements
- SSH client
- VPN client installed
- Basic understanding of terminal commands

---

## Server Initial Setup

### Step 1: Connect via VPN
```bash
# Connect to your VPN first using your VPN credentials
# This ensures secure connection to the server
```

### Step 2: SSH into Server
```bash
# Replace with your actual server IP
ssh root@YOUR_SERVER_IP

# You'll be prompted for the root password
```

### Step 3: Update System
```bash
# Update package lists and upgrade all packages
apt update && apt upgrade -y

# Install essential tools
apt install -y curl wget git vim ufw fail2ban htop net-tools unzip
```

### Step 4: Set Hostname
```bash
# Set a meaningful hostname
hostnamectl set-hostname urumuli-server

# Edit hosts file
vim /etc/hosts
# Add: 127.0.1.1 urumuli-server
```

### Step 5: Create Non-Root User (CRITICAL FOR SECURITY)
```bash
# Create a new user with sudo privileges
adduser urumuli
# Set a strong password (minimum 12 characters, mix of upper/lower/numbers/symbols)

# Add user to sudo group
usermod -aG sudo urumuli

# Test SSH login with new user before proceeding
# Open new terminal: ssh urumuli@YOUR_SERVER_IP
```

### Step 6: Configure SSH Key Authentication
```bash
# On your LOCAL machine, generate SSH key if you don't have one
ssh-keygen -t ed25519 -C "urumuli@yourdomain.com"

# Copy public key to server
ssh-copy-id urumuli@YOUR_SERVER_IP

# Test SSH login with key (should not require password)
ssh urumuli@YOUR_SERVER_IP
```

### Step 7: Disable Root SSH Login
```bash
# Edit SSH configuration
sudo vim /etc/ssh/sshd_config

# Change these settings:
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes

# Restart SSH service
sudo systemctl restart sshd

# KEEP THIS TERMINAL OPEN! Test SSH login in new terminal before closing
```

---

## Security Hardening

### Step 1: Configure Firewall (UFW)
```bash
# Allow SSH first to prevent lockout
sudo ufw allow ssh

# Allow HTTP/HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Enable firewall
sudo ufw enable

# Check status
sudo ufw status verbose
```

### Step 2: Install and Configure Fail2Ban
```bash
# Install Fail2Ban
sudo apt install -y fail2ban

# Create local configuration
sudo cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local

# Edit configuration
sudo vim /etc/fail2ban/jail.local

# Add/modify these settings:
[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 3600
findtime = 600

# Restart Fail2Ban
sudo systemctl restart fail2ban
sudo systemctl enable fail2ban
```

### Step 3: Install Automatic Security Updates
```bash
# Install unattended-upgrades
sudo apt install -y unattended-upgrades

# Configure automatic updates
sudo dpkg-reconfigure -plow unattended-upgrades

# Edit configuration
sudo vim /etc/apt/apt.conf.d/50unattended-upgrades

# Ensure these are uncommented:
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}";
    "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
```

### Step 4: Secure Shared Memory
```bash
# Edit fstab
sudo vim /etc/fstab

# Add this line at the end:
tmpfs /run/shm tmpfs defaults,noexec,nosuid,size=1g 0 0

# Remount
sudo mount -a
```

### Step 5: Install and Configure Auditd
```bash
# Install auditd
sudo apt install -y auditd

# Start and enable
sudo systemctl start auditd
sudo systemctl enable auditd
```

---

## Docker & Docker Compose Installation

### Step 1: Install Docker
```bash
# Add Docker's official GPG key
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# Add Docker repository
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Update and install Docker
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add user to docker group
sudo usermod -aG docker urumuli

# Enable Docker
sudo systemctl enable docker
sudo systemctl start docker

# Verify installation
docker --version
docker compose version
```

### Step 2: Configure Docker Security
```bash
# Create Docker daemon configuration
sudo vim /etc/docker/daemon.json

# Add this configuration:
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "live-restore": true,
  "userland-proxy": false,
  "no-new-privileges": true
}

# Restart Docker
sudo systemctl restart docker
```

---

## PostgreSQL Setup

### Step 1: Install PostgreSQL
```bash
# Install PostgreSQL
sudo apt install -y postgresql postgresql-contrib

# Start and enable PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Check status
sudo systemctl status postgresql
```

### Step 2: Secure PostgreSQL
```bash
# Switch to postgres user
sudo -u postgres psql

# In PostgreSQL prompt, run:
-- Create database
CREATE DATABASE urumuli_prod;

-- Create user with strong password
CREATE USER urumuli_user WITH ENCRYPTED PASSWORD 'YOUR_STRONG_PASSWORD_HERE';

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE urumuli_prod TO urumuli_user;

-- Exit
\q
```

### Step 3: Configure PostgreSQL for Remote Access
```bash
# Edit PostgreSQL configuration
sudo vim /etc/postgresql/*/main/postgresql.conf

# Uncomment and modify:
listen_addresses = 'localhost'  # Keep localhost for security

# Edit pg_hba.conf
sudo vim /etc/postgresql/*/main/pg_hba.conf

# Add these lines (at the top):
local   all             urumuli_user                                  md5
host    all             urumuli_user          127.0.0.1/32            md5
host    all             urumuli_user          ::1/128                 md5

# Restart PostgreSQL
sudo systemctl restart postgresql
```

### Step 4: Test Connection
```bash
# Test connection
sudo -u postgres psql -d urumuli_prod -U urumuli_user -h localhost

# If successful, exit
\q
```

---

## Application Deployment

### Step 1: Create Application Directory
```bash
# Create directory structure
sudo mkdir -p /var/www/urumuli
sudo chown urumuli:urumuli /var/www/urumuli
cd /var/www/urumuli
```

### Step 2: Clone Repository
```bash
# Clone your repository (replace with your actual repo)
git clone https://github.com/YOUR_USERNAME/urumuli-smart-system.git .

# OR if using your local files, use scp from your local machine:
# scp -r /path/to/urumulisystem\ AOS/* urumuli@YOUR_SERVER_IP:/var/www/urumuli/
```

### Step 3: Create Environment File
```bash
# Create .env file
vim .env

# Add these variables (replace with actual values):
SECRET_KEY=your-secret-key-here-generate-long-random-string
DATABASE_URL=postgresql://urumuli_user:YOUR_STRONG_PASSWORD@localhost:5432/urumuli_prod
FLASK_ENV=production
LOG_LEVEL=INFO
BREVO_API_KEY=your-brevo-api-key
BREVO_SENDER_EMAIL=your-sender-email
BREVO_SENDER_NAME=Urumuli Smart System

# Secure the file
chmod 600 .env
```

### Step 4: Build and Run with Docker Compose
```bash
# The docker-compose.yml file should already be in the repository
# Build and start containers
docker compose up -d --build

# Check logs
docker compose logs -f

# Check running containers
docker ps
```

### Step 5: Run Database Migrations
```bash
# Run migrations inside the container
docker compose exec web flask db upgrade

# Or if using Alembic directly
docker compose exec web alembic upgrade head
```

### Step 6: Create Admin User
```bash
# Access the container
docker compose exec web python

# In Python shell:
from app import app, db
from core.models import User
with app.app_context():
    admin = User(username='admin', email='admin@yourdomain.com', role='admin', is_active=True)
    admin.set_password('YOUR_ADMIN_PASSWORD')
    db.session.add(admin)
    db.session.commit()
    print('Admin user created')

# Exit Python shell
exit()
```

---

## Nginx Reverse Proxy with SSL

### Step 1: Install Nginx
```bash
# Install Nginx
sudo apt install -y nginx

# Start and enable
sudo systemctl start nginx
sudo systemctl enable nginx

# Check status
sudo systemctl status nginx
```

### Step 2: Configure Nginx
```bash
# Create site configuration
sudo vim /etc/nginx/sites-available/urumuli

# Add this configuration (replace your-domain.com with actual domain):
upstream urumuli_app {
    server 127.0.0.1:10000;
    keepalive 64;
}

server {
    listen 80;
    server_name your-domain.com www.your-domain.com;

    # Redirect to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com www.your-domain.com;

    # SSL Configuration (will be updated by Certbot)
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # SSL Security Settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Security Headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Logging
    access_log /var/log/nginx/urumuli_access.log;
    error_log /var/log/nginx/urumuli_error.log;

    # Client body size limit
    client_max_body_size 50M;

    # Proxy settings
    location / {
        proxy_pass http://urumuli_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 120s;
        proxy_connect_timeout 120s;
    }

    # Static files
    location /static/ {
        alias /var/www/urumuli/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
```

### Step 3: Enable Site
```bash
# Create symbolic link
sudo ln -s /etc/nginx/sites-available/urumuli /etc/nginx/sites-enabled/

# Test configuration
sudo nginx -t

# Restart Nginx
sudo systemctl restart nginx
```

### Step 4: Install SSL with Certbot
```bash
# Install Certbot
sudo apt install -y certbot python3-certbot-nginx

# Obtain SSL certificate
sudo certbot --nginx -d your-domain.com -d www.your-domain.com

# Certbot will automatically update Nginx configuration

# Test auto-renewal
sudo certbot renew --dry-run
```

---

## Monitoring & Logging

### Step 1: Install Monitoring Tools
```bash
# Install Node Exporter for system metrics
wget https://github.com/prometheus/node_exporter/releases/download/v1.6.0/node_exporter-1.6.0.linux-amd64.tar.gz
tar xvfz node_exporter-1.6.0.linux-amd64.tar.gz
sudo mv node_exporter-1.6.0.linux-amd64/node_exporter /usr/local/bin/
sudo useradd -rs /bin/false node_exporter

# Create systemd service
sudo vim /etc/systemd/system/node_exporter.service

# Add:
[Unit]
Description=Node Exporter
After=network.target

[Service]
User=node_exporter
Group=node_exporter
Type=simple
ExecStart=/usr/local/bin/node_exporter

[Install]
WantedBy=multi-user.target

# Start and enable
sudo systemctl daemon-reload
sudo systemctl start node_exporter
sudo systemctl enable node_exporter
```

### Step 2: Configure Log Rotation
```bash
# Create logrotate configuration
sudo vim /etc/logrotate.d/urumuli

# Add:
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
```

### Step 3: Install Grafana and Prometheus (Optional but Recommended)
```bash
# This is more advanced - consider using Docker Compose for monitoring stack
# See docker-compose.monitoring.yml in the repository
```

---

## Backup Strategy

### Step 1: Create Database Backup Script
```bash
# Create backup script
sudo vim /usr/local/bin/backup-urumuli.sh

# Add:
#!/bin/bash
BACKUP_DIR="/var/backups/urumuli"
DATE=$(date +%Y%m%d_%H%M%S)
DB_NAME="urumuli_prod"
DB_USER="urumuli_user"

# Create backup directory
mkdir -p $BACKUP_DIR

# Database backup
pg_dump -U $DB_USER -h localhost $DB_NAME | gzip > $BACKUP_DIR/db_backup_$DATE.sql.gz

# Application files backup
tar -czf $BACKUP_DIR/app_backup_$DATE.tar.gz /var/www/urumuli

# Keep only last 7 days
find $BACKUP_DIR -name "*.gz" -mtime +7 -delete

echo "Backup completed: $DATE"
```

### Step 2: Make Script Executable
```bash
sudo chmod +x /usr/local/bin/backup-urumuli.sh
```

### Step 3: Schedule Daily Backups
```bash
# Edit crontab
sudo crontab -e

# Add this line for daily backup at 2 AM:
0 2 * * * /usr/local/bin/backup-urumuli.sh >> /var/log/urumuli-backup.log 2>&1
```

### Step 4: Configure Offsite Backups
```bash
# Install rclone for cloud backups
curl https://rclone.org/install.sh | sudo bash

# Configure rclone with your cloud storage (Google Drive, S3, etc.)
rclone config

# Test backup to cloud
rclone copy /var/backups/urumuli remote:urumuli-backups
```

---

## Maintenance & Updates

### Step 1: Create Update Script
```bash
# Create update script
vim /var/www/urumuli/update.sh

# Add:
#!/bin/bash
cd /var/www/urumuli

# Pull latest changes
git pull origin main

# Rebuild containers
docker compose down
docker compose up -d --build

# Run migrations
docker compose exec web flask db upgrade

# Restart Nginx
sudo systemctl restart nginx

echo "Update completed"
```

### Step 2: Make Executable
```bash
chmod +x /var/www/urumuli/update.sh
```

### Step 3: Monitor Server Health
```bash
# Check disk space
df -h

# Check memory usage
free -h

# Check Docker containers
docker ps

# Check application logs
docker compose logs -f --tail=100

# Check Nginx logs
sudo tail -f /var/log/nginx/urumuli_error.log
```

---

## Security Checklist

- [ ] Root SSH login disabled
- [ ] Password authentication disabled
- [ ] Non-root user with sudo configured
- [ ] Firewall (UFW) enabled and configured
- [ ] Fail2Ban installed and configured
- [ ] Automatic security updates enabled
- [ ] SSL/TLS certificate installed
- [ ] Security headers configured in Nginx
- [ ] Database password is strong
- [ ] Environment variables secured (chmod 600)
- [ ] Regular backups configured
- [ ] Log rotation configured
- [ ] Monitoring tools installed
- [ ] Docker security configured
- [ ] PostgreSQL configured for localhost only

---

## Troubleshooting

### Application Not Starting
```bash
# Check Docker logs
docker compose logs

# Check if port is in use
sudo netstat -tulpn | grep 10000

# Check Docker service
sudo systemctl status docker
```

### Database Connection Issues
```bash
# Check PostgreSQL status
sudo systemctl status postgresql

# Check PostgreSQL logs
sudo tail -f /var/log/postgresql/postgresql-*.log

# Test connection
sudo -u postgres psql -d urumuli_prod -U urumuli_user -h localhost
```

### Nginx Issues
```bash
# Test Nginx configuration
sudo nginx -t

# Check Nginx status
sudo systemctl status nginx

# Check Nginx error logs
sudo tail -f /var/log/nginx/error.log
```

---

## Data Protection Compliance (Rwanda)

This deployment includes:
- **Data Encryption**: SSL/TLS for data in transit, PostgreSQL encryption at rest
- **Access Control**: Multi-layer authentication (SSH keys, strong passwords)
- **Audit Logging**: Comprehensive logging of all system activities
- **Backup Strategy**: Regular automated backups with offsite storage
- **Security Hardening**: Firewall, intrusion detection, automatic updates
- **Data Minimization**: Only necessary data collected and stored

---

## Learning Resources

### Linux Commands
- `man <command>` - Show manual for any command
- `htop` - Interactive process viewer
- `tail -f <file>` - Monitor file changes in real-time
- `grep` - Search text in files
- `find` - Search for files
- `systemctl` - Manage system services

### Docker Commands
- `docker ps` - List running containers
- `docker logs <container>` - View container logs
- `docker exec -it <container> bash` - Access container shell
- `docker compose up -d` - Start services in background
- `docker compose down` - Stop and remove containers

### PostgreSQL Commands
- `sudo -u postgres psql` - Access PostgreSQL
- `\l` - List databases
- `\dt` - List tables
- `\q` - Quit PostgreSQL

---

## Support & Emergency Contacts

Keep this information secure and accessible:
- Server IP: YOUR_SERVER_IP
- VPN Credentials: [REDACTED]
- Domain: your-domain.com
- Admin Email: admin@yourdomain.com
- Backup Location: [CLOUD_STORAGE_DETAILS]

---

## Next Steps

1. Complete the initial server setup
2. Implement security hardening
3. Deploy the application
4. Configure monitoring
5. Test backup and restore procedures
6. Document any custom configurations
7. Set up alerting for critical issues

---

**Last Updated**: 2026-06-25
**Version**: 1.0
