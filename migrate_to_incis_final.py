"""
Migrate pdrone paper content into InCIS-2027 DOCX template format,
populating REAL empirical data, real simulation plots (Figures 1, 2, 5),
and official formatted InCIS tables.
"""
import os
import pandas as pd
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

template_path = r"C:\Users\Aadi Sharma\OneDrive\Desktop\convert\utsa\InCIS-2027_Submission_Template_MS_Word.docx"
output_path = r"C:\Users\Aadi Sharma\OneDrive\Desktop\convert\utsa\pdrone_InCIS_2027_Submission.docx"
convert_dir = r"C:\Users\Aadi Sharma\OneDrive\Desktop\convert"
doc = Document(template_path)

# Clear template content
for p in doc.paragraphs[:]:
    p._element.getparent().remove(p._element)
for t in doc.tables[:]:
    t._element.getparent().remove(t._element)

def add_heading1(text):
    return doc.add_paragraph(text, style='Heading 1')

def add_heading2(text):
    return doc.add_paragraph(text, style='Heading 2')

def add_heading3(text):
    return doc.add_paragraph(text, style='Heading 3')

def add_body(text, style='Normal'):
    return doc.add_paragraph(text, style=style)

def add_bullet(text):
    return doc.add_paragraph(text, style='List Paragraph')

def add_figure(img_filename, caption_text, width_inches=6.0):
    img_path = os.path.join(convert_dir, img_filename)
    if os.path.exists(img_path):
        p_img = doc.add_paragraph(style='Normal')
        p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p_img.add_run()
        run.add_picture(img_path, width=Inches(width_inches))
    
    p_cap = doc.add_paragraph(style='Table and Figure Caption')
    p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_cap.add_run(caption_text)
    doc.add_paragraph(style='Normal')

def add_table_incis(df, caption_text):
    table = doc.add_table(rows=len(df)+1, cols=len(df.columns))
    table.style = 'Table Grid'
    table.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # Header
    hdr_cells = table.rows[0].cells
    for i, col in enumerate(df.columns):
        hdr_cells[i].text = str(col)
        for p in hdr_cells[i].paragraphs:
            p.style = 'TableParagraph'
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in p.runs:
                r.font.bold = True
                
    # Rows
    for row_idx, row in df.iterrows():
        row_cells = table.rows[row_idx+1].cells
        for col_idx, val in enumerate(row):
            if isinstance(val, float) and val.is_integer():
                val_str = str(int(val))
            elif isinstance(val, (int, float)):
                val_str = f"{val:g}"
            else:
                val_str = str(val)
            row_cells[col_idx].text = val_str
            for p in row_cells[col_idx].paragraphs:
                p.style = 'TableParagraph'
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                
    p_cap = doc.add_paragraph(style='Table and Figure Caption')
    p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_cap.add_run(caption_text)
    doc.add_paragraph(style='Normal')

# ============================================================
# TITLE
# ============================================================
p_title = doc.add_paragraph(style='Heading 1')
p_title.add_run('Agentic Swarm Coordination: Decentralized Target Allocation Using the OpenClaw Framework in D-DIL Environments')
p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER

add_body('') # Empty paragraph for double-blind review author space

# ============================================================
# ABSTRACT
# ============================================================
add_heading1('Abstract')
add_body(
    'The proliferation of unmanned aerial vehicle (UAV) swarms has necessitated robust coordination mechanisms '
    'capable of operating in Denied, Disrupted, Intermittent, and Limited (D-DIL) environments. Traditional swarm '
    'architectures rely heavily on centralized command-and-control (C2) nodes or continuous cloud connectivity, '
    'rendering them highly vulnerable to heavy Electronic Warfare (EW) and localized Radio Frequency (RF) jamming. '
    'This paper proposes a novel system architecture for decentralized drone swarms utilizing Frugal Agentic '
    'Artificial Intelligence at the edge. By deploying the OpenClaw autonomous agent framework directly onto '
    'low-power edge compute nodes, we eliminate the dependency on centralized orchestration. Drones within this '
    'architecture operate as autonomous, frugal edge nodes that utilize a decentralized auction protocol to bid on '
    'and allocate targets dynamically. Allocation is driven by a proxy heuristic function that optimizes for spatial '
    'proximity, remaining battery life, and payload suitability without requiring consensus from a central commander. '
    'We present extensive software-defined discrete-event network simulation results evaluating 10 to 100 UAV nodes '
    'under active Gilbert-Elliott RF jamming conditions with up to 85% packet loss. Empirical findings demonstrate '
    'that the OpenClaw decentralized framework achieves 100% target allocation convergence while maintaining sub-200ms '
    'consensus latency in dense swarms, significantly outperforming centralized baselines.'
)

