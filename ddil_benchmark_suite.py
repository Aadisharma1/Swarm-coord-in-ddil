#!/usr/bin/env python3
"""
DDIL Swarm Benchmark Suite — Extended Graphs
=============================================
Generates 4 publication-quality figures from the Phase 2 simulation data.
"""

import random
import statistics
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import networkx as nx
import simpy

# ---- Import core simulation components ----
# We inline the needed classes to keep this self-contained

NUM_NODES = 10
SIM_DURATION = 100.0
BROADCAST_INTERVAL = 2.0
RAW_PAYLOAD_BYTES = 256
SEMANTIC_PAYLOAD_BYTES = 32
BASE_LATENCY = 0.5
MAX_LATENCY_SPIKE = 8.0
DISCONNECT_PROBABILITY = 0.15
DISCONNECT_DURATION = 5.0
GOSSIP_TTL = 3
INFERENCE_FIDELITY_LOSS = 0.05
LINK_RELIABILITY_DECAY = 0.1
LINK_RELIABILITY_THRESHOLD = 0.25
RANDOM_SEED = 42

DROP_RATES = [0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]
NODE_COUNTS = [5, 10, 15, 20, 30]

OUT_DIR = r'C:\Users\Aadi Sharma\OneDrive\Desktop\convert'

_node_registry: Dict[int, object] = {}


@dataclass
class SemanticToken:
    token_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    origin_node: int = 0
    timestamp: float = 0.0
    payload_size: int = RAW_PAYLOAD_BYTES
    ttl: int = GOSSIP_TTL
    is_compressed: bool = False


@dataclass
class TransmissionResult:
    sender: int
    receiver: int
    token_id: str
    timestamp: float
    success: bool
    failure_reason: Optional[str] = None


class MetricsCollector:
    def __init__(self):
        self.results: List[TransmissionResult] = []
    def record(self, r): self.results.append(r)
    @property
    def total_sent(self): return len(self.results)
    @property
    def total_delivered(self): return sum(1 for r in self.results if r.success)
    @property
    def delivery_rate(self): return self.total_delivered / self.total_sent if self.total_sent else 0.0
    def failure_breakdown(self):
        fb = {}
        for r in self.results:
            if not r.success and r.failure_reason:
                fb[r.failure_reason] = fb.get(r.failure_reason, 0) + 1
        return fb


class EnvironmentController:
    def __init__(self, env, graph, packet_drop_rate=0.0, num_nodes=10):
        self.env = env
        self.graph = graph
        self.packet_drop_rate = packet_drop_rate
        self._disconnected = set()
        self._num_nodes = num_nodes
        self.env.process(self._injector())

    def _injector(self):
        while True:
            yield self.env.timeout(random.uniform(3.0, 8.0))
            nid = random.randint(0, self._num_nodes - 1)
            if random.random() < DISCONNECT_PROBABILITY and nid not in self._disconnected:
                self._disconnected.add(nid)
                self.env.process(self._reconn(nid, random.uniform(1.0, DISCONNECT_DURATION)))

    def _reconn(self, nid, dur):
        yield self.env.timeout(dur)
        self._disconnected.discard(nid)

    def is_disconnected(self, nid): return nid in self._disconnected

    def attempt(self, s, r, token):
        if self.is_disconnected(s): return False, 0.0, "Sender Disconnected"
        if self.is_disconnected(r): return False, 0.0, "Receiver Disconnected"
        if not self.graph.has_edge(s, r): return False, 0.0, "No Edge"
        sf = token.payload_size / RAW_PAYLOAD_BYTES
        if random.random() < self.packet_drop_rate * sf:
            return False, 0.0, "Packet Dropped"
        lat = BASE_LATENCY + random.expovariate(1.0 / 1.5)
        if random.random() < 0.10: lat += random.uniform(2.0, MAX_LATENCY_SPIKE)
        return True, lat, None


