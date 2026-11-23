-- Headless PoB engine worker.
--
-- Protocol: one JSON object per line on stdin -> one JSON object per line on stdout.
--   request:  {"id": 1, "cmd": "load_xml", "args": {...}}
--   response: {"id": 1, "ok": true, "result": {...}}  or  {"id": 1, "ok": false, "error": "..."}
-- On startup the worker emits {"id": null, "ok": true, "result": {"ready": true, ...}}.
-- PoB's own logging goes to stderr so stdout stays a clean protocol channel.
--
-- Run via:  app/engine/pob.sh worker.lua

local protocolOut = io.stdout
local stderr = io.stderr

-- Route all PoB print/ConPrintf output to stderr before the engine loads.
function print(...)
	local parts = {}
	for i = 1, select("#", ...) do parts[i] = tostring((select(i, ...))) end
	stderr:write(table.concat(parts, "\t"), "\n")
end

local json = require("dkjson")

-- If startup fails, HeadlessWrapper waits on io.read() for a keypress, which would
-- swallow our first request. Disable stdin reads during boot and report instead.
local realRead = io.read
io.read = function() return nil end
local bootStart = os.clock()
dofile("HeadlessWrapper.lua")
local bootSeconds = os.clock() - bootStart
io.read = realRead
if __mainObject__.promptMsg or not build then
	protocolOut:write(json.encode({ id = json.null, ok = false,
		error = "engine failed to start: " .. tostring(__mainObject__.promptMsg) }), "\n")
	os.exit(1)
end

---------------------------------------------------------------------------
-- Helpers
---------------------------------------------------------------------------

local function frame()
	runCallback("OnFrame")
end

local function requireBuild()
	if not build or not build.calcsTab or not build.calcsTab.mainOutput then
		error("no build loaded", 0)
	end
end

local function num(v)
	-- JSON can't represent inf/nan; report them as strings.
	if type(v) ~= "number" then return v end
	if v ~= v then return "NaN" end
	if v == math.huge then return "Infinity" end
	if v == -math.huge then return "-Infinity" end
	return v
end

local function cleanText(s)
	-- Strip PoB colour escapes (^7, ^xRRGGBB).
	return s and (s:gsub("%^x%x%x%x%x%x%x", ""):gsub("%^%d", "")) or s
end

-- Copy of the file-local matchFlags in Modules/Build.lua: does a skill's flag set
-- satisfy a display stat's required/excluded flags?
local function matchFlags(reqFlags, notFlags, flags)
	if type(reqFlags) == "string" then reqFlags = { reqFlags } end
	for _, flag in ipairs(reqFlags or {}) do
		if not flags[flag] then return false end
	end
	if type(notFlags) == "string" then notFlags = { notFlags } end
	for _, flag in ipairs(notFlags or {}) do
		if flags[flag] then return false end
	end
	return true
end

-- Skill flags of an actor's main skill, kept on the active effect's stat set.
local function skillFlags(actor)
	local ms = actor and actor.mainSkill
	if not ms then return {} end
	local set = ms.activeEffect and ms.activeEffect.statSet
	return set and set.skillFlags or {}
end

-- Stats PoB shows in its sidebar for the current main skill, in sidebar order.
local function displayStatList(output, actor, statList)
	local out = {}
	for _, s in ipairs(statList) do
		if s.stat and not s.childStat
			and matchFlags(s.flag, s.notFlag, skillFlags(actor)) then
			local v = output[s.stat]
			-- Same visibility rule as PoB's sidebar (buildMode:AddDisplayStatList).
			if v ~= nil and type(v) ~= "table"
				and ((s.condFunc and s.condFunc(v, output)) or (not s.condFunc and v ~= 0)) then
				table.insert(out, {
					stat = s.stat,
					label = cleanText(s.label),
					value = num(v),
					lowerIsBetter = s.lowerIsBetter or nil,
				})
			end
		end
	end
	return out
end

local function currentStats()
	local env = build.calcsTab.mainEnv
	local result = { player = displayStatList(build.calcsTab.mainOutput, env.player, build.displayStats) }
	if env.player.mainSkill.minion and build.calcsTab.mainOutput.Minion then
		result.minion = displayStatList(build.calcsTab.mainOutput.Minion, env.minion, build.minionDisplayStats)
	end
	return result
end

-- Diff two calc outputs over the sidebar stat list (mirrors buildMode:CompareStatList).
local function diffStatList(statList, actor, base, new)
	local changes = {}
	for _, s in ipairs(statList) do
		if s.stat and not s.childStat and s.stat ~= "SkillDPS"
			and matchFlags(s.flag, s.notFlag, skillFlags(actor)) then
			local a, b = base[s.stat], new[s.stat]
			if type(a) ~= "table" and type(b) ~= "table" then
				a, b = a or 0, b or 0
				local d = b - a
				if s.stat == "FullDPS" and not new[s.stat] then d = 0 end
				if (d > 0.001 or d < -0.001)
					and (not s.condFunc or s.condFunc(b, new) or s.condFunc(a, base)) then
					local pct = (a ~= 0 and a == a and a ~= math.huge) and (d / math.abs(a) * 100) or nil
					local better = d > 0
					if s.lowerIsBetter then better = not better end
					table.insert(changes, {
						stat = s.stat, label = cleanText(s.label),
						before = num(a), after = num(b), delta = num(d), pct = num(pct),
						better = better,
					})
				end
			end
		end
	end
	return changes
end

