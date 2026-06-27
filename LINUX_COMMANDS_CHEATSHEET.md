# Linux Commands Cheatsheet for DevOps
## Essential Commands for Server Management

---

## File System Navigation

```bash
pwd                    # Print working directory
ls                     # List files in current directory
ls -la                 # List all files including hidden ones with details
cd /path/to/directory  # Change directory
cd ~                   # Go to home directory
cd ..                  # Go up one directory
cd -                   # Go to previous directory
```

---

## File Operations

```bash
touch filename         # Create empty file
mkdir directory        # Create directory
mkdir -p path/to/dir   # Create nested directories
rm filename            # Remove file
rm -r directory        # Remove directory and contents
rm -rf directory       # Force remove directory (be careful!)
cp source dest         # Copy file
cp -r source dest      # Copy directory
mv source dest         # Move/rename file
cat filename           # Display file content
less filename          # View file with pagination
head -n 10 filename    # Show first 10 lines
tail -n 10 filename    # Show last 10 lines
tail -f filename       # Follow file in real-time
```

---

## File Permissions

```bash
chmod 755 file         # Set permissions (rwxr-xr-x)
chmod +x script.sh     # Make file executable
chown user:group file  # Change owner and group
chown -R user:group dir# Recursively change ownership
ls -l                  # View permissions
```

**Permission Numbers:**
- 7 = rwx (read, write, execute)
- 6 = rw- (read, write)
- 5 = r-x (read, execute)
- 4 = r-- (read only)
- 0 = --- (no permissions)

---

## Text Processing

```bash
grep "pattern" file    # Search for pattern in file
grep -r "pattern" dir  # Recursively search in directory
grep -i "pattern" file # Case-insensitive search
sed 's/old/new/g' file # Replace text in file
awk '{print $1}' file  # Process text column by column
sort file              # Sort lines in file
uniq file              # Remove duplicate lines
wc -l file             # Count lines in file
diff file1 file2       # Compare files
```

---

## System Information

```bash
uname -a               # System information
hostname               # Show hostname
df -h                  # Disk usage (human-readable)
du -sh directory       # Directory size
free -h                # Memory usage
top                    # Process monitor (interactive)
htop                   # Enhanced process monitor
ps aux                 # List all processes
ps aux | grep process  # Find specific process
uptime                 # System uptime
lscpu                  # CPU information
lsblk                  # Block devices
```

---

## Process Management

```bash
command &              # Run command in background
jobs                   # List background jobs
fg %1                  # Bring job to foreground
bg %1                  # Send job to background
kill PID               # Kill process by PID
kill -9 PID            # Force kill process
pkill processname      # Kill process by name
killall processname    # Kill all processes with name
nohup command &        # Run command immune to hangups
```

---

## Service Management (systemd)

```bash
systemctl start service    # Start service
systemctl stop service     # Stop service
systemctl restart service  # Restart service
systemctl status service   # Check service status
systemctl enable service   # Enable service at boot
systemctl disable service  # Disable service at boot
systemctl list-units       # List all services
journalctl -u service      # View service logs
journalctl -f              # Follow all logs
```

---

## Network Commands

```bash
ping host               # Ping host
ip addr                 # Show IP addresses
ifconfig                # Show network interfaces (older)
netstat -tulpn          # List listening ports
ss -tulpn               # Modern alternative to netstat
curl url                # Make HTTP request
wget url                # Download file
ssh user@host           # SSH to remote host
scp file user@host:path # Copy file to remote host
```

---

## Package Management (apt)

```bash
apt update              # Update package lists
apt upgrade            # Upgrade installed packages
apt install package     # Install package
apt remove package      # Remove package
apt autoremove          # Remove unused packages
apt search keyword      # Search for package
apt show package        # Show package information
apt list --installed    # List installed packages
```

---

## User Management