class BaselineNode:
    def __init__(self, nid, env, ctrl, graph, metrics):
        self.nid = nid; self.env = env; self.ctrl = ctrl
        self.graph = graph; self.metrics = metrics
        self.state: Dict[str, SemanticToken] = {}
        self._seen: set = set()
        env.process(self._loop())

    def _loop(self):
        yield self.env.timeout(random.uniform(0, BROADCAST_INTERVAL))
        while True:
            t = SemanticToken(origin_node=self.nid, timestamp=self.env.now,
                              payload_size=RAW_PAYLOAD_BYTES)
            self.state[t.token_id] = t; self._seen.add(t.token_id)
            for n in list(self.graph.neighbors(self.nid)):
                self.env.process(self._send(n, t))
            yield self.env.timeout(BROADCAST_INTERVAL + random.uniform(-0.3, 0.3))

    def _send(self, rid, token):
        ok, lat, reason = self.ctrl.attempt(self.nid, rid, token)
        self.metrics.record(TransmissionResult(self.nid, rid, token.token_id, self.env.now, ok, reason))
        if not ok: return
        yield self.env.timeout(lat)
        if rid in _node_registry: _node_registry[rid].recv(token)

    def recv(self, token):
        if token.token_id in self._seen: return
        self._seen.add(token.token_id)
        self.state[token.token_id] = token
        if token.ttl > 1:
            fwd = SemanticToken(token_id=token.token_id, origin_node=token.origin_node,
                                timestamp=token.timestamp, payload_size=token.payload_size,
                                ttl=token.ttl-1, is_compressed=token.is_compressed)
            for n in self.graph.neighbors(self.nid):
                if n != token.origin_node: self.env.process(self._send(n, fwd))


class AgenticNode:
    def __init__(self, nid, env, ctrl, graph, metrics):
        self.nid = nid; self.env = env; self.ctrl = ctrl
        self.graph = graph; self.metrics = metrics
        self.state: Dict[str, SemanticToken] = {}
        self._seen: set = set()
        self.link_scores = {n: 0.5 for n in graph.neighbors(nid)}
        env.process(self._loop())

    def _update_link(self, n, ok):
        old = self.link_scores.get(n, 0.5)
        self.link_scores[n] = old * 0.9 + (1.0 if ok else 0.0) * 0.1

    def _best_relay(self, target):
        best, bs = None, 0.0
        for m in self.graph.neighbors(self.nid):
            if m != target and self.graph.has_edge(m, target):
                sc = self.link_scores.get(m, 0.0) * 0.7
                if sc > bs and sc > LINK_RELIABILITY_THRESHOLD:
                    bs, best = sc, m
        return best

    def _loop(self):
        yield self.env.timeout(random.uniform(0, BROADCAST_INTERVAL))
        while True:
            raw = SemanticToken(origin_node=self.nid, timestamp=self.env.now,
                                payload_size=RAW_PAYLOAD_BYTES)
            comp = SemanticToken(token_id=raw.token_id, origin_node=self.nid,
                                 timestamp=self.env.now, payload_size=SEMANTIC_PAYLOAD_BYTES,
                                 ttl=raw.ttl, is_compressed=True)
            self.state[comp.token_id] = comp; self._seen.add(comp.token_id)
            for n in list(self.graph.neighbors(self.nid)):
                sc = self.link_scores.get(n, 0.5)
                if sc >= LINK_RELIABILITY_THRESHOLD:
                    self.env.process(self._send(n, comp))
                else:
                    relay = self._best_relay(n)
                    self.env.process(self._send(relay if relay else n, comp))
            yield self.env.timeout(BROADCAST_INTERVAL + random.uniform(-0.3, 0.3))

    def _send(self, rid, token):
        ok, lat, reason = self.ctrl.attempt(self.nid, rid, token)
        self._update_link(rid, ok)
        self.metrics.record(TransmissionResult(self.nid, rid, token.token_id, self.env.now, ok, reason))
        if not ok: return
        yield self.env.timeout(lat)
        if rid in _node_registry: _node_registry[rid].recv(token)

    def recv(self, token):
        if token.token_id in self._seen: return
        self._seen.add(token.token_id)
        if token.is_compressed and random.random() < INFERENCE_FIDELITY_LOSS:
            return
        self.state[token.token_id] = token
        if token.ttl > 1:
            fwd = SemanticToken(token_id=token.token_id, origin_node=token.origin_node,
                                timestamp=token.timestamp, payload_size=SEMANTIC_PAYLOAD_BYTES,
                                ttl=token.ttl-1, is_compressed=True)
            for n in self.graph.neighbors(self.nid):
                if n != token.origin_node:
                    sc = self.link_scores.get(n, 0.5)
                    if sc >= LINK_RELIABILITY_THRESHOLD:
                        self.env.process(self._send(n, fwd))


