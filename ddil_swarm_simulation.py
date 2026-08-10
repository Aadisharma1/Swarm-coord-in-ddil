#!/usr/bin/env python3


import random
import statistics
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import networkx as nx
import simpy

# ============================================================================
# Configuration Constants
# ============================================================================

NUM_NODES: int = 10                    # Total edge nodes in the swarm
SIM_DURATION: float = 100.0            # Simulation time units per phase
BROADCAST_INTERVAL: float = 2.0        # How often a node broadcasts its state
TOKEN_SIZE_BYTES: int = 256            # Mock compressed LLM token payload size
BASE_LATENCY: float = 0.5             # Baseline transmission latency (time units)
MAX_LATENCY_SPIKE: float = 8.0        # Maximum latency spike magnitude
DISCONNECT_PROBABILITY: float = 0.15  # Chance a node is temporarily offline
DISCONNECT_DURATION: float = 5.0      # How long an intermittent disconnect lasts
GOSSIP_TTL: int = 3                   # Max hops for gossip re-broadcast

# Packet drop rates to sweep through for the final benchmark
DROP_RATE_SWEEP: List[float] = [0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]

# Reproducibility
RANDOM_SEED: int = 42


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class SemanticToken:
    """
    Represents a compressed mock LLM payload (semantic token) that nodes
    exchange to synchronize their internal state matrices.
    """
    token_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    origin_node: int = 0
    timestamp: float = 0.0
    payload: bytes = field(default_factory=lambda: random.randbytes(TOKEN_SIZE_BYTES))
    ttl: int = GOSSIP_TTL  # Remaining hops before the token expires

    def __repr__(self) -> str:
        return f"Token({self.token_id} from N{self.origin_node} @t={self.timestamp:.1f})"


@dataclass
class TransmissionResult:
    """Records the outcome of a single transmission attempt."""
    sender: int
    receiver: int
    token_id: str
    timestamp: float
    success: bool
    failure_reason: Optional[str] = None


# ============================================================================
# Metrics Collector
# ============================================================================

class MetricsCollector:
    """
    Centralized metrics aggregator. Tracks all transmission attempts and
    outcomes across the simulation for post-hoc analysis.
    """

    def __init__(self):
        self.results: List[TransmissionResult] = []

    def record(self, result: TransmissionResult) -> None:
        """Record a single transmission result."""
        self.results.append(result)

    @property
    def total_sent(self) -> int:
        """Total number of transmission attempts."""
        return len(self.results)

    @property
    def total_delivered(self) -> int:
        """Total number of successfully delivered and parsed messages."""
        return sum(1 for r in self.results if r.success)

    @property
    def delivery_rate(self) -> float:
        """Message delivery success rate as a fraction [0.0, 1.0]."""
        if self.total_sent == 0:
            return 0.0
        return self.total_delivered / self.total_sent

    def reset(self) -> None:
        """Clear all recorded metrics for a fresh simulation phase."""
        self.results.clear()

    def summary(self) -> str:
        """Human-readable summary string."""
        failures: Dict[str, int] = {}
        for r in self.results:
            if not r.success and r.failure_reason:
                failures[r.failure_reason] = failures.get(r.failure_reason, 0) + 1
        failure_str = ", ".join(f"{k}: {v}" for k, v in sorted(failures.items()))
        return (
            f"  Sent: {self.total_sent} | Delivered: {self.total_delivered} | "
            f"Rate: {self.delivery_rate:.2%}\n"
            f"  Failure Breakdown: {failure_str if failure_str else 'None'}"
        )


# ============================================================================
# Environment Controller (DDIL Degradation Engine)
# ============================================================================