local function diffOutputs(base, new)
	local env = build.calcsTab.mainEnv
	local result = { player = diffStatList(build.displayStats, env.player, base, new) }
	if env.player.mainSkill.minion and base.Minion and new.Minion then
		result.minion = diffStatList(build.minionDisplayStats, env.minion, base.Minion, new.Minion)
	end
	return result
end

---------------------------------------------------------------------------
-- Change preview card (app/VISION.md): a fixed core set of stats that every
-- candidate change is shown against, plus any other sidebar stat that changed.
-- Built here so the numbers go from the engine to the screen untouched.
---------------------------------------------------------------------------

-- Groups, then rows; a row with several stats is shown on one line (resists, max hits).
-- "DPS" is resolved per build to CombinedDPS (hits + DoT) or TotalDPS.
local CARD_LAYOUT = {
	{ group = "Damage", rows = {
		{ { key = "DPS", label = "Total DPS" } },
	} },
	{ group = "Pools", rows = {
		{ { key = "Life", label = "Life" } },
		{ { key = "EnergyShield", label = "Energy Shield" } },
		{ { key = "Spirit", label = "Spirit" } },
	} },
	{ group = "Mitigation", rows = {
		{ { key = "Armour", label = "Armour" } },
		{ { key = "Evasion", label = "Evasion" } },
		{ label = "Resistances",
			{ key = "FireResist", label = "Fire", tone = "fire", unit = "%" },
			{ key = "ColdResist", label = "Cold", tone = "cold", unit = "%" },
			{ key = "LightningResist", label = "Lightning", tone = "lightning", unit = "%" },
			{ key = "ChaosResist", label = "Chaos", tone = "chaos", unit = "%" } },
	} },
	{ group = "Survival", rows = {
		{ { key = "TotalEHP", label = "Effective HP" } },
		{ label = "Max hit",
			{ key = "PhysicalMaximumHitTaken", label = "Physical", tone = "physical" },
			{ key = "FireMaximumHitTaken", label = "Fire", tone = "fire" },
			{ key = "ColdMaximumHitTaken", label = "Cold", tone = "cold" },
			{ key = "LightningMaximumHitTaken", label = "Lightning", tone = "lightning" },
			{ key = "ChaosMaximumHitTaken", label = "Chaos", tone = "chaos" } },
	} },
}

local function dpsKey(output)
	if type(output.CombinedDPS) == "number" and output.CombinedDPS > 0 then return "CombinedDPS" end
	return "TotalDPS"
end

-- Output keys the core set reads, for the given DPS key.
local function cardKeys(dps)
	local keys = {}
	for _, g in ipairs(CARD_LAYOUT) do
		for _, row in ipairs(g.rows) do
			for _, cell in ipairs(row) do keys[cell.key == "DPS" and dps or cell.key] = true end
		end
	end
	-- Every DPS variant counts as core, so the "other" list doesn't repeat it.
	keys.CombinedDPS, keys.TotalDPS = true, true
	return keys
end

local function cardCell(def, key, base, new)
	local a = type(base[key]) == "number" and base[key] or nil
	local b = type(new[key]) == "number" and new[key] or nil
	local cell = { key = key, label = def.label, tone = def.tone, unit = def.unit,
		before = num(a), after = num(b) }
	if a and b then
		local d = b - a
		if d > 0.001 or d < -0.001 then
			cell.delta = num(d)
			cell.pct = (a ~= 0 and a == a and a ~= math.huge) and num(d / math.abs(a) * 100) or nil
			cell.better = d > 0
		end
	end
	return cell
end

-- Card for a change from `base` to `new` calc outputs. With `new` omitted it describes
-- the build as it is (after == before, no deltas).
local function buildCard(base, new)
	new = new or base
	local dps = dpsKey(base)
	local groups = {}
	for _, g in ipairs(CARD_LAYOUT) do
		local rows = {}
		for _, row in ipairs(g.rows) do
			local cells = {}
			for _, def in ipairs(row) do
				local cell = cardCell(def, def.key == "DPS" and dps or def.key, base, new)
				if cell.before ~= nil or cell.after ~= nil then table.insert(cells, cell) end
			end
			if #cells > 0 then table.insert(rows, { label = row.label, cells = cells }) end
		end
		table.insert(groups, { group = g.group, rows = rows })
	end
	local other = {}
	if new ~= base then
		local core, seen = cardKeys(dps), {}
		local env = build.calcsTab.mainEnv
		for _, c in ipairs(diffStatList(build.displayStats, env.player, base, new)) do
			if not core[c.stat] and not seen[c.stat] and not c.stat:match("^Spec:") then
				seen[c.stat] = true
				table.insert(other, c)
			end
		end
	end
	return { core = groups, other = other }
end

-- Resolve a passive node by numeric id or (case-insensitive) exact name.
local function findNode(ref)
	local nodes = build.spec.nodes
	if type(ref) == "number" or tostring(ref):match("^%d+$") then
		local n = nodes[tonumber(ref)]
		if n then return n end
	end
	local want = tostring(ref):lower()
	local matches = {}
	for _, n in pairs(nodes) do
		if n.dn and n.dn:lower() == want then table.insert(matches, n) end
	end
	if #matches == 0 then error("no passive node named '" .. tostring(ref) .. "'", 0) end
	if #matches > 1 then
		-- Prefer the reachable one (smallest path), e.g. duplicate-named small nodes.
		table.sort(matches, function(x, y) return (x.pathDist or 1e9) < (y.pathDist or 1e9) end)
	end
	return matches[1]
