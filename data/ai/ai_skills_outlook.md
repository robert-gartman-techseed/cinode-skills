# AI skills outlook: what the 31 hot topics miss (12-month view)

Written 2026-11-08. Inputs: techseed.se (home page, 22 cases, 6 stack areas,
4 cross-enablers, Siemens + Inductive Automation partnerships), the keyword
catalog (`keywords.csv`, TechSeed user counts), and public trend sources
(Gartner 2026 hype cycles for agentic AI / secure AI / AI governance, NVIDIA
GTC and Hannover Messe 2026, Siemens Industrial AI Suite GA, Stanford job-ad
data via Forbes, TechSverige / SCB for Sweden).

## 1. The structural gap

The 31 topics (`hot_topics.csv`) describe a generic enterprise-GenAI practice:
Copilot, Azure OpenAI, RAG, agents, AI Act, AI strategy. That is what every
Swedish IT consultancy sells. TechSeed's own positioning is the opposite:
"we make the physical world a little smarter", six stack layers from
mechatronics to XR, and 20 of 22 published cases are industrial (MES, IT/OT,
smart grid, pulp & paper, beverage, maritime, edge AI in smart products, DPP).

Not one of the 31 topics names computer vision, edge/on-device AI, predictive
maintenance, digital twins, robotics or AI on OT data, although the web site
lists "Computer Vision", "Prediction", "Edge computing" and "Digital Twins" as
expertise and the catalog shows people already have these skills. The hot
list measures the AI skills we share with everyone and ignores the ones that
differentiate us.

## 2. Proposed new topics

Users = TechSeed employees with a matching keyword on their profile today
(aggregate counts from `keywords.csv`; V = Cinode-verified keyword).

### A. Physical / industrial AI (the differentiator; nothing today)

| # | Topic | Evidence on profiles | Why in the next 12 months |
|---|---|---|---|
| T32 | **Computer vision / visual inspection / VLMs** | Computer Vision 10 V (+2 custom), OpenCV 5 V, Vision Language Models 1 (+2), GPT-4o Vision 1 | Site lists Computer Vision; Siemens Industrial AI Suite went GA at Hannover Messe 2026 with visual inspection as a headline use case; VLMs make inspection buildable without labelled datasets |
| T33 | **Edge AI / on-device inference** | Edge AI 2, Edge computing 4, Nvidia Jetson 2 (+3 variants, 1 V), TinyML 1, Model Quantization 2, Real-time Inference 1, LLM Inference Optimization 1, CUDA 2 V, Siemens Industrial Edge 1 | Case "Power-Efficient Edge AI for Smart Products"; analyst note of a shift to on-prem AI close to the data; Siemens Edge Management 2.0 with IEC 62443-4-2 and air-gapped mode; small open-weight models make it practical |
| T34 | **Predictive maintenance / anomaly detection / time-series AI** | Predictive maintenance 3, Condition Monitoring 1, Time Series Analysis 6 V, Forecasting 5 V, Predictive modeling 3 (+2 V) | Site copy: "improve forecasting, automation, and operational performance"; Senseye + Industrial Copilot for maintenance; our pulp & paper, beverage, energy cases are exactly this |
| T35 | **Digital twins, simulation & synthetic data** | Digital Twin 2, Simulation 21 V, System Simulation 2, Unity 1 V, Unreal Engine 2 V, Omniverse 0 | Site lists Digital Twins under XR; NVIDIA Omniverse/Isaac, Siemens Digital Twin Composer (CES 2026), virtual commissioning adopted by ABB, KUKA, FANUC, Yaskawa; synthetic data for training vision models |
| T36 | **Robotics & physical AI / autonomy** | Robotics 15 V, ROS 3 V, Robot Programming 2, ABB RobotStudio 1, Autonomous systems/navigation/vehicles/flight 1 each, Swarm Robotics 1, Drone 4 V, Sensor fusion 1 V, LIDAR 1 | "Physical AI" was the theme of GTC and Hannover Messe 2026; our "Industrial Automation & Robotics" case; Siemens names agentic and physical AI as its roadmap |
| T37 | **AI on OT data (MES/SCADA/UNS, industrial copilots)** | SCADA 12 V, MES 9 (+4), Ignition 4 (+3 V), OPC UA 5 V, Modbus 11 V, PLC 3 V, Siemens Insights Hub 5 (+3), Industrial Copilot 0, Unified Namespace 0 | We are Siemens and Inductive Automation partners; Siemens Industrial Copilot runs on IPC/Industrial Edge and is moving to agents; the IT/OT and MES cases are our most visible work. This is where "agentic AI" meets TechSeed's customers |