class EnvironmentController:
    """
    Controls the simulated DDIL (Disrupted, Disconnected, Intermittent,
    Low-Bandwidth) network environment. Dynamically injects:
        - Configurable packet drop rates
        - Random latency spikes on surviving transmissions
        - Intermittent node disconnects (nodes go offline temporarily)
    """

    def __init__(
        self,
        env: simpy.Environment,
        graph: nx.Graph,
        packet_drop_rate: float = 0.0,
    ):
        self.env = env
        self.graph = graph
        self.packet_drop_rate = packet_drop_rate

        # Track which nodes are currently disconnected
        self._disconnected_nodes: set = set()

        # Start the background disconnect injector process
        self.env.process(self._disconnect_injector())

    def _disconnect_injector(self) -> simpy.events.ProcessGenerator:
        """
        Background SimPy process that randomly disconnects nodes for
        short durations, simulating intermittent link failures.
        """
        while True:
            yield self.env.timeout(random.uniform(3.0, 8.0))

            # Pick a random node to disconnect
            node_id = random.randint(0, NUM_NODES - 1)
            if random.random() < DISCONNECT_PROBABILITY and node_id not in self._disconnected_nodes:
                self._disconnected_nodes.add(node_id)
                duration = random.uniform(1.0, DISCONNECT_DURATION)
                print(
                    f"  [t={self.env.now:6.1f}] ENV: Node {node_id} DISCONNECTED "
                    f"(intermittent outage, ~{duration:.1f}s)"
                )
                # Schedule reconnection
                self.env.process(self._reconnect(node_id, duration))

    def _reconnect(self, node_id: int, duration: float) -> simpy.events.ProcessGenerator:
        """Reconnect a node after the disconnect duration expires."""
        yield self.env.timeout(duration)
        self._disconnected_nodes.discard(node_id)
        print(
            f"  [t={self.env.now:6.1f}] ENV: Node {node_id} RECONNECTED"
        )

    def is_disconnected(self, node_id: int) -> bool:
        """Check if a node is currently in a disconnected state."""
        return node_id in self._disconnected_nodes

    def attempt_transmission(
        self, sender_id: int, receiver_id: int, token: SemanticToken
    ) -> Tuple[bool, float, Optional[str]]:
        """
        Evaluate whether a transmission from sender to receiver succeeds
        under current DDIL conditions.

        Returns:
            (success: bool, latency: float, failure_reason: Optional[str])
        """
        # --- Check 1: Sender disconnected ---
        if self.is_disconnected(sender_id):
            return False, 0.0, "Sender Disconnected"

        # --- Check 2: Receiver disconnected ---
        if self.is_disconnected(receiver_id):
            return False, 0.0, "Receiver Disconnected"

        # --- Check 3: No edge in topology (nodes not in range) ---
        if not self.graph.has_edge(sender_id, receiver_id):
            return False, 0.0, "No Topology Edge"

        # --- Check 4: Packet drop (Gilbert-Elliott style) ---
        if random.random() < self.packet_drop_rate:
            return False, 0.0, "Packet Dropped (RF Loss)"

        # --- Transmission succeeds: compute latency ---
        # Base latency + random spike component
        latency = BASE_LATENCY + random.expovariate(1.0 / 1.5)
        # Occasional severe latency spikes (10% chance)
        if random.random() < 0.10:
            latency += random.uniform(2.0, MAX_LATENCY_SPIKE)

        return True, latency, None


# ============================================================================
# Edge Node (Autonomous Agent)
# ============================================================================

class EdgeNode:
    """
    An autonomous edge compute node in the decentralized swarm.

    Each node maintains a local state matrix and periodically broadcasts
    compressed semantic tokens to its mesh neighbors via the gossip protocol.
    Incoming tokens are parsed and merged into the local state.
    """

    def __init__(
        self,
        node_id: int,
        env: simpy.Environment,
        env_ctrl: EnvironmentController,
        graph: nx.Graph,
        metrics: MetricsCollector,
    ):
        self.node_id = node_id
        self.env = env
        self.env_ctrl = env_ctrl
        self.graph = graph
        self.metrics = metrics

        # Local state matrix: maps token_id -> SemanticToken
        # This is what nodes try to keep in sync across the swarm
        self.state_matrix: Dict[str, SemanticToken] = {}

        # Set of token IDs already seen (prevents re-broadcasting loops)
        self._seen_tokens: set = set()

        # Start the broadcast loop as a SimPy process
        self.env.process(self._broadcast_loop())

    def _broadcast_loop(self) -> simpy.events.ProcessGenerator:
        """
        Periodic broadcast process. Every BROADCAST_INTERVAL time units,
        this node generates a new semantic token and attempts to gossip
        it to all direct neighbors in the mesh topology.
        """
        # Stagger start times so nodes don't all broadcast simultaneously
        yield self.env.timeout(random.uniform(0.0, BROADCAST_INTERVAL))

        while True:
            # Generate a fresh semantic token
            token = SemanticToken(
                origin_node=self.node_id,
                timestamp=self.env.now,
            )

            # Insert into own state matrix
            self.state_matrix[token.token_id] = token
            self._seen_tokens.add(token.token_id)

            # Attempt to send to all neighbors
            neighbors = list(self.graph.neighbors(self.node_id))
            for neighbor_id in neighbors:
                self.env.process(
                    self._send_token(neighbor_id, token)
                )

            # Wait before next broadcast cycle
            yield self.env.timeout(
                BROADCAST_INTERVAL + random.uniform(-0.3, 0.3)
            )

    def _send_token(
        self, receiver_id: int, token: SemanticToken
    ) -> simpy.events.ProcessGenerator:
        """
        Attempt to transmit a semantic token to a specific neighbor node.
        Subject to DDIL environment constraints (drops, latency, disconnects).
        """
        success, latency, failure_reason = self.env_ctrl.attempt_transmission(
            self.node_id, receiver_id, token
        )

        # Record the transmission attempt
        result = TransmissionResult(
            sender=self.node_id,
            receiver=receiver_id,
            token_id=token.token_id,
            timestamp=self.env.now,
            success=success,
            failure_reason=failure_reason,
        )
        self.metrics.record(result)

        if not success:
            print(
                f"  [t={self.env.now:6.1f}] Node {self.node_id} failed to reach "
                f"Node {receiver_id}: {failure_reason}"
            )
            return

        # Simulate network latency before delivery
        yield self.env.timeout(latency)

        # Deliver token to the receiver's receive handler
        # (We access receiver nodes via a global registry set up in main)
        if receiver_id in _node_registry:
            _node_registry[receiver_id].receive_token(token)

    def receive_token(self, token: SemanticToken) -> None:
        """
        Handle an incoming semantic token. Parse it, merge into local state
        matrix, and optionally re-broadcast (gossip) if TTL allows.
        """
        # Deduplication: skip if already seen
        if token.token_id in self._seen_tokens:
            return

        self._seen_tokens.add(token.token_id)

        # Merge into local state matrix
        self.state_matrix[token.token_id] = token

        # Gossip re-broadcast: decrement TTL and forward to neighbors
        if token.ttl > 1:
            forwarded_token = SemanticToken(
                token_id=token.token_id,
                origin_node=token.origin_node,
                timestamp=token.timestamp,
                payload=token.payload,
                ttl=token.ttl - 1,
            )
            neighbors = list(self.graph.neighbors(self.node_id))
            for neighbor_id in neighbors:
                if neighbor_id != token.origin_node:
                    self.env.process(
                        self._send_token(neighbor_id, forwarded_token)
                    )