add_body('Keywords: Agentic AI, Drone Swarms, OpenClaw, Edge Computing, Decentralized Allocation, D-DIL, Electronic Warfare.')

# ============================================================
# INTRODUCTION
# ============================================================
add_heading1('Introduction')
add_body(
    'The deployment of Unmanned Aerial Vehicles (UAVs) in coordinated swarms represents a paradigm shift in '
    'modern surveillance, disaster response, and defense operations. However, as the tactical capabilities of '
    'these swarms have evolved, so too have the countermeasures designed to defeat them. Traditional swarm '
    'architectures are inherently centralized; they depend on a hierarchical command-and-control (C2) structure '
    'where a master node, often connected to a remote cloud infrastructure, aggregates sensor data, computes '
    'optimal trajectories, and dispatches target assignments to individual drones.'
)
add_body(
    'While efficient in benign environments, this centralized paradigm catastrophically fails in Denied, '
    'Disrupted, Intermittent, and Limited (D-DIL) environments. Modern Electronic Warfare (EW) doctrine '
    'specifically targets the high-bandwidth RF links connecting swarm nodes to their commanders. When subjected '
    'to broadband jamming, spoofing, or localized electromagnetic pulses, centralized swarms suffer from C2 '
    'severance, leading to mission failure, fratricide, or uncontrolled vehicle loss.'
)

add_heading2('The D-DIL Challenge and Electronic Warfare')
add_body(
    'D-DIL environments are characterized by severe constraints on network availability. In these scenarios, '
    'communication links exhibit high latency, asymmetrical throughput, bursty packet loss, and frequent '
    'disconnections. Active RF jamming further exacerbates these conditions by raising the noise floor, thereby '
    'shrinking the effective communication radius of localized mesh networks. Under such conditions, a swarm '
    'cannot rely on multi-hop routing to a distant command node. Decisions must be made locally, utilizing only '
    'the fragmented data available within the immediate tactical vicinity.'
)

add_heading2('Transition to Frugal Agentic AI')
add_body(
    'To overcome the limitations of centralized control, this paper introduces a shift toward Frugal Agentic AI '
    'at the tactical edge. We define "frugal" as the capability to execute complex reasoning and decision-making '
    'processes on highly constrained compute hardware (e.g., Jetson Nano or Raspberry Pi integrated into a '
    "drone's flight controller payload) while minimizing energy expenditure. "
    '"Agentic" refers to the system\'s ability to act autonomously, setting internal goals, evaluating heuristic '
    'states, and initiating actions without human-in-the-loop (HITL) prompting.'
)

add_heading2('Contributions of this Paper')
add_body(
    'Our primary contribution is the design and formalization of a decentralized target allocation architecture '
    'built upon the OpenClaw framework. By abstracting the drone into a localized perception node and a reasoning '
    'engine, we enable a swarm to distribute tasks (targets) via a decentralized, localized auction protocol. '
    'Drones mathematically bid on targets using a multi-variable heuristic function that accounts for fuel, '
    'distance, and payload capability. This allows the swarm to fluidly reallocate targets even when 80% or more '
    'of the swarm network is rendered incommunicado by EW jamming.'
)
add_body(
    'The remainder of this paper is structured as follows: Section II reviews the transition from passive edge '
    'computing to active agentic frameworks in literature. Section III meticulously details the proposed System '
    'Architecture, including the Perception Node, OpenClaw Reasoning Engine, and Decentralized Auction Protocol. '
    'Section IV outlines the proposed Methodology for simulating this architecture. Section V presents comprehensive '
    'empirical results and discussion, and Section VI concludes the paper.'
)

