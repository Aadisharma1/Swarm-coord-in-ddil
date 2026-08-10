import random
import time
import pandas as pd

class Drone:
    def __init__(self, drone_id, num_targets):
        self.drone_id = drone_id
        # WBL: Winning Bid List (stores the highest score seen for each target)
        self.wbl = [0.0] * num_targets
        # WAL: Winning Agent List (stores the ID of the drone with the highest score)
        self.wal = [-1] * num_targets
        
    def generate_bids(self):
        # Simulate OpenClaw heuristic: assign a random suitability score to targets
        for t in range(len(self.wbl)):
            my_score = round(random.uniform(10.0, 100.0), 2)
            if my_score > self.wbl[t]:
                self.wbl[t] = my_score
                self.wal[t] = self.drone_id

    def receive_bids(self, sender_id, sender_wbl, sender_wal):
        # Consensus Phase: Update local lists based on received broadcasts
        updated = False
        for t in range(len(self.wbl)):
            if sender_wbl[t] > self.wbl[t]:
                self.wbl[t] = sender_wbl[t]
                self.wal[t] = sender_wal[t]
                updated = True
            elif sender_wbl[t] == self.wbl[t]:
                # Tie-breaker using drone ID
                if sender_wal[t] > self.wal[t]:
                    self.wal[t] = sender_wal[t]
                    updated = True
        return updated

def check_global_consensus(drones):
    # Check if all drones have the exact same Winning Agent List
    reference_wal = drones[0].wal
    for d in drones[1:]:
        if d.wal != reference_wal:
            return False
    return True

def run_simulation(num_drones, num_targets, packet_loss_rate):
    # Initialize swarm
    drones = [Drone(i, num_targets) for i in range(num_drones)]
    
    # Phase 1: All drones generate their initial local bids
    for d in drones:
        d.generate_bids()
        
    iterations = 0
    max_iterations = 5000 # Prevent infinite loops if 100% packet loss
    
    # Phase 2: Gossip Protocol (Decentralized Auction over Lossy Network)
    while not check_global_consensus(drones) and iterations < max_iterations:
        iterations += 1
        
        # Simulate simultaneous broadcasting
        # We copy current states so updates happen synchronously
        current_wbls = [list(d.wbl) for d in drones]
        current_wals = [list(d.wal) for d in drones]
        
        for sender in drones:
            for receiver in drones:
                if sender.drone_id == receiver.drone_id:
                    continue
                
                # Electronic Warfare Simulation: Drop packets based on probability
                if random.random() >= packet_loss_rate:
                    receiver.receive_bids(sender.drone_id, 
                                          current_wbls[sender.drone_id], 
                                          current_wals[sender.drone_id])
                    
    # Assuming 1 iteration takes roughly 10ms of real-time communication
    simulated_latency_sec = iterations * 0.010 
    return simulated_latency_sec

if __name__ == "__main__":
    num_drones = 50
    num_targets = 10
    loss_rates = [0.0, 0.25, 0.50, 0.75, 0.85, 0.90]
    trials = 10 # Run multiple trials to get average results
    
    print(f"Starting Swarm Simulation...")
    print(f"Drones: {num_drones} | Targets: {num_targets} | Trials per rate: {trials}\n")
    
    results = []
    
    for loss in loss_rates:
        print(f"Simulating EW Packet Loss: {int(loss*100)}%...")
        total_latency = 0
        for _ in range(trials):
            latency = run_simulation(num_drones, num_targets, loss)
            total_latency += latency
            
        avg_latency = total_latency / trials
        results.append({"Packet Loss (%)": int(loss*100), "Avg Consensus Latency (sec)": round(avg_latency, 3)})
        
    df = pd.DataFrame(results)
    print("\n=== EXPERIMENTAL SIMULATION RESULTS ===")
    print(df.to_string(index=False))
    
    # Save to CSV so you can use it in your paper
    df.to_csv("swarm_simulation_results.csv", index=False)
    print("\nResults saved to 'swarm_simulation_results.csv'.")