```bash
whoami                 # Show current user
who                    # Show logged in users
w                      # Show who is logged in and what they're doing
adduser username       # Create new user
deluser username       # Delete user
usermod -aG group user # Add user to group
groups username        # Show user groups
passwd username        # Change user password
sudo command           # Run command as root
visudo                 # Edit sudoers file
```

---

## Compression and Archives

```bash
tar -czf archive.tar.gz directory  # Create tar.gz archive
tar -xzf archive.tar.gz            # Extract tar.gz archive
tar -czf archive.tar.gz file1 file2  # Archive specific files
zip archive.zip file              # Create zip archive
unzip archive.zip                 # Extract zip archive
gzip file                          # Compress file
gunzip file.gz                     # Decompress file
```

---

## Disk and File System

```bash
df -h                  # Disk space usage
du -sh *               # Size of each item in directory
du -h --max-depth=1    # Directory sizes to depth 1
mount                  # Show mounted filesystems
mount /dev/sdb1 /mnt   # Mount device
umount /mnt            # Unmount
fdisk -l               # List disk partitions
```

---

## Monitoring and Logs

```bash
tail -f /var/log/syslog        # Follow system log
tail -f /var/log/auth.log      # Follow auth log
journalctl -f                  # Follow systemd logs
journalctl -u service -f       # Follow specific service logs
dmesg                           # Kernel messages
last                            # Login history
lastlog                         # Last login per user
```

---

## Docker Commands

```bash
docker ps                      # List running containers
docker ps -a                    # List all containers
docker images                   # List images
docker run image                # Run container
docker exec -it container bash # Access container shell
docker logs container           # View container logs
docker stop container           # Stop container
docker start container          # Start container
docker rm container             # Remove container
docker rmi image                # Remove image
docker-compose up -d            # Start services with compose
docker-compose down            # Stop and remove services
docker-compose logs -f         # Follow compose logs
```

---

## PostgreSQL Commands

```bash
sudo -u postgres psql                    # Access PostgreSQL
psql -U username -d database            # Connect to specific database
\l                                      # List databases
\dt                                     # List tables
\d tablename                            # Describe table
\q                                      # Quit PostgreSQL
pg_dump dbname > backup.sql             # Export database
psql dbname < backup.sql                # Import database
```

---

## Git Commands

```bash
git status              # Show working tree status
git add .               # Stage all changes
git commit -m "msg"     # Commit changes
git push                # Push to remote
git pull                # Pull from remote
git branch              # List branches
git checkout branch      # Switch branch
git log                 # Show commit history
git diff                # Show changes
```

---

## Useful Aliases (add to ~/.bashrc)

```bash
alias ll='ls -la'
alias la='ls -A'
alias l='ls -CF'
alias ..='cd ..'
alias ...='cd ../..'
alias grep='grep --color=auto'
alias dc='docker compose'
alias dcd='docker compose down'
alias dcu='docker compose up -d'
alias dcl='docker compose logs -f'
```

---

## SSH Key Management

```bash
ssh-keygen -t ed25519 -C "your_email@example.com"  # Generate SSH key
cat ~/.ssh/id_ed25519.pub                           # Show public key
ssh-copy-id user@host                               # Copy key to server
ssh -i ~/.ssh/key.pem user@host                     # Connect with specific key
ssh-keygen -p -f ~/.ssh/id_ed25519                  # Change key password
```

---

## Cron Jobs (Scheduled Tasks)

```bash
crontab -e              # Edit cron jobs
crontab -l              # List cron jobs
# Cron format: * * * * * command
# | | | | |
# | | | | +----- Day of week (0-7, Sunday=0 or 7)
# | | | +------- Month (1-12)
# | | +--------- Day of month (1-31)
# | +----------- Hour (0-23)
# +------------- Minute (0-59)

# Examples:
0 2 * * * /path/to/script.sh    # Run daily at 2 AM
*/15 * * * * /path/to/script.sh  # Run every 15 minutes
0 */2 * * * /path/to/script.sh   # Run every 2 hours
```

