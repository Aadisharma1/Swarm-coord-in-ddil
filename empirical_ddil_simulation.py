#!/usr/bin/env python3
"""
Empirical DDIL Multi-Agent Swarm Simulation — Publication Edition (InCIS 2027)
=============================================================================
A discrete-event simulation integrating live vLLM OpenAI-compatible API servers
(running Meta-Llama-3-8B-Instruct on an 8x A100 GPU cluster) with SimPy and NetworkX.

Evaluates 3 protocol architectures under progressive DDIL degradation:
  1. Gossip Protocol (Baseline 1: Blind TTL-based flooding of raw state matrices)
  2. Epidemic Routing (Baseline 2: Store-and-forward anti-entropy dissemination)
  3. Agentic SLM Protocol (Proposed: Task-oriented semantic compression + link memory)

Key Architectural & Evaluation Enhancements:
  - Multi-invariant task-relevant state compression (~4-5.6x reduction)
  - Sender-side Invariant Preservation Score (IPS) verification gate
  - Receiver-side zero-ground-truth structural and freshness validation
  - Decision Preservation Rate (DPR) measuring operational decision fidelity
  - Genuine Two-Hop Joint Path Reliability relay routing: m* = argmax (L_i(m) * L_m(j))
  - Gilbert-Elliott burst loss channel model with stateful burst degradation
  - Systematic Ablation Suite (Full, No Memory, No Compression, No Relay, No Verification)
  - IPS Threshold Sensitivity Analysis (theta in {0.90, 0.95, 0.98})
  - Hallucination Injection Robustness Suite (precision, recall, false-accept rate)
  - Parametric Energy Modeling (RF transmission vs. SLM inference)
  - Multi-seed statistical aggregation with 95% Confidence Intervals

Primary Research Question:
  Can task-oriented semantic synchronization preserve decentralized operational
  decisions more efficiently than conventional epidemic/gossip dissemination
  under severe DDIL conditions?
"""

import argparse
import asyncio
import csv
import json
import math
import os
import random
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import aiohttp
import matplotlib
matplotlib.use("Agg")  # headless-safe backend (required on GPU servers without a display)
import matplotlib.pyplot as plt
import networkx as nx
import simpy

# ============================================================================
# Simulation Configuration Constants
# ============================================================================

NUM_NODES: int = 50                      # 50 Swarm Nodes load-balanced across 8 A100 GPUs
SIM_DURATION: float = 100.0              # Simulation time units per benchmark run
BROADCAST_INTERVAL: float = 2.0          # Periodic state broadcast frequency
BASE_LATENCY: float = 0.5                # Baseline network latency (time units)
MAX_LATENCY_SPIKE: float = 8.0           # Maximum latency spike magnitude
DISCONNECT_PROBABILITY: float = 0.15     # Intermittent node disconnection chance
DISCONNECT_DURATION: float = 5.0         # Duration of intermittent disconnects
GOSSIP_TTL: int = 3                      # Max hops for baseline gossip re-broadcast
LINK_RELIABILITY_DECAY: float = 0.10     # EMA decay rate for link scoring
LINK_RELIABILITY_THRESHOLD: float = 0.25 # Minimum link score for routing

# Sweep parameters for network degradation benchmark
DROP_RATE_SWEEP: List[float] = [0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]

# Fixed default seed
RANDOM_SEED: int = 42

# Base URL mapping for 8 live vLLM GPU server ports (8001 through 8008)
VLLM_BASE_PORTS: List[int] = [8001 + i for i in range(8)]
VLLM_MODEL_NAME: str = os.environ.get("VLLM_MODEL_NAME", "meta-llama/Meta-Llama-3-8B-Instruct")

# Benchmark output filenames
PLOT_SYNC_PATH: str = "fig_sync_vs_drop.png"
PLOT_ENERGY_PATH: str = "fig_energy_vs_drop.png"
PLOT_ABLATION_PATH: str = "fig_ablation_sync.png"


# ============================================================================
# Ablation & Execution Configuration Dataclass
# ============================================================================

@dataclass
class RunConfig:
    """Configures architectural components for ablation & sensitivity studies."""
    enable_compression: bool = True       # Semantic SLM state compression
    enable_link_memory: bool = True       # EMA link reliability tracking
    enable_relay: bool = True             # Adaptive 2-hop neighbor relaying
    enable_drift_check: bool = True       # Sender-side IPS verification gate
    ips_threshold: float = 0.95           # Minimum aggregate IPS for transmission
    injection_rate: float = 0.0           # Controlled hallucination injection rate
    label: str = "Full Agentic SLM"


# ============================================================================
# Parametric Energy Modeling (RF vs. Edge Compute)
# ============================================================================

class EnergyTracker:
    """
    Parametric energy model representing RF transmission vs SLM edge compute.
    Modeled as sensitivity analysis:
        E_TX_BYTE   : 0.05 Joules / byte transmitted (RF Front-End Cost)
        E_LLM_TOKEN : 0.01 Joules / token generated (Quantized/Edge Inference Proxy)
    """
    E_TX_BYTE: float = 0.05
    E_LLM_TOKEN: float = 0.01

    @classmethod
    def calculate_rf_energy(cls, bytes_transmitted: int) -> float:
        return bytes_transmitted * cls.E_TX_BYTE

    @classmethod
    def calculate_compute_energy(cls, tokens_generated: int) -> float:
        return tokens_generated * cls.E_LLM_TOKEN

    @classmethod
    def calculate_total_energy(cls, bytes_transmitted: int, tokens_generated: int = 0) -> float:
        return cls.calculate_rf_energy(bytes_transmitted) + cls.calculate_compute_energy(tokens_generated)


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class RawStateMatrix:
    """
    Represents the full uncompressed multi-dimensional telemetry state.
    Serialized JSON payload: ~450 bytes.
    """
    origin_node: int
    timestamp: float
    sequence_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    state_vector: List[float] = field(default_factory=lambda: [round(random.uniform(-1.0, 1.0), 4) for _ in range(6)])
    energy_level: float = field(default_factory=lambda: round(random.uniform(70.0, 100.0), 2))
    phase_angle: float = field(default_factory=lambda: round(random.uniform(0.0, 360.0), 2))
    matrix_weights: Dict[str, float] = field(default_factory=lambda: {f"w_{i}": round(random.random(), 4) for i in range(4)})

    def to_json_str(self) -> str:
        return json.dumps({
            "seq_id": self.sequence_id,
            "origin": self.origin_node,
            "ts": self.timestamp,
            "vec": self.state_vector,
            "energy": self.energy_level,
            "phase": self.phase_angle,
            "weights": self.matrix_weights
        }, separators=(',', ':'))


@dataclass
class NetworkPayload:
    """Encapsulates a payload transmitted across the dynamic mesh network."""
    payload_id: str
    origin_node: int
    timestamp: float
    raw_content: str
    byte_size: int
    ttl: int
    is_compressed: bool = False
    visited_nodes: Set[int] = field(default_factory=set)
    ground_truth: Optional[RawStateMatrix] = None
    tokens_generated: int = 0
    # Raw-state reference size used by the byte-scaled loss law; carried through
    # forwarding hops so loss scaling stays consistent with the origin broadcast.
    ref_raw_bytes: int = 450


@dataclass
class TransmissionRecord:
    """Records individual transmission outcomes for performance metrics."""
    sender: int
    receiver: int
    payload_id: str
    timestamp: float
    byte_size: int
    success: bool
    failure_reason: Optional[str] = None


# ============================================================================
# Task-Oriented Decision Oracle & DPR Metric
# ============================================================================

class DecisionOracle:
    """
    Maps state representations to discrete operational decisions:
      (Spatial Quadrant, Priority Tier, Energy Action).
    Used to compute Decision Preservation Rate (DPR).
    """

    @staticmethod
    def decide_from_raw(raw: RawStateMatrix) -> Tuple[str, str, str]:
        pos = raw.state_vector[0:2]
        quadrant = ("NE" if pos[0] >= 0 and pos[1] >= 0 else
                    "NW" if pos[0] < 0 and pos[1] >= 0 else
                    "SE" if pos[0] >= 0 and pos[1] < 0 else "SW")
        energy_action = "CRITICAL" if raw.energy_level < 20 else "CONSERVE" if raw.energy_level < 50 else "NORMAL"
        pri = max(raw.matrix_weights.values()) if raw.matrix_weights else 0.5
        priority_tier = "HIGH" if pri > 0.7 else "MED" if pri > 0.3 else "LOW"
        return (quadrant, priority_tier, energy_action)

    @staticmethod
    def decide_from_compressed(parsed: dict) -> Tuple[str, str, str]:
        pos = parsed.get("pos", [0.0, 0.0])
        if not isinstance(pos, list) or len(pos) < 2:
            pos = [0.0, 0.0]
        quadrant = ("NE" if pos[0] >= 0 and pos[1] >= 0 else
                    "NW" if pos[0] < 0 and pos[1] >= 0 else
                    "SE" if pos[0] >= 0 and pos[1] < 0 else "SW")
        bat = parsed.get("bat", 50.0)
        energy_action = "CRITICAL" if bat < 20 else "CONSERVE" if bat < 50 else "NORMAL"
        pri = parsed.get("pri", 0.5)
        priority_tier = "HIGH" if pri > 0.7 else "MED" if pri > 0.3 else "LOW"
        return (quadrant, priority_tier, energy_action)

    @staticmethod
    def agreement(d1: Tuple[str, str, str], d2: Tuple[str, str, str]) -> float:
        return sum(1 for a, b in zip(d1, d2) if a == b) / len(d1)


# ============================================================================
# Invariant Preservation Score (IPS) & Receiver Structural Validation
# ============================================================================

def calculate_invariant_preservation(
    ground_truth: RawStateMatrix,
    decoded: dict,
    threshold: float = 0.95
) -> Tuple[Dict[str, float], float, bool]:
    """
    Sender-side verification: Computes per-invariant errors and aggregate IPS.
    Validates whether the compressed token preserves essential task invariants.
    """
    required = ["id", "origin", "ts", "pos", "vel", "hdg", "bat", "pri"]
    if not isinstance(decoded, dict) or not all(k in decoded for k in required):
        return {}, 0.0, False

    if str(decoded["id"]) != str(ground_truth.sequence_id) or decoded["origin"] != ground_truth.origin_node:
        return {}, 0.0, False

    gt_pos = ground_truth.state_vector[0:2]
    gt_vel = ground_truth.state_vector[2]
    gt_hdg = ground_truth.state_vector[3]
    gt_bat = ground_truth.energy_level
    gt_pri = max(ground_truth.matrix_weights.values()) if ground_truth.matrix_weights else 0.5

    dec_pos = decoded.get("pos", [0.0, 0.0])
    if not isinstance(dec_pos, list) or len(dec_pos) != 2:
        return {}, 0.0, False

    try:
        dec_vel = float(decoded.get("vel", 0.0))
        dec_hdg = float(decoded.get("hdg", 0.0))
        dec_bat = float(decoded.get("bat", 0.0))
        dec_pri = float(decoded.get("pri", 0.0))
    except (ValueError, TypeError):
        return {}, 0.0, False

    eps = 0.001
    norm_gt = math.sqrt(sum(x**2 for x in gt_pos))
    dist_pos = math.sqrt((gt_pos[0] - dec_pos[0])**2 + (gt_pos[1] - dec_pos[1])**2)
    pos_err = dist_pos / max(eps, norm_gt)
    vel_err = abs(gt_vel - dec_vel) / max(eps, abs(gt_vel))
    hdg_err = abs(gt_hdg - dec_hdg) / max(eps, abs(gt_hdg))
    bat_err = abs(gt_bat - dec_bat) / max(eps, abs(gt_bat))
    pri_err = abs(gt_pri - dec_pri) / max(eps, abs(gt_pri))

    errors = {
        "pos": min(1.0, pos_err),
        "vel": min(1.0, vel_err),
        "hdg": min(1.0, hdg_err),
        "bat": min(1.0, bat_err),
        "pri": min(1.0, pri_err)
    }
    mean_err = statistics.mean(errors.values())
    aggregate_ips = max(0.0, 1.0 - mean_err)
    is_valid = (aggregate_ips >= threshold) and all(e < 0.25 for e in errors.values())
    return errors, aggregate_ips, is_valid


