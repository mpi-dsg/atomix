#!/bin/bash
# OSWorld + Atomix Server Setup Script
# Run this on a Linux server with KVM support

set -e

echo "=== OSWorld + Atomix Setup ==="

# Check KVM support
echo "Checking KVM support..."
KVM_COUNT=$(egrep -c '(vmx|svm)' /proc/cpuinfo 2>/dev/null || echo "0")
if [ "$KVM_COUNT" -eq "0" ]; then
    echo "WARNING: KVM not supported on this machine. Performance will be degraded."
else
    echo "KVM supported (count: $KVM_COUNT)"
fi

# Check Docker
echo "Checking Docker..."
if ! command -v docker &> /dev/null; then
    echo "Docker not found. Installing..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo "Docker installed. You may need to log out and back in for group changes."
fi
docker --version

# Clone repositories
echo "Cloning repositories..."
cd ~
if [ ! -d "OSWorld" ]; then
    git clone https://github.com/xlang-ai/OSWorld.git
fi

if [ ! -d "atomix" ]; then
    git clone https://github.com/YOUR_REPO/atomix.git  # Update with your repo
    # Or copy from local:
    # scp -r user@local:/path/to/atomix ~/atomix
fi

# Install Python dependencies
echo "Setting up Python environment..."
cd ~/OSWorld
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install httpx anthropic

# Install Atomix
cd ~/atomix
pip install -e .

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To start OSWorld VM:"
echo "  cd ~/OSWorld"
echo "  source .venv/bin/activate"
echo "  python quickstart.py --provider_name docker"
echo ""
echo "The VM will be accessible on port 5000 (or check output for actual port)"
echo ""
echo "To run Atomix integration from your Mac:"
echo "  export OSWORLD_HOST=<this-server-ip>"
echo "  export OSWORLD_PORT=5000"
echo "  python -m atomix.osworld_real_demo"
