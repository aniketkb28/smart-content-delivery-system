# Test cases
| Test | Command | Expected result | Result |
| --- | --- | --- | --- |
| Single download | h1 python3 client.py 10 | status=OK, servers vary | |
| 3 clients in parallel | h1, h2, h3 run client.py in background | load spread across servers | |
| Link failure | link s2 s4 down | LINK DOWN + REROUTE, downloads continue | |
| Server crash | srv1 pkill -f "server.py srv1" | srv1 excluded within ~3 s | |
| All servers down | kill all three | "No server available" in controller log | |