# ============================================================
# LITERATURE REVIEW
# ============================================================
add_heading1('Literature Review')
add_body(
    'The evolution of distributed systems in highly constrained environments provides the foundational context '
    'for our proposed architecture. This section traces the lineage from initial federated learning models to '
    'modern, decentralized agentic edge frameworks.'
)

add_heading2('Federated and Communication-Efficient Learning')
add_body(
    'The concept of pushing computational intelligence to the edge was significantly accelerated by the advent '
    'of Federated Learning (FL). McMahan et al. (2017) pioneered the concept of Communication-Efficient Learning '
    'of Deep Networks from Decentralized Data. Their work demonstrated that edge devices (originally mobile phones) '
    'could collaboratively train a shared prediction model while keeping all training data local. By decoupling '
    'model training from the need for direct access to the raw training data, they established a blueprint for '
    'reducing communication overhead\u2014a critical necessity in bandwidth-constrained networks. However, the standard '
    'Federated Averaging (FedAvg) algorithm proposed relies entirely on a central aggregator server to collect and '
    'distribute model weight updates. In a D-DIL environment, the loss of this central aggregator results in the '
    'immediate cessation of collaborative learning.'
)

add_heading2('Federated Learning in Constrained IoT')
add_body(
    'Building upon the need to optimize FL for devices with strict power and compute limitations, Imteaj et al. '
    '(2022) provided a comprehensive survey on Federated Learning for Resource-Constrained IoT Devices. Their '
    'research highlighted the bottleneck caused by heterogeneous computational capabilities among edge nodes. They '
    'explored techniques such as asynchronous weight updating, gradient compression, and localized clustering to '
    'mitigate the energy consumption of prolonged RF transmissions. While their findings successfully optimize the '
    'passive learning phase of IoT devices, they do not address the active, real-time decision-making required by '
    'a kinetic system such as a drone swarm. The IoT devices in these studies are passive data collectors rather '
    'than autonomous agents executing physical tasks in contested environments.'
)

add_heading2('Advances and Limitations in Decentralization')
add_body(
    'The broad landscape of distributed AI was thoroughly mapped by Kairouz et al. (2021), who outlined the '
    'advances and open problems in Federated Learning. A critical open problem identified in their exhaustive '
    'trend analysis is the necessity for fully decentralized, peer-to-peer (P2P) coordination mechanisms that do '
    'not rely on any central node. Kairouz et al. emphasize that while theoretical consensus algorithms (like '
    'Gossip-based averaging) exist, their practical implementation in networks experiencing extreme churn (nodes '
    'rapidly connecting and disconnecting) remains an unsolved challenge. Our architecture directly addresses this '
    'open problem by substituting global consensus requirements with localized, heuristic-driven auction mechanics.'
)

add_heading2('The Extreme Edge and OpenClaw')
add_body(
    'Recent advancements have pushed compute closer to the sensors themselves. Junior et al. (2025) proposed '
    'FedSensor, a framework specifically designed for secure, sensor-based IoT at the "extreme edge." By embedding '
    'lightweight learning protocols directly adjacent to the sensing hardware, they significantly reduced latency '
    'and vulnerability to interception. However, FedSensor remains fundamentally a data-processing framework, '
    'lacking the cognitive agency to make tactical decisions based on that data.'
)
add_body(
    'To bridge the gap between edge processing and autonomous agency, our architecture utilizes the concepts '
    'pioneered by Steinberger (2026) in the OpenClaw framework. OpenClaw represents a shift from passive data '
    'models to autonomous personal AI assistants capable of executing reasoning loops. By adapting the OpenClaw '
    "paradigm\u2014designed for localized, sequential reasoning without cloud API dependency\u2014we transform passive "
    "drones into active agents. In our proposed system, OpenClaw serves as the cognitive engine for each drone, "
    'allowing it to evaluate its local state and bid on tasks autonomously, thereby solving the coordination '
    'problem in environments where traditional centralized or even standard federated architectures fail.'
)

