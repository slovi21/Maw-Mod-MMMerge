-- NPC voices (proof of concept): plays pre-generated audio for NPC greetings
-- and for topic messages shown in NPC dialogs.
--
-- Audio lives in Data\Voices\NPC\<key>.ogg (or .wav). The key is the CRC32 of
-- the normalized text, made by Tools/Voice/extract_texts.py from the same
-- rules as normalize() below - keep the two identical. Lookup is by text, not
-- by table index: a text that MAW or a quest script changed has no file and
-- stays silent instead of playing a stale line.
--
-- Setting: npcVoices in MAWSettings.lua (false = off, nil counts as on).
-- Console: MawVoices.Stop(), MawVoices.Play(text), MawVoices.Key(text),
-- MawVoices.Enabled = false, MawVoices.Log = false.
-- Notes and open points: Tools/Voice/README.md.

if npcVoices == false then
	return
end

local band, bxor, bnot, rshift, tohex = bit.band, bit.bxor, bit.bnot, bit.rshift, bit.tohex
local byte, gsub, char = string.byte, string.gsub, string.char

local VOICE_DIR = AppPath .. "Data\\Voices\\NPC\\"
local LOG_PATH = AppPath .. "NPCVoicesLog.txt"
local EXTENSIONS = {".ogg", ".wav"}

MawVoices = {
	Enabled = true,
	Log = true,		-- one line per dialog text: key, found/missing, text start
	Volume = 1,		-- multiplier on top of the game's sound volume
}

---------------------------------------------------------------------------
-- key: CRC32 (zlib flavour) of the normalized text

local CRC = {}
for i = 0, 255 do
	local c = i
	for _ = 1, 8 do
		if band(c, 1) ~= 0 then
			c = bxor(rshift(c, 1), 0xEDB88320)
		else
			c = rshift(c, 1)
		end
	end
	CRC[i] = c
end

local function lowerASCII(c)
	return char(byte(c) + 32)
end

-- drop quotes, collapse whitespace, trim, lowercase A-Z only. Explicit
-- character sets instead of %s / string.lower: those follow the C locale.
local function normalize(s)
	s = gsub(s, '"', "")
	s = gsub(s, "[ \t\r\n\v\f]+", " ")
	s = gsub(s, "^ ", "")
	s = gsub(s, " $", "")
	return (gsub(s, "[A-Z]", lowerASCII))
end

local keyCache = {}
local function textKey(text)
	local key = keyCache[text]
	if key then
		return key
	end
	local s = normalize(text)
	local c = 0xFFFFFFFF
	for i = 1, #s do
		c = bxor(rshift(c, 8), CRC[band(bxor(c, byte(s, i)), 0xFF)])
	end
	key = tohex(bnot(c))
	keyCache[text] = key
	return key
end
MawVoices.Key = textKey

---------------------------------------------------------------------------
-- SoLoud (ExeMods\soloud_x86.dll, also used by the multiplayer module).
-- Own instance, created on first use. Floats go in as their bit pattern.

local SL, Soloud, broken
local wav, handle
local floatBuf = mem.StaticAlloc(4)

local function f32(x)
	mem.r4[floatBuf] = x
	return mem.u4[floatBuf]
end

local function audio()
	if Soloud or broken then
		return Soloud
	end
	SL = mem.dll["soloud_x86"]
	if not SL then
		broken = true
		return
	end
	local s = SL.Soloud_create()
	if s == 0 or SL.Soloud_init(s) ~= 0 then
		if s ~= 0 then
			SL.Soloud_destroy(s)
		end
		broken = true
		return
	end
	Soloud = s
	return Soloud
end

local function stop()
	if not Soloud then
		return
	end
	if handle then
		SL.Soloud_stop(Soloud, handle)
		handle = nil
	end
	if wav then
		SL.Wav_destroy(wav)
		wav = nil
	end
end
MawVoices.Stop = stop

local function findFile(key)
	for _, ext in ipairs(EXTENSIONS) do
		local path = VOICE_DIR .. key .. ext
		local f = io.open(path, "rb")
		if f then
			f:close()
			return path
		end
	end
end

local logFile
local function log(kind, key, found, text)
	if not MawVoices.Log then
		return
	end
	logFile = logFile or io.open(LOG_PATH, "a")
	if logFile then
		logFile:write(("%s %-8s %s %s %s\n"):format(os.date("%H:%M:%S"), kind, key,
			found and "play   " or "missing", (gsub(text, "[\r\n]+", " ")):sub(1, 70)))
		logFile:flush()
	end
end

local function volume()
	return band(Game.SoundVolume, 255) / 9 * MawVoices.Volume
end

-- Stops whatever plays; plays the file for 'text' if there is one.
local function playText(text, kind)
	stop()
	if not MawVoices.Enabled or not text or text == "" then
		return false
	end
	local key = textKey(text)
	local path = findFile(key)
	log(kind or "text", key, path, text)
	if not path or not audio() or volume() <= 0 then
		return false
	end
	wav = SL.Wav_create()
	if SL.Wav_load(wav, path) ~= 0 then
		SL.Wav_destroy(wav)
		wav = nil
		return false
	end
	-- playEx(soloud, sound, volume, pan, paused, bus)
	handle = SL.Soloud_playEx(Soloud, wav, f32(volume()), f32(0), 0, 0)
	return true
end
MawVoices.Play = playText

---------------------------------------------------------------------------
-- Dialog tracking.
-- Greeting: DrawNPCGreeting may run on every redraw and later handlers may
-- still replace t.Text (quests, multiplayer), so the table is read on the
-- next Tick and only a changed text plays.
-- Topics: a topic's evt (compiled Global.EVT or Lua Message()) ends in
-- SetMessage, which sets Game.NPCMessage; a change of that text while the
-- dialog is open plays it. Same idea for any other dialog message.

local inDialog, pendingGreeting, lastGreeting, lastMessage

local function readMessage()
	return Game.NPCMessage
end

local function currentMessage()
	local ok, s = pcall(readMessage)
	return ok and s or nil
end

function events.EnterAnyNPC()
	inDialog = true
	lastGreeting = nil
	lastMessage = currentMessage()  -- left over from an earlier dialog
end

function events.DrawNPCGreeting(t)
	pendingGreeting = t
end

local function leave()
	inDialog, pendingGreeting, lastGreeting, lastMessage = nil, nil, nil, nil
	stop()
end
events.ExitNPC = leave
events.ExitAnyNPC = leave
events.LeaveMap = leave

function events.Tick()
	if not inDialog then
		return
	end
	if pendingGreeting then
		local text = pendingGreeting.Text
		pendingGreeting = nil
		if text and text ~= lastGreeting then
			lastGreeting = text
			playText(text, "greeting")
		end
	end
	local msg = currentMessage()
	if msg ~= lastMessage then
		lastMessage = msg
		if msg and msg ~= "" and msg ~= lastGreeting then
			playText(msg, "topic")
		end
	end
end
