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

-- Skill flags of an actor's main skill. PoE1 PoB keeps them on mainSkill; PoE2 PoB
-- keeps them on the active effect's stat set.
local function skillFlags(actor)
	local ms = actor and actor.mainSkill
	if not ms then return {} end
	if ms.skillFlags then return ms.skillFlags end
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
			table.insert(gems, {
				name = gem.grantedEffect and gem.grantedEffect.name or gem.nameSpec,
				level = gem.level, quality = gem.quality,
				enabled = gem.enabled ~= false,
				support = gem.grantedEffect and gem.grantedEffect.support or false,
			})
		end
		local active = group.displaySkillList and group.displaySkillList[group.mainActiveSkill or 1]
		local activeName = active and active.activeEffect and active.activeEffect.grantedEffect.name
		if not activeName then
			-- displaySkillList isn't always populated (e.g. PoE2 non-main groups): use the first active gem.
			for _, g in ipairs(gems) do
				if not g.support and g.enabled then activeName = g.name break end
			end
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

-- Sidebar stats, or specific raw output keys: { keys = {"Life", "FireResist"} }
function commands.stats(args)
	requireBuild()
	if args.keys then
		local out = {}
		for _, k in ipairs(args.keys) do
			local v = build.calcsTab.mainOutput[k]
			out[k] = type(v) == "table" and "<table>" or num(v)
		end
		return out
	end
	return currentStats()
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
	}
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