def build_topo(n):
    k = min(4, n - 1) if n > 4 else max(2, n - 1)
    if k % 2 != 0: k = max(2, k - 1)
    G = nx.watts_strogatz_graph(n, k=k, p=0.3, seed=RANDOM_SEED)
    if not nx.is_connected(G):
        cs = list(nx.connected_components(G))
        for i in range(1, len(cs)):
            G.add_edge(list(cs[0])[0], list(cs[i])[0])
    return G


def sync_rate(reg):
    all_ids = set()
    for nd in reg.values(): all_ids.update(nd.state.keys())
    if not all_ids: return 0.0
    return statistics.mean(len(nd.state) / len(all_ids) for nd in reg.values())


def per_node_sync(reg):
    all_ids = set()
    for nd in reg.values(): all_ids.update(nd.state.keys())
    if not all_ids: return {}
    return {nid: len(nd.state) / len(all_ids) for nid, nd in reg.items()}


def run_sim(mode, drop, n_nodes=10):
    global _node_registry
    random.seed(RANDOM_SEED)
    env = simpy.Environment()
    graph = build_topo(n_nodes)
    metrics = MetricsCollector()
    ctrl = EnvironmentController(env, graph, drop, n_nodes)
    _node_registry.clear()
    NodeCls = AgenticNode if mode == "agentic" else BaselineNode
    for i in range(n_nodes):
        _node_registry[i] = NodeCls(i, env, ctrl, graph, metrics)
    env.run(until=SIM_DURATION)
    return metrics, sync_rate(_node_registry), per_node_sync(_node_registry), metrics.failure_breakdown()


# ============================================================================
# GRAPH 1: Main Comparative (State Sync vs Drop Rate)
# ============================================================================
def gen_graph1():
    print("  Generating Graph 1: State Sync vs Drop Rate...")
    b_syncs, a_syncs = [], []
    for dr in DROP_RATES:
        _, bs, _, _ = run_sim("baseline", dr)
        _, as_, _, _ = run_sim("agentic", dr)
        b_syncs.append(bs * 100); a_syncs.append(as_ * 100)

    fig, ax = plt.subplots(figsize=(10, 6), dpi=200)
    pcts = [d * 100 for d in DROP_RATES]

    ax.plot(pcts, b_syncs, 's--', color='#d62828', lw=2.2, ms=8,
            markerfacecolor='#f77f7f', markeredgecolor='#d62828', mew=1.5,
            label='Baseline Gossip (RAW 256B)')
    ax.plot(pcts, a_syncs, 'o-', color='#0077b6', lw=2.8, ms=9,
            markerfacecolor='#00b4d8', markeredgecolor='#023e8a', mew=1.5,
            label='Agentic SLM (Compressed 32B + Link Memory)')
    ax.fill_between(pcts, b_syncs, a_syncs, alpha=0.1, color='#0077b6')

    for x, yb, ya in zip(pcts, b_syncs, a_syncs):
        ax.annotate(f'{yb:.0f}%', (x, yb), textcoords='offset points',
                    xytext=(0, -14), ha='center', fontsize=7, color='#d62828', fontweight='bold')
        ax.annotate(f'{ya:.0f}%', (x, ya), textcoords='offset points',
                    xytext=(0, 10), ha='center', fontsize=7, color='#023e8a', fontweight='bold')

    ax.set_title('Effective State Synchronization Under DDIL Degradation\n'
                 'Baseline Gossip vs. Agentic SLM Semantic Routing',
                 fontsize=13, fontweight='bold', pad=12)
    ax.set_xlabel('Packet Drop Rate (%)', fontsize=11)
    ax.set_ylabel('State Synchronization (%)', fontsize=11)
    ax.set_xlim(-2, 82); ax.set_ylim(0, 105)
    ax.grid(True, ls=':', alpha=0.5); ax.legend(loc='lower left', fontsize=10)
    fig.tight_layout()
    fig.savefig(f'{OUT_DIR}\\bench_1_sync_vs_drop.png')
    plt.close(); print("    Done.")