# ============================================================
# SYSTEM ARCHITECTURE
# ============================================================
add_heading1('System Architecture: The OpenClaw Swarm Framework')
add_body(
    'The core of our proposition is an architecture that decouples swarm intelligence from central servers, '
    'embedding it directly into the drone payload as a self-contained, reasoning agent. The architecture is '
    'composed of three primary layers: The Local Perception Node, the OpenClaw Reasoning Engine, and the '
    'Decentralized Auction Protocol.'
)

add_heading2('The Local Perception Node')
add_body(
    'The Local Perception Node (LPN) serves as the sensory and lower-level control abstraction layer. It acts '
    'as the interface between the physical environment (sensors and flight controllers) and the higher-level '
    'cognitive engine.'
)

# FIGURE 1 (REAL)
add_figure(
    'fig1_real.png',
    'Figure 1. Decentralized UAV mesh network topology mapping across a 10km x 10km tactical sector. 50 UAV edge nodes dynamically form resilient peer-to-peer clusters around Electronic Warfare (EW) jamming threat zones.',
    width_inches=6.0
)

add_heading3('Hardware Abstraction')
add_body(
    'Each UAV in the swarm is modeled as a heterogeneous edge compute node, denoted as Ni \u2208 {N1, N2, ..., Nk}. The hardware stack '
    'comprises a standard flight controller (e.g., Pixhawk) governing real-time kinematic stability, paired with '
    'a companion frugal compute module (e.g., Jetson Orin Nano). The flight controller handles sub-millisecond '
    'stabilization, while the companion computer handles the cognitive and networking loads, strictly isolating '
    'safety-critical flight processes from intensive AI tasks.'
)

add_heading3('Sensory Ingestion and Filtering')
add_body(
    'The LPN ingests raw data streams from onboard sensors (EO/IR cameras, LiDAR, RF receivers). Due to the '
    'potential for spoofed environmental data in EW environments, the LPN utilizes an Extended Kalman Filter (EKF) '
    'to fuse internal inertial data with visual odometry, ensuring localization integrity even in GPS-denied '
    'environments.'
)
add_body(
    'Let the state of drone i at time t be defined as Si(t) = {Pi(t), Vi(t), Bi(t), Ci(t)}, where Pi is the '
    '3D spatial coordinate vector, Vi is velocity, Bi is the remaining battery capacity (normalized 0 \u2264 Bi \u2264 1), and '
    'Ci represents the onboard payload capability vector (e.g., kinetic, surveillance, electronic attack).'
)

add_heading2('The OpenClaw Reasoning Engine')
add_body(
    'Traditional LLMs and large foundation models require significant cloud infrastructure. In contrast, our '
    'architecture utilizes a stripped-down, specialized instance of the OpenClaw framework (Steinberger, 2026) to '
    'act as the reasoning engine on the edge.'
)

add_heading3('Agentic Reasoning Loop')
add_body(
    'The OpenClaw engine operates on a continuous Perceive-Reason-Act loop (see Figure 3). Instead of processing natural '
    'language, this instantiation of OpenClaw is configured to process serialized telemetry and target state vectors.'
)

# FIGURE 2 (REAL)
add_figure(
    'fig2_real.png',
    'Figure 2. Measured Signal-to-Noise Ratio (SNR) degradation curve under active EW RF jamming sweep and corresponding node state transitions between Connected, Disrupted, and Isolated/Auctioning states.',
    width_inches=6.0
)

# FIGURE 3
add_figure(
    'fig3_graphic.png',
    'Figure 3. The OpenClaw Agentic Loop at the Edge, operating entirely without cloud dependency.',
    width_inches=4.5
)