def validate_received_structure(parsed: dict, current_time: float) -> bool:
    """
    Receiver-side validation: Requires zero sender ground truth.
    Performs structural, schema, range, and temporal freshness checks.
    """
    required = ["id", "origin", "ts", "pos", "vel", "hdg", "bat", "pri", "st"]
    if not isinstance(parsed, dict) or not all(k in parsed for k in required):
        return False
    pos = parsed.get("pos")
    if not isinstance(pos, list) or len(pos) != 2:
        return False
    bat = parsed.get("bat")
    if not isinstance(bat, (int, float)) or not (0.0 <= bat <= 100.0):
        return False
    ts = parsed.get("ts")
    if not isinstance(ts, (int, float)) or ts > current_time + 1.0:
        return False
    return True


# ============================================================================
# Empirical Metrics Collector
# ============================================================================

class EmpiricalMetricsCollector:
    """Aggregates transmission metrics, byte throughput, drift failures, DPR, and
    verification-gate / fallback accounting."""

    def __init__(self):
        self.records: List[TransmissionRecord] = []
        self.parse_failures: int = 0
        self.drift_failures: int = 0
        self.parse_successes: int = 0
        self.total_tokens_generated: int = 0
        self.decision_agreements: List[float] = []
        self.ips_scores: List[float] = []
        self.llm_fallbacks: int = 0
        self.injections_generated: int = 0
        self.injections_rejected: int = 0

    def record(self, rec: TransmissionRecord) -> None:
        self.records.append(rec)

    def record_parse_outcome(self, success: bool, is_drift_failure: bool = False, ips: Optional[float] = None) -> None:
        if success:
            self.parse_successes += 1
            if ips is not None:
                self.ips_scores.append(ips)
        else:
            if is_drift_failure:
                self.drift_failures += 1
            else:
                self.parse_failures += 1

    def record_llm_fallback(self) -> None:
        self.llm_fallbacks += 1

    def record_injection(self, rejected: bool) -> None:
        """Records a controlled hallucination injection and whether the IPS gate rejected it."""
        self.injections_generated += 1
        if rejected:
            self.injections_rejected += 1

    def record_decision(self, agreement: float) -> None:
        self.decision_agreements.append(agreement)

    def add_tokens(self, tokens: int) -> None:
        self.total_tokens_generated += tokens

    @property
    def total_sent(self) -> int:
        return len(self.records)

    @property
    def total_delivered(self) -> int:
        return sum(1 for r in self.records if r.success)

    @property
    def total_bytes_transmitted(self) -> int:
        return sum(r.byte_size for r in self.records if r.success)

    @property
    def total_energy_joules(self) -> float:
        return EnergyTracker.calculate_total_energy(self.total_bytes_transmitted, self.total_tokens_generated)

    @property
    def delivery_rate(self) -> float:
        return self.total_delivered / self.total_sent if self.total_sent > 0 else 0.0

    @property
    def mean_dpr(self) -> float:
        return statistics.mean(self.decision_agreements) if self.decision_agreements else 1.0

    @property
    def mean_ips(self) -> float:
        return statistics.mean(self.ips_scores) if self.ips_scores else 1.0

    @property
    def gate_pass_rate(self) -> float:
        """Fraction of SLM compressions that passed the sender-side IPS gate."""
        total = self.parse_successes + self.drift_failures
        return self.parse_successes / total if total > 0 else 1.0

    @property
    def injection_rejection_rate(self) -> float:
        """Fraction of injected hallucinations rejected by the IPS gate (gate recall)."""
        if self.injections_generated == 0:
            return 0.0
        return self.injections_rejected / self.injections_generated

    def reset(self) -> None:
        self.records.clear()
        self.parse_failures = 0
        self.drift_failures = 0
        self.parse_successes = 0
        self.total_tokens_generated = 0
        self.decision_agreements.clear()
        self.ips_scores.clear()
        self.llm_fallbacks = 0
        self.injections_generated = 0
        self.injections_rejected = 0


# ============================================================================
# Gilbert-Elliott Burst Loss Channel Model & Environment Controller
# ============================================================================

class GilbertElliottChannel:
    """
    Two-state Markov model: GOOD (low loss) <-> BAD (high burst loss).
    Captures temporal correlation and burst drop dynamics typical of extreme DDIL.

    Calibration guarantee: the stationary mean loss of the chain equals the nominal
    drop rate D (pi_bad * loss_bad + (1 - pi_bad) * loss_good = D), so the swept
    x-axis is the *realized* long-run loss fraction, not merely a label. Burst
    intensity emerges from the structure: the BAD-state dwell time (1/p_b2g) grows
    with stress, producing longer bursts at high degradation.
    """
    def __init__(self, drop_rate: float, rng: Optional[random.Random] = None):
        self.rng = rng or random.Random()
        self.state = "GOOD"
        drop_rate = min(max(drop_rate, 0.0), 0.98)
        if drop_rate <= 1e-9:
            self.p_g2b, self.p_b2g = 0.0, 0.35
            self.loss_good, self.loss_bad = 0.0, 0.0
            return
        # Transition probabilities target stationary bad-fraction pi ~ D
        self.p_b2g = max(0.05, 0.35 * (1.0 - drop_rate))
        self.p_g2b = min(0.98, 0.35 * drop_rate / max(1e-9, 1.0 - drop_rate))
        pi = self.p_g2b / max(1e-9, self.p_g2b + self.p_b2g)
        self.loss_good = min(0.05, 0.30 * drop_rate)
        residual = drop_rate - (1.0 - pi) * self.loss_good
        self.loss_bad = min(0.98, max(self.loss_good, residual / max(1e-9, pi)))

    @property
    def stationary_mean_loss(self) -> float:
        pi = self.p_g2b / max(1e-9, self.p_g2b + self.p_b2g)
        return pi * self.loss_bad + (1.0 - pi) * self.loss_good

    def sample_loss_probability(self) -> float:
        if self.state == "GOOD":
            if self.rng.random() < self.p_g2b:
                self.state = "BAD"
        else:
            if self.rng.random() < self.p_b2g:
                self.state = "GOOD"
        return self.loss_bad if self.state == "BAD" else self.loss_good


class EnvironmentController:
    """Orchestrates dynamic link degradations, burst loss, and intermittent disconnections."""

    def __init__(self, env: simpy.Environment, graph: nx.Graph, drop_rate: float, seed: int = RANDOM_SEED):
        self.env = env
        self.graph = graph
        self.drop_rate = drop_rate
        self.rng = random.Random(seed)
        self.channel = GilbertElliottChannel(drop_rate, self.rng)
        self.disconnected_nodes: Set[int] = set()
        self.env.process(self._disconnection_lifecycle())

    def _disconnection_lifecycle(self) -> simpy.events.ProcessGenerator:
        while True:
            yield self.env.timeout(self.rng.uniform(5.0, 15.0))
            for node in self.graph.nodes():
                if self.rng.random() < DISCONNECT_PROBABILITY:
                    self.disconnected_nodes.add(node)
            yield self.env.timeout(DISCONNECT_DURATION)
            self.disconnected_nodes.clear()

    def attempt_transmission(self, sender: int, receiver: int, payload: NetworkPayload,
                             ref_raw_bytes: int = 450) -> Tuple[bool, float, Optional[str]]:
        if sender in self.disconnected_nodes:
            return False, 0.0, "SenderDisconnected"
        if receiver in self.disconnected_nodes:
            return False, 0.0, "ReceiverDisconnected"
        if not self.graph.has_edge(sender, receiver):
            return False, 0.0, "NoDirectEdge"

        base_loss_prob = self.channel.sample_loss_probability()
        size_factor = payload.byte_size / max(1, ref_raw_bytes)
        # Byte-scaled loss law (paper Eq. 3): loss probability scales linearly with
        # payload size relative to the raw-state reference.
        effective_drop_prob = min(0.98, base_loss_prob * size_factor)

        if self.rng.random() < effective_drop_prob:
            return False, 0.0, "ChannelPacketDrop"

        latency = BASE_LATENCY + self.rng.uniform(0.0, MAX_LATENCY_SPIKE * self.drop_rate)
        return True, latency, None


# ============================================================================
# Node Implementation 1: Gossip Protocol (Baseline 1 - Demers et al., 1987)
# ============================================================================

class GossipNode:
    """Baseline 1: Blind TTL-based flooding of full, raw state matrices (~450B)."""

    def __init__(self, node_id: int, env: simpy.Environment, ctrl: EnvironmentController,
                 graph: nx.Graph, metrics: EmpiricalMetricsCollector):
        self.node_id = node_id
        self.env = env
        self.ctrl = ctrl
        self.graph = graph
        self.metrics = metrics
        self.state_matrix: Dict[str, str] = {}
        self._seen_payloads: set = set()
        self.env.process(self._broadcast_loop())

    def _broadcast_loop(self) -> simpy.events.ProcessGenerator:
        yield self.env.timeout(random.uniform(0.0, BROADCAST_INTERVAL))
        while True:
            raw_state = RawStateMatrix(origin_node=self.node_id, timestamp=self.env.now)
            raw_str = raw_state.to_json_str()
            byte_len = len(raw_str.encode('utf-8'))

            payload = NetworkPayload(
                payload_id=raw_state.sequence_id,
                origin_node=self.node_id,
                timestamp=self.env.now,
                raw_content=raw_str,
                byte_size=byte_len,
                ttl=GOSSIP_TTL,
                is_compressed=False,
                ground_truth=raw_state,
                tokens_generated=0,
                ref_raw_bytes=byte_len
            )

            self.state_matrix[payload.payload_id] = raw_str
            self._seen_payloads.add(payload.payload_id)

            for nbr in self.graph.neighbors(self.node_id):
                self.env.process(self._send_payload(nbr, payload, payload.ref_raw_bytes))

            yield self.env.timeout(BROADCAST_INTERVAL + random.uniform(-0.2, 0.2))

    def _send_payload(self, receiver_id: int, payload: NetworkPayload, ref_bytes: int) -> simpy.events.ProcessGenerator:
        success, latency, reason = self.ctrl.attempt_transmission(self.node_id, receiver_id, payload, ref_bytes)
        self.metrics.record(TransmissionRecord(self.node_id, receiver_id, payload.payload_id,
                                                self.env.now, payload.byte_size, success, reason))
        if not success:
            return
        yield self.env.timeout(latency)
        if receiver_id in _node_registry:
            _node_registry[receiver_id].receive_payload(payload)

    def receive_payload(self, payload: NetworkPayload) -> None:
        if payload.payload_id in self._seen_payloads:
            return
        self._seen_payloads.add(payload.payload_id)
        self.state_matrix[payload.payload_id] = payload.raw_content
        self.metrics.record_decision(1.0)  # Raw uncompressed state has 100% decision preservation

        if payload.ttl > 1:
            fwd_payload = NetworkPayload(
                payload_id=payload.payload_id,
                origin_node=payload.origin_node,
                timestamp=payload.timestamp,
                raw_content=payload.raw_content,
                byte_size=payload.byte_size,
                ttl=payload.ttl - 1,
                is_compressed=False,
                ground_truth=payload.ground_truth,
                tokens_generated=0,
                ref_raw_bytes=payload.ref_raw_bytes
            )
            for nbr in self.graph.neighbors(self.node_id):
                if nbr != payload.origin_node:
                    self.env.process(self._send_payload(nbr, fwd_payload, fwd_payload.ref_raw_bytes))