end

local function nodeInfo(n)
	return {
		id = n.id, name = n.dn, type = n.type,
		allocated = n.alloc or false,
		ascendancy = n.ascendancyName,
		pathCost = (not n.alloc and n.path) and #n.path or nil,
		stats = n.sd and (function()
			local t = {}
			for _, line in ipairs(n.sd) do table.insert(t, cleanText(line)) end
			return t
		end)() or {},
	}
end

local function parseItem(raw)
	local item = new("Item"):Item(raw)
	if not item.base then error("could not parse item text", 0) end
	item:NormaliseQuality()
	item:BuildModList()
	return item
end

local function slotNames()
	local t = {}
	for _, slot in ipairs(build.itemsTab.orderedSlots) do
		-- Skip abyssal sub-sockets to keep the list readable; they're still valid slot names.
		if not slot.nodeId and not slot.slotName:find("Abyssal Socket") then table.insert(t, slot.slotName) end
	end
	return t
end

local function requireSlot(name)
	if not build.itemsTab.slots[name] then
		error("unknown slot '" .. tostring(name) .. "'; valid: " .. table.concat(slotNames(), ", "), 0)
	end
	return build.itemsTab.slots[name]
end

---------------------------------------------------------------------------
-- Commands
---------------------------------------------------------------------------

local commands = {}

function commands.ping()
	return { pong = true }
end