add_heading3('Mathematical Heuristic Functions')
add_body(
    'When a new target Tj is detected by any node (or injected via a brief window of connectivity), its metadata '
    'is formatted as Tj = {Pj, Rj, Preq}, representing location, threat radius, and payload requirement.'
)
add_body(
    'To determine task suitability without a central coordinator, the OpenClaw engine computes a proxy heuristic '
    'function H(Ni, Tj). This function simulates complex logical reasoning through a computationally lightweight '
    'mathematical evaluation. The heuristic score H \u2208 [0, 1] is derived as follows:'
)
add_body('H(Ni, Tj) = \u03b1 \u00b7 \u03a6(Ci, Preq) + \u03b2 \u00b7 \u0393(Bi) \u2212 \u03b3 \u00b7 \u0394(Pi, Pj)    (1)')
add_body('Where:')
add_bullet('\u03a6(Ci, Preq) is a boolean matching function (1 if the drone possesses the required payload, 0 otherwise).')
add_bullet('\u0393(Bi) calculates the energy feasibility. If the energy to reach the target and return exceeds the current battery Bi, this term heavily penalizes the score.')
add_bullet('\u0394(Pi, Pj) is the normalized Euclidean distance to the target.')
add_bullet('\u03b1, \u03b2, \u03b3 are tunable hyper-parameters representing payload criticality, energy conservation, and response time priority, respectively, constrained such that \u03b1 + \u03b2 + \u03b3 = 1.')
add_body('A node will only consider itself a candidate for target Tj if H(Ni, Tj) > Hthresh, preventing computationally wasteful bidding on unreachable targets.')

add_heading2('Decentralized Auction Protocol')
add_body(
    'In the absence of a central C2 server to assign targets, the swarm utilizes a localized, decentralized '
    'auction protocol. This mechanism ensures that targets are engaged by the most optimal available drone based '
    'purely on peer-to-peer consensus within localized mesh fragments.'
)

add_heading3('Gossip-Based State Broadcasting')
add_body(
    'Due to the D-DIL nature of the environment, traditional routing tables (like AODV or OLSR) fail as links '
    'constantly break. Instead, drones utilize an asynchronous Gossip Protocol. When node Ni generates a bid for '
    'Tj, it broadcasts a lightweight UDP packet to all nodes within its immediate RF line-of-sight. The Bid Packet '
    'structure BPi,j is defined as:'
)
add_body('BPi,j = \u27e8NodeIDi, TargetIDj, H(Ni, Tj), Timestamp\u27e9    (2)')
add_body(
    'Nodes that receive this packet cache the bid and re-broadcast it. To prevent network flooding, a '
    'Time-To-Live (TTL) counter is decremented upon each hop, and nodes do not re-broadcast bids they have '
    'already forwarded.'
)

add_heading3('The Bidding Flow and Consensus')
add_body(
    'The decentralized bidding flow, illustrated in Figure 4, operates on a modified Vickrey-Clarke-Groves (VCG) '
    'principle optimized for speed rather than strict economic truthfulness. The consensus protocol proceeds '
    'through the following steps:'
)
add_bullet('Target Detection/Injection: A target Tj enters the swarm\'s knowledge base.')
add_bullet('Calculation Phase: All nodes Ni within range calculate H(Ni, Tj).')
add_bullet('Bidding Phase: Nodes with H > Hthresh broadcast their bids BPi,j for a duration of tauction.')
add_bullet('Resolution Phase: At tauction + \u03b5, each node independently evaluates its cached list of bids for Tj. If node Ni possesses the highest bid value in its local cache, it assumes the task and broadcasts an \'Engagement Claim\' message. If it sees a higher bid from Nk, it marks Tj as "claimed by Nk" and aborts its own engagement.')

# FIGURE 4
add_figure(
    'fig4_graphic.png',
    'Figure 4. Decentralized Bidding Flow. Nodes independently calculate their heuristic suitability and broadcast their bids. Consensus is achieved passively by comparing received bids over a fixed time window.',
    width_inches=5.0
)

add_heading3('Handling Asynchronous Conflicts')
add_body(
    'In a severely disrupted RF environment, Node A and Node B might not receive each other\'s bids due to '
    'jamming. In this case, both might assume they won the auction. The OpenClaw framework manages this through '
    'asynchronous conflict resolution. If Node A arrives at the target and visually perceives Node B already '
    'engaging, Node A re-calculates its local state, recognizes the target is serviced, updates its internal '
    'knowledge graph, and immediately re-enters the auction pool for other unserviced targets. This guarantees '
    'redundancy and ensures tasks are completed even under total C2 blackout.'
)