# ============================================================================
# GRAPH 2: Message Delivery Rate + Failure Breakdown (Stacked Bar)
# ============================================================================
def gen_graph2():
    print("  Generating Graph 2: Delivery Rate & Failure Breakdown...")
    pcts = [d * 100 for d in DROP_RATES]
    b_del, a_del = [], []
    b_pkt, b_disc, a_pkt, a_disc = [], [], [], []

    for dr in DROP_RATES:
        bm, _, _, bfb = run_sim("baseline", dr)
        am, _, _, afb = run_sim("agentic", dr)
        b_del.append(bm.delivery_rate * 100)
        a_del.append(am.delivery_rate * 100)
        bt = bm.total_sent or 1; at = am.total_sent or 1
        b_pkt.append(bfb.get("Packet Dropped", 0) / bt * 100)
        b_disc.append((bfb.get("Sender Disconnected", 0) + bfb.get("Receiver Disconnected", 0)) / bt * 100)
        a_pkt.append(afb.get("Packet Dropped", 0) / at * 100)
        a_disc.append((afb.get("Sender Disconnected", 0) + afb.get("Receiver Disconnected", 0)) / at * 100)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5), dpi=200)

    # Left: delivery rates
    w = 3.0
    x = np.array(pcts)
    ax1.bar(x - w/2, b_del, w, color='#f77f7f', edgecolor='#d62828', lw=0.8, label='Baseline')
    ax1.bar(x + w/2, a_del, w, color='#90e0ef', edgecolor='#023e8a', lw=0.8, label='Agentic SLM')
    ax1.set_title('Message Delivery Success Rate', fontsize=12, fontweight='bold')
    ax1.set_xlabel('Packet Drop Rate (%)', fontsize=10)
    ax1.set_ylabel('Delivery Rate (%)', fontsize=10)
    ax1.set_ylim(0, 105); ax1.legend(fontsize=9); ax1.grid(True, axis='y', ls=':', alpha=0.5)

    # Right: failure breakdown stacked bars
    ax2.bar(x - w/2, b_pkt, w, color='#e76f51', label='Baseline: Packet Drop', edgecolor='black', lw=0.5)
    ax2.bar(x - w/2, b_disc, w, bottom=b_pkt, color='#f4a261', label='Baseline: Disconnect', edgecolor='black', lw=0.5)
    ax2.bar(x + w/2, a_pkt, w, color='#264653', label='Agentic: Packet Drop', edgecolor='black', lw=0.5)
    ax2.bar(x + w/2, a_disc, w, bottom=a_pkt, color='#2a9d8f', label='Agentic: Disconnect', edgecolor='black', lw=0.5)
    ax2.set_title('Failure Breakdown by Category', fontsize=12, fontweight='bold')
    ax2.set_xlabel('Packet Drop Rate (%)', fontsize=10)
    ax2.set_ylabel('Failure Rate (% of attempts)', fontsize=10)
    ax2.legend(fontsize=7.5, loc='upper left'); ax2.grid(True, axis='y', ls=':', alpha=0.5)

    fig.tight_layout()
    fig.savefig(f'{OUT_DIR}\\bench_2_delivery_failures.png')
    plt.close(); print("    Done.")


# ============================================================================
# GRAPH 3: Per-Node Sync Heatmap at 80% Drop
# ============================================================================
def gen_graph3():
    print("  Generating Graph 3: Per-Node Sync Heatmap at 80% Drop...")

    _, _, b_per, _ = run_sim("baseline", 0.80)
    _, _, a_per, _ = run_sim("agentic", 0.80)

    nodes = sorted(b_per.keys())
    b_vals = [b_per[n] * 100 for n in nodes]
    a_vals = [a_per[n] * 100 for n in nodes]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5), dpi=200, sharex=True)

    # Baseline
    bars1 = ax1.bar(nodes, b_vals, color='#f77f7f', edgecolor='#d62828', lw=1)
    ax1.axhline(statistics.mean(b_vals), color='#d62828', ls='--', lw=1.5, label=f'Mean: {statistics.mean(b_vals):.1f}%')
    ax1.set_ylabel('Sync (%)', fontsize=10)
    ax1.set_title('Per-Node State Sync at 80% Packet Drop: Baseline Gossip', fontsize=11, fontweight='bold')
    ax1.set_ylim(0, 100); ax1.legend(fontsize=9); ax1.grid(True, axis='y', ls=':', alpha=0.5)
    for b, v in zip(bars1, b_vals):
        ax1.text(b.get_x() + b.get_width()/2, v + 2, f'{v:.0f}%', ha='center', fontsize=8, fontweight='bold')

    # Agentic
    bars2 = ax2.bar(nodes, a_vals, color='#90e0ef', edgecolor='#023e8a', lw=1)
    ax2.axhline(statistics.mean(a_vals), color='#023e8a', ls='--', lw=1.5, label=f'Mean: {statistics.mean(a_vals):.1f}%')
    ax2.set_ylabel('Sync (%)', fontsize=10)
    ax2.set_xlabel('Node ID', fontsize=10)
    ax2.set_title('Per-Node State Sync at 80% Packet Drop: Agentic SLM', fontsize=11, fontweight='bold')
    ax2.set_ylim(0, 100); ax2.legend(fontsize=9); ax2.grid(True, axis='y', ls=':', alpha=0.5)
    ax2.set_xticks(nodes)
    for b, v in zip(bars2, a_vals):
        ax2.text(b.get_x() + b.get_width()/2, v + 2, f'{v:.0f}%', ha='center', fontsize=8, fontweight='bold')

    fig.tight_layout()
    fig.savefig(f'{OUT_DIR}\\bench_3_pernode_heatmap.png')
    plt.close(); print("    Done.")