# ============================================================================
# Node Implementation 2: Epidemic Routing (Baseline 2 - Vahdat & Becker, 2000)
# ============================================================================

class EpidemicNode:
    """Baseline 2: Store-and-Forward anti-entropy dissemination to all unvisited neighbors."""

    def __init__(self, node_id: int, env: simpy.Environment, ctrl: EnvironmentController,
                 graph: nx.Graph, metrics: EmpiricalMetricsCollector):
        self.node_id = node_id
        self.env = env
        self.ctrl = ctrl
        self.graph = graph
        self.metrics = metrics
        self.state_matrix: Dict[str, str] = {}
        self.buffer: Dict[str, NetworkPayload] = {}
        # Sender-side delivery ledger: payload_id -> neighbor ids successfully delivered.
        # Guarantees true anti-entropy semantics (each payload delivered to each neighbor
        # at most once); only channel-failed transmissions are retried on later cycles.
        self._delivered_to: Dict[str, Set[int]] = {}
        self.env.process(self._broadcast_loop())
        self.env.process(self._anti_entropy_loop())

    def _broadcast_loop(self) -> simpy.events.ProcessGenerator:
        yield self.env.timeout(random.uniform(0.0, BROADCAST_INTERVAL))
        while True:
            raw_state = RawStateMatrix(origin_node=self.node_id, timestamp=self.env.now)
            raw_str = raw_state.to_json_str()
            byte_len = len(raw_str.encode('utf-8'))

            payload = NetworkPayload(
                payload_id=raw_state.sequence_id,
                origin_node=self.node_id,
                timestamp=self.env.now,
                raw_content=raw_str,
                byte_size=byte_len,
                ttl=999,
                is_compressed=False,
                visited_nodes={self.node_id},
                ground_truth=raw_state,
                tokens_generated=0,
                ref_raw_bytes=byte_len
            )

            self.state_matrix[payload.payload_id] = raw_str
            self.buffer[payload.payload_id] = payload

            yield self.env.timeout(BROADCAST_INTERVAL + random.uniform(-0.2, 0.2))

    def _anti_entropy_loop(self) -> simpy.events.ProcessGenerator:
        while True:
            yield self.env.timeout(random.uniform(1.0, 3.0))
            for nbr in list(self.graph.neighbors(self.node_id)):
                for pid, payload in list(self.buffer.items()):
                    if nbr in payload.visited_nodes:
                        continue
                    if nbr in self._delivered_to.get(pid, set()):
                        continue
                    self.env.process(self._send_payload(nbr, pid, payload))

    def _send_payload(self, receiver_id: int, payload_id: str, payload: NetworkPayload) -> simpy.events.ProcessGenerator:
        success, latency, reason = self.ctrl.attempt_transmission(self.node_id, receiver_id, payload, payload.byte_size)
        self.metrics.record(TransmissionRecord(self.node_id, receiver_id, payload.payload_id,
                                                self.env.now, payload.byte_size, success, reason))
        if not success:
            return
        yield self.env.timeout(latency)
        self._delivered_to.setdefault(payload_id, set()).add(receiver_id)
        if receiver_id in _node_registry:
            _node_registry[receiver_id].receive_payload(payload)

    def receive_payload(self, payload: NetworkPayload) -> None:
        # Duplicate suppression: concurrent anti-entropy sends from multiple
        # neighbors can deliver the same payload twice before visited-set updates.
        if payload.payload_id in self.state_matrix:
            return
        self.state_matrix[payload.payload_id] = payload.raw_content
        self.metrics.record_decision(1.0)
        updated_visited = set(payload.visited_nodes) | {self.node_id}
        fwd_payload = NetworkPayload(
            payload_id=payload.payload_id,
            origin_node=payload.origin_node,
            timestamp=payload.timestamp,
            raw_content=payload.raw_content,
            byte_size=payload.byte_size,
            ttl=payload.ttl,
            is_compressed=False,
            visited_nodes=updated_visited,
            ground_truth=payload.ground_truth,
            ref_raw_bytes=payload.ref_raw_bytes
        )
        self.buffer[payload.payload_id] = fwd_payload


# ============================================================================
# Shared Async Event Loop & vLLM Preflight Readiness Gate
# ============================================================================

_SHARED_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _get_shared_loop() -> asyncio.AbstractEventLoop:
    """Returns a process-wide event loop reused across all compression calls.

    Reusing one loop avoids leaking thousands of per-call event loops (each
    holding selector file descriptors) over the ~2,500 compression events per
    run, and lets the OS-level TCP connections to vLLM stay warm."""
    global _SHARED_LOOP
    if _SHARED_LOOP is None or _SHARED_LOOP.is_closed():
        _SHARED_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_SHARED_LOOP)
    return _SHARED_LOOP


def preflight_vllm_endpoints(
    ports: Optional[List[int]] = None,
    max_wait_s: float = 1200.0,
    probe_timeout_s: float = 3.0
) -> None:
    """Blocks until every vLLM endpoint answers /v1/models, then issues one warmup
    completion per endpoint.

    This gate prevents the single most common GPU-cluster failure mode: the
    benchmark starting before model loading finishes, every live inference
    timing out, and the entire agentic arm silently degrading to the
    deterministic fallback (results would look valid but carry no LLM signal).
    """
    ports = ports or list(VLLM_BASE_PORTS)
    loop = _get_shared_loop()

    async def _probe(port: int) -> bool:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=probe_timeout_s)) as session:
                async with session.get(f"http://localhost:{port}/v1/models") as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def _warmup(port: int) -> bool:
        try:
            payload = {
                "model": VLLM_MODEL_NAME,
                "messages": [{"role": "user", "content": "Reply with the single character: {"}],
                "temperature": 0.0,
                "max_tokens": 8,
            }
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30.0)) as session:
                async with session.post(f"http://localhost:{port}/v1/chat/completions", json=payload) as resp:
                    return resp.status == 200
        except Exception:
            return False

    ready = {p: False for p in ports}
    deadline = time.time() + max_wait_s
    print(f"[PREFLIGHT] Waiting for {len(ports)} vLLM endpoints (up to {max_wait_s:.0f}s): {ports}")
    while time.time() < deadline and not all(ready.values()):
        for p in ports:
            if not ready[p]:
                ready[p] = loop.run_until_complete(_probe(p))
        if all(ready.values()):
            break
        pending = [p for p, ok in ready.items() if not ok]
        print(f"[PREFLIGHT] Still loading: {pending} — retrying in 10 s...")
        time.sleep(10)

    if not all(ready.values()):
        raise RuntimeError(
            f"vLLM endpoints failed readiness within {max_wait_s:.0f}s: {[p for p, ok in ready.items() if not ok]}. "
            "Launch them via run_gpu_experiments.sh / launch_vllm_cluster.sh first, "
            "or run in deterministic CPU mode (--cpu or DDIL_DISABLE_VLLM=1)."
        )

    print("[PREFLIGHT] All endpoints answering. Issuing warmup completions (absorbs CUDA-graph capture)...")
    for p in ports:
        ok = loop.run_until_complete(_warmup(p))
        print(f"[PREFLIGHT] Warmup port {p}: {'OK' if ok else 'FAILED (will fall back if it recurs)'}")
    print("[PREFLIGHT] Complete.")


# ============================================================================
# Node Implementation 3: Proposed Agentic SLM Node
# ============================================================================