-- args: { xml = "<PathOfBuilding>...", name = "optional" }
function commands.load_xml(args)
	assert(type(args.xml) == "string" and #args.xml > 0, "xml is required")
	loadBuildFromXML(args.xml, args.name or "build")
	requireBuild()
	return commands.summary()
end

function commands.export_xml()
	requireBuild()
	return { xml = build:SaveDB("code") }
end

function commands.summary()
	requireBuild()
	local spec = build.spec

	local items = {}
	for _, slot in ipairs(build.itemsTab.orderedSlots) do
		local item = build.itemsTab.items[slot.selItemId]
		if item and not slot.nodeId then
			table.insert(items, { slot = slot.slotName, name = item.name, rarity = item.rarity, base = item.baseName })
		end
	end

	local skills = {}
	for i, group in ipairs(build.skillsTab.socketGroupList) do
		local gems = {}
		for _, gem in ipairs(group.gemList) do
			-- Gem skills keep their effect on gem.gemData; item-granted skills set gem.grantedEffect.
			local ge = gem.grantedEffect or (gem.gemData and gem.gemData.grantedEffect)
			table.insert(gems, {
				name = ge and ge.name or gem.nameSpec,
				level = gem.level, quality = gem.quality,
				enabled = gem.enabled ~= false,
				support = (ge and ge.support) or false,
			})
		end
		local active = group.displaySkillList and group.displaySkillList[group.mainActiveSkill or 1]
		local activeName = active and active.activeEffect and active.activeEffect.grantedEffect.name
		if not activeName then
			-- displaySkillList isn't always populated (e.g. non-main groups): use the first active gem.
			for _, g in ipairs(gems) do
				if not g.support and g.enabled then activeName = g.name break end
			end
			activeName = activeName or (gems[1] and gems[1].name)
		end
		table.insert(skills, {
			index = i, label = group.label, slot = group.slot,
			activeSkill = activeName,
			enabled = group.enabled ~= false,
			isMain = (i == build.mainSocketGroup),
			gems = gems,
		})
	end

	local keystones, notables, points, ascPoints = {}, {}, 0, 0
	for _, n in pairs(spec.allocNodes) do
		if n.type == "Keystone" then table.insert(keystones, n.dn)
		elseif n.type == "Notable" and not n.ascendancyName then table.insert(notables, n.dn) end
		if n.ascendancyName then
			if n.type ~= "AscendClassStart" then ascPoints = ascPoints + 1 end
		elseif n.type ~= "ClassStart" and n.type ~= "Mastery" then
			points = points + 1
		end
	end
	table.sort(keystones); table.sort(notables)

	local mainSkill = build.calcsTab.mainEnv.player.mainSkill
	return {
		name = build.buildName,
		class = spec.curClassName,
		ascendancy = spec.curAscendClassName,
		level = build.characterLevel,
		treeVersion = spec.treeVersion,
		mainSkill = mainSkill and mainSkill.activeEffect and mainSkill.activeEffect.grantedEffect.name or nil,
		passivePoints = points,
		ascendancyPoints = ascPoints,
		keystones = keystones,
		notables = notables,
		items = items,
		skills = skills,
		stats = currentStats(),
	}
end

-- The change preview card's core stats for the build as it is (no change applied).
function commands.card()
	requireBuild()
	return buildCard(build.calcsTab.mainOutput)
end

-- Sidebar stats, or specific raw output keys: { keys = {"Life", "FireResist"} }
function commands.stats(args)
	requireBuild()
	if args.keys then
		local out = {}
		for _, k in ipairs(args.keys) do
			local v = build.calcsTab.mainOutput[k]
			-- Unknown keys come back as "n/a" rather than silently missing.
			if v == nil then out[k] = "n/a" else out[k] = type(v) == "table" and "<table>" or num(v) end
		end
		return out
	end
	return currentStats()
end

---------------------------------------------------------------------------
-- Explanations: how the engine arrived at a number (PoB's Calcs tab data)
---------------------------------------------------------------------------

-- Readable name for a modifier source tag like "Item:14:Victory Corona, Hallowed Crown".
local function sourceLabel(source)
	local kind = source:match("^[^:]+") or source
	if kind == "Item" then
		return "item: " .. (source:match("^Item:%d+:(.+)") or source)
	elseif kind == "Tree" then
		local node = build.spec.nodes[tonumber(source:match("^Tree:(%d+)"))]
		return "passive: " .. (node and node.dn or source)
	elseif kind == "Skill" then
		local id = source:match("^Skill:(.+)")
		local skill = id and data.skills[id]
		return "skill: " .. (skill and skill.name or id or source)
	elseif kind == "Base" then
		return "base (character/level/difficulty)"
	end
	return source:gsub(":", ": ", 1)
end

local function breakdownLines(t, out, depth)
	for _, v in ipairs(t) do
		if type(v) == "string" then table.insert(out, string.rep("  ", depth) .. cleanText(v)) end
	end
	if type(t.slots) == "table" then
		for _, s in ipairs(t.slots) do
			local parts = { (s.source or "?") .. ": base " .. tostring(s.base) }
			if s.inc and s.inc ~= "" then table.insert(parts, cleanText(tostring(s.inc)) .. " (increased)") end
			if s.more and s.more ~= "" then table.insert(parts, cleanText(tostring(s.more)) .. " (more)") end
			table.insert(parts, "= " .. tostring(s.total))
			table.insert(out, string.rep("  ", depth) .. table.concat(parts, " "))
		end
	end
end

-- PoB's own explanation of how a stat was calculated.
-- args: { stat = "ColdMaximumHitTaken" }
function commands.explain_stat(args)
	requireBuild()
	local stat = args.stat
	local env = build.calcsTab.mainEnv
	local bd = env.player.breakdown and env.player.breakdown[stat]
	if type(bd) ~= "table" then
		local close = {}
		local lower = tostring(stat):lower()
		for k in pairs(env.player.breakdown or {}) do
			if type(k) == "string" and (k:lower():find(lower, 1, true) or lower:find(k:lower(), 1, true)) then
				table.insert(close, k)
			end
		end
		table.sort(close)
		error("no breakdown for '" .. tostring(stat) .. "'" ..
			(#close > 0 and ("; try: " .. table.concat(close, ", ", 1, math.min(#close, 15))) or
			"; use stat_sources for modifier totals instead"), 0)
	end
	local lines = {}
	breakdownLines(bd, lines, 0)
	for k, v in pairs(bd) do
		if type(k) == "string" and k ~= "slots" and type(v) == "table" and type(v[1]) == "string" then
			table.insert(lines, "[" .. k .. "]")
			breakdownLines(v, lines, 1)
		end
	end
	local value = env.player.output[stat]
	return { stat = stat, value = type(value) == "number" and num(value) or nil, lines = lines }
end

-- Every active modifier feeding the named mods, with where it comes from.
-- Mod names are PoB's internal names, often the same as stat keys: Life, EnergyShield,
-- ColdResist, ElementalResist, ColdResistMax, Armour, Evasion, Damage, PhysicalDamage,
-- Speed, CritChance, Accuracy, Spirit, ...
-- args: { mods = {"ColdResist", "ElementalResist"} }
function commands.stat_sources(args)
	requireBuild()
	local modDB = build.calcsTab.mainEnv.player.modDB
	local out = {}
	for _, name in ipairs(args.mods or {}) do
		local rows, totals = {}, {}
		for _, row in ipairs(modDB:Tabulate(nil, nil, name)) do
			local v = row.value
			local value = type(v) == "number" and num(v) or tostring(v)
			table.insert(rows, { type = row.mod.type, value = value, source = sourceLabel(row.mod.source) })
			if type(v) == "number" then totals[row.mod.type] = (totals[row.mod.type] or 0) + v end
		end
		out[name] = { modifiers = rows, totals = totals }
		if #rows == 0 then out[name].note = "no active modifiers with this name" end
	end
	return out
end

-- Resolve a skill group by 1-based index or by the name of a gem in it (active gems first).
local function findSkillGroup(ref)
	local list = build.skillsTab.socketGroupList
	local i = tonumber(ref)
	if i and list[i] then return list[i], i end
	local want = tostring(ref):lower()
	for pass = 1, 2 do
		for idx, group in ipairs(list) do
			for _, gem in ipairs(group.gemList) do
				local ge = gem.grantedEffect or (gem.gemData and gem.gemData.grantedEffect)
				local name = (ge and ge.name or gem.nameSpec or ""):lower()
				if name == want and (pass == 2 or not (ge and ge.support)) then return group, idx end
			end
		end
	end
	error("no skill group with index or gem '" .. tostring(ref) .. "'", 0)
end

local DAMAGE_KEYS = { "CombinedDPS", "TotalDPS", "TotalDotDPS", "AverageHit", "AverageDamage", "Speed",
	"CritChance", "CritMultiplier", "HitChance" }

local function damageSnapshot()
	local o = build.calcsTab.mainOutput
	local snap = {}
	for _, k in ipairs(DAMAGE_KEYS) do
		if type(o[k]) == "number" then snap[k] = o[k] end
	end
	return snap
end

-- What each support gem in a skill group contributes: recalculate the skill with each
-- support disabled in turn and report what the skill loses. The build is restored after.
-- args: { group = "Lunar Assault" | 9 }
function commands.support_contributions(args)
	requireBuild()
	local group, index = findSkillGroup(args.group)
	local origMain = build.mainSocketGroup
	local disabledGem = nil  -- the gem currently switched off, so we can always restore it

	local function recalc()
		build.buildFlag = true
		frame()
	end

	local ok, result = pcall(function()
		-- Make this group the main skill so the outputs describe it.
		build.mainSocketGroup = index
		recalc()
		local mainSkill = build.calcsTab.mainEnv.player.mainSkill
		local skillName = mainSkill and mainSkill.activeEffect and mainSkill.activeEffect.grantedEffect.name
		local base = damageSnapshot()
		local primary = (base.CombinedDPS and "CombinedDPS") or "TotalDPS"
		local rows = {}
		for _, gem in ipairs(group.gemList) do
			local ge = gem.grantedEffect or (gem.gemData and gem.gemData.grantedEffect)
			if ge and ge.support and gem.enabled ~= false then
				disabledGem = gem
				gem.enabled = false
				recalc()
				local without = damageSnapshot()
				gem.enabled = true
				disabledGem = nil
				local loss = {}
				for k, v in pairs(base) do
					local w = without[k]
					if w and math.abs(v - w) > 1e-6 then
						loss[k] = { with = num(v), without = num(w), lost = num(v - w),
							pct = v ~= 0 and num((v - w) / math.abs(v) * 100) or nil }
					end
				end
				local row = {
					support = ge.name, level = gem.level, quality = gem.quality,
					primaryLost = loss[primary] and loss[primary].lost or 0,
				}
				if next(loss) then
					row.changes = loss
				else
					row.note = "no effect on this skill's damage with the current Configuration; "
						.. "it may need a condition that isn't enabled, or affect something other than damage"
				end
				table.insert(rows, row)
			end
		end
		table.sort(rows, function(a, b) return (tonumber(a.primaryLost) or 0) > (tonumber(b.primaryLost) or 0) end)
		local baseOut = {}
		for k, v in pairs(base) do baseOut[k] = num(v) end
		return {
			group = index, skill = skillName, primaryStat = primary, withAllSupports = baseOut,
			supports = rows,
			note = "Each row is the loss from removing that one support while keeping the others. "
				.. "Supports multiply each other, so the losses don't sum to the total.",
		}
	end)

	-- Always restore the main skill and recalc, even if something failed.
	if disabledGem then disabledGem.enabled = true end
	build.mainSocketGroup = origMain
	recalc()
	if not ok then error(result, 0) end
	return result
end

---------------------------------------------------------------------------
-- Hit distributions: the random draws behind PoB's average hit.
--
-- PoB folds every roll into an average. This recovers the pieces it averaged, from the
-- same calculation, so the API can sample whole hits:
--   * hit or miss                          HitChance
--   * crit or not (given a hit)            CritChance (PoB's effective chance)
--   * per damage type, a uniform roll in [min, max] after every multiplier and the
--     enemy's resistances/armour, separately for crits and non-crits
--   * per damage type, a lucky roll (best of two) with chance `lucky`
--   * double / triple damage               DoubleDamageChance, TripleDamageChance
-- Means of the reconstructed model equal PoB's AverageHit and AverageDamage exactly.
---------------------------------------------------------------------------

local DAMAGE_TYPES = { "Physical", "Lightning", "Cold", "Fire", "Chaos" }

-- Lucky chance that makes PoB's average of a [min, max] roll come out as `avg`:
-- avg = mid + lucky * (max - min) / 6, since a lucky roll averages min/3 + 2max/3.
local function luckyChance(min, max, avg)
	if max - min < 1e-9 then return 0 end
	local l = (avg - (min + max) / 2) / ((max - min) / 6)
	if l < 1e-6 then return 0 end
	return math.min(l, 1)
end

-- One hand (or the skill, for spells): its rolls as PoB modelled them.
local function rollModel(o)
	-- Stored min/max include PoB's expected double/triple damage multiplier; take it back
	-- out so those become their own draws.
	local ddChance = (o.DoubleDamageChance or 0) / 100
	local tdChance = (o.TripleDamageChance or 0) / 100
	local ddScale = 1 + ddChance + 2 * tdChance
	local types = {}
	for _, t in ipairs(DAMAGE_TYPES) do
		local entry = { type = t }
		for _, pass in ipairs({ { "hit", "StoredHit", "HitAverage" }, { "crit", "StoredCrit", "CritAverage" } }) do
			local key, stored, eff = pass[1], pass[2], pass[3]
			local min, max, avg = o[t .. stored .. "Min"], o[t .. stored .. "Max"], o[t .. stored .. "Avg"]
			if type(min) == "number" and type(avg) == "number" and avg > 0 then
				-- Enemy mitigation: PoB's effective average over its pre-mitigation average.
				local mitigation = (o[t .. eff] or avg) / avg
				entry[key] = {
					min = min * mitigation / ddScale,
					max = max * mitigation / ddScale,
					lucky = luckyChance(min, max, avg),
					mitigation = mitigation,
				}
			end
		end
		if entry.hit or entry.crit then table.insert(types, entry) end
	end
	return {
		hitChance = (o.HitChance or 100) / 100,
		critChance = (o.CritChance or 0) / 100,
		critMultiplier = o.CritMultiplier,
		doubleDamageChance = ddChance,
		tripleDamageChance = tdChance,
		types = types,
		pob = { averageHit = o.AverageHit, averageDamage = o.AverageDamage },
	}
end

-- args: { group = "Shred" | 1, config = { conditionSurrounded = true, ... } }
-- Config overrides apply only for this calculation; the build is restored afterwards.
function commands.hit_distribution(args)
	requireBuild()
	local group, index = findSkillGroup(args.group or build.mainSocketGroup)
	local input = build.configTab.input
	local known = {}
	for _, opt in ipairs(require("Modules.ConfigOptions")) do
		if opt.var then known[opt.var] = true end
	end
	local overrides = args.config or {}
	for var in pairs(overrides) do
		if not known[var] then error("unknown config var '" .. tostring(var) .. "'", 0) end
	end

	local origMain, saved = build.mainSocketGroup, {}
	local function recalc()
		build.configTab:BuildModList()
		build.buildFlag = true
		frame()
	end

	local ok, result = pcall(function()
		build.mainSocketGroup = index
		for var, value in pairs(overrides) do
			saved[var] = { value = input[var] }
			if value == json.null then value = nil end
			input[var] = value
		end
		recalc()
		local out = build.calcsTab.mainOutput
		local ms = build.calcsTab.mainEnv.player.mainSkill
		local hands = {}
		if out.MainHand or out.OffHand then
			-- A hand without an average hit doesn't attack with this skill.
			for _, h in ipairs({ { "Main Hand", out.MainHand }, { "Off Hand", out.OffHand } }) do
				if h[2] and type(h[2].AverageHit) == "number" and h[2].AverageHit > 0 then
					table.insert(hands, { hand = h[1], model = rollModel(h[2]) })
				end
			end
		elseif type(out.AverageHit) == "number" and out.AverageHit > 0 then
			table.insert(hands, { hand = "Skill", model = rollModel(out) })
		end
		local applied = {}
		for var in pairs(overrides) do applied[var] = num(input[var]) end
		return {
			group = index,
			skill = ms and ms.activeEffect and ms.activeEffect.grantedEffect.name,
			config = applied,
			hands = hands,
			pob = { averageHit = num(out.AverageHit), averageDamage = num(out.AverageDamage),
				totalDPS = num(out.TotalDPS), speed = num(out.Speed) },
		}
	end)

	for var, s in pairs(saved) do input[var] = s.value end
	build.mainSocketGroup = origMain
	recalc()
	if not ok then error(result, 0) end
	return result
end

-- Real PoE2 gem text from PoB's data. args: { name = "Shred" }
function commands.describe_skill(args)
	local want = tostring(args.name or ""):lower()
	local exact, partial = nil, {}
	for _, gem in pairs(data.gems) do
		local name = gem.name or (gem.grantedEffect and gem.grantedEffect.name)
		if name then
			if name:lower() == want then exact = gem break end
			if name:lower():find(want, 1, true) then table.insert(partial, name) end
		end
	end
	if not exact then
		table.sort(partial)
		error("no gem named '" .. tostring(args.name) .. "'" ..
			(#partial > 0 and ("; did you mean: " .. table.concat(partial, ", ", 1, math.min(#partial, 10))) or ""), 0)
	end
	local ge = exact.grantedEffect or {}
	local tags = {}
	for tag, on in pairs(exact.tags or {}) do if on then table.insert(tags, tag) end end
	table.sort(tags)
	return {
		name = exact.name, support = ge.support or false,
		description = ge.description and cleanText(ge.description) or nil,
		tags = tags,
		requirements = { str = exact.reqStr, dex = exact.reqDex, int = exact.reqInt },
	}
end

-- Full text (mods included) of equipped items. args: { slot = "Helmet" } or {} for all.
function commands.item_details(args)
	requireBuild()
	local out = {}
	for _, slot in ipairs(build.itemsTab.orderedSlots) do
		local item = build.itemsTab.items[slot.selItemId]
		if item and not slot.nodeId and (not args.slot or args.slot == slot.slotName) then
			local lines = {}
			for line in cleanText(item:BuildRaw()):gmatch("[^\n]+") do
				-- PoB tags mod lines like "{enchant}{rune}+18% ..."; keep the meaning, drop the syntax.
				local tags = {}
				line = line:gsub("{(%w+)}", function(t) table.insert(tags, t) return "" end)
				if #tags > 0 then line = line .. " (" .. table.concat(tags, ", ") .. ")" end
				-- Drop PoB bookkeeping lines that mean nothing to a reader.
				if not line:match("^Unique ID:") and not line:match("^Item Level:")
					and not line:match("^LevelReq:") and not line:match("^Implicits:") then
					table.insert(lines, line)
				end
			end
			table.insert(out, { slot = slot.slotName, name = item.name, text = table.concat(lines, "\n") })
		end
	end
	if args.slot and #out == 0 then
		requireSlot(args.slot)
		return { items = {}, note = "slot is empty" }
	end
	return { items = out }
end

-- args: { index = n }  (1-based socket group index from summary.skills)
function commands.set_main_skill(args)
	requireBuild()
	local i = tonumber(args.index)
	if not build.skillsTab.socketGroupList[i] then error("no skill group at index " .. tostring(args.index), 0) end
	build.mainSocketGroup = i
	build.buildFlag = true
	frame()
	return commands.summary()
end

-- What-if: swap an item into a slot without changing the build.
-- args: { slot = "Helmet", item = "<item text as copied from the game>" }
function commands.compare_item(args)
	requireBuild()
	local slot = requireSlot(args.slot)
	local item = parseItem(args.item)
	local calcFunc, base = build.calcsTab:GetMiscCalculator()
	local new = calcFunc({ repSlotName = slot.slotName, repItem = item }, true)
	local current = build.itemsTab.items[slot.selItemId]
	return {
		slot = slot.slotName,
		current = current and current.name or nil,
		candidate = item.name,
		changes = diffOutputs(base, new),
		card = buildCard(base, new),
	}
end

-- What-if: allocate and/or deallocate passive nodes without changing the build.
-- Adding a node also adds the shortest path to it; removing a node also removes
-- everything that depends on it (exactly what the in-game tree would do).
-- args: { add = {"Name" | id, ...}, remove = {...} }
function commands.compare_nodes(args)
	requireBuild()
	local addNodes, removeNodes = {}, {}
	local added, removed, skipped = {}, {}, {}
	for _, ref in ipairs(args.add or {}) do
		local n = findNode(ref)
		if n.alloc then
			table.insert(skipped, { node = n.dn, reason = "already allocated" })
		else
			if not n.path then error("node '" .. n.dn .. "' is not reachable from the current tree", 0) end
			for _, p in ipairs(n.path) do
				if not addNodes[p] then addNodes[p] = true; table.insert(added, p.dn) end
			end
		end
	end
	for _, ref in ipairs(args.remove or {}) do
		local n = findNode(ref)
		if not n.alloc then
			table.insert(skipped, { node = n.dn, reason = "not allocated" })
		else
			for _, d in ipairs(n.depends or { n }) do
				if not removeNodes[d] then removeNodes[d] = true; table.insert(removed, d.dn) end
			end
		end
	end
	local calcFunc, base = build.calcsTab:GetMiscCalculator()
	local new = calcFunc({
		addNodes = next(addNodes) and addNodes or nil,
		removeNodes = next(removeNodes) and removeNodes or nil,
	}, true)
	return {
		added = added, removed = removed, skipped = skipped,
		pointDelta = #added - #removed,
		changes = diffOutputs(base, new),
		card = buildCard(base, new),
	}
end

-- Rank unallocated passives by how much each (with its path) improves one stat.
-- Every candidate is evaluated independently against the current build.
-- args: { stat = "TotalDPS" | "DPS" (main skill total), query = "", types = {"Notable"}, max_cost = 5,
--         candidates = {names/ids} (overrides query/types), limit = 10,
--         also = {"Life", "TotalEHP"} (extra stats to report deltas for),
--         cards = true (attach a change preview card to each result) }
function commands.rank_nodes(args)
	requireBuild()
	local maxCost = tonumber(args.max_cost) or 5
	local also = args.also or { "Life", "EnergyShield", "TotalEHP" }
	local calcFunc, base = build.calcsTab:GetMiscCalculator()
	-- "DPS" means the main skill's total, whichever key holds it for this build (as on the card).
	local stat = args.stat or "TotalDPS"
	if stat == "DPS" then stat = dpsKey(base) end
	if type(base[stat]) ~= "number" then error("stat '" .. stat .. "' is not a numeric output", 0) end

	local pool = {}
	if args.candidates and #args.candidates > 0 then
		for _, ref in ipairs(args.candidates) do table.insert(pool, findNode(ref)) end
	else
		local types = {}
		for _, t in ipairs(args.types or { "Notable", "Keystone" }) do types[t] = true end
		local q = (args.query or ""):lower()
		for _, n in pairs(build.spec.nodes) do
			if types[n.type] and n.dn then
				local hay = n.dn:lower() .. "\n" .. table.concat(n.sd or {}, "\n"):lower()
				if q == "" or hay:find(q, 1, true) then table.insert(pool, n) end
			end
		end
	end

	local results, evaluated, skipped = {}, 0, 0
	for _, n in ipairs(pool) do
		if n.alloc or not n.path or #n.path > maxCost or evaluated >= 300 then
			skipped = skipped + 1
		else
			evaluated = evaluated + 1
			local add = {}
			for _, p in ipairs(n.path) do add[p] = true end
			local out = calcFunc({ addNodes = add }, true)
			local d = (out[stat] or 0) - base[stat]
			local extra = {}
			for _, k in ipairs(also) do
				if type(base[k]) == "number" and type(out[k]) == "number" and math.abs(out[k] - base[k]) > 0.001 then
					extra[k] = num(out[k] - base[k])
				end
			end
			table.insert(results, {
				name = n.dn, id = n.id, type = n.type, cost = #n.path,
				delta = num(d), pct = base[stat] ~= 0 and num(d / math.abs(base[stat]) * 100) or nil,
				perPoint = num(d / #n.path),
				otherDeltas = next(extra) and extra or nil,
				stats = n.sd,
				path = args.cards and (function()
					local names = {}
					for i = #n.path, 1, -1 do table.insert(names, n.path[i].dn) end
					return names
				end)() or nil,
				card = args.cards and buildCard(base, out) or nil,
			})
		end
	end
	table.sort(results, function(a, b) return (tonumber(a.delta) or 0) > (tonumber(b.delta) or 0) end)
	local top = {}
	for i = 1, math.min(#results, tonumber(args.limit) or 10) do top[i] = results[i] end
	return { stat = stat, baseline = num(base[stat]), evaluated = evaluated, skipped = skipped, results = top }
end

-- Search the passive tree by name or stat text.
-- args: { query = "life", types = {"Notable","Keystone"}, reachable_only = true,
--         allocated = true|false (omit for either), limit = 25 }
function commands.search_nodes(args)
	requireBuild()
	local q = (args.query or ""):lower()
	local types
	if args.types then
		types = {}
		for _, t in ipairs(args.types) do types[t] = true end
	end
	local hits = {}
	for _, n in pairs(build.spec.nodes) do
		if n.dn and (not types or types[n.type]) and n.type ~= "ClassStart" and n.type ~= "AscendClassStart"
			and (not args.reachable_only or n.alloc or n.path)
			and (args.allocated == nil or args.allocated == json.null or (n.alloc or false) == args.allocated) then
			local hay = n.dn:lower()
			if n.sd then hay = hay .. "\n" .. table.concat(n.sd, "\n"):lower() end
			if q == "" or hay:find(q, 1, true) then table.insert(hits, n) end
		end
	end
	table.sort(hits, function(a, b)
		local da = a.alloc and 0 or (a.path and #a.path or 1e9)
		local db = b.alloc and 0 or (b.path and #b.path or 1e9)
		if da ~= db then return da < db end
		return a.dn < b.dn
	end)
	local out = {}
	for i = 1, math.min(#hits, args.limit or 25) do table.insert(out, nodeInfo(hits[i])) end
	return { total = #hits, nodes = out }
end

-- Commit tree changes. args: { add = {...}, remove = {...} }
function commands.apply_nodes(args)
	requireBuild()
	local spec = build.spec
	for _, ref in ipairs(args.remove or {}) do
		local n = findNode(ref)
		if n.alloc then spec:DeallocNode(n) end
	end
	for _, ref in ipairs(args.add or {}) do
		local n = findNode(ref)
		if not n.alloc then
			if not n.path then error("node '" .. n.dn .. "' is not reachable", 0) end
			spec:AllocNode(n)
		end
	end
	spec:AddUndoState()
	build.buildFlag = true
	frame()
	return commands.summary()
end

-- Commit an item into a slot. args: { slot = "Helmet", item = "<item text>" }
function commands.equip_item(args)
	requireBuild()
	local slot = requireSlot(args.slot)
	local item = parseItem(args.item)
	build.itemsTab:AddItem(item, true)
	slot:SetSelItemId(item.id)
	build.itemsTab:PopulateSlots()
	build.itemsTab:AddUndoState()
	build.buildFlag = true
	frame()
	return commands.summary()
end

-- Config options (the Configuration tab). args: { query = "shock", set_only = false }
function commands.list_config(args)
	requireBuild()
	local input = build.configTab.input
	local q = (args.query or ""):lower()
	local out = {}
	for _, opt in ipairs(require("Modules.ConfigOptions")) do
		if opt.var and opt.label then
			local label = cleanText(opt.label)
			local value = input[opt.var]
			if (not args.set_only or value ~= nil)
				and (q == "" or label:lower():find(q, 1, true) or opt.var:lower():find(q, 1, true)) then
				local entry = { var = opt.var, label = label, type = opt.type, value = num(value),
					requires = opt.ifOption, tooltip = opt.tooltip and cleanText(opt.tooltip) or nil }
				if opt.list then
					entry.choices = {}
					for _, c in ipairs(opt.list) do table.insert(entry.choices, { value = c.val, label = c.label }) end
				end
				table.insert(out, entry)
			end
		end
	end
	return { options = out }
end

-- args: { var = "conditionStationary", value = true }  (value = null clears it)
function commands.set_config(args)
	requireBuild()
	local known = false
	for _, opt in ipairs(require("Modules.ConfigOptions")) do
		if opt.var == args.var then known = true break end
	end
	if not known then error("unknown config var '" .. tostring(args.var) .. "'", 0) end
	local value = args.value
	if value == json.null then value = nil end
	build.configTab.input[args.var] = value
	build.configTab:UpdateControls()
	build.configTab:BuildModList()
	build.buildFlag = true
	frame()
	return { var = args.var, value = num(build.configTab.input[args.var]), stats = currentStats() }
end

-- Timing probe for optimizer planning: how long does one what-if node eval take?
-- args: { samples = 50 }
function commands.benchmark(args)
	requireBuild()
	local calcFunc = build.calcsTab:GetMiscCalculator()
	local candidates = {}
	for _, n in pairs(build.spec.nodes) do
		if not n.alloc and n.path and #n.path == 1 and n.type ~= "Mastery" then table.insert(candidates, n) end
	end
	local samples = math.min(args.samples or 50, #candidates)
	local t0 = os.clock()
	for i = 1, samples do calcFunc({ addNodes = { [candidates[i]] = true } }, true) end
	local elapsed = os.clock() - t0
	return { samples = samples, total_ms = elapsed * 1000, per_eval_ms = samples > 0 and elapsed * 1000 / samples or nil }
end

---------------------------------------------------------------------------
-- Main loop
---------------------------------------------------------------------------

local function send(msg)
	protocolOut:write(json.encode(msg), "\n")
	protocolOut:flush()
end

send({ id = json.null, ok = true, result = { ready = true, boot_seconds = bootSeconds } })

for line in io.stdin:lines() do
	if line:match("%S") then
		local req, _, decodeErr = json.decode(line, 1, json.null)
		if not req then
			send({ id = json.null, ok = false, error = "invalid JSON: " .. tostring(decodeErr) })
		else
			local handler = commands[req.cmd]
			if not handler then
				send({ id = req.id, ok = false, error = "unknown command: " .. tostring(req.cmd) })
			else
				local ok, result = xpcall(function() return handler(req.args or {}) end, debug.traceback)
				if ok then
					send({ id = req.id, ok = true, result = result })
				else
					-- Errors raised with level 0 are user-facing; keep the first line only.
					send({ id = req.id, ok = false, error = tostring(result):match("^[^\n]*") })
					stderr:write(tostring(result), "\n")
				end
			end
		end
	end
end
