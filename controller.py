import json, os, time, heapq
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ipv4, tcp
from ryu.lib import hub

VIP, VMAC, SPORT = '10.0.0.100', '00:00:00:00:00:64', 8080
STATUS_DIR = '/tmp/cds_status'
W_LOAD, W_NET = 1.0, 0.5            # scoring weights (tune in experiments)

# (dpid, port) -> (neighbour dpid, neighbour port)
LINKS = {(1,4):(2,1),(2,1):(1,4),(1,5):(3,1),(3,1):(1,5),
         (2,2):(4,1),(4,1):(2,2),(3,2):(4,2),(4,2):(3,2)}
# capacity in Mbps of each switch egress port we monitor
BW = {(1,4):10,(2,1):10,(2,2):10,(4,1):10,(1,5):5,(3,1):5,(3,2):5,(4,2):5,
      (4,3):20,(4,4):10,(4,5):5}
HOSTS = {'10.0.0.1':(1,1),'10.0.0.2':(1,2),'10.0.0.3':(1,3),
         '10.0.0.11':(4,3),'10.0.0.12':(4,4),'10.0.0.13':(4,5)}
SERVERS = {'10.0.0.11':'srv1','10.0.0.12':'srv2','10.0.0.13':'srv3'}
mac_of = lambda ip: '00:00:00:00:00:%02x' % int(ip.split('.')[-1])