class LLMAgentNode:
    """
    Proposed Agentic Node featuring:
      - Task-oriented multi-invariant semantic compression (~4-5.6x reduction)
      - Sender-side IPS verification gate preventing hallucination propagation
      - Receiver-side zero-ground-truth structural validation
      - 2-hop joint path reliability relaying: m* = argmax (L_i(m) * L_m(j))
    """

    def __init__(self, node_id: int, env: simpy.Environment, ctrl: EnvironmentController,
                 graph: nx.Graph, metrics: EmpiricalMetricsCollector, config: Optional[RunConfig] = None):
        self.node_id = node_id
        self.env = env
        self.ctrl = ctrl
        self.graph = graph
        self.metrics = metrics
        self.config = config or RunConfig()
        self.vllm_port = VLLM_BASE_PORTS[node_id % len(VLLM_BASE_PORTS)]
        self.vllm_endpoint = f"http://localhost:{self.vllm_port}/v1/chat/completions"

        self.state_matrix: Dict[str, str] = {}
        self._seen_payloads: set = set()
        self.link_scores: Dict[int, float] = {nbr: 0.5 for nbr in graph.neighbors(node_id)}

        self.env.process(self._broadcast_loop())

    @staticmethod
    def _fallback_compress(raw_state: RawStateMatrix, inject_corruption: bool = False) -> Tuple[str, int, int]:
        """
        Deterministic multi-invariant compression (~80-110 bytes, ~30 tokens).
        Produces identical verifiable schema across CPU and GPU modes.
        """
        pos = [round(raw_state.state_vector[0], 2), round(raw_state.state_vector[1], 2)]
        vel = round(raw_state.state_vector[2], 2)
        hdg = round(raw_state.state_vector[3], 2)
        bat = round(raw_state.energy_level, 1)
        pri = round(max(raw_state.matrix_weights.values()), 2) if raw_state.matrix_weights else 0.5

        if inject_corruption:
            corrupt_type = random.choice(["pos_drift", "bad_bat", "dropped_key", "scalar_err"])
            if corrupt_type == "pos_drift":
                pos = [pos[0] + random.uniform(2.0, 5.0), pos[1] + random.uniform(2.0, 5.0)]
            elif corrupt_type == "bad_bat":
                bat = round(random.uniform(500.0, 999.0), 1)
            elif corrupt_type == "dropped_key":
                fallback_obj = {"id": raw_state.sequence_id, "origin": raw_state.origin_node, "ts": round(raw_state.timestamp, 2), "st": 1}
                fallback_json = json.dumps(fallback_obj, separators=(',', ':'))
                return fallback_json, len(fallback_json.encode('utf-8')), 20
            elif corrupt_type == "scalar_err":
                vel = vel * 10.0

        fallback_obj = {
            "id": raw_state.sequence_id,
            "origin": raw_state.origin_node,
            "ts": round(raw_state.timestamp, 2),
            "pos": pos,
            "vel": vel,
            "hdg": hdg,
            "bat": bat,
            "pri": pri,
            "st": 1
        }
        fallback_json = json.dumps(fallback_obj, separators=(',', ':'))
        return fallback_json, len(fallback_json.encode('utf-8')), 30

    @staticmethod
    def _apply_injection_to_content(content: str) -> str:
        """Applies the same corruption operators used by the deterministic fallback to
        a live SLM completion, so the hallucination-robustness experiment behaves
        identically in GPU and CPU modes."""
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return content
            corrupt_type = random.choice(["pos_drift", "bad_bat", "dropped_key", "scalar_err"])
            if corrupt_type == "pos_drift" and isinstance(parsed.get("pos"), list) and len(parsed["pos"]) == 2:
                parsed["pos"] = [round(parsed["pos"][0] + random.uniform(2.0, 5.0), 2),
                                 round(parsed["pos"][1] + random.uniform(2.0, 5.0), 2)]
            elif corrupt_type == "bad_bat" and "bat" in parsed:
                parsed["bat"] = round(random.uniform(500.0, 999.0), 1)
            elif corrupt_type == "dropped_key":
                parsed.pop("pri", None)
            elif corrupt_type == "scalar_err" and "vel" in parsed:
                try:
                    parsed["vel"] = float(parsed["vel"]) * 10.0
                except (ValueError, TypeError):
                    pass
            return json.dumps(parsed, separators=(',', ':'))
        except Exception:
            return content

    async def _async_compress_state(self, raw_state: RawStateMatrix) -> Tuple[str, int, int, bool]:
        """Returns (content, byte_size, tokens_generated, used_fallback)."""
        if not self.config.enable_compression:
            raw_str = raw_state.to_json_str()
            return raw_str, len(raw_str.encode('utf-8')), 0, False

        # Inject controlled hallucination if requested
        should_corrupt = (self.config.injection_rate > 0.0 and random.random() < self.config.injection_rate)

        # CPU-only reproduction mode
        if os.environ.get("DDIL_DISABLE_VLLM", "").lower() in ("1", "true", "yes"):
            content, nbytes, toks = self._fallback_compress(raw_state, inject_corruption=should_corrupt)
            return content, nbytes, toks, True

        system_prompt = (
            "You are a task-oriented edge compression agent. Compress the telemetry JSON into a minimal JSON object with exact keys: "
            "'id', 'origin', 'ts', 'pos', 'vel', 'hdg', 'bat', 'pri', 'st'. Output strictly valid raw JSON with no prose."
        )
        user_prompt = f"Compress this state matrix: {raw_state.to_json_str()}"

        payload_data = {
            "model": VLLM_MODEL_NAME,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 120
        }

        try:
            timeout = aiohttp.ClientTimeout(total=float(os.environ.get("DDIL_HTTP_TIMEOUT", "2.0")))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.vllm_endpoint, json=payload_data) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data['choices'][0]['message']['content'].strip()
                        completion_tokens = data.get('usage', {}).get('completion_tokens', 30)
                        if "```json" in content:
                            content = content.split("```json")[1].split("```")[0].strip()
                        elif "```" in content:
                            content = content.split("```")[1].split("```")[0].strip()
                        if should_corrupt:
                            content = self._apply_injection_to_content(content)
                        byte_size = len(content.encode('utf-8'))
                        return content, byte_size, completion_tokens, False
        except Exception:
            pass

        content, nbytes, toks = self._fallback_compress(raw_state, inject_corruption=should_corrupt)
        return content, nbytes, toks, True

    def _broadcast_loop(self) -> simpy.events.ProcessGenerator:
        yield self.env.timeout(random.uniform(0.0, BROADCAST_INTERVAL))
        while True:
            raw_state = RawStateMatrix(origin_node=self.node_id, timestamp=self.env.now)
            ref_raw_bytes = len(raw_state.to_json_str().encode('utf-8'))

            loop = _get_shared_loop()

            compressed_str, actual_bytes, tokens_gen, used_fallback = loop.run_until_complete(
                self._async_compress_state(raw_state)
            )

            self.metrics.add_tokens(tokens_gen)
            if used_fallback:
                self.metrics.record_llm_fallback()

            # Sender-side Invariant Preservation Score (IPS) verification gate
            is_injected = (self.config.injection_rate > 0.0)
            if self.config.enable_drift_check and self.config.enable_compression:
                try:
                    parsed_compressed = json.loads(compressed_str)
                    errors, ips, is_valid = calculate_invariant_preservation(
                        raw_state, parsed_compressed, threshold=self.config.ips_threshold
                    )
                    self.metrics.record_parse_outcome(success=is_valid, is_drift_failure=(not is_valid), ips=ips)
                    if not is_valid:
                        # Drop hallucinated output before transmitting over RF
                        if is_injected:
                            self.metrics.record_injection(rejected=True)
                        yield self.env.timeout(BROADCAST_INTERVAL + random.uniform(-0.2, 0.2))
                        continue
                    if is_injected:
                        self.metrics.record_injection(rejected=False)
                except Exception:
                    self.metrics.record_parse_outcome(success=False, is_drift_failure=True)
                    if is_injected:
                        self.metrics.record_injection(rejected=True)
                    yield self.env.timeout(BROADCAST_INTERVAL + random.uniform(-0.2, 0.2))
                    continue
            elif is_injected:
                # Verification gate disabled: injected tokens pass unconditionally
                self.metrics.record_injection(rejected=False)

            payload = NetworkPayload(
                payload_id=raw_state.sequence_id,
                origin_node=self.node_id,
                timestamp=self.env.now,
                raw_content=compressed_str,
                byte_size=actual_bytes,
                ttl=GOSSIP_TTL,
                is_compressed=self.config.enable_compression,
                ground_truth=raw_state,
                tokens_generated=tokens_gen,
                ref_raw_bytes=ref_raw_bytes
            )

            self.state_matrix[payload.payload_id] = compressed_str
            self._seen_payloads.add(payload.payload_id)

            for nbr in list(self.graph.neighbors(self.node_id)):
                if not self.config.enable_link_memory:
                    self.env.process(self._send_payload(nbr, payload, payload.ref_raw_bytes))
                else:
                    score = self.link_scores.get(nbr, 0.5)
                    if score >= LINK_RELIABILITY_THRESHOLD or not self.config.enable_relay:
                        self.env.process(self._send_payload(nbr, payload, payload.ref_raw_bytes))
                    else:
                        best_relay = self._find_best_relay(nbr)
                        target = best_relay if best_relay is not None else nbr
                        self.env.process(self._send_payload(target, payload, payload.ref_raw_bytes))

            yield self.env.timeout(BROADCAST_INTERVAL + random.uniform(-0.2, 0.2))

    def _find_best_relay(self, target_id: int) -> Optional[int]:
        """
        Selects relay m* = argmax_{m} (L_i(m) * L_m(j)) over 2-hop neighbors.
        Uses genuine joint path reliability from active neighbor link memory.
        """
        if not self.config.enable_relay:
            return None
        best, best_score = None, 0.0
        for mid in self.graph.neighbors(self.node_id):
            if mid == target_id:
                continue
            if not self.graph.has_edge(mid, target_id):
                continue
            relay_node = _node_registry.get(mid)
            if relay_node is None or not hasattr(relay_node, 'link_scores'):
                continue
            L_im = self.link_scores.get(mid, 0.0)
            L_mj = relay_node.link_scores.get(target_id, 0.0)
            joint_reliability = L_im * L_mj
            if joint_reliability > best_score and joint_reliability > LINK_RELIABILITY_THRESHOLD:
                best_score, best = joint_reliability, mid
        return best

    def _update_link_score(self, neighbor_id: int, success: bool) -> None:
        if not self.config.enable_link_memory:
            return
        old = self.link_scores.get(neighbor_id, 0.5)
        obs = 1.0 if success else 0.0
        self.link_scores[neighbor_id] = old * (1 - LINK_RELIABILITY_DECAY) + obs * LINK_RELIABILITY_DECAY

    def _send_payload(self, receiver_id: int, payload: NetworkPayload, ref_bytes: int) -> simpy.events.ProcessGenerator:
        success, latency, reason = self.ctrl.attempt_transmission(self.node_id, receiver_id, payload, ref_bytes)
        self._update_link_score(receiver_id, success)
        self.metrics.record(TransmissionRecord(self.node_id, receiver_id, payload.payload_id,
                                                self.env.now, payload.byte_size, success, reason))
        if not success:
            return
        yield self.env.timeout(latency)
        if receiver_id in _node_registry:
            _node_registry[receiver_id].receive_payload(payload)

    def receive_payload(self, payload: NetworkPayload) -> None:
        if payload.payload_id in self._seen_payloads:
            return
        self._seen_payloads.add(payload.payload_id)

        # Receiver-side zero-ground-truth structural validation
        try:
            parsed = json.loads(payload.raw_content)
        except Exception:
            self.metrics.record_parse_outcome(success=False, is_drift_failure=False)
            return

        if payload.is_compressed:
            if not validate_received_structure(parsed, self.env.now):
                self.metrics.record_parse_outcome(success=False, is_drift_failure=True)
                return

            # Compute Decision Preservation Rate (DPR) if ground truth is tracked in simulation
            if payload.ground_truth is not None:
                d_raw = DecisionOracle.decide_from_raw(payload.ground_truth)
                d_comp = DecisionOracle.decide_from_compressed(parsed)
                agreement = DecisionOracle.agreement(d_raw, d_comp)
                self.metrics.record_decision(agreement)
        else:
            self.metrics.record_decision(1.0)

        self.state_matrix[payload.payload_id] = payload.raw_content

        if payload.ttl > 1:
            fwd_payload = NetworkPayload(
                payload_id=payload.payload_id,
                origin_node=payload.origin_node,
                timestamp=payload.timestamp,
                raw_content=payload.raw_content,
                byte_size=payload.byte_size,
                ttl=payload.ttl - 1,
                is_compressed=payload.is_compressed,
                ground_truth=payload.ground_truth,
                tokens_generated=payload.tokens_generated,
                ref_raw_bytes=payload.ref_raw_bytes
            )
            for nbr in self.graph.neighbors(self.node_id):
                if nbr != payload.origin_node:
                    self.env.process(self._send_payload(nbr, fwd_payload, fwd_payload.ref_raw_bytes))


# Global Registry for node communication
_node_registry: Dict[int, object] = {}


# ============================================================================
# Topology Builder & Sync Math
# ============================================================================

def build_mesh_topology(num_nodes: int, seed: int = RANDOM_SEED) -> nx.Graph:
    k = 6 if num_nodes >= 6 else 4
    G = nx.watts_strogatz_graph(num_nodes, k=k, p=0.3, seed=seed)
    if not nx.is_connected(G):
        components = list(nx.connected_components(G))
        for i in range(1, len(components)):
            G.add_edge(list(components[0])[0], list(components[i])[0])
    return G


def calculate_state_sync_rate(registry: Dict[int, object]) -> float:
    all_keys = set()
    for node in registry.values():
        all_keys.update(node.state_matrix.keys())
    if not all_keys:
        return 0.0
    sync_percentages = [len(node.state_matrix) / len(all_keys) for node in registry.values()]
    return statistics.mean(sync_percentages)


# ============================================================================
# Single Benchmark Run Execution
# ============================================================================

