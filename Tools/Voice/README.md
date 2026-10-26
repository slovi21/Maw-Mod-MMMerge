# NPC voices — proof of concept

Can MM6/7/8 dialogue in MMMerge + MAW be voiced like Morrowind's *Voices of
Vvardenfell*? This folder holds a working pipeline for **NPC greetings** and a
first cut for **topic texts**, tested on the MM8 start area.

```
Data/mmmerge.T.lod ──extract_texts.py──► texts.csv ──generate_voices.py──► Data/Voices/NPC/<key>.ogg
                                                         (Kokoro-82M, local)          │
in game: text about to be shown ──CRC32 of normalized text = key──► file exists? play : stay silent
                                  (Scripts/General/zzzMaw_NPCVoices.lua, SoLoud)
```

## Files

| File | What it does |
|---|---|
| `extract_texts.py` | Reads the text LOD (read-only), parses `NPCGreet.txt`, `NPCData.txt`, and with `--topics` also `NPCText.txt` + `Global.EVT`; writes one CSV row per distinct text with its key, speakers, portrait ids and a gender guess. |
| `generate_voices.py` | Synthesizes CSV rows with Kokoro-82M via kokoro-onnx, one fixed voice per NPC, writes `<key>.ogg` (ffmpeg) or `<key>.wav`, plus a `voices.csv` manifest. `--dry-run` shows the plan. |
| `../../Scripts/General/zzzMaw_NPCVoices.lua` | In game: computes the key of each greeting/dialog message, plays the matching file with SoLoud, stops on exit. |
| `../../Scripts/General/zz_Maw-initialize.lua` | New setting `npcVoices` (default on; `false` disables the module before it registers anything). |

## The key — why lookup is by text

`key = CRC32(normalize(text))`, 8 hex digits. `normalize` removes `"`,
collapses `[ \t\r\n\v\f]+` to one space, trims, lowercases A–Z only, all on
the raw cp1252 bytes. Python (`extract_texts.normalize`) and Lua
(`zzzMaw_NPCVoices.lua normalize`) must stay byte-identical.

Keying by text instead of table index means: when MAW, a quest script or the
multiplayer module replaces a text, there is simply no file for it and the
line stays silent — never a stale recording. It also makes the quote/line-break
differences between the LOD cells and what the engine hands to Lua irrelevant.

Collision risk with ~5,000 texts and 32 bits: about 0.3 %; one would play a
wrong line, nothing worse.

## How to run

```bash
python extract_texts.py --game "C:/.../Might and Magic 8 MAW Mod 4.5" --out texts.csv --topics
python generate_voices.py texts.csv --out "<MM8>/Data/Voices/NPC" \
    --model kokoro-v1.0.onnx --voices voices-v1.0.bin --limit 50 --override 30=af_heart
python generate_voices.py texts.csv --out "<MM8>/Data/Voices/NPC" \
    --model ... --voices ... --kind topic --npc 27 1 2
```

Setup used: Python 3.13 venv (kokoro-onnx 0.6.1 needs 3.10–3.13),
`pip install kokoro-onnx==0.6.1`, model files `kokoro-v1.0.onnx` (325 MB) and
`voices-v1.0.bin` (28 MB) from
<https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0>,
ffmpeg on PATH. Keep work folders short: Windows tools fail on paths over 260
characters.

## Results (2026-10-26)

**Text volume** (from `mmmerge.T.lod`, after de-duplication):

| | distinct texts | characters |
|---|---|---|
| greetings (`NPCGreet.txt`, Greeting 1 + 2) | 495 | 74,444 |
| topic messages reachable from special NPCs' topics | 974 | 236,786 |
| together | 1,469 | 311,230 |

Measured on the test batch: Kokoro speaks ~17 characters per second, so
greetings alone are ~1.2 h of audio and greetings + topics ~5 h (the whole
merged dialogue, ~718k characters with news and all NPCText, ~12 h). Ogg
Vorbis q4, mono 24 kHz, is ~7.4 KB per second → ~135 MB for greetings +
topics.

**Generated for the test:** the first 50 greetings (NPCGreet 1–30: Dagger
Wound Island and Ravenshore) and the 14 topic messages of S'ton (27), Brekish
Onefang (1) and Dadeross (2): 64 files, 9.2 min, 4.1 MB. Faster than real
time on CPU (a 13 s line took ~6 s including model load); no GPU needed.
One voice (`am_fenrir`) talks noticeably fast (21–23 chars/s); `--speed 0.9`
or another voice for that NPC would help.