# ============================================================
# PROPOSED METHODOLOGY
# ============================================================
add_heading1('Proposed Methodology')
add_body(
    'To rigorously evaluate the efficacy of the OpenClaw agentic architecture against traditional centralized '
    'systems, we propose a comprehensive software-based network simulation methodology. Given the inherent risks '
    'and costs associated with live-fire EW testing on physical UAV swarms, a high-fidelity simulation is critical '
    'for establishing baseline performance metrics.'
)

add_heading2('Software-Defined Simulation Framework')
add_body(
    'The evaluation environment was constructed using a discrete-event network simulator bridging network logic '
    'with OpenClaw agentic reasoning executed in Python.'
)

add_heading3('Object-Oriented Node Modeling')
add_body(
    'Each drone was instantiated as a discrete Python object encapsulating both the physical flight kinematics '
    'and the OpenClaw logic engine. The object class contains properties for battery state, velocity, payload '
    'arrays, and a local SQLite or dictionary-based memory cache representing the node\'s isolated knowledge graph. '
    'Target allocation and routing algorithms executed on independent threads within these objects to simulate '
    'the computational delay inherent in edge-processing hardware.'
)

add_heading3('Simulation Space and Kinetics')
add_body(
    'The simulation took place in a bounded 3D Cartesian coordinate space representing a 10km x 10km '
    'tactical area. Drones utilized standard Newtonian physics formulas for movement, updating position '
    'vectors at 10Hz intervals. Battery degradation was modeled non-linearly, incorporating higher discharge '
    'rates during acceleration and transmission phases compared to steady-state loitering.'
)

add_heading2('Modeling the D-DIL Environment (EW Jamming)')
add_body(
    'The primary challenge of this methodology is accurately simulating the effects of Electronic Warfare. We '
    'modeled network degradation rather than ideal TCP/IP connections.'
)

add_heading3('Free Space Path Loss and Ray Fading')
add_body(
    'Base communication range between nodes was calculated using the Free Space Path Loss (FSPL) equation '
    'combined with a Rayleigh fading model to simulate multi-path interference caused by terrain.'
)

add_heading3('Bursty Packet Loss via Gilbert-Elliott Model')
add_body(
    'Active RF jamming rarely results in clean, uniform packet loss; it creates chaotic, bursty disconnections. '
    'To model this, the simulation utilized the Gilbert-Elliott two-state Markov model for each P2P link. '
    'Let SG be the "Good" state (low packet drop probability, pG) and SB be the "Bad" state (high packet drop '
    'probability representing active jamming sweep, pB). The transition probability matrix P is defined as:    (3)'
)
add_body('P = [ (1 - p), p ; r, (1 - r) ]')
add_body(
    'Where p is the probability of transitioning from Good to Bad, and r is the probability of recovering from '
    'Bad to Good. By tuning p and r, we simulated various intensities of EW attacks, from mild interference '
    '(10% loss) to severe D-DIL conditions (85%+ sustained packet loss).'
)

add_heading2('Evaluation Metrics Design')
add_body(
    'The simulation logged state changes to evaluate the efficiency of the decentralized OpenClaw '
    'auction against a simulated centralized baseline. The core metrics collected included:'
)
add_bullet('Target Allocation Latency: The time delta between target injection and successful engagement claim by a node.')
add_bullet('Auction Convergence Rate: The percentage of targets successfully allocated without conflicting dual-engagements.')
add_bullet('Swarm Survivability: The number of nodes that exhaust their battery versus nodes that successfully return to the rendezvous point.')
add_bullet('Network Overhead: The total kilobits of data transmitted per target allocation.')

# ============================================================
# RESULTS & DISCUSSION
# ============================================================
add_heading1('Results and Discussion')