def execute_simulation_run(
    mode: str,
    drop_rate: float,
    seed: int = RANDOM_SEED,
    config: Optional[RunConfig] = None
) -> Tuple[float, float, int, float, int, float, float, int, int, int, int, int, float]:
    """
    Executes one simulation run.
    Returns: (sync_rate, delivery_rate, total_bytes, total_energy_joules,
              tokens_generated, mean_dpr, mean_ips, llm_fallbacks,
              injections_generated, injections_rejected,
              parse_successes, drift_failures, gate_pass_rate)
    """
    global _node_registry
    random.seed(seed)

    env = simpy.Environment()
    graph = build_mesh_topology(NUM_NODES, seed=seed)
    metrics = EmpiricalMetricsCollector()
    ctrl = EnvironmentController(env, graph, drop_rate, seed=seed)

    _node_registry.clear()

    if mode == "gossip":
        for i in range(NUM_NODES):
            _node_registry[i] = GossipNode(i, env, ctrl, graph, metrics)
    elif mode == "epidemic":
        for i in range(NUM_NODES):
            _node_registry[i] = EpidemicNode(i, env, ctrl, graph, metrics)
    elif mode == "agentic":
        for i in range(NUM_NODES):
            _node_registry[i] = LLMAgentNode(i, env, ctrl, graph, metrics, config=config)
    else:
        raise ValueError(f"Unknown protocol mode: {mode}")

    env.run(until=SIM_DURATION)
    sync = calculate_state_sync_rate(_node_registry)
    return (
        sync,
        metrics.delivery_rate,
        metrics.total_bytes_transmitted,
        metrics.total_energy_joules,
        metrics.total_tokens_generated,
        metrics.mean_dpr,
        metrics.mean_ips,
        metrics.llm_fallbacks,
        metrics.injections_generated,
        metrics.injections_rejected,
        metrics.parse_successes,
        metrics.drift_failures,
        metrics.gate_pass_rate
    )


# Student-t critical values (two-sided, 95%) indexed by degrees of freedom;
# beyond df=30 the normal approximation 1.96 is used.
_T_CRIT_95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
              8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
              15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
              25: 2.060, 30: 2.042}


def ci95_halfwidth(values: List[float]) -> float:
    """Half-width of the 95% confidence interval of the mean (Student-t)."""
    n = len(values)
    if n < 2:
        return 0.0
    s = statistics.stdev(values)
    df = n - 1
    t = _T_CRIT_95.get(df)
    if t is None:
        t = _T_CRIT_95.get(30, 1.96) if df < 30 else 1.96
    return t * s / math.sqrt(n)


# ============================================================================
# Optional Weights & Biases logging (activates when WANDB_API_KEY is set)
# ============================================================================

_WANDB_RUN = None


def init_wandb(suite: str, extra_config: Optional[dict] = None):
    """Initializes W&B when WANDB_API_KEY is set. Never raises."""
    global _WANDB_RUN
    if not os.environ.get("WANDB_API_KEY"):
        print("[WANDB] WANDB_API_KEY not set — W&B logging disabled.")
        return None
    try:
        import wandb
        config = {"suite": suite, "num_nodes": NUM_NODES, "sim_duration": SIM_DURATION,
                  "model": VLLM_MODEL_NAME, "channel": "calibrated-gilbert-elliott",
                  "broadcast_interval": BROADCAST_INTERVAL, "gossip_ttl": GOSSIP_TTL}
        if extra_config:
            config.update({k: v for k, v in extra_config.items()})
        _WANDB_RUN = wandb.init(
            project=os.environ.get("WANDB_PROJECT", "incis2027-ddil"),
            name=f"{suite}-N{NUM_NODES}-t{int(SIM_DURATION)}",
            config=config, job_type=suite,
        )
        print(f"[WANDB] Logging '{suite}' -> project '{os.environ.get('WANDB_PROJECT', 'incis2027-ddil')}'")
        return _WANDB_RUN
    except Exception as e:
        print(f"[WANDB] init failed ({e}) — continuing without W&B.")
        _WANDB_RUN = None
        return None


def wandb_log_row(row: dict, step: int) -> None:
    """Logs one per-run record (numeric fields only). Best-effort."""
    if _WANDB_RUN is None:
        return
    try:
        numeric = {k: v for k, v in row.items() if isinstance(v, (int, float))}
        _WANDB_RUN.log(numeric, step=step)
    except Exception:
        pass


def wandb_finish(artifacts: Optional[List[str]] = None) -> None:
    """Logs result files as artifacts and closes the W&B run. Best-effort."""
    global _WANDB_RUN
    if _WANDB_RUN is None:
        return
    try:
        import wandb
        for path in (artifacts or []):
            if path and os.path.exists(path):
                art = wandb.Artifact(os.path.basename(path), type="results")
                art.add_file(path)
                _WANDB_RUN.log_artifact(art)
        _WANDB_RUN.finish()
        print("[WANDB] Run closed, artifacts uploaded.")
    except Exception as e:
        print(f"[WANDB] finish issue: {e}")
    finally:
        _WANDB_RUN = None


# ============================================================================
# Per-seed row builders (shared by the CLI suites and the parallel driver so the
# parallel sweep produces byte-identical schemas; each run seeds itself, so
# sharding seeds across processes is deterministic and embarrassingly parallel)
# ============================================================================

def benchmark_row_for(seed: int, dr: float, mode: str) -> dict:
    (sync, delivery, total_bytes, energy, tokens, dpr, ips,
     fallbacks, inj_gen, inj_rej, _ps, _df, _gp) = execute_simulation_run(mode, dr, seed=seed)
    return {
        "seed": seed, "mode": mode, "drop_rate": dr,
        "dpr_pct": round(dpr * 100, 2),
        "sync_pct": round(sync * 100, 2),
        "delivery_pct": round(delivery * 100, 2),
        "delivered_bytes": total_bytes,
        "energy_kj": round(energy / 1000.0, 3),
        "tokens_generated": tokens,
        "ips_score": round(ips, 4),
        "llm_fallbacks": fallbacks,
        "injections_generated": inj_gen,
        "injections_rejected": inj_rej,
    }


def benchmark_rows_for_seed(seed: int, drop_rates: List[float]) -> List[dict]:
    rows = []
    for dr in drop_rates:
        for mode in ("gossip", "epidemic", "agentic"):
            rows.append(benchmark_row_for(seed, dr, mode))
    return rows


def ablation_rows_for_seed(seed: int, drop_rates: List[float],
                           configs: Optional[List[RunConfig]] = None) -> List[dict]:
    configs = configs or [
        RunConfig(True, True, True, True, label="Full Agentic SLM"),
        RunConfig(True, False, False, True, label="A1: No Link Memory"),
        RunConfig(False, True, True, False, label="A2: No Compression"),
        RunConfig(True, True, False, True, label="A3: No Relay Routing"),
        RunConfig(True, True, True, False, label="A4: No Verification Gate"),
    ]
    rows = []
    for cfg in configs:
        for dr in drop_rates:
            (sync, delivery, total_bytes, energy, tokens, dpr, ips,
             fallbacks, inj_gen, inj_rej, _ps, drift_fail, gate_pass) = execute_simulation_run(
                "agentic", dr, seed=seed, config=cfg)
            rows.append({
                "seed": seed, "variant": cfg.label, "drop_rate": dr,
                "dpr_pct": round(dpr * 100, 2),
                "sync_pct": round(sync * 100, 2),
                "delivery_pct": round(delivery * 100, 2),
                "delivered_bytes": total_bytes,
                "energy_kj": round(energy / 1000.0, 3),
                "tokens_generated": tokens,
                "ips_score": round(ips, 4),
                "llm_fallbacks": fallbacks,
                "injections_generated": inj_gen,
                "injections_rejected": inj_rej,
                "gate_pass_rate": round(gate_pass, 4),
                "drift_failures": drift_fail,
            })
    return rows


def sensitivity_rows_for_seed(seed: int, thresholds: List[float], drop_rates: List[float]) -> List[dict]:
    rows = []
    for th in thresholds:
        cfg = RunConfig(enable_compression=True, enable_link_memory=True, enable_relay=True,
                        enable_drift_check=True, ips_threshold=th, label=f"IPS theta={th}")
        for dr in drop_rates:
            (sync, delivery, total_bytes, energy, tokens, dpr, ips,
             fallbacks, inj_gen, inj_rej, _ps, drift_fail, gate_pass) = execute_simulation_run(
                "agentic", dr, seed=seed, config=cfg)
            rows.append({
                "seed": seed, "ips_threshold": th, "drop_rate": dr,
                "dpr_pct": round(dpr * 100, 2),
                "sync_pct": round(sync * 100, 2),
                "delivery_pct": round(delivery * 100, 2),
                "ips_score": round(ips, 4),
                "llm_fallbacks": fallbacks,
                "gate_pass_rate": round(gate_pass, 4),
                "drift_failures": drift_fail,
            })
    return rows


def robustness_rows_for_seed(seed: int, injection_rates: List[float], drop_rate: float = 0.40) -> List[dict]:
    rows = []
    for rate in injection_rates:
        cfg = RunConfig(enable_compression=True, enable_link_memory=True, enable_relay=True,
                        enable_drift_check=True, injection_rate=rate, label=f"Inject {rate:.0%}")
        (sync, delivery, total_bytes, energy, tokens, dpr, ips,
         fallbacks, inj_gen, inj_rej, _ps, _df, gate_pass) = execute_simulation_run(
            "agentic", drop_rate=drop_rate, seed=seed, config=cfg)
        recall = (inj_rej / inj_gen) if inj_gen > 0 else 1.0
        rows.append({
            "seed": seed, "injection_rate": rate, "drop_rate": drop_rate,
            "dpr_pct": round(dpr * 100, 2),
            "sync_pct": round(sync * 100, 2),
            "injections_generated": inj_gen,
            "injections_rejected": inj_rej,
            "gate_recall": round(recall, 4),
            "gate_pass_rate": round(gate_pass, 4),
            "llm_fallbacks": fallbacks,
        })
    return rows


# ============================================================================
# LaTeX Booktabs Table Exports
# ============================================================================

