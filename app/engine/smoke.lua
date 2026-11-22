-- Smoke test: boot PoB headless, load a build, print key stats.
-- Usage (from repo root):  app/engine/pob.sh smoke.lua <build.xml>
local buildPath = arg[1]  -- capture before PoB overwrites the global `arg`

local t0 = os.clock()
dofile("HeadlessWrapper.lua")
print(string.format("engine booted in %.1fs", os.clock() - t0))

local f = assert(io.open(buildPath, "r"))
local xml = f:read("*a")
f:close()
t0 = os.clock()
loadBuildFromXML(xml, "smoke")
print(string.format("build loaded in %.1fs\n", os.clock() - t0))

local o = build.calcsTab.mainOutput
print("Class:   " .. tostring(build.spec.curClassName) .. " / " .. tostring(build.spec.curAscendClassName))
print("Level:   " .. tostring(build.characterLevel))
for _, k in ipairs({"Life","EnergyShield","Mana","TotalDPS","FireResist","ColdResist","LightningResist","ChaosResist","Armour","Evasion","TotalEHP"}) do
	print(string.format("%-16s %s", k, tostring(o[k])))
end