# Global node registry so nodes can deliver tokens to each other
_node_registry: Dict[int, EdgeNode] = {}


# ============================================================================
# Topology Builder
# ============================================================================

def build_mesh_topology(num_nodes: int) -> nx.Graph:
    """
    Build a connected random mesh graph representing the peer-to-peer
    communication topology of the swarm.

    Uses a Watts-Strogatz small-world model to create realistic mesh
    connectivity: each node connects to its k nearest neighbors in a ring,
    with some edges randomly rewired for shorter path lengths.

    Args:
        num_nodes: Number of nodes in the graph.

    Returns:
        A connected NetworkX graph.
    """
    # k=4 neighbors, 30% rewiring probability
    G = nx.watts_strogatz_graph(num_nodes, k=4, p=0.3, seed=RANDOM_SEED)

    # Ensure full connectivity (add edges if any components are isolated)
    if not nx.is_connected(G):
        components = list(nx.connected_components(G))
        for i in range(1, len(components)):
            u = list(components[0])[0]
            v = list(components[i])[0]
            G.add_edge(u, v)

    print(f"[TOPOLOGY] Built mesh: {G.number_of_nodes()} nodes, "
          f"{G.number_of_edges()} edges")
    print(f"[TOPOLOGY] Avg degree: {sum(d for _, d in G.degree()) / G.number_of_nodes():.1f}")
    print(f"[TOPOLOGY] Diameter: {nx.diameter(G)}")
    return G


# ============================================================================
# Single Simulation Run
# ============================================================================

def run_single_simulation(
    packet_drop_rate: float,
    duration: float = SIM_DURATION,
    verbose: bool = True,
) -> float:
    """
    Execute one full simulation at a given packet drop rate.

    Args:
        packet_drop_rate: Fraction of packets dropped [0.0, 1.0].
        duration: How long the simulation runs (time units).
        verbose: Whether to print per-event logs.

    Returns:
        Message delivery success rate as a float [0.0, 1.0].
    """
    global _node_registry

    random.seed(RANDOM_SEED)

    # --- Setup ---
    env = simpy.Environment()
    graph = build_mesh_topology(NUM_NODES)
    metrics = MetricsCollector()

    # Create the DDIL environment controller
    env_ctrl = EnvironmentController(
        env=env,
        graph=graph,
        packet_drop_rate=packet_drop_rate,
    )

    # Instantiate all edge nodes
    _node_registry.clear()
    for i in range(NUM_NODES):
        node = EdgeNode(
            node_id=i,
            env=env,
            env_ctrl=env_ctrl,
            graph=graph,
            metrics=metrics,
        )
        _node_registry[i] = node

    if verbose:
        print(f"\n{'='*70}")
        print(f"  SIMULATION START | Packet Drop Rate: {packet_drop_rate:.0%}")
        print(f"{'='*70}")

    # --- Run ---
    env.run(until=duration)

    # --- Results ---
    if verbose:
        print(f"\n--- Phase Complete (Drop Rate: {packet_drop_rate:.0%}) ---")
        print(metrics.summary())

        # State matrix sync check
        all_token_ids = set()
        for node in _node_registry.values():
            all_token_ids.update(node.state_matrix.keys())
        sync_rates = []
        for node in _node_registry.values():
            if len(all_token_ids) > 0:
                rate = len(node.state_matrix) / len(all_token_ids)
                sync_rates.append(rate)
        if sync_rates:
            print(
                f"  State Matrix Sync: mean={statistics.mean(sync_rates):.2%}, "
                f"min={min(sync_rates):.2%}, max={max(sync_rates):.2%}"
            )

    return metrics.delivery_rate


