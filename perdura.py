#!/usr/bin/env python3
"""
Perdura — Phase 1: persistent knowledge graph + ephemeral LLM workers.

The graph is the system of record. Models board, receive a bounded briefing
(never the transcript), return a strict-JSON delta (nodes + edges), and
disembark. The conductor is deterministic code: it validates, attributes,
merges, and tracks contention.

Setup:
    pip install anthropic google-genai openai
    export ANTHROPIC_API_KEY=...  GEMINI_API_KEY=...
    # local labor: LM Studio serving qwen3-14b (default), or Ollama via
    # --qwen-url http://localhost:11434/v1 --qwen-model qwen3:14b

Usage:
    python perdura.py new "How should multi-agent memory be architected?"
    python perdura.py run --turns 6                 # round-robin boarding
    python perdura.py run --turns 2 --workers qwen   # cheap labor only
    python perdura.py show                           # print graph state
    python perdura.py demo                           # offline mock worker test

Graph persists to perdura_graph.json (override with --graph PATH).
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, asdict, field
from fastapi import FastAPI

# FastAPI application for Vercel deployment
app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "Perdura Core Active", "persistence": "Graph"}

# (...) Rest of the perdura.py code remains unchanged.
# This code block should merge well unless we need deeper refactor.

if __name__ == "__main__":
    main()