class CDS(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.dps, self.down, self.host_down = {}, set(), set()
        self.util, self.prev, self.flows = {}, {}, {}
        self.policy = os.environ.get('POLICY', 'aware')   # rr | least | aware
        self.rr = 0
        hub.spawn(self.monitor_loop)

    # ---------- switch setup ----------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features(self, ev):
        dp = ev.msg.datapath; p, o = dp.ofproto_parser, dp.ofproto
        self.dps[dp.id] = dp
        self.add_flow(dp, 0, p.OFPMatch(),
                      [p.OFPActionOutput(o.OFPP_CONTROLLER, o.OFPCML_NO_BUFFER)])

    def add_flow(self, dp, prio, match, actions, idle=0, flags=0):
        p, o = dp.ofproto_parser, dp.ofproto
        inst = [p.OFPInstructionActions(o.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=prio, match=match,
                                 instructions=inst, idle_timeout=idle, flags=flags))

    # ---------- link / port events (failure detection) ----------
    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status(self, ev):
        msg = ev.msg; dpid = msg.datapath.id; port = msg.desc.port_no
        o = msg.datapath.ofproto
        is_down = bool(msg.desc.state & o.OFPPS_LINK_DOWN) or \
                  bool(msg.desc.config & o.OFPPC_PORT_DOWN)
        key = (dpid, port)
        if key in LINKS:
            pair = {key, LINKS[key]}
            if is_down and key not in self.down:
                self.down |= pair
                self.logger.warning('LINK DOWN s%d-s%d', dpid, LINKS[key][0])
                self.reroute_affected()
            elif not is_down and key in self.down:
                self.down -= pair
                self.logger.warning('LINK UP s%d-s%d', dpid, LINKS[key][0])
        elif key in HOSTS.values():
            (self.host_down.add if is_down else self.host_down.discard)(key)
            if is_down:
                self.reroute_affected()

    # ---------- monitoring ----------
    def monitor_loop(self):
        while True:
            for dp in list(self.dps.values()):
                dp.send_msg(dp.ofproto_parser.OFPPortStatsRequest(
                    dp, 0, dp.ofproto.OFPP_ANY))
            self.reroute_affected()
            self.log_metrics()
            hub.sleep(2)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def stats(self, ev):
        dpid, now = ev.msg.datapath.id, time.time()
        for s in ev.msg.body:
            k = (dpid, s.port_no)
            if k not in BW: continue
            if k in self.prev:
                b0, t0 = self.prev[k]
                bps = (s.tx_bytes - b0) * 8 / max(now - t0, 1e-3)
                self.util[k] = min(1.0, bps / (BW[k] * 1e6))
            self.prev[k] = (s.tx_bytes, now)

    def server_status(self, sip):
        try:
            with open(f'{STATUS_DIR}/{SERVERS[sip]}.json') as f:
                d = json.load(f)
            return d if time.time() - d['ts'] <= 3 else None
        except Exception:
            return None

    def alive(self, sip):
        return self.server_status(sip) is not None and HOSTS[sip] not in self.host_down

    def log_metrics(self):
        row = {'t': round(time.time(), 1), 'policy': self.policy,
               'util': {f'{k[0]}:{k[1]}': round(v, 2) for k, v in self.util.items()},
               'flows': {s: sum(1 for f in self.flows.values() if f['server'] == s)
                         for s in SERVERS},
               'alive': {s: self.alive(s) for s in SERVERS}}
        with open('/tmp/cds_metrics.jsonl', 'a') as f:
            f.write(json.dumps(row) + '\n')

    # ---------- path computation ----------
    def port_to(self, a, b):
        for (d, p), (n, _) in LINKS.items():
            if d == a and n == b and (d, p) not in self.down:
                return p

    def link_cost(self, a, p):
        u = max(self.util.get((a, p), 0), self.util.get(LINKS[(a, p)], 0))
        return 1 + 10 * u            # congested links become expensive

    def path(self, src, dst):
        dist, prev, pq = {src: 0}, {}, [(0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == dst: break
            if d > dist.get(u, 1e9): continue
            for (a, p), (n, _) in LINKS.items():
                if a != u or (a, p) in self.down: continue
                nd = d + self.link_cost(a, p)
                if nd < dist.get(n, 1e9):
                    dist[n], prev[n] = nd, u
                    heapq.heappush(pq, (nd, n))
        if dst not in dist: return None
        r = [dst]
        while r[-1] != src: r.append(prev[r[-1]])
        return r[::-1], dist[dst]

    def path_valid(self, path):
        return all(self.port_to(a, b) for a, b in zip(path, path[1:]))

    # ---------- server selection ----------
    def choose(self, cip):
        cands = []
        for sip in SERVERS:
            if not self.alive(sip): continue
            r = self.path(HOSTS[cip][0], HOSTS[sip][0])
            if not r: continue
            path, cost = r
            assigned = sum(1 for f in self.flows.values() if f['server'] == sip)
            active = self.server_status(sip)['active']
            sl = self.util.get(HOSTS[sip], 0)       # server access-link utilisation
            score = W_LOAD * (active + assigned) + W_NET * (cost + 10 * sl)
            cands.append((sip, path, assigned, score))
        if not cands: return None
        if self.policy == 'rr':
            self.rr += 1; c = cands[self.rr % len(cands)]
        elif self.policy == 'least':
            c = min(cands, key=lambda x: x[2])
        else:
            c = min(cands, key=lambda x: x[3])
        return c[0], c[1]

    # ---------- flow install / remove ----------
    def install(self, cip, cport, sip, path):
        n, first = len(path), None
        for i, dpid in enumerate(path):                       # client -> server
            dp = self.dps[dpid]; p, o = dp.ofproto_parser, dp.ofproto
            out = HOSTS[sip][1] if i == n-1 else self.port_to(dpid, path[i+1])
            m = p.OFPMatch(eth_type=0x0800, ip_proto=6, ipv4_src=cip,
                           ipv4_dst=VIP if i == 0 else sip,
                           tcp_src=cport, tcp_dst=SPORT)
            acts = []
            if i == 0:
                acts = [p.OFPActionSetField(ipv4_dst=sip),
                        p.OFPActionSetField(eth_dst=mac_of(sip))]
            acts.append(p.OFPActionOutput(out))
            if i == 0: first = acts
            self.add_flow(dp, 10, m, acts, idle=10,
                          flags=o.OFPFF_SEND_FLOW_REM if i == 0 else 0)
        rp = path[::-1]
        for i, dpid in enumerate(rp):                         # server -> client
            dp = self.dps[dpid]; p = dp.ofproto_parser
            out = HOSTS[cip][1] if i == n-1 else self.port_to(dpid, rp[i+1])
            m = p.OFPMatch(eth_type=0x0800, ip_proto=6,
                           ipv4_src=sip if i == 0 else VIP, ipv4_dst=cip,
                           tcp_src=SPORT, tcp_dst=cport)
            acts = []
            if i == 0:
                acts = [p.OFPActionSetField(ipv4_src=VIP),
                        p.OFPActionSetField(eth_src=VMAC)]
            acts.append(p.OFPActionOutput(out))
            self.add_flow(dp, 10, m, acts, idle=10)
        return first

    def remove(self, cip, cport, sip, path):
        for dpid in path:
            dp = self.dps[dpid]; p, o = dp.ofproto_parser, dp.ofproto
            for s, d, sp, dpt in [(cip, VIP, cport, SPORT), (cip, sip, cport, SPORT),
                                  (VIP, cip, SPORT, cport), (sip, cip, SPORT, cport)]:
                m = p.OFPMatch(eth_type=0x0800, ip_proto=6, ipv4_src=s, ipv4_dst=d,
                               tcp_src=sp, tcp_dst=dpt)
                dp.send_msg(p.OFPFlowMod(datapath=dp, command=o.OFPFC_DELETE,
                                         out_port=o.OFPP_ANY, out_group=o.OFPG_ANY,
                                         match=m))

    # ---------- failure handling ----------
    def reroute_affected(self):
        for key, f in list(self.flows.items()):
            cip, cport = key; sip = f['server']
            srv_ok, path_ok = self.alive(sip), self.path_valid(f['path'])
            if srv_ok and path_ok: continue
            self.remove(cip, cport, sip, f['path'])
            if srv_ok:                                    # path failure: same server, new path
                r = self.path(HOSTS[cip][0], HOSTS[sip][0])
                if r:
                    f['path'] = r[0]
                    self.install(cip, cport, sip, r[0])
                    self.logger.warning('REROUTE %s:%d -> %s via %s', cip, cport,
                                        SERVERS[sip], r[0])
                    continue
            del self.flows[key]                           # server failure: client retries
            self.logger.warning('DROP flow %s:%d (server %s unavailable)',
                                cip, cport, SERVERS[sip])

    # ---------- new connections ----------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg; dp = msg.datapath
        pkt = packet.Packet(msg.data)
        ip, t = pkt.get_protocol(ipv4.ipv4), pkt.get_protocol(tcp.tcp)
        if not ip or not t or ip.dst != VIP or t.dst_port != SPORT: return
        key = (ip.src, t.src_port)
        f = self.flows.get(key)
        if f is None:
            if not (t.bits & tcp.TCP_SYN): return
            c = self.choose(ip.src)
            if not c:
                self.logger.error('No server available for %s', ip.src); return
            sip, path = c
            self.flows[key] = {'server': sip, 'path': path}
            self.logger.info('SELECT %s:%d -> %s via %s [%s]', ip.src, t.src_port,
                             SERVERS[sip], path, self.policy)
        else:
            sip, path = f['server'], f['path']
        first = self.install(ip.src, t.src_port, sip, path)
        dp.send_msg(dp.ofproto_parser.OFPPacketOut(
            datapath=dp, buffer_id=dp.ofproto.OFP_NO_BUFFER,
            in_port=msg.match['in_port'], actions=first, data=msg.data))

    @set_ev_cls(ofp_event.EventOFPFlowRemoved, MAIN_DISPATCHER)
    def flow_removed(self, ev):
        msg = ev.msg
        if msg.reason == msg.datapath.ofproto.OFPRR_IDLE_TIMEOUT:
            self.flows.pop((msg.match['ipv4_src'], msg.match['tcp_src']), None)