# ============================================================================
# Full Benchmark Sweep & Visualization
# ============================================================================

def run_benchmark_sweep() -> None:
    """
    Sweep across all configured packet drop rates, collect delivery metrics,
    and produce the final matplotlib performance plot.
    """
    print("\n" + "=" * 70)
    print("  DDIL SWARM SIMULATION BENCHMARK")
    print(f"  Nodes: {NUM_NODES} | Duration: {SIM_DURATION} time units | "
          f"Gossip TTL: {GOSSIP_TTL}")
    print("=" * 70)

    drop_rates: List[float] = []
    delivery_rates: List[float] = []

    for rate in DROP_RATE_SWEEP:
        # Run with verbose=True only for first and last
        verbose = (rate == DROP_RATE_SWEEP[0] or rate == DROP_RATE_SWEEP[-1])
        delivery = run_single_simulation(
            packet_drop_rate=rate,
            verbose=verbose,
        )
        drop_rates.append(rate * 100)  # Convert to percentage
        delivery_rates.append(delivery * 100)  # Convert to percentage

        print(
            f"  >> Drop Rate: {rate:5.0%} | "
            f"Delivery Rate: {delivery:6.2%}"
        )

    # --- Print Summary Table ---
    print(f"\n{'='*50}")
    print(f"  {'Drop Rate':>12} | {'Delivery Rate':>15}")
    print(f"  {'-'*12}-+-{'-'*15}")
    for dr, dlr in zip(drop_rates, delivery_rates):
        print(f"  {dr:11.0f}% | {dlr:14.1f}%")
    print(f"{'='*50}\n")

    # --- Generate Matplotlib Plot ---
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)

    # Main delivery rate line
    ax.plot(
        drop_rates, delivery_rates,
        marker='o', color='#0077b6', linewidth=2.5, markersize=8,
        markerfacecolor='#00b4d8', markeredgecolor='#023e8a',
        markeredgewidth=1.5, label='Gossip Protocol Delivery Rate',
        zorder=3,
    )

    # Theoretical baseline: perfect delivery = 100% - drop_rate
    theoretical = [100.0 - dr for dr in drop_rates]
    ax.plot(
        drop_rates, theoretical,
        linestyle='--', color='#adb5bd', linewidth=1.5,
        label='Theoretical Single-Hop Baseline (1 - drop rate)',
        zorder=2,
    )

    # Styling
    ax.set_title(
        'Message Delivery Success Rate vs. Environmental Packet Drop Rate\n'
        'Decentralized Gossip Protocol Under Progressive DDIL Degradation',
        fontsize=13, fontweight='bold', pad=15,
    )
    ax.set_xlabel('Environmental Packet Drop Rate (%)', fontsize=11)
    ax.set_ylabel('Message Delivery Success Rate (%)', fontsize=11)
    ax.set_xlim(-2, 82)
    ax.set_ylim(0, 105)
    ax.grid(True, linestyle=':', alpha=0.6, color='#dee2e6')
    ax.legend(loc='lower left', fontsize=10, framealpha=0.95)

    # Annotate key data points
    for x, y in zip(drop_rates, delivery_rates):
        ax.annotate(
            f'{y:.1f}%',
            xy=(x, y), xytext=(0, 12),
            textcoords='offset points', ha='center', fontsize=8,
            color='#023e8a', fontweight='bold',
        )

    # Add configuration text box
    config_text = (
        f"Nodes: {NUM_NODES}\n"
        f"Topology: Watts-Strogatz (k=4, p=0.3)\n"
        f"Gossip TTL: {GOSSIP_TTL} hops\n"
        f"Broadcast Interval: {BROADCAST_INTERVAL}s\n"
        f"Disconnect Prob: {DISCONNECT_PROBABILITY:.0%}"
    )
    ax.text(
        0.98, 0.98, config_text,
        transform=ax.transAxes, fontsize=8,
        verticalalignment='top', horizontalalignment='right',
        bbox=dict(boxstyle='round,pad=0.5', facecolor='#f8f9fa',
                  edgecolor='#adb5bd', alpha=0.9),
    )

    fig.tight_layout()
    output_path = r'C:\Users\Aadi Sharma\OneDrive\Desktop\convert\ddil_benchmark_results.png'
    fig.savefig(output_path)
    plt.close()
    print(f"[OUTPUT] Benchmark plot saved to: {output_path}")


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    run_benchmark_sweep()
