#!/usr/bin/env python3
"""
Unit and Verification Tests for Empirical DDIL Simulation Components
"""

import math
import pytest
import random
import statistics
from empirical_ddil_simulation import (
    RawStateMatrix,
    DecisionOracle,
    calculate_invariant_preservation,
    validate_received_structure,
    GilbertElliottChannel,
    LLMAgentNode,
    RunConfig,
    _node_registry
)
import simpy
import networkx as nx


def test_decision_oracle_basic():
    raw = RawStateMatrix(origin_node=0, timestamp=1.0)
    raw.state_vector = [0.5, 0.5, 1.2, 45.0, 0.0, 0.0]
    raw.energy_level = 85.0
    raw.matrix_weights = {"w_0": 0.85, "w_1": 0.2}

    d_raw = DecisionOracle.decide_from_raw(raw)
    assert d_raw == ("NE", "HIGH", "NORMAL")

    compressed = {
        "id": raw.sequence_id,
        "origin": 0,
        "ts": 1.0,
        "pos": [0.5, 0.5],
        "vel": 1.2,
        "hdg": 45.0,
        "bat": 85.0,
        "pri": 0.85,
        "st": 1
    }
    d_comp = DecisionOracle.decide_from_compressed(compressed)
    assert d_comp == ("NE", "HIGH", "NORMAL")
    assert DecisionOracle.agreement(d_raw, d_comp) == 1.0


def test_decision_oracle_mismatch():
    d1 = ("NE", "HIGH", "NORMAL")
    d2 = ("SW", "HIGH", "CRITICAL")
    assert DecisionOracle.agreement(d1, d2) == 1.0 / 3.0


def test_ips_distinguishes_different_vectors():
    """Demonstrates that unlike sum(v_state), IPS catches vector distortion."""
    raw = RawStateMatrix(origin_node=0, timestamp=1.0)
    raw.state_vector = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]  # sum is 6.0
    raw.energy_level = 80.0
    raw.matrix_weights = {"w_0": 0.5}

    # Compressed accurately
    accurate = {
        "id": raw.sequence_id,
        "origin": 0,
        "ts": 1.0,
        "pos": [1.0, 1.0],
        "vel": 1.0,
        "hdg": 1.0,
        "bat": 80.0,
        "pri": 0.5
    }
    _, ips_acc, valid_acc = calculate_invariant_preservation(raw, accurate, threshold=0.95)
    assert ips_acc >= 0.99
    assert valid_acc is True

    # Badly distorted vector that would have had the same sum(v_state) = 6.0
    distorted = {
        "id": raw.sequence_id,
        "origin": 0,
        "ts": 1.0,
        "pos": [6.0, 0.0],  # Severe spatial distortion!
        "vel": 0.0,
        "hdg": 0.0,
        "bat": 80.0,
        "pri": 0.5
    }
    _, ips_dist, valid_dist = calculate_invariant_preservation(raw, distorted, threshold=0.95)
    assert ips_dist < 0.60
    assert valid_dist is False


def test_receiver_structural_validation():
    # Valid payload
    valid_payload = {
        "id": "abc", "origin": 1, "ts": 10.0,
        "pos": [0.2, -0.5], "vel": 1.0, "hdg": 90.0,
        "bat": 75.0, "pri": 0.8, "st": 1
    }
    assert validate_received_structure(valid_payload, current_time=10.0) is True

    # Missing battery key
    missing_key = dict(valid_payload)
    del missing_key["bat"]
    assert validate_received_structure(missing_key, current_time=10.0) is False

    # Out of range battery
    bad_bat = dict(valid_payload)
    bad_bat["bat"] = 150.0
    assert validate_received_structure(bad_bat, current_time=10.0) is False

    # Future timestamp
    future_ts = dict(valid_payload)
    future_ts["ts"] = 50.0
    assert validate_received_structure(future_ts, current_time=10.0) is False


def test_relay_joint_reliability_calculation():
    """Verify relay selection uses L_im * L_mj."""
    env = simpy.Environment()
    G = nx.Graph()
    G.add_edges_from([(0, 1), (0, 2), (1, 3), (2, 3), (0, 3)])

    class DummyCtrl:
        def attempt_transmission(self, *args, **kwargs):
            return True, 0.1, None

    class DummyMetrics:
        def record(self, *args): pass
        def record_parse_outcome(self, *args, **kwargs): pass
        def record_decision(self, *args): pass
        def add_tokens(self, *args): pass

    _node_registry.clear()
    n0 = LLMAgentNode(0, env, DummyCtrl(), G, DummyMetrics(), config=RunConfig())
    n1 = LLMAgentNode(1, env, DummyCtrl(), G, DummyMetrics(), config=RunConfig())
    n2 = LLMAgentNode(2, env, DummyCtrl(), G, DummyMetrics(), config=RunConfig())
    n3 = LLMAgentNode(3, env, DummyCtrl(), G, DummyMetrics(), config=RunConfig())

    _node_registry[0] = n0
    _node_registry[1] = n1
    _node_registry[2] = n2
    _node_registry[3] = n3

    # Direct link from 0 to 3 is degraded
    n0.link_scores[3] = 0.10

    # Path 1: 0 -> 1 (0.9), 1 -> 3 (0.3) => joint = 0.27
    n0.link_scores[1] = 0.90
    n1.link_scores[3] = 0.30

    # Path 2: 0 -> 2 (0.6), 2 -> 3 (0.8) => joint = 0.48
    n0.link_scores[2] = 0.60
    n2.link_scores[3] = 0.80

    best_relay = n0._find_best_relay(target_id=3)
    assert best_relay == 2  # Node 2 must be chosen because 0.48 > 0.27


def test_gilbert_elliott_channel():
    rng = random.Random(42)
    ch = GilbertElliottChannel(drop_rate=0.5, rng=rng)
    loss_samples = [ch.sample_loss_probability() for _ in range(500)]
    assert any(s == ch.loss_good for s in loss_samples)
    assert any(s == ch.loss_bad for s in loss_samples)


if __name__ == "__main__":
    test_decision_oracle_basic()
    test_decision_oracle_mismatch()
    test_ips_distinguishes_different_vectors()
    test_receiver_structural_validation()
    test_relay_joint_reliability_calculation()
    test_gilbert_elliott_channel()
    print("[ALL UNIT TESTS PASSED SUCCESSFULLY]")