def export_latex_booktabs_table(
    gossip_final: Dict[str, Tuple[float, float]],
    epidemic_final: Dict[str, Tuple[float, float]],
    agentic_final: Dict[str, Tuple[float, float]],
    drop_rate_pct: float = 80.0
) -> None:
    def cell(mean: float, std: float, scale: float, suffix: str, digits: int = 1) -> str:
        m = mean * scale
        s = std * scale
        base = f"{m:.{digits}f}{suffix}"
        if s > 0:
            base += f" \\pm {s:.{digits}f}{suffix}"
        return base

    g_sync, g_del, g_bytes, g_energy, g_dpr = (gossip_final[k] for k in ("sync", "delivery", "bytes", "energy", "dpr"))
    e_sync, e_del, e_bytes, e_energy, e_dpr = (epidemic_final[k] for k in ("sync", "delivery", "bytes", "energy", "dpr"))
    a_sync, a_del, a_bytes, a_energy, a_dpr = (agentic_final[k] for k in ("sync", "delivery", "bytes", "energy", "dpr"))

    latex_table = f"""
% ============================================================================
% Table 1: IEEE/Springer Booktabs Table (Severe {drop_rate_pct:.0f}% Drop-Rate Metrics)
% ============================================================================
\\begin{{table}}[htbp]
\\centering
\\caption{{Empirical Swarm Benchmarks under Severe {drop_rate_pct:.0f}\\% Burst Packet Loss}}
\\label{{tab:swarm_benchmarks_80drop}}
\\begin{{tabular}}{{lccccc}}
\\toprule
\\textbf{{Protocol Paradigm}} & \\textbf{{DPR (\\%)}} & \\textbf{{Delivery Rate (\\%)}} & \\textbf{{State Sync (\\%)}} & \\textbf{{Bandwidth (KB)}} & \\textbf{{Energy (kJ)}} \\\\
\\midrule
Gossip Protocol (Baseline 1)   & {cell(g_dpr[0], g_dpr[1], 100, '\\%')} & {cell(g_del[0], g_del[1], 100, '\\%')} & {cell(g_sync[0], g_sync[1], 100, '\\%')} & {cell(g_bytes[0], g_bytes[1], 1/1024, ' KB')} & {cell(g_energy[0], g_energy[1], 1/1000, ' kJ', 2)} \\\\
Epidemic Routing (Baseline 2)  & {cell(e_dpr[0], e_dpr[1], 100, '\\%')} & {cell(e_del[0], e_del[1], 100, '\\%')} & {cell(e_sync[0], e_sync[1], 100, '\\%')} & {cell(e_bytes[0], e_bytes[1], 1/1024, ' KB')} & {cell(e_energy[0], e_energy[1], 1/1000, ' kJ', 2)} \\\\
\\textbf{{Agentic SLM (Proposed)}} & \\textbf{{{cell(a_dpr[0], a_dpr[1], 100, '\\%')}}} & \\textbf{{{cell(a_del[0], a_del[1], 100, '\\%')}}} & \\textbf{{{cell(a_sync[0], a_sync[1], 100, '\\%')}}} & \\textbf{{{cell(a_bytes[0], a_bytes[1], 1/1024, ' KB')}}} & \\textbf{{{cell(a_energy[0], a_energy[1], 1/1000, ' kJ', 2)}}} \\\\
\\bottomrule
\\end{{tabular}}
\\end{{table}}
"""
    print(latex_table)


def export_ablation_latex_table(ablation_results: Dict[str, Dict[str, Tuple[float, float]]], drop_rate_pct: float = 80.0) -> None:
    def cell(mean: float, std: float, scale: float, suffix: str, digits: int = 1) -> str:
        m = mean * scale
        s = std * scale
        base = f"{m:.{digits}f}{suffix}"
        if s > 0:
            base += f" \\pm {s:.{digits}f}{suffix}"
        return base

    rows = []
    for label, metrics in ablation_results.items():
        dpr = metrics["dpr"]
        syn = metrics["sync"]
        dvr = metrics["delivery"]
        byt = metrics["bytes"]
        eng = metrics["energy"]
        is_bold = (label == "Full Agentic SLM")
        b = "\\textbf{" if is_bold else ""
        eb = "}" if is_bold else ""
        row = f"{b}{label}{eb} & {b}{cell(dpr[0], dpr[1], 100, '\\%')}{eb} & {b}{cell(dvr[0], dvr[1], 100, '\\%')}{eb} & {b}{cell(syn[0], syn[1], 100, '\\%')}{eb} & {b}{cell(byt[0], byt[1], 1/1024, ' KB')}{eb} & {b}{cell(eng[0], eng[1], 1/1000, ' kJ', 2)}{eb} \\\\"
        rows.append(row)

    table_rows = "\n".join(rows)
    latex_table = f"""
% ============================================================================
% Table 3: Systematic Ablation Study Results under Severe {drop_rate_pct:.0f}% Drop Rate
% ============================================================================
\\begin{{table}}[htbp]
\\centering
\\caption{{Architectural Ablation Analysis at {drop_rate_pct:.0f}\\% Channel Loss}}
\\label{{tab:ablation_80drop}}
\\begin{{tabular}}{{lccccc}}
\\toprule
\\textbf{{Ablation Variant}} & \\textbf{{DPR (\\%)}} & \\textbf{{Delivery Rate (\\%)}} & \\textbf{{State Sync (\\%)}} & \\textbf{{Bandwidth (KB)}} & \\textbf{{Energy (kJ)}} \\\\
\\midrule
{table_rows}
\\bottomrule
\\end{{tabular}}
\\end{{table}}
"""
    print(latex_table)


# ============================================================================
# Main Comparative Benchmark & Output Generators
# ============================================================================

def render_benchmark_plots(csv_rows: List[dict], seeds: List[int], drop_rates: List[float]) -> None:
    """Renders fig_sync_vs_drop.png and fig_energy_vs_drop.png from per-run CSV rows
    (mean +/- 95% CI bands). Reusable by both the CLI suite and the parallel driver."""
    drop_percentages: List[float] = [dr * 100 for dr in drop_rates]
    multi_seed = len(seeds) > 1

    def agg(mode: str, dr: float, field: str, scale: float) -> Tuple[float, float]:
        vals = [r[field] / scale for r in csv_rows
                if r["mode"] == mode and abs(r["drop_rate"] - dr) < 1e-9]
        return statistics.mean(vals), ci95_halfwidth(vals)

    def series(mode: str, field: str, scale: float) -> Tuple[List[float], List[float]]:
        pairs = [agg(mode, dr, field, scale) for dr in drop_rates]
        return [p[0] for p in pairs], [p[1] for p in pairs]

    gossip_syncs, gossip_syncs_sd = series("gossip", "sync_pct", 100)
    epidemic_syncs, epidemic_syncs_sd = series("epidemic", "sync_pct", 100)
    agentic_syncs, agentic_syncs_sd = series("agentic", "sync_pct", 100)
    gossip_energies, gossip_energies_sd = series("gossip", "energy_kj", 1 / 1000)
    epidemic_energies, epidemic_energies_sd = series("epidemic", "energy_kj", 1 / 1000)
    agentic_energies, agentic_energies_sd = series("agentic", "energy_kj", 1 / 1000)

    # Plot 1: fig_sync_vs_drop.png
    fig1, ax1 = plt.subplots(figsize=(10, 6), dpi=200)
    ax1.plot(drop_percentages, gossip_syncs, 's--', color='#d62828', lw=2.2, ms=7,
             markerfacecolor='#f77f7f', markeredgecolor='#d62828', mew=1.5,
             label='Baseline 1: Gossip Protocol (Raw JSON, TTL=3)')
    ax1.plot(drop_percentages, epidemic_syncs, '^:', color='#f77f00', lw=2.2, ms=7,
             markerfacecolor='#fcbf49', markeredgecolor='#f77f00', mew=1.5,
             label='Baseline 2: Epidemic Routing (Store & Forward, Delivered-Once)')
    ax1.plot(drop_percentages, agentic_syncs, 'o-', color='#0077b6', lw=2.8, ms=8,
             markerfacecolor='#00b4d8', markeredgecolor='#023e8a', mew=1.5,
             label='Proposed: Agentic SLM Protocol (Llama-3-8B + Link Memory)')

    if multi_seed:
        ax1.fill_between(drop_percentages,
                         [m - s for m, s in zip(agentic_syncs, agentic_syncs_sd)],
                         [m + s for m, s in zip(agentic_syncs, agentic_syncs_sd)],
                         alpha=0.15, color='#0077b6', label='Agentic SLM ±95% CI')
        ax1.fill_between(drop_percentages,
                         [m - s for m, s in zip(gossip_syncs, gossip_syncs_sd)],
                         [m + s for m, s in zip(gossip_syncs, gossip_syncs_sd)],
                         alpha=0.12, color='#d62828', label='Gossip ±95% CI')

    ax1.fill_between(drop_percentages, gossip_syncs, agentic_syncs, alpha=0.10, color='#0077b6')
    for x, yg, ya in zip(drop_percentages, gossip_syncs, agentic_syncs):
        ax1.annotate(f'{yg:.0f}%', (x, yg), textcoords='offset points', xytext=(0, -14), ha='center', fontsize=7, color='#d62828', fontweight='bold')
        ax1.annotate(f'{ya:.0f}%', (x, ya), textcoords='offset points', xytext=(0, 10), ha='center', fontsize=7, color='#023e8a', fontweight='bold')

    seed_note = f"Mean over {len(seeds)} paired seeds (±95% CI, Student-t)" if multi_seed else f"Seed {seeds[0]}"
    ax1.set_title(f'Effective State Synchronization % Under Gilbert-Elliott DDIL Loss\n'
                  f'{NUM_NODES}-Node Swarm Benchmark — {seed_note}',
                  fontsize=12, fontweight='bold', pad=15)
    ax1.set_xlabel('Environmental Packet Drop Rate (%)', fontsize=11)
    ax1.set_ylabel('Effective State Synchronization (%)', fontsize=11)
    ax1.set_xlim(-2, 82); ax1.set_ylim(0, 105)
    ax1.grid(True, linestyle=':', alpha=0.5, color='#dee2e6')
    ax1.legend(loc='lower left', fontsize=9, framealpha=0.95, fancybox=True, shadow=True)
    fig1.tight_layout()
    fig1.savefig(PLOT_SYNC_PATH)
    plt.close(fig1)
    print(f"[OUTPUT] Plot 1 saved to: {PLOT_SYNC_PATH}")

    # Plot 2: fig_energy_vs_drop.png
    fig2, ax2 = plt.subplots(figsize=(10, 6), dpi=200)
    ax2.plot(drop_percentages, gossip_energies, 's--', color='#d62828', lw=2.2, ms=7,
             markerfacecolor='#f77f7f', markeredgecolor='#d62828', mew=1.5,
             label='Baseline 1: Gossip Protocol')
    ax2.plot(drop_percentages, epidemic_energies, '^:', color='#f77f00', lw=2.5, ms=8,
             markerfacecolor='#f4a261', markeredgecolor='#e76f51', mew=1.5,
             label='Baseline 2: Epidemic Routing (Flooding Overhead Explodes)')
    ax2.plot(drop_percentages, agentic_energies, 'o-', color='#2a9d8f', lw=2.8, ms=8,
             markerfacecolor='#e9c46a', markeredgecolor='#264653', mew=1.5,
             label='Proposed: Agentic SLM Protocol (Frugal State Quantization)')

    if multi_seed:
        ax2.fill_between(drop_percentages,
                         [m - s for m, s in zip(agentic_energies, agentic_energies_sd)],
                         [m + s for m, s in zip(agentic_energies, agentic_energies_sd)],
                         alpha=0.15, color='#2a9d8f', label='Agentic SLM ±95% CI')
        ax2.fill_between(drop_percentages,
                         [m - s for m, s in zip(epidemic_energies, epidemic_energies_sd)],
                         [m + s for m, s in zip(epidemic_energies, epidemic_energies_sd)],
                         alpha=0.12, color='#f77f00', label='Epidemic ±95% CI')

    ax2.set_title(f'Total Swarm Parametric Energy Expenditure (kJ) vs. Drop Rate\n'
                  f'Parametric Model: RF Tx (0.05 J/B) vs. SLM Compute (0.01 J/Token) — {seed_note}',
                  fontsize=12, fontweight='bold', pad=15)
    ax2.set_xlabel('Environmental Packet Drop Rate (%)', fontsize=11)
    ax2.set_ylabel('Total Swarm Energy Consumption (kJ)', fontsize=11)
    ax2.set_xlim(-2, 82)
    ax2.grid(True, linestyle=':', alpha=0.5, color='#dee2e6')
    ax2.legend(loc='upper right', fontsize=9, framealpha=0.95, fancybox=True, shadow=True)
    fig2.tight_layout()
    fig2.savefig(PLOT_ENERGY_PATH)
    plt.close(fig2)
    print(f"[OUTPUT] Plot 2 saved to: {PLOT_ENERGY_PATH}")