**Verified offline:**

- Lua key = Python key for all 3,953 real greeting/NPCText cells plus quoted,
  CRLF and extra-whitespace variants, run in the game's own LuaJIT
  (`ExeMods/MMExtension/lua51.dll`, LuaJIT 2.1.0-beta3) from a 32-bit host.
- Dialog logic with a stubbed engine: a greeting redrawn three times plays
  once; a new dialog message stops the greeting and plays its own file; a text
  without a file stops playback; a message left over from an earlier dialog
  does not play; nothing plays outside a dialog.
- `soloud_x86.dll` (SoLoud 202002, MiniAudio backend): `Soloud_init`,
  `Wav_load` of an Ogg file, `Soloud_playEx` with float arguments passed as
  their bit pattern, `Soloud_stop`, `Wav_destroy` — the exact calls the module
  makes.

**In game:** see "In-game test" below.

## Voices

Kokoro v1.0 English voices with the better quality grades, British (`b*`) and
American (`a*`). Each NPC keeps one voice for all of its lines
(`npc_id % pool size`). Special NPCs have no sex in `NPCData.txt`; the guess
uses the player-portrait table (`Character portraits.txt`, NPCPic → Def sex)
and the street-name lists (`NPC names.txt`). That labels only ~28 % of the
greeting texts; unknown speakers default to the male pool. Fix per NPC with
`--override NPC=VOICE` or the CSV's `voice` column. For a real release this
needs a curated table (NPC id → voice), ideally with race-specific voices
(lizardmen, minotaurs, dragons) — Kokoro has no such timbres; a model with
voice design from a text prompt (e.g. Qwen3-TTS, Apache-2.0) would be the
next thing to try. No voice is cloned from a real person.

Licences: Kokoro-82M weights Apache-2.0, kokoro-onnx MIT, so the generated
audio can be shipped. espeak-ng (GPL-3, pulled in by `espeakng-loader`) is
only the phonemizer on the generating machine and is not shipped.

## The Lua module

- `events.DrawNPCGreeting` only remembers the table; the next `Tick` reads
  `t.Text`. Reason: the hook can run on every redraw, and later handlers
  (`LocalizationAndQuests.lua` quest greetings, `RemoveTransitionLimits.lua`,
  the multiplayer `Evt.lua`) may still replace `t.Text`.
- Topics and other dialog messages: every `Tick` while a dialog is open
  (`EnterAnyNPC` … `ExitNPC`/`ExitAnyNPC`), `Game.NPCMessage` is compared with
  the last value; a change plays the new text.
- One own SoLoud instance, created on first use (the multiplayer module's
  instance only exists while multiplayer runs). Volume = game sound volume
  (low byte of `Game.SoundVolume`, 0–9, like `StepSounds.lua`) × `MawVoices.Volume`.
- Off switch: `npcVoices = false` in `MAWSettings.lua` (the module returns
  before registering handlers), or `MawVoices.Enabled = false` in the console.
  Without files in `Data/Voices/NPC` it does nothing audible.
- `NPCVoicesLog.txt` in the game folder gets one line per dialog text (key,
  `play`/`missing`, start of text) — the coverage report while this is a POC.
  `MawVoices.Log = false` turns it off.
- Console: `MawVoices.Key(text)`, `MawVoices.Play(text)`, `MawVoices.Stop()`.
- It is a plain `General/` script, not a MawCore module, so it can be dropped
  by deleting one file. Moving it into MawCore would put the SoLoud binding
  into `Engine.lua` (the only file allowed to use `mem.*`).

## Topic texts — how they reach the screen (the open risk)

How a topic text is produced in MMMerge (MM8 engine):

