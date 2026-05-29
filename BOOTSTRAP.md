# agent-seed bootstrap

Human reference for the complete setup. The AI does not read this file.

## Hardware

| Component | Spec                                    |
| --------- | --------------------------------------- |
| Machine   | Minisforum AI Pro-370                   |
| CPU       | Ryzen AI 9 HX 370 (12C/24T, Zen 5)      |
| RAM       | 32GB DDR5-5600 (upgradeable to 128GB)   |
| iGPU      | Radeon 890M (RDNA 3.5, 16 CUs, gfx1151) |
| Storage   | 1TB NVMe SSD                            |
| Network   | Gigabit Ethernet, WiFi 6E               |

## OS

**Ubuntu 24.04 LTS** (headless — no desktop environment).
SSH access only.

### Initial setup commands (run once)

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install SSH server
sudo apt install openssh-server -y
sudo systemctl enable ssh --now

# Install Tailscale (remote access from anywhere)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up  # authenticate via browser URL

# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull the model
ollama pull qwen3.6:27b-instruct-q4_K_M

# Verify Ollama API works
curl http://localhost:11434/v1/chat/completions \
  -d '{"model":"qwen3.6:27b","messages":[{"role":"user","content":"say hello"}]}'
```

### Connecting from laptop

```bash
# After Tailscale is set up on both machines:
ssh username@minipc-tailscale-ip
cd ~/projects/dev/agent-seed
git log --oneline -10
cat CHANGELOG.md
```

## Model

| Current                                | Future                                                     |
| -------------------------------------- | ---------------------------------------------------------- |
| Qwen 3.6-27B Q4_K_M (~16.8GB, 3-6 t/s) | Qwen 3.7 when open weights drop (estimated June-July 2026) |

To swap models, update `.model-config.json` and pull the new model:

```bash
ollama pull qwen3.7:27b
# edit .model-config.json → "model": "qwen3.7:27b"
```

Model-agnostic — the agent calls `localhost:11434/v1` and doesn't care which model is behind it.

## Sub-agents

Sub-agents run **sequentially** on the same Ollama instance (one model, queued requests). They don't need parallel model instances — context isolation is the value, not parallelism.

For truly parallel sub-agents, upgrade RAM to 64GB (~$80-100).

## Sandboxing

The agent should not be able to destroy the host OS. Sandboxing is given as substrate, not discovered (discovering it costs a real machine).

## Observation

You don't interact. You observe:

- `git log` — every change
- `CHANGELOG.md` — narrative of what was done and why
- `scripts/improve` — current state summary

## Architecture Diagram

```
Your laptop                    MiniPC (24/7 at home)
──────────                    ──────────────────────
SSH via Tailscale ──►         Ubuntu 24.04 LTS
                                ├── Tailscale ←── VPN tunnel
                                ├── Ollama ←─── localhost:11434
                                │   └── Qwen 3.6-27B
                                └── agent-seed/
                                    ├── AGENTS.md
                                    ├── GOAL.md
                                    ├── .model-config.json
                                    ├── scripts/go
                                    ├── scripts/commit
                                    └── CHANGELOG.md

You observe:                    Daemon loop:
git log                         survey → call Ollama → do → commit → sleep
CHANGELOG.md
```