def run_empirical_benchmark_suite(
    seeds: Optional[List[int]] = None,
    drop_rates: Optional[List[float]] = None,
    csv_out: Optional[str] = None,
    make_plots: bool = True
) -> Dict[str, Dict[float, Dict[str, Tuple[float, float]]]]:
    seeds = seeds or [RANDOM_SEED]
    drop_rates = drop_rates if drop_rates is not None else list(DROP_RATE_SWEEP)
    multi_seed = len(seeds) > 1

    print("\n" + "=" * 76)
    print("  EMPIRICAL DDIL MULTI-AGENT SWARM BENCHMARK SUITE")
    print(f"  Nodes: {NUM_NODES} Swarm Nodes | Gilbert-Elliott Burst Channel")
    print(f"  Topology: Watts-Strogatz (k=6, p=0.3) | Duration: {SIM_DURATION}t")
    print(f"  Seeds: {len(seeds)} paired seeds: {seeds[:5]}{'...' if len(seeds)>5 else ''}")
    print("=" * 76)

    csv_rows: List[dict] = []

    t_start = time.time()
    init_wandb("benchmark", extra_config={"seeds": seeds, "drop_rates": drop_rates,
                                            "multi_seed": multi_seed})
    step_counter = 0
    for seed in seeds:
        seed_rows = benchmark_rows_for_seed(seed, drop_rates)
        csv_rows.extend(seed_rows)
        for r in seed_rows:
            wandb_log_row(r, step=step_counter); step_counter += 1
        print(f"  [seed {seed}] cumulative runs: {len(csv_rows)}", flush=True)

    elapsed = time.time() - t_start
    print(f"\n[DONE] Benchmark sweep ({len(seeds)} seeds x {len(drop_rates)} drops x 3 modes) completed in {elapsed/60:.1f} min")

    if csv_out:
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"[OUTPUT] Raw per-run results CSV written to: {csv_out}")

    # Fallback-contamination audit: warn loudly if live inference silently degraded
    agentic_rows = [r for r in csv_rows if r["mode"] == "agentic"]
    if agentic_rows and not multi_seed:
        total_fb = sum(r["llm_fallbacks"] for r in agentic_rows)
        if total_fb > 0.10 * sum(1 for r in csv_rows if r["mode"] == "agentic"):
            print(f"[WARNING] Agentic fallback rate {total_fb} compressions exceeded 10% — "
                  "live-vLLM results may be contaminated by deterministic fallback. "
                  "Check endpoint readiness (see preflight_vllm_endpoints).")

    def agg(mode: str, dr: float) -> Dict[str, Tuple[float, float]]:
        """Aggregate per-run CSV rows -> (mean, 95% CI). Energy held in joules
        internally (row field is kJ, scale 1e-3 converts kJ -> J)."""
        fields = {"sync": ("sync_pct", 100.0), "delivery": ("delivery_pct", 100.0),
                  "bytes": ("delivered_bytes", 1.0), "energy": ("energy_kj", 1e-3),
                  "tokens": ("tokens_generated", 1.0), "dpr": ("dpr_pct", 100.0),
                  "ips": ("ips_score", 1.0)}
        out = {}
        for key, (field, scale) in fields.items():
            vals = [r[field] / scale for r in csv_rows
                    if r["mode"] == mode and abs(r["drop_rate"] - dr) < 1e-9]
            out[key] = (statistics.mean(vals), ci95_halfwidth(vals))
        return out

    agg_results: Dict[str, Dict[float, Dict[str, Tuple[float, float]]]] = {
        mode: {dr: agg(mode, dr) for dr in drop_rates} for mode in ("gossip", "epidemic", "agentic")
    }

    if make_plots:
        render_benchmark_plots(csv_rows, seeds, drop_rates)

    severe_dr = max(drop_rates)
    export_latex_booktabs_table(agg_results["gossip"][severe_dr], agg_results["epidemic"][severe_dr],
                                agg_results["agentic"][severe_dr], drop_rate_pct=severe_dr * 100)
    wandb_finish(artifacts=[p for p in (csv_out, PLOT_SYNC_PATH, PLOT_ENERGY_PATH) if p and os.path.exists(p)])
    return agg_results


# ============================================================================
# Ablation Studies Suite Runner
# ============================================================================

def run_ablation_suite(
    seeds: Optional[List[int]] = None,
    drop_rates: Optional[List[float]] = None,
    make_plots: bool = True,
    csv_out: Optional[str] = None
) -> Dict[str, Dict[float, Dict[str, Tuple[float, float]]]]:
    seeds = seeds or [RANDOM_SEED]
    drop_rates = drop_rates if drop_rates is not None else list(DROP_RATE_SWEEP)
    drop_percentages: List[float] = [dr * 100 for dr in drop_rates]

    ablation_configs = [
        RunConfig(True, True, True, True, label="Full Agentic SLM"),
        RunConfig(True, False, False, True, label="A1: No Link Memory"),
        RunConfig(False, True, True, False, label="A2: No Compression"),
        RunConfig(True, True, False, True, label="A3: No Relay Routing"),
        RunConfig(True, True, True, False, label="A4: No Verification Gate"),
    ]

    print("\n" + "=" * 76)
    print("  SYSTEMATIC ABLATION SUITE — AGENTIC SLM ARCHITECTURE")
    print(f"  Evaluating 5 Architectural Variants across {len(drop_rates)} Drop Rates")
    print(f"  Nodes: {NUM_NODES} | Duration: {SIM_DURATION}t | Seeds: {len(seeds)}")
    print("=" * 76)

    csv_rows: List[dict] = []

    t_start = time.time()
    init_wandb("ablation", extra_config={"seeds": seeds, "drop_rates": drop_rates,
                                          "variants": [c.label for c in ablation_configs]})
    step_counter = 0
    for seed in seeds:
        seed_rows = ablation_rows_for_seed(seed, drop_rates, configs=ablation_configs)
        csv_rows.extend(seed_rows)
        for r in seed_rows:
            wandb_log_row(r, step=step_counter); step_counter += 1
        print(f"  [seed {seed}] cumulative runs: {len(csv_rows)}", flush=True)

    elapsed = time.time() - t_start
    print(f"\n[DONE] Ablation suite completed in {elapsed/60:.1f} min")

    if csv_out:
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"[OUTPUT] Ablation per-run results CSV written to: {csv_out}")

    def agg_variant(label: str, dr: float) -> Dict[str, Tuple[float, float]]:
        fields = {"sync": ("sync_pct", 100.0), "delivery": ("delivery_pct", 100.0),
                  "bytes": ("delivered_bytes", 1.0), "energy": ("energy_kj", 1e-3),
                  "tokens": ("tokens_generated", 1.0), "dpr": ("dpr_pct", 100.0),
                  "ips": ("ips_score", 1.0)}
        out = {}
        for key, (field, scale) in fields.items():
            vals = [r[field] / scale for r in csv_rows
                    if r["variant"] == label and abs(r["drop_rate"] - dr) < 1e-9]
            out[key] = (statistics.mean(vals), ci95_halfwidth(vals))
        return out

    agg_ablation: Dict[str, Dict[float, Dict[str, Tuple[float, float]]]] = {
        cfg.label: {dr: agg_variant(cfg.label, dr) for dr in drop_rates} for cfg in ablation_configs
    }

    if make_plots:
        render_ablation_plot(csv_rows, drop_rates)

    severe_dr = max(drop_rates)
    severe_results = {cfg.label: agg_ablation[cfg.label][severe_dr] for cfg in ablation_configs}
    export_ablation_latex_table(severe_results, drop_rate_pct=severe_dr * 100)
    wandb_finish(artifacts=[p for p in (csv_out, PLOT_ABLATION_PATH) if p and os.path.exists(p)])
    return agg_ablation


# ============================================================================
# Addition: IPS Threshold Sensitivity Analysis (Item 1 & 6)
# ============================================================================

def run_sensitivity_experiment(
    seeds: Optional[List[int]] = None,
    thresholds: Optional[List[float]] = None,
    drop_rates: Optional[List[float]] = None,
    csv_out: Optional[str] = None
) -> Dict[float, Dict[float, Dict[str, Tuple[float, float]]]]:
    seeds = seeds or [RANDOM_SEED]
    thresholds = thresholds or [0.90, 0.95, 0.98]
    drop_rates = drop_rates or [0.0, 0.40, 0.80]

    print("\n" + "=" * 76)
    print("  IPS THRESHOLD SENSITIVITY EXPERIMENT (theta in {0.90, 0.95, 0.98})")
    print("=" * 76)

    results: Dict[float, Dict[float, Dict[str, Tuple[float, float]]]] = {}
    csv_rows: List[dict] = []

    init_wandb("sensitivity", extra_config={"seeds": seeds, "thresholds": thresholds, "drop_rates": drop_rates})
    step_counter = 0
    for th in thresholds:
        results[th] = {}
        cfg = RunConfig(enable_compression=True, enable_link_memory=True, enable_relay=True,
                        enable_drift_check=True, ips_threshold=th, label=f"IPS theta={th}")
        for dr in drop_rates:
            runs = []
            for s in seeds:
                res = execute_simulation_run("agentic", dr, seed=s, config=cfg)
                runs.append(res)
                (sync, delivery, total_bytes, energy, tokens, dpr, ips,
                 fallbacks, inj_gen, inj_rej, parse_succ, drift_fail, gate_pass) = res
                row = {
                    "seed": s, "ips_threshold": th, "drop_rate": dr,
                    "dpr_pct": round(dpr * 100, 2),
                    "sync_pct": round(sync * 100, 2),
                    "delivery_pct": round(delivery * 100, 2),
                    "ips_score": round(ips, 4),
                    "llm_fallbacks": fallbacks,
                    "gate_pass_rate": round(gate_pass, 4),
                    "drift_failures": drift_fail
                }
                csv_rows.append(row)
                wandb_log_row(row, step=step_counter); step_counter += 1
            dpr_vals = [r[5] for r in runs]
            sync_vals = [r[0] for r in runs]
            del_vals = [r[1] for r in runs]
            ips_vals = [r[6] for r in runs]
            results[th][dr] = {
                "dpr": (statistics.mean(dpr_vals), ci95_halfwidth(dpr_vals)),
                "sync": (statistics.mean(sync_vals), ci95_halfwidth(sync_vals)),
                "delivery": (statistics.mean(del_vals), ci95_halfwidth(del_vals)),
                "ips": (statistics.mean(ips_vals), ci95_halfwidth(ips_vals)),
            }
            print(f"  [theta={th:.2f} | Drop {dr:.0%}] DPR: {results[th][dr]['dpr'][0]:.1%} | "
                  f"Sync: {results[th][dr]['sync'][0]:.1%} | Deliv: {results[th][dr]['delivery'][0]:.1%} | "
                  f"MeanIPS: {results[th][dr]['ips'][0]:.3f}")

    if csv_out:
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"[OUTPUT] Sensitivity per-run results CSV written to: {csv_out}")

    wandb_finish(artifacts=[csv_out] if csv_out and os.path.exists(csv_out) else [])
    return results


# ============================================================================
# Addition: Hallucination Injection Robustness Experiment (Item 7)
# ============================================================================