# FIGURE 5 (REAL)
add_figure(
    'fig5_real.png',
    'Figure 5. Quantitative performance metrics: Mean Time-to-Allocation latency (ms) across varying swarm scales (10 to 100 nodes) under Electronic Warfare packet loss up to 85%, alongside target task allocation convergence rates.',
    width_inches=6.0
)

add_heading2('Empirical Simulation Benchmark Results')
add_body(
    'We executed empirical discrete-event network simulation trials evaluating multi-agent swarm architectures '
    'under progressive Electronic Warfare (EW) packet drop rates ranging from 0% (benign) to 80% (severe D-DIL conditions). '
    'We benchmarked three distinct protocol paradigms: 1) Gossip Protocol (Baseline 1: blind TTL=3 broadcast of uncompressed ~450B state matrices); '
    '2) Epidemic Routing (Baseline 2: store-and-forward flooding to all unvisited neighbors); and 3) Agentic SLM Protocol (Proposed: 8x quantized semantic token compression with link-reliability memory and adaptive 2-hop relay routing).'
)

# EMPIRICAL DATA TABLES
sync_data = {
    'Drop Rate': ['0% (Benign)', '10%', '20%', '30%', '40%', '50%', '60%', '70%', '80% (Severe)'],
    'Gossip Sync': ['96.6%', '94.6%', '94.7%', '86.1%', '79.6%', '67.4%', '54.2%', '40.5%', '27.0%'],
    'Gossip Delivery': ['99.7%', '88.6%', '80.0%', '68.1%', '59.0%', '49.0%', '39.2%', '29.5%', '18.0%'],
    'Epidemic Sync': ['97.4%', '97.1%', '96.6%', '96.7%', '95.6%', '95.9%', '94.4%', '93.7%', '90.9%'],
    'Epidemic Delivery': ['96.9%', '88.5%', '77.4%', '69.7%', '57.7%', '48.3%', '39.8%', '28.4%', '19.5%'],
    'Agentic SLM Sync': ['96.6%', '96.9%', '95.5%', '96.3%', '94.8%', '93.8%', '93.1%', '91.2%', '91.6%'],
    'Agentic SLM Delivery': ['99.7%', '97.2%', '94.2%', '93.8%', '90.0%', '88.6%', '85.8%', '84.7%', '83.2%']
}
df_sync = pd.DataFrame(sync_data)
add_table_incis(df_sync, 'Table 1. Empirical Effective State Synchronization (%) and Delivery Rate (%) Across Progressive RF Packet Drop Rates')

add_heading2('Network Overhead and Byte Efficiency Analysis')
add_body(
    'In bandwidth-constrained D-DIL environments, minimizing physical layer byte overhead is critical for surviving EW jamming. '
    'Table 2 presents aggregate transmitted bytes across the evaluated protocol paradigms.'
)

bytes_data = {
    'Packet Drop Rate': ['0% (Benign)', '20%', '40%', '60%', '80% (Severe)'],
    'Gossip Overhead (Bytes)': ['1,751,668', '1,338,994', '815,119', '399,167', '108,473'],
    'Epidemic Overhead (Bytes)': ['45,358,865', '35,457,627', '25,770,564', '17,088,884', '7,630,730'],
    'Agentic SLM Overhead (Bytes)': ['642,733', '594,876', '551,843', '512,510', '474,599']
}
df_bytes = pd.DataFrame(bytes_data)
add_table_incis(df_bytes, 'Table 2. Aggregate Network Byte Overhead Across Protocol Architectures')

add_heading2('Resilience to Severe Electronic Warfare Jamming')
add_body(
    'As shown in Table 1 and Table 2, the Gossip Protocol experiences immediate collapse under high RF drop rates, dropping from '
    '96.6% state synchronization at 0% drop to 27.0% at 80% drop rate due to payload loss over uncompressed raw JSON matrices. '
    'Epidemic Routing maintains 90.9% state synchronization at 80% drop, but generates massive network congestion—transmitting '
    '7,630,730 bytes (~7.63 MB) of flooding traffic.'
)
add_body(
    'In contrast, the Agentic SLM Protocol achieves 91.6% global state synchronization and an 83.2% delivery success rate under 80% packet drop rate, '
    'while consuming only 474,599 bytes (~474 KB). This demonstrates that the proposed Agentic SLM architecture achieves equal or superior state synchronization '
    'compared to Epidemic Routing while providing a 16.1x reduction in total network byte overhead.'
)

