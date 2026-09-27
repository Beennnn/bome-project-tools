# Draft — post for forum.bome.com

Not published. Needs a Bome forum account.

Suggested category: **MIDI Translator Pro**, or Feature Requests if the roadmap question
is the main point.

Suggested title: **Scripting .bmtp projects — and is a network API on the roadmap?**

---

Hi all,

MT Pro is the hub of a live keyboard rig here: three keyboard zones feeding a DAW, a
breath controller, a Stream Deck, a pedalboard, and a fallback path for when the DAW dies
mid-song. The project is 88 translators across 10 presets and MT Pro carries all of it
without complaint. Saying that first because everything below is a consequence of the
tool being good enough to grow into.

**Past a certain size, some edits are inherently bulk edits.** A recent one here: "when a
split point moves, silence the notes held in the zones concerned" had been written as
7 triggers × 3 destinations = **21 translators**, all saying the same thing. A fourth
zone would have meant 28. Restructuring it through a Perform action brought it to 7 + 3,
and a fourth destination now costs one translator instead of seven.

Done by hand that is a long and error-prone afternoon — delete 21 entries, add 3,
renumber, hope nothing was missed. As a script it is ten minutes and it is repeatable. So
I scripted it against the `.bmtp` file.

**What I worked out about the format, offered in case it is useful to others here** — and
with the caveat that it is observation rather than knowledge, so corrections are very
welcome:

- a translator is spread over four indexed keys (`Name`, `Incoming`, `Outgoing`,
  `Options`), and numbering stays contiguous;
- lines come out sorted alphabetically by key, then numerically by index — writing them
  in another order is harmless, MT Pro simply normalises on the next save;
- `Outgoing<N>=None` is the literal string for a translator that emits nothing;
- `Options` is `Actv/Stop/OutO` then `StMa` + eight hex digits giving the **number** of
  rules, then the rules concatenated;
- the channel is 0-based (`num="11"` is channel 12);
- the XML prefix names the action type (`MID3`, `Prfm`);
- a port named on a translator overrides the preset's `DefaultInPorts`, and an alias is
  expanded into each incoming action rather than resolved at run time — which is why
  setting a default without also clearing those names has no effect;
- editing the file outside MT Pro invalidates the signature, so the project needs to be
  reopened and saved before BomeBox or MT Player will take it again.

All verified against 1.9.2 by reopening what the script produced. Some of it may well be
version-specific, and I would rather be told than keep guessing.

**The actual question: is anything programmable on the roadmap?**

Two different needs, and I think they want two different answers:

**Live control** — activate a preset, arm a fallback, add or drop a MIDI route while
playing. MT Pro already has the actions for all of this internally (Preset Action, MIDI
Router Action); what is missing is a way to reach them from outside. **OSC would fit
naturally here** — same shape as the MIDI it already speaks, just over the network.

**Project editing** — read the structure, add or remove translators, renumber, validate,
save. This is not real-time control, it is CRUD on a document, and OSC is the wrong shape
for it: no structured request/response, no rich types, no atomic multi-step change. **A
local HTTP + JSON API would fit far better.**

And on MCP specifically, since that is where a lot of this is heading: Ableton does not
ship one either. But because an open control surface exists — AbletonOSC, itself
third-party — **at least six community MCP servers** have grown on top of it
([Simon-Kansara](https://github.com/Simon-Kansara/ableton-live-mcp-server),
[nozomi-koborinai](https://github.com/nozomi-koborinai/ableton-osc-mcp),
[ahujasid](https://github.com/ahujasid/ableton-mcp), among others). Ableton wrote none of
them. It was enough that something programmable existed, and the community did the rest
within months.

So the request is not "please build an MCP server". It is: **if any officially supported
programmable surface appears, the MCP layer will follow on its own.**

Meanwhile a script against the file works, and I am happy to share what I have. But it is
inference rather than documentation, it breaks the signature, and it cannot run while
MT Pro is open — none of which would be true of a supported path.

Thanks for a tool solid enough to be worth this much effort.
