# 🌐 VOX — Secure Agentic Orchestration Framework

VOX is a declarative framework for building **secure, event-driven, multi-agent systems** with controlled execution and modular extensibility.

It is designed for real-world automation where AI assists in decision-making while maintaining strict human oversight, predictable behavior, and data sovereignty.

---

## 🧠 Core Concept

VOX enables agents to be defined declaratively using `agent.yml`, while execution logic is implemented through dynamically loaded roles.

At runtime, VOX:

* Discovers agents automatically from the filesystem
* Validates configurations and performs health checks
* Dynamically loads roles and capabilities
* Boots all agents and starts their communication interfaces

This allows a **plug-and-play architecture**, where agents can be added without modifying the core system.

---

## ⚙️ Architecture Overview

### 🧩 Declarative Agents

Agents are defined via YAML:

* Identity (`name`, `id`)
* Capabilities (AI, messaging, networking)
* Communication interfaces
* Behavioral constraints (personality, rules)

---

### 🔌 Capability System

All external integrations are abstracted as capabilities:

```
core/capabilities/
  ai/        # LLM integrations (Ollama)
  comm/      # Communication (Telegram, Voice)
  net/       # Email, web interfaces
```

Capabilities are:

* Dynamically loaded
* Explicitly declared by each agent
* Fully controlled by the core system

---

### 🧠 Role-Based Execution

Behavior is implemented via modular roles:

```python
@self.on("inbound_message")
async def handle_message(...):
```

* Roles subscribe to events
* Roles are dynamically discovered at runtime
* Agents are unaware of their roles at definition time
* Execution logic is fully decoupled

---

### 🔄 Event-Driven Model

VOX operates as an event-driven system:

* Events are emitted (`on_boot`, `inbound_message`, etc.)
* Agents broadcast events to their roles
* Roles react independently

This enables:

* Reactive workflows
* Loose coupling
* Composable behavior

---

## 🔐 Control & Security Model

VOX is designed for **controlled execution in real environments**.

### 🛡️ Human-in-the-Loop

* Agents propose actions
* Execution requires explicit confirmation
* No irreversible operations are performed autonomously

---

### 🔒 Built-in Safeguards

* Input sanitization (`input_sanitizer.py`)
* Rate limiting (`rate_limiter.py`)
* Capability-level access control

---

### 📊 Observability

VOX logs:

* Commands
* Decisions
* Outputs
* Inter-agent communication

Providing full traceability and auditability.

---

## 🧠 AI Integration Philosophy

VOX uses LLMs for:

* Parsing user input into structured commands
* Intent classification within constrained action sets
* Assisted content generation

LLMs are **not responsible for execution logic**.

Instead:

* Commands are predefined and structured
* Execution is deterministic
* AI is constrained, not trusted blindly

---

## 🔄 Multi-Agent Orchestration

VOX supports hierarchical agent systems.

Example:

* Interface Agent → user interaction
* Coordinator Agent → task routing
* Domain Agents → specialized execution

Agents can:

* Communicate via events
* Escalate tasks (`report_to_master`)
* Operate independently or hierarchically

---

## 🚀 Quickstart

### 1. Clone the repository

```bash
git clone https://code.datorum.net/vox.git
cd vox
```

---

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

---

### 3. (Optional) Run a local LLM

VOX supports local models via Ollama:

```bash
ollama run llama3
```

---

### 4. Configure an agent

Edit an `agent.yml` inside `/agents`:

```yaml
name: "AgentName"
id: "your-id"
capabilities:
  ai.ollama:
    base_url: "http://localhost:11434"
    model: "llama3"
  comm.telegram:
    user_id: "..."
```

---

### 5. Run VOX

```bash
python vox.py
```

---

### 6. Observe the system boot

VOX will:

* Discover agents
* Load roles dynamically
* Initialize capabilities
* Start communication channels
* Emit startup events

---

## 📁 Project Structure

```
core/
  agent.py        # Agent lifecycle & orchestration
  role.py         # Role abstraction & event system
  capabilities/   # Pluggable integrations
  security/       # Input sanitization & rate limiting

agents/
  <agent_name>/
    agent.yml
    roles/

docs/
  creating_an_agent.md
  messaging_contract.md
```

---

## 🧬 Design Goals

* **Security-first execution**
* **Data sovereignty (local-first AI)**
* **Cost efficiency (no API dependency)**
* **Modular extensibility**
* **Deterministic control over AI behavior**

---

## 🚧 Status

VOX is under active development and already supports real-world workflows including:

* Messaging-based interaction
* Email processing
* Multi-agent coordination
* Task planning and automation
