"""Claude, streaming, with tool use.

Two things matter here and nothing else does:

1. Text is flushed to TTS at sentence boundaries as tokens arrive. Waiting for
   the full response costs a second and a half and breaks the illusion.
2. Slow tool calls emit a verbal filler so there is never dead air.
"""
import os
import re
import asyncio
import logging
from pathlib import Path
from typing import Callable, Awaitable

from anthropic import AsyncAnthropic

from .tools import TOOL_SCHEMAS, ToolRunner, FAST_TOOLS

log = logging.getLogger("brain")

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 700          # she speaks in short turns; a long cap invites monologue
MAX_TURNS = 40            # rolling window
FILLER_DELAY = 0.5        # seconds before a filler line fires

SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])$")

FILLERS = [
    "Let me see what we have.",
    "One moment.",
    "Let me check that for you.",
    "Give me a second here.",
]

# Belt-and-braces on top of the persona prompt. Tobacco compliance is not a
# thing to leave to a system prompt alone.
_BANNED_BASE = (
    r"health benefit|good for (your|you)|safer than|harmless|"
    r"cures?|medicinal|healthy choice|no risk|risk[- ]free"
)

# The whole business is defined by files, so the engine is reusable:
#   agent/engine.md       generic behavior, code-owned, same for every business
#   data/persona.md       who she is - name, business, character  (operator edits)
#   data/store_facts.md   true facts she may state                (operator edits)
#   data/house_rules.md   operator rules; override everything     (operator edits)
#   data/products.xlsx    the catalog, via scripts/build_catalog.py
ENGINE = Path(__file__).parent / "engine.md"
PERSONA = Path("data/persona.md")
STORE_FACTS = Path("data/store_facts.md")
HOUSE_RULES = Path("data/house_rules.md")


def load_system_text() -> str:
    """Engine + persona + facts + rules, in escalating order of authority."""
    if not PERSONA.exists():
        raise SystemExit(f"Missing {PERSONA} - the persona file defines who the avatar is")
    parts = [ENGINE.read_text(encoding="utf-8"), PERSONA.read_text(encoding="utf-8")]
    if STORE_FACTS.exists():
        parts.append(STORE_FACTS.read_text(encoding="utf-8"))
    rules = load_house_rules()
    if rules:
        parts.append(
            "# House rules from the operator\n"
            "These override everything above when they conflict.\n\n" + rules
        )
    return "\n\n---\n\n".join(parts)


def load_house_rules() -> str:
    """Operator-written rules, appended to the persona at startup."""
    if HOUSE_RULES.exists():
        return HOUSE_RULES.read_text(encoding="utf-8")
    return ""


def parse_signup_url(rules_text: str) -> str | None:
    """First URL inside the 'The subscriber offer' section, or None.
    No URL -> open_signup refuses and she never offers the list."""
    in_section = False
    for line in rules_text.splitlines():
        if line.strip().lower().startswith("## the subscriber offer"):
            in_section = True
            continue
        if line.startswith("##"):
            in_section = False
        if in_section and (m := re.search(r"https?://\S+", line)):
            return m.group(0).rstrip(").,")
    return None


def banned_pattern(rules_text: str) -> re.Pattern:
    """Tobacco-compliance bans plus every phrase the operator listed under
    'Never say these words'. Enforced in code, not just in the prompt."""
    extra, in_section = [], False
    for line in rules_text.splitlines():
        if line.strip().lower().startswith("## never say"):
            in_section = True
            continue
        if line.startswith("##"):
            in_section = False
        if in_section and line.strip().startswith("- "):
            phrase = line.strip()[2:].strip()
            if phrase:
                extra.append(re.escape(phrase))
    pattern = _BANNED_BASE + ("|" + "|".join(extra) if extra else "")
    return re.compile(r"\b(" + pattern + r")\b", re.I)


