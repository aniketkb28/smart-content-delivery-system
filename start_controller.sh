POLICY=${1:-aware}
cd "$(dirname "$0")"
sudo pkill -f ryu-manager 2>/dev/null
sudo rm -rf /tmp/cds_status /tmp/cds_metrics.jsonl /tmp/h*.log /tmp/srv*.log
echo "Starting Ryu controller, policy=$POLICY"
POLICY=$POLICY ryu-manager --ofp-tcp-listen-port 6633 controller.py