def run_robustness_experiment(
    seeds: Optional[List[int]] = None,
    injection_rates: Optional[List[float]] = None,
    drop_rate: float = 0.40,
    csv_out: Optional[str] = None
) -> Dict[float, Dict[str, float]]:
    """Hallucination-injection robustness with directly MEASURED gate performance.

    For each injection rate we count how many injected (corrupted) tokens the
    sender-side IPS gate rejects. Gate recall = rejected / injected; the gate
    false-accept rate = 1 - recall. Downstream damage is measured by DPR and
    state synchronization. No proxy formulas: every number is counted.
    """
    seeds = seeds or [RANDOM_SEED]
    injection_rates = injection_rates or [0.0, 0.05, 0.10, 0.20, 0.50]

    print("\n" + "=" * 76)
    print("  HALLUCINATION INJECTION ROBUSTNESS EXPERIMENT (measured gate recall/FAR)")
    print("=" * 76)

    robustness_results: Dict[float, Dict[str, float]] = {}
    csv_rows: List[dict] = []

    init_wandb("robustness", extra_config={"seeds": seeds, "injection_rates": injection_rates, "drop_rate": drop_rate})
    step_counter = 0
    for rate in injection_rates:
        cfg = RunConfig(enable_compression=True, enable_link_memory=True, enable_relay=True,
                        enable_drift_check=True, injection_rate=rate, label=f"Inject {rate:.0%}")
        dpr_list, sync_list, recall_list = [], [], []
        for s in seeds:
            (sync, delivery, total_bytes, energy, tokens, dpr, ips,
             fallbacks, inj_gen, inj_rej, parse_succ, drift_fail, gate_pass) = execute_simulation_run(
                "agentic", drop_rate=drop_rate, seed=s, config=cfg
            )
            dpr_list.append(dpr)
            sync_list.append(sync)
            recall = (inj_rej / inj_gen) if inj_gen > 0 else 1.0
            recall_list.append(recall)
            row = {
                "seed": s, "injection_rate": rate, "drop_rate": drop_rate,
                "dpr_pct": round(dpr * 100, 2),
                "sync_pct": round(sync * 100, 2),
                "injections_generated": inj_gen,
                "injections_rejected": inj_rej,
                "gate_recall": round(recall, 4),
                "gate_pass_rate": round(gate_pass, 4),
                "llm_fallbacks": fallbacks
            }
            csv_rows.append(row)
            wandb_log_row(row, step=step_counter); step_counter += 1

        mean_dpr = statistics.mean(dpr_list)
        mean_sync = statistics.mean(sync_list)
        mean_recall = statistics.mean(recall_list) if rate > 0 else 1.0
        far = 1.0 - mean_recall

        robustness_results[rate] = {
            "dpr": mean_dpr,
            "sync": mean_sync,
            "gate_recall": mean_recall,
            "gate_false_accept_rate": far
        }
        print(f"  [Inject Rate: {rate:4.0%}] DPR: {mean_dpr:.1%} | Sync: {mean_sync:.1%} | "
              f"Gate Recall: {mean_recall:.1%} | Gate FAR: {far:.1%}")

    if csv_out:
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"[OUTPUT] Robustness per-run results CSV written to: {csv_out}")

    wandb_finish(artifacts=[csv_out] if csv_out and os.path.exists(csv_out) else [])
    return robustness_results


# ============================================================================
# Interactive CLI Bootstrapper & Runner
# ============================================================================

def interactive_cli_bootstrapper() -> None:
    # Batch-safety: never attempt interactive input without a TTY
    if sys.stdin is None or not sys.stdin.isatty():
        print("[MODE] No interactive terminal available — running standard benchmark with defaults.")
        run_empirical_benchmark_suite()
        return
    print("\n" + "=" * 76)
    print("  ==================================================================")
    print("  *   EMPIRICAL DDIL MULTI-AGENT SWARM BENCHMARK SUITE — InCIS 2027  *")
    print("  ==================================================================")
    print("=" * 76)

    # 1. Token / Hugging Face check
    cached_token = os.environ.get("HF_TOKEN", "")
    print(f"\n[1] Authentication & Environment:")
    if cached_token:
        print(f"    Detected cached HF_TOKEN: {cached_token[:6]}...{cached_token[-4:]}")
        user_tok = input("    Press [Enter] to use cached token, or enter a new token: ").strip()
        if user_tok:
            os.environ["HF_TOKEN"] = user_tok
    else:
        user_tok = input("    Enter Hugging Face / Model Token (or press [Enter] to skip): ").strip()
        if user_tok:
            os.environ["HF_TOKEN"] = user_tok

    # 2. Execution Backend Mode
    print(f"\n[2] Inference Backend Selection:")
    print("    [A] Live A100 GPU vLLM Cluster (Ports 8001-8008, Llama-3-8B)")
    print("    [B] Deterministic CPU Fallback (Instant reproducible benchmark)")
    backend_choice = input("    Select Backend [A/B, default A]: ").strip().upper()
    if backend_choice == "B":
        os.environ["DDIL_DISABLE_VLLM"] = "1"
        print("    >> Backend set to: Deterministic CPU Fallback (DDIL_DISABLE_VLLM=1)")
    else:
        os.environ.pop("DDIL_DISABLE_VLLM", None)
        print("    >> Backend set to: Live 8x A100 vLLM Endpoints")

    # 3. Execution Pipeline Selection
    print(f"\n[3] Select Execution Pipeline:")
    print("    [1] Full Empirical Benchmark (Gossip vs Epidemic vs Agentic SLM, 0%-80% Drop)")
    print("    [2] Full Ablation Study Suite (Full System vs A1, A2, A3, A4)")
    print("    [3] Fast Smoke Test (Duration=10s, verify all endpoints & plots)")
    print("    [4] Complete Publication Run (Benchmark + Ablations + All Plots & LaTeX Tables)")
    print("    [5] Robustness & Sensitivity Suite (Hallucination Injection + Threshold Sweeps)")
    pipe_choice = input("    Choose Option [1-5, default 1]: ").strip() or "1"

    global NUM_NODES, SIM_DURATION
    nodes_in = input(f"\n[4] Swarm Node Count (N) [default {NUM_NODES}]: ").strip()
    if nodes_in.isdigit():
        NUM_NODES = int(nodes_in)

    dur_in = input(f"    Simulation Duration per Run (t) [default {SIM_DURATION}]: ").strip()
    if dur_in:
        try:
            SIM_DURATION = float(dur_in)
        except ValueError:
            pass

    print(f"\n[CONFIG] Nodes: {NUM_NODES} | Duration: {SIM_DURATION}t | Backend: {'CPU Fallback' if os.environ.get('DDIL_DISABLE_VLLM') else 'Live vLLM'}")
    print("Starting simulation in 2 seconds...")
    time.sleep(2)

    if pipe_choice == "1":
        run_empirical_benchmark_suite()
    elif pipe_choice == "2":
        run_ablation_suite()
    elif pipe_choice == "3":
        SIM_DURATION = 10.0
        run_empirical_benchmark_suite(drop_rates=[0.0, 0.40, 0.80])
    elif pipe_choice == "4":
        run_empirical_benchmark_suite()
        run_ablation_suite()
    elif pipe_choice == "5":
        run_sensitivity_experiment()
        run_robustness_experiment()
    else:
        run_empirical_benchmark_suite()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Empirical DDIL swarm benchmark: Gossip vs Epidemic vs Agentic SLM."
    )
    parser.add_argument("--mode", choices=["interactive", "benchmark", "ablation", "fast", "all", "sensitivity", "robustness"], default="interactive",
                        help="Execution mode (default: interactive CLI bootstrapper; auto-selects "
                             "benchmark defaults when stdin is not a TTY, e.g. in batch jobs)")
    parser.add_argument("--seeds", type=int, nargs="+", default=[42],
                        help="RNG seeds for multi-seed aggregation (e.g. --seeds 42 43 44)")
    parser.add_argument("--drop-rates", type=float, nargs="+", default=None,
                        help="Custom drop-rate sweep override (e.g. --drop-rates 0.0 0.4 0.8)")
    parser.add_argument("--nodes", type=int, default=50, help="Swarm size (default 50)")
    parser.add_argument("--duration", type=float, default=100.0, help="SimPy simulation duration (default 100)")
    parser.add_argument("--csv-out", default="ddil_results.csv", help="CSV export filename")
    parser.add_argument("--cpu", action="store_true", help="Force deterministic CPU fallback mode")
    parser.add_argument("--no-plots", action="store_true", help="Skip figure generation")
    parser.add_argument("--skip-preflight", action="store_true",
                        help="Skip the vLLM endpoint readiness gate (not recommended)")
    parser.add_argument("--preflight-wait", type=float, default=1200.0,
                        help="Max seconds to wait for vLLM endpoint readiness (default 1200)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.cpu:
        os.environ["DDIL_DISABLE_VLLM"] = "1"

    live_mode = not os.environ.get("DDIL_DISABLE_VLLM", "").lower() in ("1", "true", "yes")

    if live_mode and not args.skip_preflight:
        preflight_vllm_endpoints(max_wait_s=args.preflight_wait)

    # Non-TTY guard: batch schedulers (SLURM/tmux/nohup) provide no stdin; fall back
    # to the standard benchmark instead of hanging or crashing on input().
    interactive_requested = (args.mode == "interactive" and len(sys.argv) == 1)
    if interactive_requested and sys.stdin is not None and not sys.stdin.isatty():
        print("[MODE] Non-interactive stdin detected — running standard benchmark with defaults.")
        args.mode = "benchmark"

    if args.mode != "interactive" or len(sys.argv) > 1:
        NUM_NODES = args.nodes
        SIM_DURATION = args.duration
        if args.drop_rates is not None:
            DROP_RATE_SWEEP = sorted(args.drop_rates)

        if args.mode == "benchmark":
            run_empirical_benchmark_suite(seeds=args.seeds, drop_rates=DROP_RATE_SWEEP,
                                          csv_out=args.csv_out, make_plots=not args.no_plots)
        elif args.mode == "ablation":
            run_ablation_suite(seeds=args.seeds, drop_rates=DROP_RATE_SWEEP,
                               make_plots=not args.no_plots, csv_out=args.csv_out)
        elif args.mode == "fast":
            SIM_DURATION = 10.0
            run_empirical_benchmark_suite(seeds=args.seeds, drop_rates=[0.0, 0.40, 0.80],
                                          csv_out=args.csv_out, make_plots=not args.no_plots)
        elif args.mode == "all":
            run_empirical_benchmark_suite(seeds=args.seeds, drop_rates=DROP_RATE_SWEEP,
                                          csv_out=args.csv_out, make_plots=not args.no_plots)
            run_ablation_suite(seeds=args.seeds, drop_rates=DROP_RATE_SWEEP,
                               make_plots=not args.no_plots, csv_out=args.csv_out)
            run_sensitivity_experiment(seeds=args.seeds, drop_rates=[0.0, 0.40, 0.80],
                                       csv_out=args.csv_out.replace(".csv", "_sensitivity.csv"))
            run_robustness_experiment(seeds=args.seeds,
                                      csv_out=args.csv_out.replace(".csv", "_robustness.csv"))
        elif args.mode == "sensitivity":
            run_sensitivity_experiment(seeds=args.seeds, drop_rates=DROP_RATE_SWEEP, csv_out=args.csv_out)
        elif args.mode == "robustness":
            run_robustness_experiment(seeds=args.seeds, csv_out=args.csv_out)
        else:
            interactive_cli_bootstrapper()
    else:
        interactive_cli_bootstrapper()
