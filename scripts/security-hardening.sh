#!/bin/bash
# Security Hardening Script for Ubuntu Server
# This script implements enterprise-grade security measures
# Run with: sudo bash security-hardening.sh

set -e  # Exit on any error

echo "=========================================="
echo "Starting Security Hardening"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    print_error "Please run as root (use sudo)"
    exit 1
fi

# Update system
echo "Updating system packages..."
apt update && apt upgrade -y
print_success "System updated"

# Install security tools
echo "Installing security tools..."
apt install -y fail2ban ufw auditd rkhunter chkrootkit aide
print_success "Security tools installed"

# Configure UFW Firewall
echo "Configuring UFW Firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow http
ufw allow https
ufw --force enable
print_success "Firewall configured"

# Configure Fail2Ban
echo "Configuring Fail2Ban..."
cat > /etc/fail2ban/jail.local << EOF
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 3
destemail = admin@yourdomain.com
sendername = Fail2Ban
action = %(action_mwl)s

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 3600

[nginx-http-auth]
enabled = true
filter = nginx-http-auth
port = http,https
logpath = /var/log/nginx/error.log

[nginx-limit-req]
enabled = true
filter = nginx-limit-req
port = http,https
logpath = /var/log/nginx/error.log
EOF

systemctl restart fail2ban
systemctl enable fail2ban
print_success "Fail2Ban configured"

# Secure SSH Configuration
echo "Securing SSH configuration..."
sed -i 's/#PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/#X11Forwarding yes/X11Forwarding no/' /etc/ssh/sshd_config
sed -i 's/#MaxAuthTries 6/MaxAuthTries 3/' /etc/ssh/sshd_config
sed -i 's/#ClientAliveInterval 0/ClientAliveInterval 300/' /etc/ssh/sshd_config
sed -i 's/#ClientAliveCountMax 3/ClientAliveCountMax 2/' /etc/ssh/sshd_config

print_warning "SSH password authentication disabled. Ensure you have SSH keys set up!"
print_success "SSH configuration secured"

# Secure shared memory
echo "Securing shared memory..."
if ! grep -q "tmpfs /run/shm" /etc/fstab; then
    echo "tmpfs /run/shm tmpfs defaults,noexec,nosuid,size=1g 0 0" >> /etc/fstab
    mount -a
    print_success "Shared memory secured"
else
    print_warning "Shared memory already secured"
fi

# Configure sysctl for network security
echo "Configuring kernel security parameters..."
cat > /etc/sysctl.d/99-security.conf << EOF
# IP Spoofing protection
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# Ignore ICMP broadcast requests
net.ipv4.icmp_echo_ignore_broadcasts = 1

# Disable source packet routing
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0

# Ignore send redirects
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0

# Block SYN attacks
net.ipv4.tcp_max_syn_backlog = 2048
net.ipv4.tcp_synack_retries = 2
net.ipv4.tcp_syn_retries = 5

# Log Martians
net.ipv4.conf.all.log_martians = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1

# Ignore ICMP redirects
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 1
net.ipv4.conf.default.secure_redirects = 1

# Disable IPv6 if not needed (uncomment if needed)
#net.ipv6.conf.all.disable_ipv6 = 1
#net.ipv6.conf.default.disable_ipv6 = 1
#net.ipv6.conf.lo.disable_ipv6 = 1
EOF

sysctl -p /etc/sysctl.d/99-security.conf
print_success "Kernel security parameters configured"

# Install and configure automatic security updates
echo "Configuring automatic security updates..."
apt install -y unattended-upgrades apt-listchanges
cat > /etc/apt/apt.conf.d/50unattended-upgrades << EOF
Unattended-Upgrade::Allowed-Origins {
    "\${distro_id}:\${distro_codename}";
    "\${distro_id}:\${distro_codename}-security";
};
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Automatic-Reboot-Time "02:00";
Unattended-Upgrade::MinimalSteps "true";
Unattended-Upgrade::Mail "admin@yourdomain.com";
Unattended-Upgrade::MailOnlyOnError "true";
EOF

cat > /etc/apt/apt.conf.d/20auto-upgrades << EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
APT::Periodic::Unattended-Upgrade "1";
EOF

print_success "Automatic security updates configured"

# Configure AIDE (Advanced Intrusion Detection Environment)
echo "Configuring AIDE..."
aide --init
mv /var/lib/aide/aide.db.new /var/lib/aide/aide.db
print_success "AIDE configured"

# Install and configure rkhunter
echo "Configuring Rootkit Hunter..."
cat > /etc/default/rkhunter << EOF
CRON_DAILY_RUN="true"
CRON_DB_UPDATE="true"
APT_AUTOGEN="true"
EOF

rkhunter --update
rkhunter --propupd
print_success "Rootkit Hunter configured"

# Disable unused services
echo "Disabling unused services..."
systemctl disable bluetooth 2>/dev/null || true
systemctl stop bluetooth 2>/dev/null || true
print_success "Unused services disabled"

# Set file permissions
echo "Setting secure file permissions..."
chmod 600 /etc/ssh/sshd_config
chmod 644 /etc/passwd
chmod 644 /etc/group
chmod 600 /etc/shadow
chmod 600 /etc/gshadow
print_success "File permissions secured"

# Configure login banners
echo "Configuring login banners..."
cat > /etc/issue << EOF
AUTHORIZED ACCESS ONLY
All connections are monitored and recorded
Disconnect immediately if you are not an authorized user
EOF

cat > /etc/issue.net << EOF
AUTHORIZED ACCESS ONLY
All connections are monitored and recorded
Disconnect immediately if you are not an authorized user
EOF

print_success "Login banners configured"

# Install and configure logrotate for additional security logs
echo "Configuring log rotation..."
cat > /etc/logrotate.d/security << EOF
/var/log/auth.log {
    daily
    missingok
    rotate 52
    compress
    delaycompress
    notifempty
    create 0640 syslog adm
    sharedscripts
    postrotate
        systemctl reload rsyslog >/dev/null 2>&1 || true
    endscript
}
EOF

print_success "Log rotation configured"

# Restart services
echo "Restarting services..."
systemctl restart sshd
systemctl restart rsyslog
print_success "Services restarted"

# Security check summary
echo ""
echo "=========================================="
echo "Security Hardening Complete"
echo "=========================================="
echo ""
print_success "System has been hardened with the following measures:"
echo "  - UFW Firewall configured (SSH, HTTP, HTTPS only)"
echo "  - Fail2Ban installed and configured"
echo "  - SSH secured (no root login, no password auth)"
echo "  - Shared memory secured"
echo "  - Kernel security parameters configured"
echo "  - Automatic security updates enabled"
echo "  - AIDE (Intrusion Detection) configured"
echo "  - Rootkit Hunter configured"
echo "  - Unused services disabled"
echo "  - File permissions secured"
echo "  - Login banners configured"
echo ""
print_warning "IMPORTANT: Before closing this session:"
echo "  1. Test SSH login with your key in a new terminal"
echo "  2. Ensure you can access the server without password"
echo "  3. Keep this session open until you verify SSH access"
echo ""
print_warning "Next steps:"
echo "  1. Set up SSH keys if not already done"
echo "  2. Configure your specific email in Fail2Ban"
echo "  3. Review and customize firewall rules if needed"
echo "  4. Schedule regular AIDE checks"
echo ""
