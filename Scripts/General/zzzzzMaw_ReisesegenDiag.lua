-- Diagnosis for Reisesegen (Travelers' Boon, spell 101) and the other Dark Elf
-- spells (100..103). Read-only: it never touches a cast, a buff or a skill.
-- Writes MawReisesegen.log next to the game (80 lines at most per session).
--
-- Two suspects are told apart by one cast:
--   1. the engine's mastery limit for the Dark Elf ability (skill 21) no longer
--      follows Race Skillz.txt since Skillz.dll is gone -> "engineLimit" vs
--      "raceTable" differ, "cast" never shows up or shows a low mastery;
--   2. MawBuffApply wipes Torch Light / Wizard Eye -> the cast shows up, the
--      buffs 16 and 19 are gone one or five seconds later, Feather Fall (5) stays.

local LOG = "MawReisesegen.log"
local LIMIT = 80
local count = 0
local pending = {}		-- {due = Game.Time, tag = string, index = party slot}
local subCastUntil = 0	-- sub-casts of spells 1/12/13 are logged until then
local listedOnce = false

local function val(getter)
	local ok, v = pcall(getter)
	if ok then
		return tostring(v)
	end
	return "ERR<" .. tostring(v):gsub("%s+", " "):sub(1, 60) .. ">"
end

local function write(line)
	if count >= LIMIT then
		return
	end
	count = count + 1
	pcall(function()
		local f = io.open(LOG, "a")
		if f then
			f:write(os.date("%Y-%m-%d %H:%M:%S") .. " " .. line .. "\n")
			f:close()
		end
	end)
end

local function skillText(raw)
	local s, m = SplitSkill(raw)
	return s .. "/" .. m
end

-- everything the Dark Elf ability depends on, for one character
local function describe(pl)
	local race = function()
		return Game.CharacterPortraits[pl.Face].Race
	end
	return string.format("name=%s class=%s race=%s engineLimit=%s masteryLimit=%s raceTable=%s raw=%s getSkill=%s SP=%s",
		val(function() return pl.Name end),
		val(function() return pl.Class end),
		val(race),
		val(function() return Game.Classes.Skills[pl.Class][21] end),
		val(function() return Skillz.MasteryLimit(pl, 21) end),
		val(function() return Skillz.MasteryTable_get(race(), pl.Class, 21) end),
		val(function() return skillText(pl.Skills[21]) end),
		val(function() return skillText(pl:GetSkill(21)) end),
		val(function() return pl.SP end))
end

local function buffText(id)
	return val(function()
		local b = Party.SpellBuffs[id]
		return string.format("%d:left=%.1fmin,power=%d,skill=%d,caster=%d",
			id, (b.ExpireTime - Game.Time) / 256, b.Power, b.Skill, b.Caster)
	end)
end

local function snapshot(tag, index)
	write(string.format("%s buffs[%s %s %s] mawbuff1=%s mawbuff12=%s rework=%s player=%s",
		tag, buffText(5), buffText(16), buffText(19),
		val(function() return vars.mawbuff[1] end),
		val(function() return vars.mawbuff[12] end),
		val(function() return vars.MAWSETTINGS.buffRework end),
		tostring(index)))
end

-- once per session: the whole party, so the limits are on record without a cast
local function onLoadMap()
	if listedOnce then
		return
	end
	listedOnce = true
	write("LOADED diagnosis, Party.High=" .. val(function() return Party.High end))
	for i = 0, Party.High do
		write("PARTY " .. i .. " " .. describe(Party[i]))
	end
end

-- the click in the spell book: does the attempt reach the engine's cast?
local function onAction(t)
	if t.Action == 142 and t.Param >= 100 and t.Param <= 103 then
		write(string.format("ACTION142 spell=%s player=%s", tostring(t.Param), tostring(t.Param2)))
	end
end

local function onCast(t)
	local sp = t.SpellId
	if sp >= 100 and sp <= 103 then
		write(string.format("CAST spell=%s player=%s kind=%s target=%s scroll=%s skill=%s mastery=%s spCost=%s | %s",
			tostring(sp),
			tostring(t.PlayerIndex),
			val(function() return t.TargetKind end),
			val(function() return t.TargetId end),
			val(function() return t.IsSpellScroll end),
			val(function() return t.Skill end),
			val(function() return t.Mastery end),
			val(function() return t.SPCost end),
			describe(t.Player)))
		snapshot("SNAP+0", t.PlayerIndex)
		local now = Game.Time
		pending[#pending + 1] = {due = now + 128, tag = "SNAP+1s", index = t.PlayerIndex}
		pending[#pending + 1] = {due = now + 640, tag = "SNAP+5s", index = t.PlayerIndex}
		subCastUntil = now + 640
	elseif (sp == 1 or sp == 12 or sp == 13) and Game.Time < subCastUntil then
		write(string.format("SUBCAST spell=%s player=%s handled=%s", tostring(sp), tostring(t.PlayerIndex),
			val(function() return t.Handled end)))
	end
end

local function onTick()
	if #pending == 0 then
		return
	end
	local now = Game.Time
	for i = #pending, 1, -1 do
		local p = pending[i]
		if now >= p.due then
			table.remove(pending, i)
			snapshot(p.tag, p.index)
		elseif p.due - now > 100000 then
			table.remove(pending, i)	-- game time jumped back (load): drop it
		end
	end
end

-- a failing diagnosis must never break the game: every handler runs in pcall
local function safe(fn)
	return function(t)
		local ok, err = pcall(fn, t)
		if not ok then
			write("HANDLER FAILED: " .. tostring(err):gsub("%s+", " "):sub(1, 200))
		end
	end
end

events.LoadMap = safe(onLoadMap)
events.Action = safe(onAction)
events.PlayerCastSpell = safe(onCast)
events.Tick = safe(onTick)