### B. Where the agentic stack is heading (broaden existing topics)

| # | Topic | Evidence | Why |
|---|---|---|---|
| T16 → | **Evaluation, guardrails & agent observability** (rename, widen) | LLM Evaluation 1, Model Evaluation 3, Evaluation 5 V, Guardrails 0, Langfuse/LangSmith 0 | Gartner's 2026 agentic hype cycle adds governance, security and FinOps profiles next to the core tech; "agent evals" is the skill every agent role now lists |
| T27 → | **AI & agent security** (widen) | Red teaming 1, OWASP 3 V, Zero-Trust 1, Prompt injection 0, ISO/IEC 42001 0 | Gartner Hype Cycle for Secure AI (Oct 2026), Application Security 2026 on securing the agentic lifecycle, "Guardian Agents" emerging; OWASP Top 10 for LLM/agentic apps; buyers ask for this before signing |
| T09 → | **Agent platforms & protocols (MCP, A2A, agent SDKs)** (widen) | MCP (1 clean), OpenAI Agents 1, Azure AI Foundry Agents 1, n8n 2, Agent Orchestration 1 | MCP alone is a protocol; the marketable skill is building on the agent platform layer (MCP + A2A + vendor SDKs) |
| T13 → | **MLOps / LLMOps / AI FinOps** (add cost) | MLflow 1, Cost Optimization 3 V | Gartner: FinOps now a profile in the agentic hype cycle; inference cost is the 2027 budget line |
| T11 → | add **voice agents** as narrower | OpenAI Whisper 1, Speech Recognition 1 V, Speech-to-text 1 V, Text to speech 1 | Real-time voice is the next conversational-AI wave; fits HMI / operational interfaces |
| T23 → | add **spec-driven / agentic coding** as synonym | Claude Code 5, Codex 1 (+2), Cursor 1 | Already in definition; just add the newer keywords as aliases |
| new T38 | **Small / open-weight models & sovereign AI (self-hosting)** | Ollama V (product), SLM 0, Fine-tuning T08 0 | Air-gapped OT, Nordic public sector and defence want on-prem models; ties T08 (fine-tuning) to a reason to exist |

### C. Regulation that reaches our customers through AI

| # | Topic | Evidence | Why |
|---|---|---|---|
| T39 | **Product compliance for connected & AI products: CRA, NIS2, Machinery Regulation, DPP** | NIS2 5 (+5), Cyber Resilience Act 2 (+2), IEC 62443 1, Digitala Produktpass (DPP) 3 (+1) | Our own case "Digital Product Passport Readiness"; CRA and NIS2 are what industrial buyers actually ask about; the AI Act's Annex I (embedded high-risk) timing is in flux after the Digital Omnibus, so bundle it with the rest of product compliance rather than selling AI Act alone |

## 3. Hot elsewhere, but low fit for TechSeed

Humanoid robots, world models as a stand-alone skill, consumer AI products,
M365 Copilot licensing/adoption (T17 stays because sales asked for it, but it
does not match anything on the site). Prompt engineering (T06) is losing
ground to context engineering and agent design in job ads; keep it as the
umbrella but expect the keyword to age.

## 4. Market signals (Sweden)

Job-ad demand for AI competence up 328 % since 2016 (TechSverige, Jan 2026);
35 % of Swedish enterprises used AI in 2025 vs 25 % in 2024 (SCB via Alice
Labs); ~190 AI consultancies in Sweden, most under 10 people, so the generic
GenAI market is crowded; eWork Q1 2026 reports client demand shifting toward
engineering. Agentic AI mentions in job ads went from 151 (2024) to 16,541
(2025) in Stanford's data. All of this says: compete on industrial + AI, not
on GenAI alone.

## 5. Next steps

