#!/usr/bin/env python3
"""
Multi-model debate / consensus loop (Du et al. "society of minds" pattern).

Agents: Claude (Anthropic API) + Gemini (Google API) + Qwen (local via Ollama/LM Studio).

Round 0: each agent answers the question independently.
Rounds 1..N: each agent sees the other agents' latest answers and critiques/revises.
Final: a synthesizer (Claude by default) produces the consensus answer and notes
       remaining disagreements.

Setup:
    pip install anthropic google-genai openai
    export ANTHROPIC_API_KEY=...
    export GEMINI_API_KEY=...
    ollama pull qwen3:14b   (or any local model; LM Studio works too)

Usage:
    python debate.py "Your question here"
    python debate.py "Your question" --rounds 3 --qwen-model qwen3:8b
    python debate.py "Your question" --qwen-url http://localhost:1234/v1   # LM Studio
"""

import argparse
import os
import sys
import textwrap
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Agent abstractions — each just needs .name and .generate(prompt) -> str
# ---------------------------------------------------------------------------


@dataclass
class Agent:
    name: str
    history: list = field(default_factory=list)  # list of this agent's answers per round

    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class ClaudeAgent(Agent):
    def __init__(self, name="Claude", model="claude-sonnet-4-5"):
        super().__init__(name=name)
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def generate(self, prompt: str) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()


class GeminiAgent(Agent):
    def __init__(self, name="Gemini", model="gemini-2.5-flash"):
        super().__init__(name=name)
        from google import genai
        self.client = genai.Client()  # reads GEMINI_API_KEY
        self.model = model

    def generate(self, prompt: str) -> str:
        resp = self.client.models.generate_content(model=self.model, contents=prompt)
        return resp.text.strip()


class QwenAgent(Agent):
    """Local model via any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM)."""

    def __init__(self, name="Qwen", model="qwen3:14b",
                 base_url="http://localhost:11434/v1"):
        super().__init__(name=name)
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url, api_key="local")
        self.model = model

    def generate(self, prompt: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Debate prompts
# ---------------------------------------------------------------------------

INITIAL_PROMPT = """\
You are one of several AI agents independently answering a question. \
Give your best answer with brief reasoning. Be concrete and commit to a position. \
End with a line: FINAL ANSWER: <your answer in one sentence>.

Question: {question}
"""

DEBATE_PROMPT = """\
You are {name}, one of several AI agents debating a question. \
Below is your previous answer and the other agents' latest answers.

Critically examine the other answers: where are they right, where are they wrong, \
and what did they catch that you missed? Then give your UPDATED answer. \
You may keep your position if you still believe it is correct, but you must \
address the strongest opposing point. \
End with a line: FINAL ANSWER: <your answer in one sentence>.

Question: {question}

Your previous answer:
{own_answer}

Other agents' answers:
{others}
"""

SYNTHESIS_PROMPT = """\
You are the synthesizer for a multi-agent debate. Below are the final positions \
of each agent after {rounds} debate round(s).

1. State the consensus answer (or majority position if no full consensus).
2. Note any remaining disagreement and which position you find most defensible, and why.
3. Rate consensus strength: STRONG / PARTIAL / NONE.

Question: {question}

Final positions:
{positions}
"""


# ---------------------------------------------------------------------------
# Debate loop
# ---------------------------------------------------------------------------

def hr(title=""):
    line = "=" * 70
    print(f"\n{line}\n{title}\n{line}" if title else line)


def wrap(text, width=70):
    return "\n".join(
        textwrap.fill(line, width) if line.strip() else line
        for line in text.splitlines()
    )


def run_debate(agents, question, rounds):
    # Round 0 — independent answers
    hr("ROUND 0 — independent answers")
    for agent in agents:
        answer = agent.generate(INITIAL_PROMPT.format(question=question))
        agent.history.append(answer)
        print(f"\n--- {agent.name} ---\n{wrap(answer)}")

    # Debate rounds — each agent sees the others' latest answers
    for r in range(1, rounds + 1):
        hr(f"ROUND {r} — critique and revise")
        # Snapshot latest answers so all agents respond to the same state
        latest = {a.name: a.history[-1] for a in agents}
        for agent in agents:
            others = "\n\n".join(
                f"[{name}]:\n{ans}" for name, ans in latest.items()
                if name != agent.name
            )
            prompt = DEBATE_PROMPT.format(
                name=agent.name,
                question=question,
                own_answer=latest[agent.name],
                others=others,
            )
            answer = agent.generate(prompt)
            agent.history.append(answer)
            print(f"\n--- {agent.name} ---\n{wrap(answer)}")

    # Synthesis — first agent (Claude) acts as chairman
    hr("SYNTHESIS")
    positions = "\n\n".join(f"[{a.name}]:\n{a.history[-1]}" for a in agents)
    synthesis = agents[0].generate(
        SYNTHESIS_PROMPT.format(question=question, rounds=rounds, positions=positions)
    )
    print(f"\n{wrap(synthesis)}\n")


def main():
    p = argparse.ArgumentParser(description="Multi-model debate loop")
    p.add_argument("question", help="The question to debate")
    p.add_argument("--rounds", type=int, default=2,
                   help="Number of critique/revise rounds after round 0 (default 2)")
    p.add_argument("--claude-model", default="claude-sonnet-4-5")
    p.add_argument("--gemini-model", default="gemini-2.5-flash")
    p.add_argument("--qwen-model", default="qwen3:14b")
    p.add_argument("--qwen-url", default="http://localhost:11434/v1",
                   help="OpenAI-compatible base URL (Ollama default; LM Studio: http://localhost:1234/v1)")
    args = p.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY")
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("Set GEMINI_API_KEY")

    agents = [
        ClaudeAgent(model=args.claude_model),
        GeminiAgent(model=args.gemini_model),
        QwenAgent(model=args.qwen_model, base_url=args.qwen_url),
    ]

    run_debate(agents, args.question, args.rounds)


if __name__ == "__main__":
    main()