# ============================================================================
# GRAPH 4: Scalability — Sync vs Node Count at 60% Drop
# ============================================================================
def gen_graph4():
    print("  Generating Graph 4: Scalability (Sync vs Node Count at 60% Drop)...")
    b_syncs, a_syncs = [], []
    b_msgs, a_msgs = [], []

    for nc in NODE_COUNTS:
        bm, bs, _, _ = run_sim("baseline", 0.60, nc)
        am, as_, _, _ = run_sim("agentic", 0.60, nc)
        b_syncs.append(bs * 100); a_syncs.append(as_ * 100)
        b_msgs.append(bm.total_sent); a_msgs.append(am.total_sent)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5), dpi=200)

    # Left: sync vs node count
    ax1.plot(NODE_COUNTS, b_syncs, 's--', color='#d62828', lw=2, ms=8,
             markerfacecolor='#f77f7f', mew=1.5, label='Baseline Gossip')
    ax1.plot(NODE_COUNTS, a_syncs, 'o-', color='#0077b6', lw=2.5, ms=9,
             markerfacecolor='#00b4d8', mew=1.5, label='Agentic SLM')
    ax1.fill_between(NODE_COUNTS, b_syncs, a_syncs, alpha=0.1, color='#0077b6')
    ax1.set_title('State Sync vs Swarm Scale (60% Drop Rate)', fontsize=12, fontweight='bold')
    ax1.set_xlabel('Number of Nodes', fontsize=10)
    ax1.set_ylabel('State Synchronization (%)', fontsize=10)
    ax1.set_ylim(0, 105); ax1.legend(fontsize=9); ax1.grid(True, ls=':', alpha=0.5)
    for x, yb, ya in zip(NODE_COUNTS, b_syncs, a_syncs):
        ax1.annotate(f'{yb:.0f}%', (x, yb), textcoords='offset points',
                     xytext=(0, -14), ha='center', fontsize=7.5, color='#d62828', fontweight='bold')
        ax1.annotate(f'{ya:.0f}%', (x, ya), textcoords='offset points',
                     xytext=(0, 10), ha='center', fontsize=7.5, color='#023e8a', fontweight='bold')

    # Right: network overhead (total messages)
    w = 1.5
    x = np.array(NODE_COUNTS)
    ax2.bar(x - w/2, [m/1000 for m in b_msgs], w, color='#f77f7f', edgecolor='#d62828', lw=0.8, label='Baseline')
    ax2.bar(x + w/2, [m/1000 for m in a_msgs], w, color='#90e0ef', edgecolor='#023e8a', lw=0.8, label='Agentic SLM')
    ax2.set_title('Network Overhead vs Swarm Scale (60% Drop)', fontsize=12, fontweight='bold')
    ax2.set_xlabel('Number of Nodes', fontsize=10)
    ax2.set_ylabel('Total Transmission Attempts (x1000)', fontsize=10)
    ax2.legend(fontsize=9); ax2.grid(True, axis='y', ls=':', alpha=0.5)

    fig.tight_layout()
    fig.savefig(f'{OUT_DIR}\\bench_4_scalability.png')
    plt.close(); print("    Done.")


# ============================================================================
# Entry
# ============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  DDIL BENCHMARK SUITE — Generating 4 Graphs")
    print("=" * 60)
    gen_graph1()
    gen_graph2()
    gen_graph3()
    gen_graph4()
    print("\n  All 4 benchmark graphs saved to:")
    print(f"    1. {OUT_DIR}\\bench_1_sync_vs_drop.png")
    print(f"    2. {OUT_DIR}\\bench_2_delivery_failures.png")
    print(f"    3. {OUT_DIR}\\bench_3_pernode_heatmap.png")
    print(f"    4. {OUT_DIR}\\bench_4_scalability.png")
    print("=" * 60)