1. Done 2026-11-08: T32-T39 added to `hot_topics.csv`, `canonical_skills.csv`,
   `ai_skillset_proposal.csv`, `skill_relations.json`; 101 keyword mappings in
   `topic_mapping.csv`; T09, T11, T13, T16, T23, T27 widened. T37 mappings are
   OT platform skills (SCADA, MES, Ignition, OPC UA) marked `related`, so its
   coverage number shows the platform base, not AI on it.
2. Done: see 1.
3. Ask sales to tag the 20 industrial cases with the new topics so the AI
   Skillset mirrors what the web site sells.
4. Create the missing anchor keywords in Cinode where no one has them yet:
   Unified Namespace, Siemens Industrial Copilot, AI Guardrails, Prompt
   Injection, ISO/IEC 42001, Small Language Models, NVIDIA Omniverse / Isaac
   Sim, Synthetic Data, A2A.

## Sources

- techseed.se home, /cases (fetched 2026-11-08); `data/external/techseed_web/what_we_build.md`
- Gartner: [Hype Cycle for Agentic AI 2026](https://www.gartner.com/en/articles/hype-cycle-for-agentic-ai.html); [Secure AI 2026 abstract](https://www.gartner.com/en/documents/8487445); [AI Governance 2026 abstract](https://www.gartner.com/en/documents/8487545); [AppSec 2026 via NeuralTrust](https://neuraltrust.ai/news/gartner-hype-cycle-for-application-security-2026)
- NVIDIA: [Hannover Messe 2026](https://blogs.nvidia.com/blog/ai-manufacturing-hannover-messe/); [GTC 2026 physical AI](https://blogs.nvidia.com/blog/gtc-2026-virtual-worlds-physical-ai/); [Robotics 24/7](https://www.robotics247.com/article/nvidia-gtc-2026-nvidia-global-robotics-leaders-look-to-take-physical-ai-to-the-real-world)
- Siemens: [Industrial AI at SPS](https://www.computer-automation.de/engineering-en/siemens--generative-ai-is-changing-manufacturing-and-engineering.htm); [Industrial Copilot maintenance](https://www.eejournal.com/industry_news/siemens-expands-industrial-copilot-with-new-generative-ai-powered-maintenance-offering); [Industrial AI Suite / Edge Management 2.0](https://www.pulse.bot/ai/news/siemens-expands-industrial-automation-datacenter-with-edge-ai-and-cybersecurity-e4d504e1-5ef6-40df-a7c5-39abf246596a/); [Everest Group review](https://www.everestgrp.com/report/egr-2026-75-r-8159/)
- Skills demand: [Forbes / Stanford job-ad data](https://www.forbes.com/sites/bernardmarr/2026/08/31/the-ai-skills-employers-actually-want-in-2026/); [Analytics Insight 2027](https://www.analyticsinsight.net/artificial-intelligence/7-ai-skills-that-will-matter-more-than-prompt-engineering-in-2027); [ITPro / Gartner upskilling](https://www.itpro.com/software/software-engineers-are-in-for-a-rough-ride-as-ai-adoption-ramps-up-80-percent-will-be-forced-to-upskill-by-2027-as-the-profession-is-transformed)
- Sweden: [TechSverige](https://techsverige.se/en/2026/01/efterfragan-pa-ai-kompetens-okar-kraftigt-pa-arbetsmarknaden/); [Realtid / PwC](https://www.realtid.se/it-tech/markant-okning-av-ai-jobb-i-sverige/); [eWork Q1 2026](https://www.eworkgroup.com/hubfs/Consultancy%20Market%20Trends%20%26%20Insights%20Sweden%20Q1%202026.pdf); [consultant.dev Q1 2026](https://consultant.dev/blog/q1-2026-market-report); [Alice Labs](https://alicelabs.ai/en/insights/ai-consulting-stockholm)
- AI Act timing (conflicting, check the Commission): [PEX Network](https://www.processexcellencenetwork.com/ai/articles/eu-ai-act-2026-what-actually-changed-and-what-deployers-still-must-do); [Studio Meyer on the Omnibus](https://studiomeyer.io/en/blog/eu-ai-act-2026-after-the-omnibus)