1. Clicking a topic runs the global event with the topic's number
   (`NPCTopic.txt` # = event id in `Global.EVT`). The merge raises
   `events.ClickNPCTopic(i)` just before it
   (`Scripts/Structs/After/RemoveNPCTablesLimits.lua:290/303`).
2. The compiled event executes `SetMessage` (evt command `0x1E`, one `i4`
   `Str`; `Scripts/Core/evt.lua:951`). For global events `Str` indexes
   `Game.NPCText` (see `evtdeco.lua` `GetStr`). `Global.EVT` holds 1,943
   `SetMessage` commands pointing at 1,529 distinct NPCText entries; one
   event often has several, chosen by quest bits.
3. The engine keeps the text to display in `Game.NPCMessage`
   (`[0xFFD458]` in MM8, `EditPChar`, "current message displayed in a dialog
   with some NPC", `Scripts/Structs/00 structs.lua:271`).
4. After the evt has run, MMExtension calls Lua handlers for that event and
   then `events.EvtGlobal(evtId, seq)` (`internal.AfterProcessEvent`,
   `Scripts/Core/evt.lua:342`).

Lua `Message(text)` (`Scripts/Structs/After/Functions.lua:86`) writes the text
into `Game.NPCText[high]` or `evt.str[499]` and then issues the same
`evt.SetMessage`, so it ends in the same place. MAW has ~90 `Message(` and
~80 `evt.SetMessage` calls in its scripts.

Options to catch the text:

| Option | Pro | Con |
|---|---|---|
| Hook the evt interpreter where it dispatches a command (`0x443895`, MM8) and react to `0x1E` | sees every SetMessage, also a repeat of the same one | the multiplayer module already hooks exactly this address (`Multiplayer/Hooks.lua`, `mulhook` → `events.EvtCommandCall`) and only while multiplayer runs; a second hook there would clash |
| `events.EvtGlobal` + read `Game.NPCMessage` | no engine patch | misses `Message()` calls made outside a global event (e.g. from `ClickNPCTopic` or `Action` handlers) |
| **poll `Game.NPCMessage` each Tick while a dialog is open** (implemented) | no engine patch; covers compiled evt, Lua `Message()`, `Question()`; one pointer read per frame | clicking the same topic twice gives the same text and does not replay; a dialog text equal to the greeting is skipped |

Not covered yet:

- Messages that come from map string tables (`evt.str`, map `.str` files in the
  games LODs) — the extractor only reads `NPCText`; the playback side would
  already handle them.
- Texts with `%s`/`%d` filled in at run time (2 in the greetings) — skipped
  by the generator.
- New NPCText entries the merge adds through `Data/03 LocalizeTables.txt`
  (28 rows, e.g. hire/promotion texts) are not in the LOD.
- Street NPCs (news, `NPCNews.txt`, 919 entries) and house/shop dialogue.
- Multiplayer: on a client, `Evt.lua` replaces the greeting with the host's
  text; the key still works because it is computed from the final text.

## In-game test (2026-10-26)

Install: MM8 + MMMerge + MAW 4.5 (local), with only two additions:
`Scripts/General/zzzMaw_NPCVoices.lua` and `Data/Voices/NPC/` (64 files). The
tester played a new game on Emerald Island and then on Dagger Wound Island and
reported that it worked. `NPCVoicesLog.txt` from that session (34 lines):

- **Greetings play:** S'ton (both variants, first visit and "seen"),
  Brekish Onefang, Dadeross; re-entering a dialog plays the greeting again.
- **Topic texts play — the `Game.NPCMessage` poll catches compiled
  `Global.EVT` `SetMessage` output:** 3 of Brekish's, 4 of Dadeross's, 3 of
  S'ton's topics played; switching topics switches the line.
- **Keys match the game:** every logged text that exists unchanged in the LOD
  had the extractor's key (6 of 6 among the unvoiced lines checked by hand:
  NPCGreet 133, NPCText 22, 23, 1627, NPCNews 1, …).
- **Edited text stays silent:** NPCText 2287 (a hireling's offer) contains
  `%17`, which the engine fills in; its runtime key differs, nothing played.
- **Coverage gaps seen:**
  - Brekish's topics *Take this crystal…*/*Bring the portal crystal…*
    (NPCText 22, 23) were not generated: the NPC gets those topics at run time
    (`SetNPCTopic`, evt command 0x27), while the extractor only follows the
    topics in `NPCData.txt`. Fix: follow `SetNPCTopic` in Global.EVT and map
    evts, or simply voice every NPCText entry any `SetMessage` reaches
    (1,529).
  - A news line (NPCNews 1, *If the pirates make it through…*) came through
    the greeting path; `NPCNews.txt` is not extracted yet.

Stopping on exit was reported working by the tester; the log does not
record stops.

## Next steps

1. Curated speaker table (NPC id → voice, race), better voices for non-human
   NPCs.
2. Extract map strings so house/map-event messages get keys too.
3. Decide how audio ships (separate download vs. repo; ~135 MB for greetings
   + topics of all three games).
4. Optional: subtitle-paced auto-advance, volume slider in the extra settings
   menu, a per-NPC "already heard" rule.
