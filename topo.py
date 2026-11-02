#!/usr/bin/env python3
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel

VIP, VMAC = '10.0.0.100', '00:00:00:00:00:64'

def build():
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=False)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6633)
    s = {i: net.addSwitch(f's{i}', protocols='OpenFlow13',
                          dpid=f'{i:016x}', failMode='secure') for i in range(1, 5)}
    clients = [net.addHost(f'h{i}', ip=f'10.0.0.{i}/24',
                       mac=f'00:00:00:00:00:{i:02x}') for i in (1, 2, 3)]
    servers = [net.addHost(f'srv{i}', ip=f'10.0.0.{10+i}/24',
                       mac=f'00:00:00:00:00:{10+i:02x}') for i in (1, 2, 3)]

    for i, h in enumerate(clients, 1):
        net.addLink(h, s[1], port2=i, bw=100)
    net.addLink(s[1], s[2], port1=4, port2=1, bw=10, delay='5ms')
    net.addLink(s[1], s[3], port1=5, port2=1, bw=5,  delay='20ms')
    net.addLink(s[2], s[4], port1=2, port2=1, bw=10, delay='5ms')
    net.addLink(s[3], s[4], port1=2, port2=2, bw=5,  delay='20ms')
    for (srv, bw), port in zip(zip(servers, (20, 10, 5)), (3, 4, 5)):
        net.addLink(srv, s[4], port2=port, bw=bw)

    net.start()
    net.waitConnected()
    net.staticArp()                         # all real hosts know each other
    for h in clients:                       # clients resolve VIP to a fake MAC
        h.cmd(f'arp -s {VIP} {VMAC}')
    for srv in servers:
        srv.cmd(f'python3 server.py {srv.name} > /tmp/{srv.name}.log 2>&1 &')
    return net

if __name__ == '__main__':
    setLogLevel('info')
    net = build()
    CLI(net)
    net.stop()