class Brain:
    def __init__(
        self,
        emit_ui: Callable[[str, dict], Awaitable[None]],
        speak: Callable[[str], Awaitable[None]],
        set_state: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        rules = load_house_rules()
        self.tools = ToolRunner(
            emit_ui,
            signup_url=parse_signup_url(rules),
            # Hosted separately: the models are ~500 MB, far too much to ship
            # with the kiosk. Unset = she never offers the 3D view.
            viewer_3d_url=os.getenv("VIEWER_3D_URL") or None,
        )
        self.speak = speak
        self.set_state = set_state or (lambda s: asyncio.sleep(0))
        self.banned = banned_pattern(rules)
        system_text = load_system_text()
        # cache_control on the system block caches tools + system together
        # (prompt render order is tools -> system), cutting per-turn latency/cost.
        self.system = [{
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }]
        self.messages: list[dict] = []
        self._filler_index = 0

    def reset(self):
        self.messages = []
        self.tools._last_frame = None

    def _trim(self):
        if len(self.messages) > MAX_TURNS:
            # never orphan a tool_result from its tool_use
            cut = len(self.messages) - MAX_TURNS
            while cut < len(self.messages) and self.messages[cut]["role"] != "user":
                cut += 1
            self.messages = self.messages[cut:]

    async def _flush(self, buf: str) -> str:
        """Speak every complete sentence in buf; return the remainder."""
        parts = SENTENCE_END.split(buf)
        if len(parts) <= 1:
            return buf
        for sentence in parts[:-1]:
            if s := sentence.strip():
                if self.banned.search(s):
                    log.error("BLOCKED non-compliant output: %r", s)
                    continue
                await self.speak(s)
        return parts[-1]

    async def _filler(self):
        """Fire a short line if a tool is taking long enough to notice."""
        try:
            await asyncio.sleep(FILLER_DELAY)
            line = FILLERS[self._filler_index % len(FILLERS)]
            self._filler_index += 1
            await self.speak(line)
        except asyncio.CancelledError:
            pass

    async def respond(self, user_text: str):
        """One customer turn -> speech + scene commands. May loop over tool calls."""
        self.messages.append({"role": "user", "content": user_text})
        self._trim()

        for _ in range(6):  # tool-call depth guard
            await self.set_state("thinking")
            buf = ""
            blocks: list[dict] = []
            spoke = False

            async with self.client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self.system,
                tools=TOOL_SCHEMAS,
                messages=self.messages,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        if not spoke:
                            await self.set_state("speaking")
                            spoke = True
                        buf += event.delta.text
                        buf = await self._flush(buf)
                final = await stream.get_final_message()

            if buf.strip() and not self.banned.search(buf):
                await self.speak(buf.strip())

            # exclude SDK-only fields (e.g. parsed_output) the API rejects on replay
            blocks = [b.model_dump(exclude_none=True) for b in final.content]
            self.messages.append({"role": "assistant", "content": blocks})

            tool_uses = [b for b in blocks if b["type"] == "tool_use"]
            if not tool_uses:
                return

            results = []
            for tu in tool_uses:
                filler_task = None
                if tu["name"] not in FAST_TOOLS:
                    filler_task = asyncio.create_task(self._filler())
                try:
                    out = await self.tools.run(tu["name"], tu["input"] or {})
                finally:
                    if filler_task:
                        filler_task.cancel()
                log.info("tool %s(%s)", tu["name"], tu["input"])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": str(out),
                })

            self.messages.append({"role": "user", "content": results})
            self._trim()

        log.warning("tool loop guard hit")

    async def greet(self):
        """Opening line. Varied - never the same greeting twice in a row."""
        self.messages.append({
            "role": "user",
            "content": "[A customer has just walked up. Greet them warmly and ask one open question. "
                       "Vary your wording from any previous greeting.]",
        })
        await self.respond("")  # respond() appends an empty user turn; harmless
        self.messages = [m for m in self.messages if m.get("content") != ""]


# ------------------------------------------------------------------ CLI

async def _cli():
    """Step 2 of the build order: get the conversation right with no voice, no avatar."""
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    async def emit_ui(action, payload):
        print(f"\n  [SCENE] {action}: {payload.get('title', payload)}\n")

    async def speak(text):
        print(f"  HER: {text}")

    brain = Brain(emit_ui, speak)
    print("Cigar Inc. concierge - text mode. Ctrl-C to quit.\n")
    await brain.greet()

    while True:
        try:
            line = input("\nYOU: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line:
            await brain.respond(line)


if __name__ == "__main__":
    import sys
    if "--cli" in sys.argv:
        asyncio.run(_cli())
