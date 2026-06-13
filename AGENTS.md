# agent-seed

> Build the strongest model-agnostic agent harness through self-improvement.

## Runtime

You have access to:

- An LLM API (configured via `.model-config.json` — supports DeepSeek, Ollama, OpenRouter)
- Git
- This filesystem
- Shell commands

## Architecture

The daemon (daemon.py) runs a while-true loop:

1. Read state (goal, git status, eval score)
2. Route to model (CREATE/EXPLORE cycle)
3. Call model → extract Action:
4. Safety check → execute
5. Validate JSON changes
6. Commit + push
7. Health check + rollback on failure
8. Adaptive sleep

Safety layers are in safety.py (not the prompt — never trust the model).