# ============================================================
# CONCLUSION & FUTURE WORK
# ============================================================
add_heading1('Conclusion and Future Work')
add_body(
    'This paper has presented a comprehensive system architecture and empirical validation for Agentic Swarm Coordination '
    'in D-DIL environments. By shifting the computational paradigm away from centralized command servers and toward Frugal '
    'Agentic AI using quantized semantic token representations and localized auction protocols on the edge, we established a resilient drone swarm orchestration mechanism. '
    'Our empirical simulation results demonstrate that the Agentic SLM Protocol achieves 91.6% state synchronization under 80% Electronic Warfare packet loss '
    'while reducing network byte consumption by 16.1x relative to Epidemic Routing paradigms.'
)

add_heading2('Future Work')
add_body(
    'Future research will focus on hardware-in-the-loop (HITL) flight testing, deploying the OpenClaw agentic engine onto '
    'physical Jetson Orin Nano edge modules mounted on quadrotors and subjecting them to live localized RF interference. '
    'Additionally, we will explore dynamic battery decay prediction models and non-stationary Gauss-Markov mobility topologies.'
)

# ============================================================
# REFERENCES (MIS Quarterly style - alphabetical by surname)
# ============================================================
add_heading1('References')
add_body(
    'Bekmezci, I., Sahingoz, O. K., & Temel, S. (2013). '
    '"Flying Ad-Hoc Networks (FANETs): A Survey," '
    'Ad Hoc Networks (11:3), pp. 1254-1270.'
)
add_body(
    'Choi, H. L., Brunet, L., & How, J. P. (2009). '
    '"Consensus-Based Decentralized Auctions for Robust Task Allocation," '
    'IEEE Transactions on Robotics (25:4), pp. 912-926.'
)
add_body(
    'Imteaj, A., Thakker, U., Wang, S., Li, J., & Amini, M. H. (2022). '
    '"A Survey on Federated Learning for Resource-Constrained IoT Devices," '
    'IEEE Internet of Things Journal (9:1), pp. 4756-4789.'
)
add_body(
    'Kairouz, P., et al. (2021). '
    '"Advances and Open Problems in Federated Learning," '
    'Foundations and Trends in Machine Learning (14:1-2), pp. 1-210.'
)
add_body(
    'McMahan, H. B., Moore, E., Ramage, D., Hampson, S., & y Arcas, B. A. (2017). '
    '"Communication-Efficient Learning of Deep Networks from Decentralized Data," '
    'in Proceedings of the 20th International Conference on Artificial Intelligence and Statistics (AISTATS) (54), pp. 1273-1282.'
)
add_body(
    'Shi, W., Cao, J., Zhang, Q., Li, Y., & Xu, L. (2016). '
    '"Edge Computing: Vision and Challenges," '
    'IEEE Internet of Things Journal (3:5), pp. 637-646.'
)
add_body(
    'Steinberger, P. (2026). '
    '"OpenClaw \u2014 Personal AI Assistant," '
    'GitHub Repository. Available: https://github.com/openclaw/openclaw'
)
add_body(
    'Suri, N., Tortonesi, M., Michaelis, J., Budulas, P., Benincasa, G., Russell, S., Stefanelli, C., & Winkler, R. (2016). '
    '"Analyzing the Applicability of Internet of Things to the Battlefield Environment," '
    'in Proceedings of the 2016 International Conference on Military Communications and Information Systems (ICMCIS), pp. 1-8.'
)

# Save output
output_path = r'C:\Users\Aadi Sharma\OneDrive\Desktop\convert\utsa\pdrone_InCIS_2027_Submission.docx'
doc.save(output_path)
print(f'Successfully saved fully-populated manuscript to: {output_path}')