---

## System Monitoring Commands

```bash
vmstat 1               # Virtual memory statistics every second
iostat 1              # I/O statistics every second
mpstat 1              # CPU statistics every second
sar                    # System activity reporter
nethogs                # Network bandwidth per process
iotop                  # I/O monitoring
```

---

## Firewall (UFW) Commands

```bash
sudo ufw enable                 # Enable firewall
sudo ufw disable                # Disable firewall
sudo ufw status                 # Check status
sudo ufw allow 22/tcp           # Allow SSH
sudo ufw allow 80/tcp           # Allow HTTP
sudo ufw allow 443/tcp          # Allow HTTPS
sudo ufw deny 23/tcp            # Deny specific port
sudo ufw reload                 # Reload firewall
```

---

## Finding Files

```bash
find /path -name filename       # Find file by name
find /path -type d -name dir    # Find directory by name
find /path -size +100M          # Find files larger than 100MB
find /path -mtime -7            # Find files modified in last 7 days
locate filename                 # Quick file search (uses database)
updatedb                       # Update locate database
```

---

## Environment Variables

```bash
echo $PATH                     # Show PATH variable
export VAR=value               # Set environment variable
unset VAR                      # Unset variable
env                            # Show all environment variables
printenv                       # Show all environment variables
```

---

## History and Shell

```bash
history                        # Show command history
!100                           # Run command number 100
!!                             # Run last command
!$                             # Last argument of last command
ctrl+r                         # Search command history
ctrl+a                         # Go to beginning of line
ctrl+e                         # Go to end of line
ctrl+u                         # Clear to beginning of line
ctrl+k                         # Clear to end of line
ctrl+l                         # Clear screen
```

---

## Quick Reference Card

**File Operations:**
- `ls` - list, `cd` - change directory, `cp` - copy, `mv` - move, `rm` - remove

**Permissions:**
- `chmod` - change permissions, `chown` - change owner

**Processes:**
- `ps` - list processes, `kill` - kill process, `top` - monitor

**Network:**
- `ping` - test connection, `curl` - HTTP request, `ssh` - remote login

**System:**
- `df` - disk space, `free` - memory, `uname` - system info

**Services:**
- `systemctl start/stop/restart/enable/disable service`

**Docker:**
- `docker ps/images/run/exec/logs`
- `docker compose up/down/logs`

---

## Learning Resources

- **Man pages**: `man command` - Built-in documentation
- **Help flags**: `command --help` - Quick help
- **TLDR pages**: `tldr command` - Simplified man pages (install first)
- **Explainshell.com** - Explains shell commands
- **Linux Journey** - https://linuxjourney.com/
- **OverTheWire Bandit** - Learn Linux through games

---

## Safety Tips

1. **Always double-check before using `rm -rf`** - It deletes permanently
2. **Test commands with `echo` first** - See what will happen
3. **Use tab completion** - Prevent typos
4. **Keep backups** - Before major operations
5. **Read error messages** - They usually tell you what's wrong
6. **Use `sudo` carefully** - Only when needed
7. **Keep your system updated** - `apt update && apt upgrade`

---

## Troubleshooting Common Issues

**Command not found:**
- Check if package is installed: `dpkg -l | grep package`
- Install if missing: `apt install package`
- Check PATH: `echo $PATH`

**Permission denied:**
- Check permissions: `ls -la`
- Use sudo if appropriate: `sudo command`
- Change permissions: `chmod +x file`

**Disk full:**
- Check space: `df -h`
- Find large files: `du -h --max-depth=1 | sort -hr`
- Clean up: `apt autoremove`, `docker system prune`

**Service won't start:**
- Check status: `systemctl status service`
- Check logs: `journalctl -u service`
- Check configuration: `systemctl cat service`

---

**Last Updated**: 2026-06-25
