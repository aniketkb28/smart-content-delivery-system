# Smart Content Delivery System (SDN + TCP sockets)

Multiple TCP content servers behind one virtual IP. A Ryu OpenFlow 1.3
controller selects a server and path per connection from live server health and
link load, and reroutes when a link or server fails. Built and tested in Mininet.

## Team
- <Name 1> - <role>
- <Name 2> - <role>
- <Name 3> - <role>

## Architecture
- Topology: 3 clients (h1-h3), 4 switches (s1-s4), 3 servers (srv1-srv3)
- Two paths between s1 and s4: via s2 (10 Mbps, 5 ms) or via s3 (5 Mbps, 20 ms)
- Clients connect to VIP 10.0.0.100:8080; the controller rewrites headers to the chosen server
- Details: docs/architecture.md

## Files
| File | Purpose |
| --- | --- |
| topo.py | Mininet topology, starts servers |
| controller.py | Ryu controller (selection, monitoring, rerouting) |
| server.py | TCP content server (raw sockets, heartbeat) |
| client.py | TCP download client (raw sockets, retries) |
| start_controller.sh | Starts the controller with a chosen policy |

## Requirements
Ubuntu 22.04 (or 20.04), Python 3, Mininet, Open vSwitch, Ryu (see "Setup").

## Setup
    sudo apt install -y mininet openvswitch-switch iperf tcpdump
    pip3 install --user "setuptools==58.0.0" "eventlet==0.30.2" ryu
    echo 'export PATH=$PATH:$HOME/.local/bin' >> ~/.bashrc && source ~/.bashrc

## How to run
Terminal 1 (controller, policy = rr | least | aware):

    ./start_controller.sh aware

Terminal 2 (network; start the controller first):

    sudo mn -c
    sudo python3 topo.py

At the `mininet>` prompt:

    h1 python3 client.py 10

## Failure scenarios
    link s2 s4 down          # path failure
    link s2 s4 up
    srv1 pkill -f "server.py srv1"     # server crash (wait 15 s)
    srv1 python3 server.py srv1 > /tmp/srv1.log 2>&1 &

## Tests and results
See docs/test-cases.md and the results/ folder.

## Limitations
Single controller, 2 s polling interval, in-flight connections to a crashed server are lost.
