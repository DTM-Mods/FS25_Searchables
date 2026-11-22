"""Offline tests of FS25_Searchables: loads the mod's real Lua files (in modDesc.xml order) into Lua 5.1 through lupa,
with small stubs for the game engine, and exercises the search logic, the map search and the GUI hooks.

Usage: python tests/test_offline.py            (needs: pip install lupa)
       python tests/test_offline.py map        only tests with "map" in their name
Exit code 0 when every test passes. Every test gets a fresh Lua state.
SEARCHABLES_ROOT=<folder> tests another copy of the mod (an unzipped build) instead of this repository."""
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

import lupa.lua51 as lua51   # Lua 5.1 like the game; plain lupa.LuaRuntime is a newer Lua

ROOT = os.environ.get("SEARCHABLES_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="searchables_test_")
# GIANTS Lua has `continue`, Lua 5.1 doesn't: same replacement as .github/ci/fs25_mod.py (keeps line numbers)
CONTINUE_RE = re.compile(r"(?<![\w.:'\"])continue(?![\w])")

MODDESC = ET.parse(os.path.join(ROOT, "modDesc.xml")).getroot()
SCRIPTS = [e.get("filename") for e in MODDESC.find("extraSourceFiles")]
ACTIONS = [e.get("name") for e in MODDESC.find("actions")]
TEXTS = {e.get("name"): e.get("text")
         for e in ET.parse(os.path.join(ROOT, "translations", "translation_en.xml")).getroot().iter("text")}


def read(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


# ------------------------------------------------------------------------------------------------ engine stubs (Lua)
STUBS = r'''
g_currentModName = "FS25_Searchables"
g_time = 0
CALLS, LOG, DRAWN, EVENTS, CONSOLE, LISTENERS = {}, {}, {}, {}, {}, {}
function hit(name) CALLS[name] = (CALLS[name] or 0) + 1 end
function calls(name) return CALLS[name] or 0 end

-- test helpers
function eq(actual, expected, what)
    if actual ~= expected then
        error(string.format("%s: expected <%s>, got <%s>", what or "value", tostring(expected), tostring(actual)), 2)
    end
end
function near(actual, expected, what)
    if type(actual) ~= "number" or math.abs(actual - expected) > 1e-6 then
        error(string.format("%s: expected about %s, got %s", what or "value", tostring(expected), tostring(actual)), 2)
    end
end
function names(list, field)
    local t = {}
    for i, entry in ipairs(list) do t[i] = tostring(entry[field or "name"]) end
    return table.concat(t, " | ")
end
function logged(text)
    for _, line in ipairs(LOG) do if line:find(text, 1, true) then return true end end
    return false
end

local function logger(kind)
    return function(fmt, ...) table.insert(LOG, kind .. ": " .. string.format(fmt, ...)) end
end
Logging = {info = logger("info"), warning = logger("warning"), error = logger("error")}

Utils = {
    getFilename = function(name, dir) return dir .. name end,
    appendedFunction = function(old, new) return function(...) if old ~= nil then old(...) end; return new(...) end end,
    overwrittenFunction = function(old, new) return function(self, ...) return new(self, old, ...) end end,
    limitTextToWidth = function(text) return text end,
}
function string.trim(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end
function math.clamp(v, low, high) return math.min(math.max(v, low), high) end
function utf8Strlen(s) return pyLen(s) end
function utf8ToLower(s) return pyLower(s) end
MathUtil = {vector2Length = function(x, z) return math.sqrt(x * x + z * z) end}
function getNormalizedScreenValues(x, y) return x / 1920, y / 1080 end

-- Class(): what the game's classes have (class, superClass, isa)
function Class(members, parent)
    members = members or {}
    if parent ~= nil then setmetatable(members, {__index = parent}) end
    members.class = function() return members end
    members.superClass = function() return parent end
    members.isa = function(self, other)
        local c = self:class()
        while c ~= nil do
            if c == other then return true end
            c = c.superClass()
        end
        return false
    end
    return {__index = members}
end
-- a game class with the given functions; functions listed by name only count their calls
function gameClass(name, functions, parent)
    local class = {}
    local mt = Class(class, parent)
    class.new = function(fields) return setmetatable(fields or {}, mt) end
    for key, value in pairs(functions or {}) do
        if type(key) == "number" then
            class[value] = function() hit(name .. "." .. value) end
        else
            class[key] = value
        end
    end
    _G[name] = class
    return class
end

-- GUI elements: just the tree, visibility, position and text
El = {}
El.__index = El
function El.new(t)
    t = t or {}
    t.elements = t.elements or {}
    t.absPosition = t.absPosition or {0, 0}
    t.absSize = t.absSize or {0, 0}
    if t.visible == nil then t.visible = true end
    for _, child in ipairs(t.elements) do child.parent = t end
    return setmetatable(t, El)
end
function El:addElement(child) child.parent = self; table.insert(self.elements, child) end
function El:delete()
    if self.parent ~= nil then
        for i, child in ipairs(self.parent.elements) do
            if child == self then table.remove(self.parent.elements, i); break end
        end
    end
    self.parent = nil
    self.isDeleted = true
end
function El:getIsVisible()
    local element = self
    while element ~= nil do
        if element.visible == false then return false end
        element = element.parent
    end
    return true
end
function El:setVisible(visible) self.visible = visible end
function El:getDescendants(predicate, found)
    found = found or {}
    for _, child in ipairs(self.elements) do
        if predicate == nil or predicate(child) then table.insert(found, child) end
        child:getDescendants(predicate, found)
    end
    return found
end
function El:getDescendantById(id) return self:getDescendants(function(e) return e.id == id end)[1] end
function El:updateAbsolutePosition() end
function El:setAbsolutePosition(x, y)
    local dx, dy = x - self.absPosition[1], y - self.absPosition[2]
    self.absPosition = {x, y}
    for _, child in ipairs(self:getDescendants()) do
        child.absPosition = {child.absPosition[1] + dx, child.absPosition[2] + dy}
    end
end
function El:setText(text) self.text = text end
function El:getText() return self.text or "" end
function El:setForcePressed(pressed) self.forcePressed = pressed; self.isCapturingInput = pressed end   -- TextInput
function El:openIme() self.imeOpened = true end
function El:reloadData() self.reloads = (self.reloads or 0) + 1 end
function El:update(dt) end

local function buildElement(node, target)
    local element = El.new({typeName = node.tag, id = node.attrs.id, profile = node.attrs.profile, text = node.attrs.text,
        target = target, absSize = {0.15, 0.03}})
    if node.tag == "TextInput" then element.text = "" end
    for _, child in ipairs(node.children) do element:addElement(buildElement(child, target)) end
    return element
end
g_gui = {
    screens = {},
    loadProfiles = function(self, filename) hit("loadProfiles"); PROFILES_LOADED = filename end,
    -- builds the elements of the mod's real gui xml (pyXmlNodes) under the parent
    loadGuiRec = function(self, xmlFile, key, parent, target)
        for _, node in ipairs(pyXmlNodes(xmlFile)) do parent:addElement(buildElement(node, target)) end
    end,
    getIsDialogVisible = function() return false end,
    getIsGuiVisible = function() return GUI_VISIBLE == true end,
    changeScreen = function(self, source, screenClass) hit("changeScreen"); CHANGED_SCREEN = screenClass end,
}
Input = {MOUSE_BUTTON_LEFT = 1}
RenderText = {ALIGN_LEFT = 0, ALIGN_CENTER = 1, ALIGN_RIGHT = 2}
function setTextAlignment() end
function setTextColor() end
function drawFilledRect() hit("drawFilledRect") end
function renderText(x, y, size, text) table.insert(DRAWN, text) end
function getTextWidth(size, text) return size * #text * 0.5 end
Overlay = {}
Overlay.__index = Overlay
function Overlay.new(filename) return setmetatable({filename = filename}, Overlay) end
function Overlay:setColor() end
function Overlay:setDimension() end
function Overlay:setPosition(x, y) self.x, self.y = x, y end
function Overlay:render() hit("overlayRender") end
function Overlay:delete() end

-- input: the actions come from modDesc.xml (INPUT_ACTIONS is set by the test)
g_inputBinding = {
    lastInputMode = 1,
    registerActionEvent = function(self, action, target, callback)
        table.insert(EVENTS, {action = action, target = target, callback = callback})
        return true, #EVENTS
    end,
    setActionEventTextVisibility = function(self, eventId, isVisible) EVENTS[eventId].textVisible = isVisible end,
}
GS_INPUT_HELP_MODE_KEYBOARD, GS_INPUT_HELP_MODE_GAMEPAD = 1, 2
-- runs the action event registered last, as the game does on the key press
function fireSearchAction() local event = EVENTS[#EVENTS]; event.callback(event.target) end
function addModEventListener(listener) table.insert(LISTENERS, listener) end
function addConsoleCommand(name, description, functionName, target) CONSOLE[name] = {functionName = functionName, target = target} end
function removeConsoleCommand(name) CONSOLE[name] = nil end

g_i18n = {
    getText = function(self, key)
        local text = TEXTS[key]
        if text == nil then error("text '" .. tostring(key) .. "' is not in translations/translation_en.xml") end
        return text
    end,
    getPower = function(self, kw) return kw * 1.36 end,
    formatMoney = function(self, value) return string.format("$%d", value) end,
}

-- store
StoreSpecies = {VEHICLE = 1, PLACEABLE = 2, HANDTOOL = 3}
STORE_ITEMS, CATEGORIES, BRANDS, FILL_TYPES = {}, {}, {}, {}
local function specType(name)
    return {getValueFunc = function(storeItem, realItem, configurations, saleItem, returnValues, returnRange)
        if not (returnValues and returnRange) then error("spec function called without returnValues / returnRange") end
        local value = storeItem.specs[name]
        if type(value) == "table" then return value[1], value[2] end
        return value
    end}
end
local specTypes = {power = specType("power"), weight = specType("weight"), capacity = specType("capacity")}
g_storeManager = {
    getItems = function() return STORE_ITEMS end,
    getCategoryByName = function(self, name) return CATEGORIES[name] end,
    getSpecTypeByName = function(self, name) return specTypes[name] end,
    getIsItemUnlocked = function(self, storeItem) return not storeItem.locked end,
    getItemByXMLFilename = function(self, filename)
        for _, storeItem in ipairs(STORE_ITEMS) do if storeItem.xmlFilename == filename then return storeItem end end
    end,
}
StoreItemUtil = {loadSpecsFromXML = function(storeItem) hit("loadSpecs"); storeItem.specs = storeItem.lazySpecs end}
g_brandManager = {getBrandByIndex = function(self, index) return BRANDS[index] end}
g_modManager = {nameToMod = {}}
g_shopController = {makeDisplayItem = function(self, storeItem) hit("makeDisplayItem"); return {storeItem = storeItem} end}
FillType = {UNKNOWN = 1}
g_fillTypeManager = {
    getFillTypes = function() return FILL_TYPES end,
    getFillTypeByIndex = function(self, index) return FILL_TYPES[index] end,
    getFillTypeNameByIndex = function(self, index) return FILL_TYPES[index] ~= nil and FILL_TYPES[index].name or nil end,
    getFillTypeByName = function(self, name)
        for _, fillType in ipairs(FILL_TYPES) do if fillType.name == name then return fillType end end
    end,
}

-- world
NODES, HIDDEN_NODES, MAP_HOTSPOTS = {}, {}, {}
function getWorldTranslation(node) local p = NODES[node]; return p[1], p[2], p[3] end
function entityExists(node) return NODES[node] ~= nil end
function getVisibility(node) return not HIDDEN_NODES[node] end
VehiclePropertyState = {OWNED = 1, LEASED = 2, SHOP_CONFIG = 3}
FarmManager = {SPECTATOR_FARM_ID = 0}
FarmlandManager = {NO_OWNER_FARM_ID = 0}
AccessHandler = {EVERYONE = 0}
MissionStatus = {CREATED = 1, RUNNING = 2, FINISHED = 3}
FruitType = {UNKNOWN = 0}
gameClass("Bale")
gameClass("MapHotspot", {"delete",
    getClickCircle = function() return {} end,
    setWorldPosition = function(self, x, z) self.worldX, self.worldZ = x, z end,
    getWorldPosition = function(self) return self.worldX, self.worldZ end,
    getIsVisible = function(self) return self.visible ~= false end})
MapHotspot.CATEGORY_OTHER = 9
MapHotspot.new = function(mt) return setmetatable({}, mt) end
g_currentMission = {
    missionInfo = {},
    getFarmId = function() return 1 end,
    getMoney = function() return 75000 end,
    addMapHotspot = function(self, hotspot) table.insert(MAP_HOTSPOTS, hotspot) end,
    removeMapHotspot = function(self, hotspot)
        for i, other in ipairs(MAP_HOTSPOTS) do if other == hotspot then table.remove(MAP_HOTSPOTS, i); break end end
    end,
    setMapTargetHotspot = function(self, hotspot) MAP_TARGET = hotspot end,
}
g_farmlandManager = {getFarmlands = function() return {} end, getFarmlandOwner = function() return 0 end,
    getFarmlandAtWorldPosition = function() return nil end}
g_fieldManager = {fields = {}}

-- the game classes the mod hooks
gameClass("Gui", {"update", "draw", "mouseEvent"})
gameClass("HelpLineManager", {loadMapData = function() hit("HelpLineManager.loadMapData"); return true end,
    loadFromXML = function(self, filename) HELP_LOADED = filename end})
gameClass("ShopMenu", {"update", "assignMenuButtonInfo", "updateButtonsPanel",
    pushDetail = function(self, frame) hit("ShopMenu.pushDetail"); self.currentPage = frame end,
    popDetail = function(self) hit("ShopMenu.popDetail"); self.currentPage = self.tilesPage end})
gameClass("InGameMenu", {"assignMenuButtonInfo", goToPage = function(self, page) self.wentToPage = page end})
gameClass("ShopItemsFrame", {"resetListSelection",
    setDisplayItems = function(self, items, ...) hit("ShopItemsFrame.setDisplayItems"); self.shown = items; self.shownArgs = {...} end,
    setCategory = function(self, name, title) self.categoryName, self.categoryTitle = name, title end})
gameClass("ShopCategoriesFrame")
gameClass("ConstructionScreen", {"rebuildData", "update", "registerMenuActionEvents", "updateMenuState"})
-- the controls page: sections and rows come from self.sections = {{title, rows = {{name, keys}}}}
gameClass("InGameMenuSettingsFrame", {"update", "setupControlsView",
    getNumberOfSections = function(self, list) return #self.sections end,
    getNumberOfItemsInSection = function(self, list, section) return #self.sections[section].rows end,
    getTitleForSectionHeader = function(self, list, section) return self.sections[section].title end,
    populateCellForItemInSection = function(self, list, section, index, cell)
        local row = self.sections[section].rows[index]
        cell.elements[1]:setText(row[1])
        cell.elements[2]:setText(row[2])
    end})
gameClass("PlayerInputComponent", {"registerGlobalPlayerActionEvents"})
gameClass("IngameMapElement", {"draw", "update", "registerActionEvents",
    mouseEvent = function() hit("IngameMapElement.mouseEvent"); return "game" end,
    getIsActive = function() return true end,
    panToHotspot = function(self, hotspot) self.pannedTo = hotspot end})

-- what parseQuery made of a text, as one line: "w[fendt] f[power>200,price<=50000] x[mod]"
function parsed(text)
    local query = Searchables.parseQuery(text)
    local filters = {}
    for i, f in ipairs(query.filters) do
        filters[i] = f.kind .. (f.min ~= nil and ((f.minStrict and ">" or ">=") .. tostring(f.min)) or "")
            .. (f.max ~= nil and ((f.maxStrict and "<" or "<=") .. tostring(f.max)) or "")
    end
    return string.format("w[%s] f[%s] x[%s]", table.concat(query.words, ","), table.concat(filters, ","), table.concat(query.excludes, ","))
end
'''

# ------------------------------------------------------------------------------------------------ a small shop (Lua)
STORE = r'''
local function fillType(name, title, extra)
    local t = extra or {}
    t.name, t.title, t.index = name, title, #FILL_TYPES + 1
    table.insert(FILL_TYPES, t)
    FillType[name] = t.index
end
FILL_TYPES[1] = {name = "UNKNOWN", title = "Unknown", index = 1, showOnPriceTable = false}
fillType("WHEAT", "Wheat"); fillType("WOOL", "Wool"); fillType("DIESEL", "Diesel", {showOnPriceTable = false})
fillType("FLOUR", "Flour"); fillType("PEA", "Peas"); fillType("LIME", "Lime"); fillType("STRAW", "Straw"); fillType("BREAD", "Bread")
fillType("ELECTRICCHARGE", "Electric charge", {showOnPriceTable = false}); fillType("METHANE", "Methane")

BRANDS = {{title = "Fendt"}, {title = "Krone"}}
CATEGORIES = {TRACTORSL = {title = "Large Tractors"}, TRAILERS = {title = "Trailers"}, SILOS = {title = "Silos"},
    HARVESTERS = {title = "Harvesters"}}
g_modManager.nameToMod.FS25_Pack = {title = "Super Pack", author = "Jane Modder"}

ITEM = {}
local V, P, H = StoreSpecies.VEHICLE, StoreSpecies.PLACEABLE, StoreSpecies.HANDTOOL
local function item(key, t)
    t.xmlFilename = key .. ".xml"
    if t.showInStore == nil then t.showInStore = true end
    ITEM[key] = t
    table.insert(STORE_ITEMS, t)
end
item("fendt724", {name = "724 Vario", species = V, brandIndex = 1, categoryName = "TRACTORSL", price = 250000,
    specs = {power = {150, 237}, weight = 9.5, maxSpeed = 50}})
item("fendt1050", {name = "1050 Vario", species = V, brandIndex = 1, categoryName = "TRACTORSL", price = 450000,
    specs = {power = 517, weight = 14, maxSpeed = 60}})
item("former", {name = "Fendt Former", species = V, brandIndex = 1, categoryName = "TEDDERS", price = 30000,
    specs = {workingWidth = {width = 12.5, minWidth = 10}, neededPower = {base = 60}, speedLimit = 20}})
item("trailer", {name = "TX 560", species = V, brandIndex = 2, categoryNames = {"TRAILERS"}, price = 60000,
    specs = {capacity = {30000, 56000}}, isMod = true, customEnvironment = "FS25_Pack"})
item("x9", {name = "X9 1100", species = V, categoryName = "HARVESTERS", price = 900000, specs = {power = 700}})
item("broken", {name = "Broken Plow", species = V, price = 10000})                       -- its specs can't be read
item("lazy", {name = "Lazy Loader", species = V, price = 20000, lazySpecs = {power = 100}})  -- specs read on demand
item("chainsaw", {name = "Chainsaw", species = H, price = 500})
item("hidden", {name = "Hidden Fendt", species = V, price = 1, showInStore = false})
item("bundle", {name = "Fendt Bundle", species = V, price = 1, isBundleItem = true})
item("locked", {name = "Locked Fendt", species = V, price = 1, locked = true})
item("silo", {name = "Grain Silo", species = P, categoryName = "SILOS", price = 45000, functions = {"Keeps your harvest dry"},
    specs = {}, brush = {category = {title = "Buildings"}, tab = {title = "Storage"}}})
item("bakery", {name = "Bakery", species = P, price = 80000,
    specs = {prodPointInputFillTypes = {FLOUR = true}, prodPointOutputFillTypes = {"BREAD"}}})
item("cannery", {name = "Cannery", species = P, price = 120000, lazySpecs = {prodPointInputFillTypes = {FillType.PEA}}})
'''

# ------------------------------------------------------------------------------------------------ a small farm (Lua)
# Farm 1 is ours, the camera is at 0,0. Needs STORE.
WORLD = r'''
local nextNode = 100
local function node(x, z) nextNode = nextNode + 1; NODES[nextNode] = {x, 0, z}; return nextNode end
NODES[900] = {0, 0, 0}
g_cameraManager = {getActiveCamera = function() return 900 end}

local function vehicle(t)
    t.rootNode = node(t.x, t.z)
    t.propertyState = t.propertyState or VehiclePropertyState.OWNED
    t.getOwnerFarmId = function() return t.farm or 1 end
    if t.fullName ~= nil then t.getFullName = function() return t.fullName end end
    if t.fill ~= nil then   -- {fill type, liters, capacity}
        t.getFillUnitFillType = function() return t.fill[1] end
        t.getFillUnitFillLevel = function() return t.fill[2] end
        t.getFillUnitCapacity = function() return t.fill[3] end
    end
    if t.isMotorized then
        t.getConsumerFillUnitIndex = function(self, fillTypeIndex) return fillTypeIndex == FillType.DIESEL and 1 or nil end
        t.getDamageAmount = function() return t.damage or 0 end
        t.getDirtAmount = function() return t.dirt or 0 end
        t.getIsAIActive = function() return t.helper == true end
        t.getIsMotorStarted = function() return t.helper == true end
    end
    return t
end
VEHICLE = {
    tractor = vehicle({fullName = "Fendt 724 Vario", typeName = "tractor", configFileName = "fendt724.xml", x = 100, z = 0,
        isMotorized = true, fill = {FillType.DIESEL, 40, 400}, damage = 0.5, dirt = 0.1,
        spec_licensePlates = {licensePlateData = {characters = {"A", "B", "-", "1", "2", "3"}}}}),
    harvester = vehicle({fullName = "John Deere X9", typeName = "combineDrivable", configFileName = "x9.xml", x = 300, z = 400,
        isMotorized = true, fill = {FillType.DIESEL, 800, 1000}, helper = true, propertyState = VehiclePropertyState.LEASED}),
    theirs = vehicle({fullName = "Fendt 900 Neighbour", typeName = "tractor", x = 5, z = 5, farm = 2}),
    preview = vehicle({fullName = "Fendt Shop Preview", typeName = "tractor", x = 6, z = 6, propertyState = VehiclePropertyState.SHOP_CONFIG}),
    woolBig = vehicle({isPallet = true, typeName = "pallet", spec_pallet = {fillUnitIndex = 1}, fill = {FillType.WOOL, 1500, 2000}, x = 20, z = 0}),
    woolSmall = vehicle({isPallet = true, typeName = "pallet", spec_pallet = {fillUnitIndex = 1}, fill = {FillType.WOOL, 500, 2000}, x = 10, z = 0}),
    limeBag = vehicle({typeName = "bigBag", spec_bigBag = {fillUnitIndex = 1}, fill = {FillType.LIME, 1000, 1000}, x = 30, z = 0}),
    emptyPallet = vehicle({typeName = "treeSaplingPallet", x = 40, z = 0}),
    theirPallet = vehicle({isPallet = true, typeName = "pallet", fill = {FillType.WOOL, 900, 2000}, x = 1, z = 1, farm = 2}),
}
g_currentMission.vehicleSystem = {vehicles = {VEHICLE.tractor, VEHICLE.harvester, VEHICLE.theirs, VEHICLE.preview, VEHICLE.woolBig,
    VEHICLE.woolSmall, VEHICLE.limeBag, VEHICLE.emptyPallet, VEHICLE.theirPallet}}

local function bale(x, z, liters, extra)
    local b = Bale.new(extra or {})
    b.nodeId = node(x, z)
    b.getOwnerFarmId = function() return 1 end
    b.getFillType = function() return FillType.STRAW end
    b.getFillLevel = function() return liters end
    return b
end
BALE = {round = bale(-100, 10, 4000, {isRoundbale = true}), hiddenInStorage = bale(0, 60, 4000)}
HIDDEN_NODES[BALE.hiddenInStorage.nodeId] = true
g_currentMission.itemSystem = {itemsToSave = {[BALE.round] = true, [BALE.hiddenInStorage] = true, [{notABale = true}] = true}}
g_currentMission.economyManager = {getPricePerLiter = function(self, fillTypeIndex)
    return ({[FillType.WOOL] = 1, [FillType.STRAW] = 0.1, [FillType.LIME] = 0.2})[fillTypeIndex] or 0
end}

local function placeable(name, typeName, x, z, farm, extra)
    local p = extra or {}
    p.typeName = typeName
    p.rootNode = node(x, z)
    p.getOwnerFarmId = function() return farm end
    p.getName = function() return name end
    return p
end
PLACEABLE = {
    silo = placeable("Grain Silo", "silo", 500, 0, 1, {storeItem = ITEM.silo}),
    barn = placeable("Cow Barn", "cowHusbandry", 600, 0, 1, {spec_husbandryAnimals = {},
        getNumOfAnimals = function() return 12 end,
        getConditionInfos = function() return {{title = "Water", ratio = 0.1}, {title = "Straw", ratio = 0.9},
            {title = "Cleanliness", ratio = 0.9, invertedBar = true}} end,
        getFoodInfos = function() return {{title = "Hay", ratio = 0.5}} end}),
    bakery = placeable("Bakery", "productionPoint", 700, 0, 1, {spec_productionPoint = {productionPoint = {
        productions = {{inputs = {{type = FillType.FLOUR, amount = 10}}, outputs = {{type = FillType.BREAD, amount = 5}}}},
        storage = {getFillLevel = function(self, i) return i == FillType.BREAD and 990 or 0 end,
            getCapacity = function() return 1000 end}}}}),
    elevator = placeable("Grain Elevator", "sellingStation", 800, 0, AccessHandler.EVERYONE),
    plant = placeable("Ready Mix Plant", "decoration", 850, 0, 1),
    theirs = placeable("Neighbour Shed", "shed", 2, 2, 2),
}
g_currentMission.placeableSystem = {placeables = {PLACEABLE.silo, PLACEABLE.barn, PLACEABLE.bakery, PLACEABLE.elevator,
    PLACEABLE.plant, PLACEABLE.theirs}}

local function station(name, x, z, prices, extra)
    local s = extra or {}
    if s.isSellingPoint == nil then s.isSellingPoint = true end
    s.rootNode = node(x, z)
    s.acceptedFillTypes = {}
    for fillTypeIndex in pairs(prices) do s.acceptedFillTypes[fillTypeIndex] = true end
    s.getEffectiveFillTypePrice = function(self, fillTypeIndex) return prices[fillTypeIndex] end
    s.getName = function() return name end
    return s
end
local function storage(farm, levels, owner)
    return {getOwnerFarmId = function() return farm end, getFillLevels = function() return levels end,
        unloadingStations = {[{}] = {owningPlaceable = owner}}}
end
g_currentMission.storageSystem = {
    getUnloadingStations = function() return {
        station("Grain Elevator", 800, 0, {[FillType.WHEAT] = 0.25, [FillType.WOOL] = 1.5}),
        station("Mill", 0, 900, {[FillType.WHEAT] = 0.5}),
        station("Secret Buyer", 0, 1, {[FillType.WHEAT] = 9}, {hideFromPricesMenu = true}),
        station("Farm Silo Unloading", 0, 2, {[FillType.WHEAT] = 9}, {isSellingPoint = false}),
    } end,
    getStorages = function() return {
        storage(1, {[FillType.WHEAT] = 25000, [FillType.WOOL] = 0.2}, PLACEABLE.silo),
        storage(2, {[FillType.WHEAT] = 99000}, PLACEABLE.theirs),
    } end,
}

-- land: 5 is ours with a field, 12 and 178 are for sale with a field, 44 is for sale without one, 1780 is the neighbour's
local function farmland(id, x, z, owner, extra)
    local f = extra or {}
    f.id, f.xWorldPos, f.zWorldPos, f.owner = id, x, z, owner
    return f
end
FARMLAND = {
    [5] = farmland(5, -100, 0, 1, {name = "5", price = 50000, areaInHa = 3.4}),
    [12] = farmland(12, -500, 0, 0, {name = "12", price = 80000, areaInHa = 6.4, npcIndex = 3}),
    [178] = farmland(178, -500, 500, 0, {price = 120000, areaInHa = 4.2, getNPC = function() return {title = "Miller"} end}),
    [44] = farmland(44, -900, 0, 0, {price = 250000, areaInHa = 2.2}),
    [1780] = farmland(1780, -900, 500, 2, {name = "", price = 90000, areaInHa = 3.3}),
    [9] = farmland(9, 0, 5000, 0, {showOnFarmlandsScreen = false}),
}
g_npcManager = {getNPCByIndex = function(self, index) return index == 3 and {title = "Huber"} or nil end}
g_farmlandManager = {
    getFarmlands = function() return FARMLAND end,
    getFarmlandOwner = function(self, id) return FARMLAND[id].owner end,
    getFarmlandAtWorldPosition = function(self, x, z)
        for _, f in pairs(FARMLAND) do
            if math.abs(x - f.xWorldPos) < 50 and math.abs(z - f.zWorldPos) < 50 then return f end
        end
    end,
}
FIELD = {
    [5] = {farmland = FARMLAND[5], posX = -100, posZ = 0, areaHa = 3.2, getName = function() return "5" end, getId = function() return 5 end},
    [12] = {farmland = FARMLAND[12], posX = -500, posZ = 0, areaHa = 6.1, name = "12"},
    [178] = {farmland = FARMLAND[178], posX = -500, posZ = 500, areaHa = 4, getId = function() return 178 end},
}
g_fieldManager = {fields = {FIELD[5], FIELD[12], FIELD[178], {name = "no position"}},
    farmlandIdFieldMapping = {[5] = FIELD[5], [12] = FIELD[12], [178] = FIELD[178]}}
-- the ground at each field's center
FIELD_STATES = {
    ["-100:0"] = {isValid = true, fruitTypeIndex = 1, growthState = 5, limeLevel = 0, sprayLevel = 1, plowLevel = 1},
    ["-500:0"] = {isValid = true, fruitTypeIndex = 1, growthState = 2, limeLevel = 1, sprayLevel = 0, plowLevel = 1},
    ["-500:500"] = {isValid = false},
}
FieldState = {new = function()
    return {update = function(self, x, z)
        for key, value in pairs(FIELD_STATES[x .. ":" .. z] or {}) do self[key] = value end
    end}
end}
g_fruitTypeManager = {getFruitTypeByIndex = function(self, index)
    return index == 1 and {name = "WHEAT", fillType = {title = "Wheat"}, minHarvestingGrowthState = 5, maxHarvestingGrowthState = 6,
        witheredState = 8, cutStates = {[7] = true}} or nil
end}

local function mission(title, field, reward, extra)
    local m = extra or {}
    m.title = title
    m.getField = function() return field end
    m.getReward = function() return reward end
    return m
end
g_missionManager = {getMissions = function() return {
    mission("Harvesting", FIELD[12], 5000, {status = MissionStatus.CREATED, type = {name = "harvestMission"},
        getNPC = function() return {title = "Huber"} end}),
    mission("Fertilizing", FIELD[178], 3000, {status = MissionStatus.RUNNING, farmId = 1, getIsInProgress = function() return true end}),
    mission("Plowing", FIELD[5], 1000, {status = MissionStatus.RUNNING, farmId = 2, getIsInProgress = function() return true end}),
    mission("Mowing", FIELD[5], 2000, {status = MissionStatus.FINISHED, farmId = 1, getIsInProgress = function() return false end}),
} end}

-- names of what a map search finds, nearest first
function found(text) return names(WheresMyStuff.runSearch(text, 1)) end
'''

# ------------------------------------------------------------------------------------------------ fake screens (Lua)
SCREENS = r'''
-- the shop: a view with the Balance panel somewhere inside, a tile page (open) and the items frame
function makeShop()
    local balance = El.new({profile = "fs25_shopMoneyBoxBg", absPosition = {0.8, 0.9}, absSize = {0.15, 0.05}})
    local view = El.new({elements = {El.new({profile = "fs25_shopHeader", elements = {El.new({profile = "fs25_other"}), balance}})}})
    local frame = ShopItemsFrame.new({name = "items"})
    local tiles = ShopCategoriesFrame.new({name = "tiles"})
    local menu = ShopMenu.new({name = "shop", currentPage = tiles, tilesPage = tiles, currentPageId = 1, pageShopItemDetails = frame,
        customButtonEvents = {}, buttonsPanel = El.new({absPosition = {0.1, 0.02}, absSize = {0.8, 0.04}})})
    g_gui.screens[ShopMenu] = view
    g_shopMenu = menu
    ShopMenu.update(menu, 16)
    return menu, frame, view, balance
end
function typeInto(box, text) box.input:setText(text) end
'''


# ------------------------------------------------------------------------------------------------ the test harness
class Xml:
    """The few XML functions of the engine the mod uses (recent searches, the search box layout)."""

    def __init__(self):
        self.files, self.next = {}, 1

    def add(self, root, path):
        self.files[self.next] = (root, path)
        self.next += 1
        return self.next - 1

    def load(self, name, path, *_):
        try:
            return self.add(ET.parse(path).getroot(), path)
        except (OSError, ET.ParseError):
            return 0

    @staticmethod
    def parse_key(key):
        key, _, attr = key.partition("#")
        parts = []
        for part in key.split("."):
            m = re.match(r"(\w+)\((\d+)\)$", part)
            parts.append((m.group(1), int(m.group(2))) if m else (part, 0))
        return parts, attr or None

    def find(self, handle, key, create=False):
        parts, attr = self.parse_key(key)
        node = self.files[handle][0]
        if parts[0][0] != node.tag:
            return None, attr
        for tag, index in parts[1:]:
            children = [c for c in node if c.tag == tag]
            while create and len(children) <= index:
                children.append(ET.SubElement(node, tag))
            if index >= len(children):
                return None, attr
            node = children[index]
        return node, attr

    def has(self, handle, key):
        node, attr = self.find(handle, key)
        return node is not None and (attr is None or node.get(attr) is not None)

    def get(self, handle, key):
        node, attr = self.find(handle, key)
        return None if node is None else node.get(attr)

    def set(self, handle, key, value):
        node, attr = self.find(handle, key, create=True)
        node.set(attr, value)

    def save(self, handle):
        ET.ElementTree(self.files[handle][0]).write(self.files[handle][1], encoding="utf-8")


class Env:
    """One Lua state with the engine stubs; by default with the mod's scripts loaded.
    without: game globals that are missing (nil) when the scripts load. parts: STORE / WORLD / SCREENS to run after."""
    count = 0

    def __init__(self, *parts, without=(), load=True, before=""):
        Env.count += 1
        self.profile = os.path.join(TMP, "profile%d" % Env.count).replace("\\", "/") + "/"
        self.lua = lua51.LuaRuntime(unpack_returned_tuples=True)
        self.xml = xml = Xml()
        G = self.lua.globals()
        G.pyLower = lambda s: s.lower()
        G.pyLen = len
        G.pyXmlNodes = self.xml_nodes
        G.TEXTS = self.lua.table_from(TEXTS)
        G.InputAction = self.lua.table_from({name: name for name in ACTIONS})
        G.g_currentModDirectory = ROOT.replace("\\", "/") + "/"
        G.getUserProfileAppPath = lambda: self.profile
        G.createFolder = lambda path: os.makedirs(path, exist_ok=True)
        G.fileExists = os.path.exists
        G.loadXMLFile = xml.load
        G.createXMLFile = lambda name, path, root: xml.add(ET.Element(root), path)
        G.hasXMLProperty = xml.has
        G.getXMLString = xml.get
        G.setXMLString = xml.set
        G.saveXMLFile = xml.save
        G.delete = lambda handle: xml.files.pop(handle, None) and None
        self.lua.execute(STUBS)
        self.lua.execute(before)
        for name in without:
            G[name] = None
        if load:
            self.load_scripts()
        for part in parts:
            self.lua.execute(part)

    def xml_nodes(self, handle):
        def convert(e):
            return self.lua.table_from({"tag": e.tag, "attrs": self.lua.table_from(dict(e.attrib)),
                                        "children": self.lua.table_from([convert(c) for c in e])})
        return self.lua.table_from([convert(c) for c in self.xml.files[handle][0]])

    def load_scripts(self):
        run = self.lua.eval("function(source, name) local chunk, err = loadstring(source, name); "
                            "if chunk == nil then error(err, 0) end; chunk() end")
        for script in SCRIPTS:
            run(CONTINUE_RE.sub("do end", read(script)), "@" + script)

    def run(self, code):
        return self.lua.execute(code)


TESTS = []


def test(func):
    TESTS.append(func)
    return func


def check(actual, expected, what):
    if actual != expected:
        raise AssertionError("%s: expected <%s>, got <%s>" % (what, expected, actual))


# ================================================================================================ 1. search logic
@test
def test_query_words_and_filters():
    """What a typed search turns into: words, spec filters (kind, comparison, range, unit) and excluded words."""
    env = Env()
    cases = [
        ("Fendt  700", "w[fendt,700] f[] x[]"),                  # words are lowercased, plain numbers stay words
        ("hp>200", "w[] f[power>200] x[]"),
        ("hp>=200", "w[] f[power>=200] x[]"),
        ("hp<200", "w[] f[power<200] x[]"),
        ("hp<=200", "w[] f[power<=200] x[]"),
        ("hp=200", "w[] f[power>=200<=200] x[]"),
        ("200hp", "w[] f[power>=200<=200] x[]"),
        ("ps>200", "w[] f[power>200] x[]"),
        ("200-300hp", "w[] f[power>=200<=300] x[]"),
        ("hp300-200", "w[] f[power>=200<=300] x[]"),              # a range typed backwards
        ("needhp<150", "w[] f[neededPower<150] x[]"),            # not read as "hp"
        ("$<50k", "w[] f[price<50000] x[]"),
        ("$100k-200k", "w[] f[price>=100000<=200000] x[]"),
        ("$144,500", "w[] f[price>=144500<=144500] x[]"),
        ("$144.500", "w[] f[price>=144500<=144500] x[]"),        # dot as thousands separator
        ("$1.5m", "w[] f[price>=1500000<=1500000] x[]"),
        ("price>100000", "w[] f[price>100000] x[]"),
        ("width>9", "w[] f[width>9] x[]"),
        ("6-9m", "w[] f[width>=6<=9] x[]"),
        ("l>30000", "w[] f[capacity>30000] x[]"),
        ("cap>=30000", "w[] f[capacity>=30000] x[]"),
        ("30000liters", "w[] f[capacity>=30000<=30000] x[]"),
        ("t<10", "w[] f[weight<10] x[]"),
        ("kph>50", "w[] f[speed>50] x[]"),
        ("50km/h", "w[] f[speed>=50<=50] x[]"),
        ("kmh<=40", "w[] f[speed<=40] x[]"),                     # not read as "m" (width)
        ("ha>5", "w[] f[area>5] x[]"),
        ("fuel<20", "w[] f[fuel<20] x[]"),
        ("fuel<20%", "w[] f[fuel<20] x[]"),
        ("damage>30", "w[] f[damage>30] x[]"),
        # typed with spaces, other currency signs
        ("hp > 200", "w[] f[power>200] x[]"),
        ("200 hp", "w[] f[power>=200<=200] x[]"),
        ("$ 100k - 200k", "w[] f[price>=100000<=200000] x[]"),
        ("width >= 9", "w[] f[width>=9] x[]"),
        ("€<50k", "w[] f[price<50000] x[]"),
        ("£<50k", "w[] f[price<50000] x[]"),
        # mixed with words, and words to leave out
        ("fendt hp>300 $<400k", "w[fendt] f[power>300,price<400000] x[]"),
        ("Tractor -MOD", "w[tractor] f[] x[mod]"),
        ("for sale $<100k ha>5", "w[for,sale] f[price<100000,area>5] x[]"),
        # half-typed filters are ignored, not searched as words
        ("fendt hp", "w[fendt] f[] x[]"),
        ("fendt hp>", "w[fendt] f[] x[]"),
        ("fendt $", "w[fendt] f[] x[]"),
        ("fendt 200-", "w[fendt] f[] x[]"),
        ("fendt width>", "w[fendt] f[] x[]"),
        # fuel, damage and dirt are also plain search words
        ("fuel damage dirt", "w[fuel,damage,dirt] f[] x[]"),
        # what the farm can pay (g_currentMission:getMoney() is 75000 in the stubs)
        ("affordable", "w[] f[price<=75000] x[]"),
    ]
    for text, expected in cases:
        check(env.lua.globals().parsed(text), expected, "parseQuery(%r)" % text)
    env.run('''
        -- mph is turned into the km/h the game stores
        local filter = Searchables.parseQuery("mph>30").filters[1]
        eq(filter.kind, "speed", "mph kind"); near(filter.min, 48.28032, "30 mph in km/h"); eq(filter.minStrict, true, "mph strict")
        eq(Searchables.isQueryEmpty(Searchables.parseQuery("  ")), true, "blank text is an empty query")
        eq(Searchables.isQueryEmpty(Searchables.parseQuery("fendt hp>")), false, "a word is not an empty query")
        -- no money known (no mission): "affordable" is no filter and no word
        g_currentMission = nil
        eq(parsed("affordable"), "w[affordable] f[] x[]", "affordable without a mission")
    ''')


@test
def test_parse_amount():
    """Typed amounts: thousands separators, k and m."""
    env = Env()
    env.run('''
        local cases = {{"144,500", 144500}, {"50k", 50000}, {"1.5m", 1500000}, {"144.500", 144500}, {"1.234.567", 1234567},
            {"1.5", 1.5}, {"0", 0}, {"250", 250}, {"1,5k", 15000}}
        for _, case in ipairs(cases) do eq(Searchables.parseAmount(case[1]), case[2], "parseAmount " .. case[1]) end
        for _, text in ipairs({"", "abc", "12x", "k", "-5", "1..2"}) do
            eq(Searchables.parseAmount(text), nil, "parseAmount '" .. text .. "'")
        end
        eq(Searchables.parseAmount(nil), nil, "parseAmount nil")
    ''')


@test
def test_text_matching():
    """Every word must be in the text, excluded words must not; matching is plain text, not a Lua pattern."""
    env = Env()
    env.run('''
        local function matches(text, search) return Searchables.matchesQueryText(text, Searchables.parseQuery(search)) end
        eq(matches("fendt 724 vario\\nlarge tractors", "vario FENDT"), true, "all words, any order, any case")
        eq(matches("fendt 724 vario", "fendt 900"), false, "one word missing")
        eq(matches("fendt 724 vario\\nmod", "fendt -mod"), false, "excluded word present")
        eq(matches("fendt 724 vario", "fendt -mod"), true, "excluded word absent")
        eq(matches("fendt 724 vario", "-mod"), true, "only an exclude")
        eq(matches("tx 560 (blue)", "(blue)"), true, "pattern characters are plain text")
        eq(matches("tx 560", "5.0"), false, "a dot is not a wildcard")
        eq(table.concat(Searchables.getSearchWords("  John   DEERE 6R "), ","), "john,deere,6r", "words")
        eq(#Searchables.getSearchWords(nil), 0, "no text, no words")
        eq(Searchables.toLower("ÄRGER"), "ärger", "lowercase goes through the game's utf8ToLower")
    ''')


@test
def test_store_item_text():
    """What a shop item is searchable by: name, brand, category, mod, and for buildings description and products."""
    env = Env(STORE)
    env.run('''
        local function has(key, search) return Searchables.matchesStoreItem(ITEM[key], Searchables.parseQuery(search)) end
        eq(has("fendt724", "fendt 724"), true, "brand and name")
        eq(has("fendt724", "large tractors"), true, "category title")
        eq(has("former", "tedders"), true, "a category the shop does not know falls back to its name")
        eq(has("trailer", "krone trailers"), true, "categoryNames list")
        eq(has("trailer", "super pack"), true, "mod title")
        eq(has("trailer", "jane"), true, "mod author")
        eq(has("trailer", "mod"), true, "'mod' finds mod items")
        eq(has("trailer", "tx -mod"), false, "'-mod' hides mod items")
        eq(has("fendt724", "724 -mod"), true, "'-mod' keeps base game items")
        eq(has("fendt724", "krone"), false, "another brand")
        eq(has("silo", "harvest dry"), true, "building description")
        eq(has("silo", "buildings storage"), true, "construction category and tab")
        eq(has("bakery", "flour"), true, "product by name key")
        eq(has("bakery", "bread"), true, "product by name in a list")
        eq(has("fendt724", "harvest"), false, "vehicles have no description text")

        -- products come from the building's file, read later: the text is rebuilt until the specs are there
        eq(has("cannery", "peas"), false, "products unknown before the specs are read")
        StoreItemUtil.loadSpecsFromXML(ITEM.cannery)
        eq(has("cannery", "peas"), true, "product by index once the specs are read")
        -- a finished text is kept
        ITEM.fendt724.name = "Renamed"
        eq(has("fendt724", "724"), true, "finished text is cached")
    ''')


@test
def test_entry_text_and_used_items():
    """List entries: display items, bare store items, entries without a store item; used items and their own price."""
    env = Env(STORE)
    env.run('''
        local function matches(entry, search) return Searchables.matchesEntry(entry, Searchables.parseQuery(search)) end
        eq(Searchables.getEntryStoreItem({storeItem = ITEM.fendt724}), ITEM.fendt724, "display item")
        eq(Searchables.getEntryStoreItem(ITEM.fendt724), ITEM.fendt724, "the store item itself")
        eq(Searchables.getEntryStoreItem({title = "Fences"}), nil, "no store item")
        eq(Searchables.getEntryStoreItem("text"), nil, "not a table")
        eq(Searchables.getEntryText({title = "Fences", name = "fence01"}), "fence01\\nfences", "falls back to name and title")
        eq(Searchables.getEntryText(5), "", "not a table: no text")
        eq(matches({title = "Fences"}, "fence"), true, "entry without a store item matches by title")
        eq(matches({title = "Fences"}, "fence $<10"), false, "but never a spec filter")

        local new = {storeItem = ITEM.fendt724}
        local used = {storeItem = ITEM.fendt724, concreteItem = {price = 90000, age = 14, operatingTime = 3600}}
        eq(matches(used, "fendt used"), true, "'used' finds second hand items")
        eq(matches(new, "fendt used"), false, "'used' does not find new items")
        eq(matches(used, "$<100k"), true, "a used item is filtered by its own price")
        eq(matches(new, "$<100k"), false, "a new one by the shop price")
        eq(matches(used, "hp>200 $<100k"), true, "other filters still use the shop's specs")

        local list = {new, used, {title = "Fences"}}
        eq(Searchables.filterEntries(list, Searchables.parseQuery("")), list, "nothing typed: the same list, not a copy")
        eq(#Searchables.filterEntries(list, Searchables.parseQuery("used")), 1, "filtered list")
    ''')


@test
def test_spec_filters():
    """Numeric filters against shop specs: ranges overlap, strict and inclusive limits, units, items without the spec."""
    env = Env(STORE)
    env.run('''
        local function has(key, search) return Searchables.matchesStoreItem(ITEM[key], Searchables.parseQuery(search)) end
        -- 724 Vario has engines from 150 to 237 hp: it matches when any of them fits
        eq(has("fendt724", "hp>200"), true, "hp>200 inside the range")
        eq(has("fendt724", "hp>237"), false, "hp>237 is strict")
        eq(has("fendt724", "hp>=237"), true, "hp>=237")
        eq(has("fendt724", "hp<150"), false, "hp<150 is strict")
        eq(has("fendt724", "hp<=150"), true, "hp<=150")
        eq(has("fendt724", "100-149hp"), false, "range below")
        eq(has("fendt724", "230-300hp"), true, "range overlapping the top")
        eq(has("fendt724", "hp=200"), true, "exact value inside the range")
        eq(has("fendt1050", "hp=200"), false, "exact value, other engine")
        eq(has("trailer", "hp>1"), false, "no engine: no hp match")
        eq(has("fendt724", "$<250k"), false, "price strict")
        eq(has("fendt724", "$250k"), true, "price exact")
        eq(has("fendt724", "$200k-300k"), true, "price range")
        eq(has("fendt724", "fendt hp>200 $<300k"), true, "words and two filters")
        eq(has("fendt724", "fendt hp>200 $<200k"), false, "one filter fails")
        -- working width 10 to 12.5 m
        eq(has("former", "width>9"), true, "width>9")
        eq(has("former", "width<10"), false, "width<10 strict")
        eq(has("former", "9-11m"), true, "9-11m")
        eq(has("former", "6-9m"), false, "6-9m")
        eq(has("fendt724", "width>1"), false, "a tractor has no working width")
        -- power needed: 60 kW in the file, shown and filtered as hp (81.6)
        eq(has("former", "needhp<100"), true, "needhp<100")
        eq(has("former", "needhp<80"), false, "needhp<80")
        -- speed in km/h; tools have a working speed limit; mph is converted
        eq(has("fendt1050", "kph>55"), true, "kph>55"); eq(has("fendt724", "kph>55"), false, "kph>55 slower tractor")
        eq(has("former", "kph<30"), true, "speed limit of a tool")
        eq(has("fendt1050", "mph>35"), true, "35 mph is 56 km/h"); eq(has("fendt724", "mph>35"), false, "50 km/h is under 35 mph")
        -- capacity 30000 to 56000 l, weight
        eq(has("trailer", "l>50000"), true, "l>50000"); eq(has("trailer", "l<30000"), false, "l<30000")
        eq(has("fendt724", "t<10"), true, "t<10"); eq(has("fendt1050", "t<10"), false, "t<10 heavier")
        -- buildings only have a price
        eq(has("silo", "$<50k"), true, "building price"); eq(has("silo", "hp>1"), false, "building hp")

        -- specs are read from the item's file on first use, once
        eq(has("lazy", "hp>50"), true, "specs read on demand")
        eq(has("lazy", "hp>50"), true, "again")
        eq(calls("loadSpecs"), 1, "read once")
        -- an item whose specs can't be read is tried once per kind, then left alone
        eq(has("broken", "hp>50"), false, "unreadable specs")
        eq(has("broken", "hp>50"), false, "unreadable specs again")
        eq(calls("loadSpecs"), 2, "not read again on every search")
        eq(Searchables.getStoreItemValueRange(nil, "power"), nil, "no store item")
    ''')


@test
def test_shop_search_ranking():
    """Whole-shop search: only buyable items, name matches first then by name, with a filter sorted by its value."""
    env = Env(STORE)
    env.run('''
        local function search(text)
            local items = {}
            for i, displayItem in ipairs(SearchablesShop.findDisplayItems(Searchables.parseQuery(text))) do items[i] = displayItem.storeItem end
            return names(items)
        end
        -- hidden, bundle and locked items are not listed; "Fendt Former" has the word in its name, the others in the brand
        eq(search("fendt"), "Fendt Former | 1050 Vario | 724 Vario", "name matches first, then by name")
        eq(search("hp>100"), "724 Vario | 1050 Vario | X9 1100", "sorted by horsepower, lowest first")
        eq(search("$<100k"), "Chainsaw | Broken Plow | Lazy Loader | Fendt Former | TX 560", "sorted by price")
        eq(search("fendt $>100k"), "724 Vario | 1050 Vario", "words and filter")
        eq(search("silo"), "", "buildings are not in the shop search")
        eq(search("zzz"), "", "nothing found")
        -- display items are made once per store item
        local made = calls("makeDisplayItem")
        search("fendt")
        eq(calls("makeDisplayItem"), made, "display items are reused")
    ''')


@test
def test_is_instance_of():
    """Class checks used to recognise shop pages."""
    env = Env()
    env.run('''
        local frame = ShopItemsFrame.new()
        eq(Searchables.isInstanceOf(frame, ShopItemsFrame), true, "isa")
        eq(Searchables.isInstanceOf(frame, ShopCategoriesFrame), false, "another class")
        eq(Searchables.isInstanceOf(frame, nil), false, "class missing in this game version")
        eq(Searchables.isInstanceOf("text", ShopItemsFrame), false, "not an object")
        eq(Searchables.isInstanceOf({}, ShopItemsFrame), false, "plain table")
        -- objects without isa(): walk class() / superClass()
        local base, sub = {}, {}
        base.superClass = function() return nil end
        sub.superClass = function() return base end
        local object = {class = function() return sub end}
        eq(Searchables.isInstanceOf(object, base), true, "superclass through class()")
        eq(Searchables.isInstanceOf(object, ShopItemsFrame), false, "unrelated class through class()")
    ''')


@test
def test_recent_searches():
    """Recent searches: newest first, no duplicates, at most five, saved to modSettings and read back."""
    env = Env()
    env.run('''
        for _, text in ipairs({"fendt", "hp>200", "  wool ", "FENDT", "", "a", "b", "c"}) do Searchables.addRecentSearch("shop", text) end
        Searchables.addRecentSearch("map", "field 12")
        Searchables.addRecentSearch(nil, "ignored")
        eq(table.concat(Searchables.getRecentSearches("shop"), "|"), "c|b|a|FENDT|wool", "newest first, one fendt, five kept")
        Searchables.recentSearches = {}
        Searchables.loadRecentSearches()
        eq(table.concat(Searchables.getRecentSearches("shop"), "|"), "c|b|a|FENDT|wool", "read back from the file")
        eq(table.concat(Searchables.getRecentSearches("map"), "|"), "field 12", "per box")
        eq(#Searchables.getRecentSearches("construction"), 0, "a box without searches")
    ''')
    assert os.path.exists(env.profile + "modSettings/FS25_Searchables.xml"), "the recent searches file was not written"


# ================================================================================================ 2. map search
@test
def test_map_scan_stock():
    """Pallets, big bags and bales: what is scanned, how it is grouped and valued, what lies on fields."""
    env = Env(STORE, WORLD)
    env.run('''
        local items = WheresMyStuff.scanItems(1)
        table.sort(items, function(a, b) return a.x > b.x end)
        local seen = {}
        for i, item in ipairs(items) do seen[i] = string.format("%s:%d:%d", item.kind, item.fillTypeIndex, item.fillLevel) end
        -- not: the neighbour's pallet, vehicles, the bale hidden inside a storage, things that are not bales
        eq(table.concat(seen, " "), string.format("pallet:%d:0 bigBag:%d:1000 pallet:%d:1500 pallet:%d:500 bale:%d:4000",
            FillType.UNKNOWN, FillType.LIME, FillType.WOOL, FillType.WOOL, FillType.STRAW), "our stock")
        eq(#WheresMyStuff.scanItems(nil), 6, "every farm")
        eq(#WheresMyStuff.scanItems(2), 1, "the neighbour")

        local groups = WheresMyStuff.groupItems(WheresMyStuff.scanItems(1))
        eq(names(groups, "title"), "Wool | Straw | Lime | Empty pallets / big bags", "groups, most valuable first")
        eq(groups[1].pallet, 2, "wool pallets"); eq(groups[1].liters, 2000, "wool liters"); eq(groups[1].value, 2000, "wool value")
        eq(groups[2].bale, 1, "straw bales"); eq(groups[3].bigBag, 1, "lime big bags")
        eq(WheresMyStuff.formatCounts(groups[1]), "2 pallets", "counts")
        eq(WheresMyStuff.findGroup(groups, "wool"), groups[1], "group by name, any case")
        eq(WheresMyStuff.findGroup(groups, "empty"), groups[4], "the empty group")
        eq(WheresMyStuff.findGroup(groups, "milk"), nil, "no such group")

        -- the bale lies on the farmland of field 5
        local leftovers = WheresMyStuff.getLeftovers(WheresMyStuff.scanItems(1))
        eq(#leftovers, 1, "items on fields"); eq(leftovers[1].kind, "bale", "the bale")
        local location, onField = WheresMyStuff.getLocation(-100, 10)
        eq(location, "Field 5", "location name"); eq(onField, true, "on a field")
        eq(WheresMyStuff.getLocation(-900, 0), "Farmland 44", "farmland without a field")
        eq(WheresMyStuff.getLocation(3000, 3000), "Off farmland", "off the map's farmlands")

        eq(WheresMyStuff.formatNumber(1234567), "1,234,567", "thousands"); eq(WheresMyStuff.formatNumber(999.6), "1,000", "rounded")
        eq(WheresMyStuff.formatNumber(-1500), "-1,500", "negative"); eq(WheresMyStuff.formatNumber(0), "0", "zero")
    ''')


@test
def test_map_search_vehicles_and_stock():
    """Map search for vehicles (name, type, plate, condition, shop specs) and stock (fill type, kind, fill level)."""
    env = Env(STORE, WORLD)
    env.run('''
        -- our tractor only: not the neighbour's, not the shop preview. Low fuel and damage are shown on it.
        eq(found("fendt"), "Fendt 724 Vario (fuel 10%, damage 50%)", "by name")
        eq(found("large tractors"), "Fendt 724 Vario (fuel 10%, damage 50%)", "by shop category")
        eq(found("ab123"), "Fendt 724 Vario (fuel 10%, damage 50%)", "by license plate without the dash")
        eq(found("ab-123"), "Fendt 724 Vario (fuel 10%, damage 50%)", "by license plate as written")
        eq(found("vehicle"), "Fendt 724 Vario (fuel 10%, damage 50%) | John Deere X9 (helper working, leased)", "every vehicle, nearest first")
        eq(found("fuel<20"), "Fendt 724 Vario (fuel 10%, damage 50%)", "fuel filter")
        eq(found("fuel>50"), "John Deere X9 (helper working, leased)", "fuel filter the other way")
        eq(found("fuel"), "Fendt 724 Vario (fuel 10%, damage 50%)", "'fuel' alone: low on fuel")
        eq(found("damaged"), "Fendt 724 Vario (fuel 10%, damage 50%)", "damaged")
        eq(found("damage>60"), "", "damage filter")
        eq(found("parked"), "Fendt 724 Vario (fuel 10%, damage 50%)", "parked")
        eq(found("helper"), "John Deere X9 (helper working, leased)", "helper")
        eq(found("leased"), "John Deere X9 (helper working, leased)", "leased")
        eq(found("vehicle hp>300"), "John Deere X9 (helper working, leased)", "shop specs of owned vehicles")
        eq(found("vehicle -deere"), "Fendt 724 Vario (fuel 10%, damage 50%)", "excluded word")

        eq(found("wool"), "Wool (Pallet) - 500 L | Wool (Pallet) - 1,500 L", "pallets by fill type, nearest first")
        eq(found("wool l>1000"), "Wool (Pallet) - 1,500 L", "fill level filter")
        eq(found("big bag"), "Lime (Big bag) - 1,000 L", "big bags")
        eq(found("round bale"), "Straw (Bale) - 4,000 L", "round bales")
        eq(found("square bale"), "", "no square bales")
        eq(found("empty"), "Empty (Pallet)", "empty pallets")
        eq(found("pallet"), "Wool (Pallet) - 500 L | Wool (Pallet) - 1,500 L | Empty (Pallet)", "by kind")
        eq(found("neighbour"), "", "nothing of another farm")
        eq(#WheresMyStuff.search("", 1), 0, "nothing typed")
        eq(#WheresMyStuff.search("-wool", 1), 0, "only an excluded word")
    ''')


@test
def test_map_search_buildings_storage_selling():
    """Buildings with animal and production status, stored goods, and where to sell."""
    env = Env(STORE, WORLD)
    env.run('''
        eq(found("silo"), "Grain Silo", "by name, type and category")
        eq(found("water"), "Cow Barn (12 animals; low: Water, Cleanliness)", "pens say what is low (also bars where full is bad)")
        eq(found("needs care"), "Cow Barn (12 animals; low: Water, Cleanliness)", "pens needing care")
        eq(found("missing"), "Bakery (makes Bread; missing Flour; full Bread)", "production missing an input")
        eq(found("flour"), "Bakery (makes Bread; missing Flour; full Bread)", "production by what it needs")
        eq(found("makes bread"), "Bakery (makes Bread; missing Flour; full Bread)", "production by what it makes")
        eq(found("elevator"), "Grain Elevator", "public buildings are found too")
        eq(found("shed"), "", "not the neighbour's buildings")
        eq(found("building"), "Grain Silo | Cow Barn (12 animals; low: Water, Cleanliness) | Bakery (makes Bread; missing Flour; full Bread)"
            .. " | Grain Elevator | Ready Mix Plant", "every building, nearest first")

        -- "stored <goods>": our storages only, with how much; under half a liter does not count
        eq(found("stored wheat"), "Grain Silo: Wheat 25,000 l", "stored wheat")
        eq(found("stored wheat l>30000"), "", "stored with a fill level filter")
        eq(found("stored wheat l>20000"), "Grain Silo: Wheat 25,000 l", "stored with a fill level filter that fits")
        eq(found("stored"), "Grain Silo: Wheat 25,000 l", "everything stored")
        eq(found("stored wool"), "", "0.2 l of wool is nothing")

        -- "sell <goods>": selling points on the price list, price per 1000 l, best one marked
        eq(found("sell wheat"), "Grain Elevator: Wheat $250 / 1000 l | Mill: Wheat $500 / 1000 l (best price)", "sell wheat")
        eq(found("sell wool"), "Grain Elevator: Wool $1500 / 1000 l (best price)", "sell wool")
        eq(found("sell peas"), "", "nobody buys peas")
        eq(WheresMyStuff.hotspots[1] == nil, true, "no markers when nothing is found")
    ''')


@test
def test_map_search_fields_and_land():
    """Fields with crop and work to do, properties by number, land for sale, NPC owners, contracts."""
    env = Env(STORE, WORLD)
    env.run('''
        eq(found("wheat"), "Field 5 (Wheat, ready to harvest, needs lime) | Field 12 (Wheat, growing, needs fertilizer)", "fields by crop")
        -- a field state word searches fields only (the Ready Mix Plant is a building)
        eq(found("ready"), "Field 5 (Wheat, ready to harvest, needs lime)", "ready to harvest")
        eq(found("wheat growing"), "Field 12 (Wheat, growing, needs fertilizer)", "growing")
        eq(found("mix"), "Ready Mix Plant", "the plant is found otherwise")
        eq(found("fertilize"), "Field 12 (Wheat, growing, needs fertilizer)", "needs fertilizer")
        -- "lime" finds the field that needs it and the lime itself
        eq(found("lime"), "Lime (Big bag) - 1,000 L | Field 5 (Wheat, ready to harvest, needs lime)", "lime")
        eq(found("owned"), "Field 5 (Wheat, ready to harvest, needs lime)", "our own land: the field, not also its property")

        -- numbers match whole numbers; a property is left out when its field matched
        eq(found("178"), "Field 178", "field 178, not property 1780, not also property 178")
        eq(found("1780"), "Property 1780", "property 1780")
        eq(found("17"), "", "part of a number")
        eq(found("44"), "Property 44 (for sale, $250000, 2.2 ha)", "a property without a field")
        eq(found("property"), "Property 5 | Property 12 (for sale, $80000, 6.4 ha) | Property 178 (for sale, $120000, 4.2 ha)"
            .. " | Property 44 (for sale, $250000, 2.2 ha) | Property 1780", "every property on the farmlands screen, nearest first")

        eq(found("for sale $<100k ha>5"), "Property 12 (for sale, $80000, 6.4 ha)", "land for sale by price and size")
        eq(found("for sale"), "Property 12 (for sale, $80000, 6.4 ha) | Property 178 (for sale, $120000, 4.2 ha)"
            .. " | Property 44 (for sale, $250000, 2.2 ha)", "'for sale' is not a selling search")
        eq(found("huber"), "Field 12 (Wheat, growing, needs fertilizer)", "land of an NPC farmer (npcIndex)")
        eq(found("miller"), "Field 178", "land of an NPC farmer (getNPC)")
        eq(found("field ha>5"), "Field 12 (Wheat, growing, needs fertilizer)", "field size")

        -- contracts only when asked for: open ones and the ones our farm works on
        eq(found("contract"), "Harvesting: Field 12 ($5000, available) | Fertilizing: Field 178 ($3000, active)", "contracts")
        eq(found("contract huber"), "Harvesting: Field 12 ($5000, available)", "contracts of a farmer")
        eq(found("contract $>4000"), "Harvesting: Field 12 ($5000, available)", "contracts by reward")
        eq(found("harvesting"), "", "contracts are not in a plain search")

        -- the ground is read again after a few seconds, not on every letter
        FIELD_STATES["-100:0"].growthState = 8
        eq(found("withered"), "", "field status is kept for a moment")
        g_time = g_time + WheresMyStuff.FIELD_STATUS_MAX_AGE + 1
        eq(found("withered"), "Field 5 (Wheat, withered, needs lime)", "and read again later")
        -- work the game's settings switch off does not count
        g_currentMission.missionInfo.limeRequired = false
        g_time = g_time + WheresMyStuff.FIELD_STATUS_MAX_AGE + 1
        eq(found("5"), "Field 5 (Wheat, withered)", "lime switched off")
    ''')


@test
def test_map_hotspots():
    """Markers: one per result, nearest first, capped; they follow their object and hide when it is gone."""
    env = Env(STORE, WORLD)
    env.run('''
        local matches = WheresMyStuff.runSearch("wool", 1)
        eq(#WheresMyStuff.hotspots, 2, "one marker per match"); eq(#MAP_HOTSPOTS, 2, "added to the map")
        eq(WheresMyStuff.searchQuery, "wool", "the search is remembered"); eq(WheresMyStuff.searchMatchCount, 2, "match count")
        local hotspot = WheresMyStuff.hotspots[1]
        eq(hotspot:getName(), "Wool (Pallet) - 500 L", "nearest first")
        eq(hotspot.kind, "pallet", "kind"); eq(hotspot:getCategory(), MapHotspot.CATEGORY_OTHER, "map filter")
        eq(table.concat(WheresMyStuffHotspot.getColor("pallet"), ","), "1,0.6,0.1,1", "pallet color")
        eq(table.concat(WheresMyStuffHotspot.getColor("something new"), ","), "1,1,1,1", "unknown kind: white")
        for kind in pairs(WheresMyStuff.KIND_SEARCH_WORDS) do
            eq(WheresMyStuffHotspot.COLORS[kind] ~= nil, true, "a color for kind " .. kind)
        end

        -- follows the pallet, hides when it is sold
        local x, z = hotspot:getWorldPosition()
        eq(x, 10, "position"); eq(z, 0, "position z")
        NODES[VEHICLE.woolSmall.rootNode] = {77, 0, 88}
        x, z = hotspot:getWorldPosition()
        eq(x, 77, "follows its object"); eq(z, 88, "follows its object z")
        NODES[VEHICLE.woolSmall.rootNode] = nil
        eq(hotspot:getIsVisible(), false, "hidden once the object is gone")
        x, z = hotspot:getWorldPosition()
        eq(x, 10, "last known position")
        NODES[VEHICLE.woolSmall.rootNode] = {10, 0, 0}

        -- fields have no object: fixed position, always there
        WheresMyStuff.runSearch("178", 1)
        eq(#MAP_HOTSPOTS, 1, "the old markers are removed")
        local fieldHotspot = WheresMyStuff.hotspots[1]
        eq(fieldHotspot:getIsStatic(), true, "a field marker is static"); eq(fieldHotspot:getIsVisible(), true, "and visible")
        x, z = fieldHotspot:getWorldPosition()
        eq(x, -500, "field position"); eq(z, 500, "field position z")

        -- at most MAX_HOTSPOTS, the nearest ones; objects without a scene node (0) get none
        WheresMyStuff.MAX_HOTSPOTS = 3
        local items = {}
        for i = 1, 5 do items[i] = {kind = "field", name = "far " .. (6 - i), x = (6 - i) * 100, z = 0} end
        local hotspots, skipped = WheresMyStuff.showHotspots(items)
        eq(names(hotspots), "far 1 | far 2 | far 3", "the nearest are marked"); eq(skipped, 2, "left out")
        hotspots = WheresMyStuff.showHotspots({{kind = "building", name = "no node", node = 0, x = 1, z = 1}})
        eq(#hotspots, 0, "no marker without a scene node")

        WheresMyStuff.runSearch("wool", 1)
        WheresMyStuff.removeHotspots()
        eq(#MAP_HOTSPOTS, 0, "all removed"); eq(WheresMyStuff.searchQuery, nil, "search forgotten")
    ''')


@test
def test_map_console_commands():
    """The wms console commands on the fake farm."""
    env = Env(STORE, WORLD)
    env.run('''
        print = function() end   -- the commands print their lists
        eq(WheresMyStuff:consoleSummary(), "Total estimated value: $2,600. Use 'wmsList <fillType>' to see where items are.", "wmsSummary")
        eq(WheresMyStuff:consoleSummary("x"), "Invalid farm id 'x'", "wmsSummary with a bad farm")
        eq(WheresMyStuff:consoleSummary("7"), "Farm 7 has no pallets, big bags or bales.", "wmsSummary of an empty farm")
        eq(WheresMyStuff:consoleList("wool"), "2 item(s). Use 'wmsShow WOOL' to mark them on the map.", "wmsList")
        eq(WheresMyStuff:consoleLeftovers(), "1 item(s) on fields. Use 'wmsShow LEFTOVERS' to mark them on the map.", "wmsLeftovers")
        eq(WheresMyStuff:consoleGoto("wool"), "Target set: Wool (Pallet) - 500 L at Off farmland.", "wmsGoto")
        eq(MAP_TARGET, WheresMyStuff.hotspots[1], "the nearest is the map target")
        eq(WheresMyStuff:consoleTarget("2"), "Target set: Wool (Pallet) - 1,500 L at Off farmland.", "wmsTarget")
        eq(WheresMyStuff:consoleTarget("9"), "Pick a number from 1 to 2.", "wmsTarget out of range")
        eq(WheresMyStuff:consoleHide(), "Removed 2 map marker(s).", "wmsHide")
        eq(WheresMyStuff:consoleFind("john", "deere"), "1 match(es) for 'john deere', marked on the map. Use 'wmsTarget <number>' to navigate to one.", "wmsFind")
        eq(WheresMyStuff:consoleFind("zzz"), "Nothing matching 'zzz'.", "wmsFind without a match")
        eq(WheresMyStuff:consoleField("5"), "  search sees: Wheat, ready to harvest, needs lime", "wmsField")
        eq(WheresMyStuff:consoleField("77"), "No field 77. Usage: wmsField <field number>", "wmsField unknown")
        -- a spectator has no farm
        g_currentMission.getFarmId = function() return FarmManager.SPECTATOR_FARM_ID end
        eq(WheresMyStuff:consoleSummary(), "No farm selected. Join a farm or pass a farm id.", "no farm")
    ''')


@test
def test_map_search_without_game_systems():
    """The map search copes when parts of the game are missing (other game version, dedicated server)."""
    env = Env(STORE, WORLD)
    env.run('''
        local mission = g_currentMission
        mission.vehicleSystem, mission.itemSystem, mission.placeableSystem, mission.storageSystem, mission.economyManager = nil, nil, nil, nil, nil
        g_missionManager, g_cameraManager, g_npcManager, FieldState = nil, nil, nil, nil
        eq(found("fendt"), "", "no vehicle system"); eq(found("wool"), "", "no stock"); eq(found("silo"), "", "no placeables")
        eq(found("sell wheat"), "", "no storage system: nothing to sell to"); eq(found("stored wheat"), "", "no storages")
        eq(found("contract"), "", "no mission manager")
        -- fields are still found, without crop status
        eq(found("field 5"), "Field 5", "fields without FieldState")
        eq(#WheresMyStuff.runSearch("property", 1), 5, "properties, unsorted without a camera")
        eq(WheresMyStuff.getViewerPosition(), nil, "no camera")
        -- vehicles the mod can't ask about fuel, damage or plates
        mission.vehicleSystem = {vehicles = {{rootNode = VEHICLE.tractor.rootNode, typeName = "locomotive", getOwnerFarmId = function() return 1 end}}}
        eq(found("locomotive"), "locomotive", "a bare vehicle is named by its type")
    ''')


# ================================================================================================ 3. GUI
@test
def test_search_box_layout_files():
    """gui/searchBox.xml has the elements the script looks up, and its own profiles exist in searchablesProfiles.xml."""
    box = ET.parse(os.path.join(ROOT, "gui", "searchBox.xml")).getroot()
    ids = {e.get("id") for e in box.iter() if e.get("id")}
    looked_up = set(re.findall(r'getDescendantById\("(\w+)"\)', read("scripts/SearchablesSearchBox.lua")))
    assert looked_up, "no getDescendantById calls found in SearchablesSearchBox.lua"
    check(sorted(looked_up - ids), [], "ids the script looks up but searchBox.xml does not have")
    profiles = {e.get("name") for e in ET.parse(os.path.join(ROOT, "gui", "searchablesProfiles.xml")).getroot().iter("Profile")}
    used = {e.get("profile") for e in box.iter() if (e.get("profile") or "").startswith("searchables")}
    check(sorted(used - profiles), [], "searchables* profiles used in searchBox.xml but not defined")
    # the shop looks for the Balance panel by these game profiles; the input's icon box sticks out by ICON_BOX_WIDTH
    assert box.find(".//TextInput") is not None, "searchBox.xml has no TextInput"


@test
def test_translations_cover_script_texts():
    """Every text the scripts ask for is in translation_en.xml (also the keys built from kinds and states)."""
    env = Env()
    keys = set()
    for script in SCRIPTS:
        keys |= set(re.findall(r'getText\("(searchables_\w+)"\)', read(script)))
    keys |= {"searchables_kind_" + kind for kind in env.lua.eval("WheresMyStuff.KIND_SEARCH_WORDS")}
    keys |= {"searchables_state_" + state for state in env.lua.eval("WheresMyStuff.FIELD_STATUS_WORDS")}
    assert len(keys) > 40, "too few text keys found in the scripts: %d" % len(keys)
    check(sorted(k for k in keys if k not in TEXTS), [], "texts missing in translation_en.xml")
    check(sorted(a for a in ACTIONS if "input_" + a not in TEXTS), [], "actions without an input_ text")
    # format texts must keep their placeholders
    for key, count in (("searchables_countFound", 1), ("searchables_countOf", 2), ("searchables_fieldName", 1),
                       ("searchables_farmlandName", 1), ("searchables_animalCount", 1), ("searchables_moreResults", 1)):
        check(len(re.findall(r"%[ds]", TEXTS[key])), count, "placeholders in " + key)


@test
def test_search_box_element():
    """The box itself: made from gui/searchBox.xml as the view's last child, count text, clear, X click, failures."""
    env = Env()
    broken = os.path.join(TMP, "boxWithoutInput.xml")
    open(broken, "w", encoding="utf-8").write('<GUI><GuiElement profile="searchablesBox"><Text id="searchablesCount"/></GuiElement></GUI>')
    env.lua.globals().BROKEN_BOX_FILENAME = broken
    env.run('''
        local changes = {}
        local first = El.new({id = "gameElement"})
        local view = El.new({elements = {first}})
        local screen = ShopMenu.new({name = "screen"})
        g_gui.screens[ShopMenu] = view
        local box = SearchablesSearchBox.new(screen, "Type here", function(text) table.insert(changes, text) end)
        eq(view.elements[1], first, "the game's elements stay first")
        eq(view.elements[2], box.root, "the box is the last child: drawn on top")
        eq(box.input.typeName, "TextInput", "the input is the TextInput of searchBox.xml")
        eq(box.input.target, screen, "the screen is the input's target")
        eq(box.placeholder:getText(), "Type here", "placeholder text")
        eq(box.iconBox ~= nil and box.penIcon ~= nil and box.clearIcon ~= nil and box.countText ~= nil, true, "icons and count text found")

        box:setCount(3, 10); eq(box.countText:getText(), "3 of 10", "count of total")
        box:setCount(10, 10); eq(box.countText:getText(), "10 found", "everything shown")
        box:setCount(4); eq(box.countText:getText(), "4 found", "no total")
        box:setCount(0, 10); eq(box.countText:getText(), "nothing found", "nothing")
        box:setCount(nil); eq(box.countText:getText(), "", "cleared")

        -- typed or pasted text is noticed on update, once
        box:update()
        eq(box.clearIcon.visible, false, "no X in an empty box"); eq(box.penIcon.visible, true, "but the pen")
        box.input:setText("fendt"); box:update(); box:update()
        eq(table.concat(changes, "|"), "fendt", "onChanged once per change")
        eq(box.clearIcon.visible, true, "X shown with text"); eq(box.penIcon.visible, false, "pen hidden with text")
        eq(box:hasText(), true, "hasText")
        -- Esc clears and tells the owner; reset() clears quietly
        box.input.onEscPressedCallback()
        eq(table.concat(changes, "|"), "fendt|", "Esc clears"); eq(box:getText(), "", "text gone")
        box:update()
        eq(box.clearIcon.visible, false, "X hidden when empty"); eq(box.placeholder.visible, true, "placeholder back")
        box.input:setText("abc"); box:update(); box:reset(); box:update()
        eq(table.concat(changes, "|"), "fendt||abc", "reset does not call onChanged")

        -- the X at the right: a click clears, the game's own mouse handling is skipped; other clicks go to the game
        box:setPosition(0.5, 0.5)
        box.input:setText("wool"); box:update()
        local x, y = box.iconBox.absPosition[1] + 0.001, box.iconBox.absPosition[2] + 0.001
        eq(x > 0.5, true, "the icon box moved with the box")
        Gui.mouseEvent(g_gui, x, y, true, false, Input.MOUSE_BUTTON_LEFT)
        eq(box:getText(), "wool", "mouse down does not clear yet")
        Gui.mouseEvent(g_gui, x, y, false, true, Input.MOUSE_BUTTON_LEFT)
        eq(box:getText(), "", "mouse up on the X clears"); eq(calls("Gui.mouseEvent"), 0, "the click did not reach the game")
        Gui.mouseEvent(g_gui, 0.1, 0.1, false, true, Input.MOUSE_BUTTON_LEFT)
        eq(calls("Gui.mouseEvent"), 1, "a click elsewhere goes to the game")

        -- focus (Ctrl+F): cursor in the box, on-screen keyboard; not while hidden
        eq(box:focus(), true, "focus"); eq(box:getIsCapturing(), true, "typing"); eq(box.input.imeOpened, true, "on-screen keyboard")
        box:setVisible(false)
        eq(box:getIsCapturing(), false, "hiding stops typing"); eq(box:focus(), false, "no focus while hidden")

        box:delete()
        eq(#view.elements, 1, "delete removes the box from the view")
        box:delete()   -- twice, and after the game deleted the screen: no error

        -- no view, no layout file, a layout without the input: an error in the log, no box, no Lua error
        g_gui.screens[ShopMenu] = nil
        eq(SearchablesSearchBox.new(ShopMenu.new({name = "bare", elements = {}}), nil, nil), nil, "screen without a view")
        eq(logged("has no view"), true, "logged")
        g_gui.screens[ShopMenu] = view
        local filename = Searchables.SEARCH_BOX_FILENAME
        Searchables.SEARCH_BOX_FILENAME = filename .. ".missing"
        eq(SearchablesSearchBox.new(screen, nil, nil), nil, "layout file missing")
        eq(logged("Could not load"), true, "logged")
        Searchables.SEARCH_BOX_FILENAME = BROKEN_BOX_FILENAME   -- a layout where the text input has another id
        eq(SearchablesSearchBox.new(screen, nil, nil), nil, "layout without the input")
        eq(logged("could not be created"), true, "logged")
        eq(#view.elements, 1, "nothing left behind in the view")
    ''')


@test
def test_search_box_recent_list():
    """Recent searches under an empty box that is being typed in: drawn after the screen, a click picks one."""
    env = Env()
    env.run('''
        local changes = {}
        local screen = ShopMenu.new({name = "screen", elements = {El.new()}})   -- no g_gui.screens entry: first element is the view
        local box = SearchablesSearchBox.new(screen, nil, function(text) table.insert(changes, text) end)
        eq(screen.elements[1].elements[1], box.root, "falls back to the screen's first element")
        box:setPosition(0.4, 0.8)
        box.recentKey = "shop"
        Searchables.recentSearches.shop = {"fendt", "hp>200"}

        Gui.draw(g_gui)
        eq(#box.recentRows, 0, "not shown while not typing")
        box:captureInput()
        Gui.draw(g_gui)
        eq(#box.recentRows, 2, "shown while typing in an empty box")
        eq(table.concat(DRAWN, "|"), "Recent searches|fendt|hp>200", "drawn texts")
        eq(calls("Gui.draw"), 2, "the game's own draw still runs")

        local row = box.recentRows[2]
        Gui.mouseEvent(g_gui, row.x + 0.01, row.y + row.height * 0.5, false, true, Input.MOUSE_BUTTON_LEFT)
        eq(box:getText(), "hp>200", "a click types the search"); eq(table.concat(changes, "|"), "hp>200", "and runs it")
        eq(calls("Gui.mouseEvent"), 0, "the click did not reach the game")
        Gui.draw(g_gui)
        eq(#box.recentRows, 0, "hidden once there is text")

        -- typing ends: the search is remembered
        box:update(); box:releaseInput(); box:update()
        eq(Searchables.getRecentSearches("shop")[1], "hp>200", "remembered, newest first")
        eq(#Searchables.getRecentSearches("shop"), 2, "no duplicate")
    ''')


@test
def test_shop_box_position():
    """The shop box goes under the Balance panel found in the view; without it to the button bar, else a corner."""
    env = Env(STORE, SCREENS)
    env.run('''
        local menu, frame, view, balance = makeShop()
        local box = SearchablesShop.box
        eq(calls("ShopMenu.update"), 1, "the game's update still runs")
        eq(view.elements[#view.elements], box.root, "the box was added to the shop's view")
        eq(box.recentKey, "shop", "recent searches key"); eq(box.root.visible, true, "visible on the tile page")
        local width, height = box:getSize()
        near(width, 0.15 + 31 / 1920, "box width includes the icon box")
        near(box.root.absPosition[1], 0.8, "left edge of the Balance panel")
        near(box.root.absPosition[2], 0.9 - 12 / 1080 - height, "just under the Balance panel")

        -- nudged with searchablesMove
        eq(Searchables:consoleMove("shop", "10", "-5"), "Offset is now 10, -5 px. Put these values in OFFSET_X / OFFSET_Y to keep them.", "searchablesMove")
        near(box.root.absPosition[1], 0.8 + 10 / 1920, "moved right"); near(box.root.absPosition[2], 0.9 - 12 / 1080 - height - 5 / 1080, "moved down")
        eq(Searchables:consoleMove("nowhere", "1", "1"):sub(1, 6), "Usage:", "searchablesMove usage")
        SearchablesShop.OFFSET_X, SearchablesShop.OFFSET_Y = 0, 0

        -- the wider Balance panel (with slots) has another profile
        balance.profile = "fs25_shopMoneySlotsBoxBg"
        SearchablesShop.balancePanel = nil
        g_time = g_time + 1000
        ShopMenu.update(menu, 16)
        near(box.root.absPosition[1], 0.8, "the slots variant is found too")

        -- Balance panel hidden (or renamed by a game patch): right end of the bottom button bar
        balance:setVisible(false)
        g_time = g_time + 1000
        ShopMenu.update(menu, 16)
        near(box.root.absPosition[1], 0.1 + 0.8 - width, "right end of the button bar")
        near(box.root.absPosition[2], 0.02 + (0.04 - height) * 0.5, "centered in the button bar")
        -- neither: bottom right corner
        menu.buttonsPanel = nil
        ShopMenu.update(menu, 16)
        near(box.root.absPosition[1], 1 - width - 40 / 1920, "corner x"); near(box.root.absPosition[2], 40 / 1080, "corner y")

        -- hidden on pages that are neither tiles nor an item list
        menu.currentPage = El.new({name = "garage"})
        ShopMenu.update(menu, 16)
        eq(box.root.visible, false, "hidden on other pages")
    ''')


@test
def test_shop_whole_shop_search():
    """Typing on a tile page lists results in the shop's item frame; going back clears the search."""
    env = Env(STORE, SCREENS)
    env.run('''
        local menu, frame = makeShop()
        local box = SearchablesShop.box
        local function shown()
            local items = {}
            for i, displayItem in ipairs(frame.shown) do items[i] = displayItem.storeItem end
            return names(items)
        end
        box:captureInput()
        typeInto(box, "f"); ShopMenu.update(menu, 16)
        eq(calls("ShopMenu.pushDetail"), 0, "one letter does not search the whole shop")
        typeInto(box, "fendt"); ShopMenu.update(menu, 16)
        eq(calls("ShopMenu.pushDetail"), 1, "the results open on top of the tile page")
        eq(shown(), "Fendt Former | 1050 Vario | 724 Vario", "results in the item frame")
        eq(frame.categoryName, "SEARCHABLES", "results category"); eq(frame.categoryTitle, "Search results: fendt", "results title")
        eq(menu.currentDisplayItems, frame.shown, "the menu's own list is what is shown")
        eq(box:getIsCapturing(), true, "still typing after the page change"); eq(box.input.blockTime, 0, "without the key delay")
        eq(box.countText:getText(), "3 found", "count")

        typeInto(box, "fendt 1050"); ShopMenu.update(menu, 16)
        eq(shown(), "1050 Vario", "results follow the typing"); eq(calls("ShopMenu.pushDetail"), 1, "the results page is reused")
        typeInto(box, "hp>600"); ShopMenu.update(menu, 16)
        eq(shown(), "X9 1100", "a filter alone is enough")

        -- deleting down to one letter closes the results but keeps what is typed
        typeInto(box, "f"); ShopMenu.update(menu, 16)
        eq(calls("ShopMenu.popDetail"), 1, "results closed"); eq(box:getText(), "f", "typed text kept")
        eq(SearchablesShop.isShowingResults, false, "not showing results")

        -- the player goes back from the results: search cleared
        typeInto(box, "fendt"); ShopMenu.update(menu, 16)
        menu:popDetail()
        eq(box:getText(), "", "going back clears the search"); eq(box.countText:getText(), "", "and the count")

        -- another tab: cleared too
        typeInto(box, "krone"); ShopMenu.update(menu, 16)
        menu.currentPageId = 2
        ShopMenu.update(menu, 16)
        eq(box:getText(), "", "another tab clears the search"); eq(SearchablesShop.isShowingResults, false, "results state reset")
    ''')


@test
def test_shop_item_list_filter():
    """On an item list every list the game hands the frame is filtered, also when the game hands back a filtered one."""
    env = Env(STORE, SCREENS)
    env.run('''
        local menu, frame = makeShop()
        local box = SearchablesShop.box
        local full = {{storeItem = ITEM.fendt724}, {storeItem = ITEM.trailer}, {storeItem = ITEM.former}}
        menu.currentPage = frame
        menu.currentDisplayItems = full
        frame:setDisplayItems(full, "extra", 7)
        eq(frame.shown, full, "nothing typed: the game's own list"); eq(frame.shownArgs[1], "extra", "other arguments pass through")
        eq(box.countText:getText(), "", "no count")

        typeInto(box, "tx"); ShopMenu.update(menu, 16)
        eq(#frame.shown, 1, "filtered"); eq(frame.shown[1], full[2], "the trailer")
        eq(frame.shownArgs[2], 7, "arguments kept for the refresh"); eq(box.countText:getText(), "1 of 3", "count")
        eq(menu.currentDisplayItems, frame.shown, "the menu's reference follows")

        -- the game refreshes with the list it was given last (the filtered one): still filtered from the full list
        typeInto(box, "fendt"); frame:setDisplayItems(frame.shown)
        eq(#frame.shown, 2, "filtered from the full list, not from the filtered one"); eq(box.countText:getText(), "2 of 3", "count of the full list")
        typeInto(box, "zzz"); ShopMenu.update(menu, 16)
        eq(#frame.shown, 0, "nothing matches"); eq(box.countText:getText(), "nothing found", "nothing found")

        box.input.onEscPressedCallback()
        eq(frame.shown, full, "Esc brings the full list back")
        -- the game may call it without a list
        frame:setDisplayItems(nil)
        eq(frame.shown, nil, "no list: passed on untouched")
        -- the first entry can be printed for debugging
        print = function() end
        frame:setDisplayItems(full)
        eq(Searchables:consoleDumpItem(), "Done.", "searchablesDumpItem")
    ''')


@test
def test_shop_search_action_and_menu_buttons():
    """Ctrl+F is registered with the menu's buttons; while typing the menu's buttons wait until typing ends."""
    env = Env(STORE, SCREENS)
    env.run('''
        local menu = makeShop()
        local box = SearchablesShop.box
        ShopMenu.assignMenuButtonInfo(menu)
        eq(calls("ShopMenu.assignMenuButtonInfo"), 1, "the game registers its buttons")
        eq(#EVENTS, 1, "the Search action is registered"); eq(EVENTS[1].action, "SEARCHABLES_SEARCH", "the action of modDesc.xml")
        eq(EVENTS[1].textVisible, false, "without a button text"); eq(menu.customButtonEvents[1], 1, "removed with the menu's own events")
        fireSearchAction()
        eq(box:getIsCapturing(), true, "Ctrl+F puts the cursor in the box")

        -- while typing the input context is the text input's: wait
        ShopMenu.assignMenuButtonInfo(menu)
        eq(calls("ShopMenu.assignMenuButtonInfo"), 1, "buttons wait while typing"); eq(#EVENTS, 1, "so does the Search action")
        typeInto(box, "fendt"); ShopMenu.update(menu, 16)
        box:releaseInput(); ShopMenu.update(menu, 16)
        eq(calls("ShopMenu.updateButtonsPanel"), 1, "buttons registered once typing ends")
        eq(Searchables.getRecentSearches("shop")[1], "fendt", "and the search is remembered")
        -- another menu's buttons are not held back
        box:captureInput()
        InGameMenu.assignMenuButtonInfo(InGameMenu.new())
        eq(calls("InGameMenu.assignMenuButtonInfo"), 1, "other menus are not held back")

        -- the action is missing (modDesc not loaded as expected): nothing registered, no error
        box:releaseInput()
        InputAction.SEARCHABLES_SEARCH = nil
        ShopMenu.assignMenuButtonInfo(menu)
        eq(#EVENTS, 1, "no action, no event")
    ''')


@test
def test_construction_search():
    """Construction: every category and tab is filtered in place, the box sits under the item menu, closing resets."""
    env = Env(STORE)
    env.run('''
        local silo, bakery, cannery, fence = {storeItem = ITEM.silo}, {storeItem = ITEM.bakery}, {storeItem = ITEM.cannery}, {name = "Wooden Fence"}
        local tabA, tabB, tabC = {silo, bakery}, {cannery}, {fence}
        local screen = ConstructionScreen.new({name = "construction", items = {{tabA, tabB}, {tabC}}, menuEvents = {},
            menuBox = El.new({absPosition = {0.02, 0.3}, absSize = {0.2, 0.6}}), itemList = El.new()})
        g_gui.screens[ConstructionScreen] = El.new()
        StoreItemUtil.loadSpecsFromXML(ITEM.cannery)

        -- before the game built its lists there is nothing to search: box hidden
        ConstructionScreen.update(screen, 16)
        local box = SearchablesConstruction.box
        eq(box.root.visible, false, "hidden until the lists are known")
        ConstructionScreen.rebuildData(screen)
        ConstructionScreen.update(screen, 16)
        eq(calls("ConstructionScreen.rebuildData"), 1, "the game's rebuild still runs"); eq(box.root.visible, true, "visible")
        local _, height = box:getSize()
        near(box.root.absPosition[1], 0.02, "left edge of the item menu"); near(box.root.absPosition[2], 0.3 - 8 / 1080 - height, "just under it")
        screen.menuBox.absPosition = {0.02, 0.02}
        ConstructionScreen.update(screen, 16)
        near(box.root.absPosition[2], 0.02 + 0.6 + 8 / 1080, "no room below: above the menu")
        screen.menuBox:setVisible(false)
        ConstructionScreen.update(screen, 16)
        eq(box.root.visible, false, "hidden with the item menu")
        screen.menuBox:setVisible(true)

        local function shown() return names(screen.items[1][1], "storeItem") == "" and 0 or #screen.items[1][1], #screen.items[1][2], #screen.items[2][1] end
        local function type(text) box.input:setText(text); ConstructionScreen.update(screen, 16) end
        type("bread")
        eq(screen.items[1][1][1], bakery, "the bakery makes bread"); eq(table.concat({shown()}, ","), "1,0,0", "other tabs are empty")
        eq(box.countText:getText(), "1 of 4", "count over all tabs"); eq(screen.itemList.reloads, 1, "the list is redrawn")
        type("peas"); eq(table.concat({shown()}, ","), "0,1,0", "the cannery uses peas")
        type("storage"); eq(screen.items[1][1][1], silo, "by tab title")
        type("fence"); eq(table.concat({shown()}, ","), "0,0,1", "an entry without a store item, by name")
        type("$<100k"); eq(table.concat({shown()}, ","), "2,0,0", "price filter")
        type("$<100k -silo"); eq(screen.items[1][1][1], bakery, "price filter and excluded word")

        -- the game rebuilds its lists while a search is typed (money changed): the new lists are filtered
        type("silo")
        screen.items = {{tabA, tabB}, {tabC}}
        ConstructionScreen.rebuildData(screen)
        eq(#screen.items[1][1], 1, "new lists are filtered at once")

        -- Esc: full lists, the very same tables
        box.input.onEscPressedCallback()
        eq(screen.items[1][1], tabA, "the game's own list is back"); eq(screen.items[2][1], tabC, "in every tab")
        eq(box.countText:getText(), "", "no count")
        -- closing the screen with a search: next visit starts with everything
        type("silo")
        box.root.onCloseCallback()
        eq(box:getText(), ""); eq(screen.items[1][1], tabA, "closing resets the lists")

        -- end of typing: the screen registers its menu actions again; Ctrl+F is among them
        box:captureInput(); ConstructionScreen.update(screen, 16); box:releaseInput(); ConstructionScreen.update(screen, 16)
        eq(calls("ConstructionScreen.updateMenuState"), 1, "menu actions restored after typing")
        ConstructionScreen.registerMenuActionEvents(screen)
        eq(EVENTS[1].action, "SEARCHABLES_SEARCH", "Search action"); eq(screen.menuEvents[1], 1, "removed with the screen's events")
        fireSearchAction()
        eq(box:getIsCapturing(), true, "Ctrl+F focuses the box")

        -- a game patch changed how the lists are kept: one warning, search off, no error
        screen.items = {{"not a list"}}
        ConstructionScreen.rebuildData(screen); ConstructionScreen.rebuildData(screen)
        local warnings = 0
        for _, line in ipairs(LOG) do if line:find("not laid out as expected", 1, true) then warnings = warnings + 1 end end
        eq(warnings, 1, "warned once")
        ConstructionScreen.update(screen, 16)
        eq(box.root.visible, false, "box hidden")
        type("silo")
        eq(screen.items[1][1], "not a list", "lists left alone")
        screen.items = nil
        ConstructionScreen.rebuildData(screen)
    ''')


@test
def test_controls_search():
    """Controls page: rows are read through the game's own cell filling and the list is remapped while searching."""
    env = Env()
    env.run('''
        local function cell(extra)
            local c = El.new({elements = {El.new({text = ""}), El.new({text = ""})}})
            for key, value in pairs(extra or {}) do c[key] = value end
            return c
        end
        local header, row = cell({isHeader = true}), cell()
        local list = El.new({elements = {header, row}, absPosition = {0.2, 0.1}, absSize = {0.6, 0.6}})
        local frame = InGameMenuSettingsFrame.new({name = "settings", controlsList = list, elements = {El.new()},
            categoryHeaderText = El.new({absPosition = {0.2, 0.85}, absSize = {0.3, 0.05}}),
            sections = {{title = "Vehicle", rows = {{"Cruise control", "KEY 3"}, {"Lights", "KEY F"}}},
                {title = "General", rows = {{"Pause", "KEY P"}, {"Search", "Left Ctrl + F"}}}}})
        list.dataSource = frame

        InGameMenuSettingsFrame.update(frame, 16)
        local box = SearchablesControls.box
        local width, height = box:getSize()
        near(box.root.absPosition[1], 0.2 + 0.6 - width, "right end of the list"); near(box.root.absPosition[2], 0.85 + (0.05 - height) * 0.5, "level with the title")
        eq(frame:getNumberOfSections(list), 2, "unfiltered")

        local function type(text) box.input:setText(text); InGameMenuSettingsFrame.update(frame, 16) end
        local function rows()
            local lines = {}
            for section = 1, frame:getNumberOfSections(list) do
                for index = 1, frame:getNumberOfItemsInSection(list, section) do
                    local target = cell()
                    frame:populateCellForItemInSection(list, section, index, target)
                    table.insert(lines, frame:getTitleForSectionHeader(list, section) .. ": " .. target.elements[1].text)
                end
            end
            return table.concat(lines, " | ")
        end
        type("ctrl")
        eq(rows(), "General: Search", "by key"); eq(box.countText:getText(), "1 of 4", "count"); eq(list.reloads, 1, "list reloaded")
        eq(header.elements[1].text, "", "the header cell is not used for reading rows")
        type("vehicle"); eq(rows(), "Vehicle: Cruise control | Vehicle: Lights", "by section")
        type("CRUISE control"); eq(rows(), "Vehicle: Cruise control", "by action name")
        type("key -lights"); eq(rows(), "Vehicle: Cruise control | General: Pause", "excluded word")
        type("zzz"); eq(rows(), "", "nothing"); eq(box.countText:getText(), "nothing found", "nothing found")
        eq(frame:populateCellForItemInSection(list, 3, 1, cell()), nil, "a row the filter does not have: nothing, no error")

        -- a key was changed: the page rebuilds its list, rows are read again and the search stays
        type("f5")
        frame.sections[1].rows[2][2] = "KEY F5"
        InGameMenuSettingsFrame.setupControlsView(frame)
        eq(rows(), "Vehicle: Lights", "rows read again after the page rebuilt its list")

        type(""); eq(rows(), "Vehicle: Cruise control | Vehicle: Lights | General: Pause | General: Search", "empty box: everything")
        eq(box.countText:getText(), "", "no count")
        -- a filter only (no words) does not filter controls
        type("hp>200"); eq(frame:getNumberOfSections(list), 2, "spec filters mean nothing here")
        -- leaving the menu resets
        type("pause"); box.root.onCloseCallback()
        eq(box:getText(), ""); eq(SearchablesControls.mapping, nil, "closing resets the filter")

        -- pages of the settings frame without a controls list get no box; an empty list is left unfiltered
        SearchablesControls.delete()
        InGameMenuSettingsFrame.update(InGameMenuSettingsFrame.new({name = "general settings"}), 16)
        eq(SearchablesControls.box, nil, "no box without a controls list")
        frame.sections = {{title = "Vehicle", rows = {}}}
        InGameMenuSettingsFrame.update(frame, 16)
        SearchablesControls.box.input:setText("lights"); InGameMenuSettingsFrame.update(frame, 16)
        eq(SearchablesControls.mapping, nil, "no rows to read: unfiltered")
    ''')


@test
def test_map_search_box():
    """Map: a box per full screen map, the search runs once typing pauses, results list, clicks, quick find."""
    env = Env(STORE, WORLD)
    env.run('''
        gameClass("MapFrame")
        local frame = MapFrame.new({name = "mapFrame", elements = {El.new()}})
        local ingameMap = {worldCenterOffsetX = 1024, worldCenterOffsetZ = 1024, worldSizeX = 2048, worldSizeZ = 2048,
            mapExtensionScaleFactor = 1, mapExtensionOffsetX = 0, mapExtensionOffsetZ = 0,
            layout = {getMapObjectPosition = function(self, x, z) return x, 1 - z, 0, true end}}
        local map = IngameMapElement.new({target = frame, absPosition = {0.1, 0.1}, absSize = {0.8, 0.8}, ingameMap = ingameMap})
        local small = IngameMapElement.new({target = MapFrame.new({elements = {El.new()}}), absPosition = {0, 0}, absSize = {0.2, 0.2}})

        IngameMapElement.update(small, 16)
        eq(next(WheresMyStuffMapSearch.boxes), nil, "small preview maps get no box")
        IngameMapElement.update(map, 16)
        local box = WheresMyStuffMapSearch.boxes[frame]
        eq(calls("IngameMapElement.update"), 2, "the game's update still runs")
        eq(frame.elements[1].elements[1], box.root, "the box is in the map frame's view")
        local width, height = box:getSize()
        near(box.root.absPosition[1], 0.1 + 0.4 - width * 0.5, "centered on the map"); near(box.root.absPosition[2], 0.9 - 110 / 1080 - height, "at its top")

        -- the search waits until typing pauses
        box.input:setText("wool"); IngameMapElement.update(map, 16)
        eq(#WheresMyStuff.hotspots, 0, "not searched on the key press")
        g_time = g_time + WheresMyStuffMapSearch.SEARCH_DELAY
        IngameMapElement.update(map, 16)
        eq(#WheresMyStuff.hotspots, 2, "searched after the pause"); eq(box.countText:getText(), "2 found", "count")

        -- arrows and the list of the nearest results
        IngameMapElement.draw(map)
        eq(calls("IngameMapElement.draw"), 1, "the game's draw still runs"); eq(calls("overlayRender"), 2, "an arrow per result")
        eq(#box.listRows, 2, "list rows")
        eq(table.concat(DRAWN, "|"), "10 m|1. Wool (Pallet) - 500 L|20 m|2. Wool (Pallet) - 1,500 L", "distance and name per row")
        near(WheresMyStuffMapSearch.getArrowSize(1, 0), 160, "arrows start big"); near(WheresMyStuffMapSearch.getArrowSize(1, 900), 40, "and shrink")
        near(WheresMyStuffMapSearch.getArrowSize(3, 80), 160, "later arrows start later")
        local x, y, visible = WheresMyStuffMapSearch.worldToScreen(ingameMap, 0, 1024)
        near(x, 0.5, "world to map x"); near(y, 0, "world to map y")

        -- a click on a row pans there, a double click also sets the navigation target; other clicks go to the map
        local row = box.listRows[2]
        local cx, cy = row.x + 0.01, row.y + row.height * 0.5
        eq(IngameMapElement.mouseEvent(map, cx, cy, true, false, Input.MOUSE_BUTTON_LEFT, false), true, "mouse down on a row is used")
        eq(IngameMapElement.mouseEvent(map, cx, cy, false, true, Input.MOUSE_BUTTON_LEFT, false), true, "mouse up on a row is used")
        eq(map.pannedTo, WheresMyStuff.hotspots[2], "the map pans to the result"); eq(MAP_TARGET, nil, "one click: no target yet")
        IngameMapElement.mouseEvent(map, cx, cy, false, true, Input.MOUSE_BUTTON_LEFT, false)
        eq(MAP_TARGET, WheresMyStuff.hotspots[2], "double click: navigation target")
        eq(IngameMapElement.mouseEvent(map, 0.5, 0.05, false, true, Input.MOUSE_BUTTON_LEFT, false), "game", "clicks on the map go to the game")
        eq(calls("IngameMapElement.mouseEvent"), 1, "exactly that one")

        -- Enter searches at once and centers on the nearest
        box.input:setText("fendt"); IngameMapElement.update(map, 16)
        box.input.onEnterPressedCallback()
        eq(#WheresMyStuff.hotspots, 1, "Enter does not wait"); eq(map.pannedTo, WheresMyStuff.hotspots[1], "and centers the map on the nearest")
        -- Esc: markers gone
        box.input.onEscPressedCallback()
        g_time = g_time + WheresMyStuffMapSearch.SEARCH_DELAY
        IngameMapElement.update(map, 16)
        eq(#WheresMyStuff.hotspots, 0, "an empty box removes the markers"); eq(box.countText:getText(), "", "and the count")
        -- a spectator has no farm: no search, no error
        g_currentMission.getFarmId = function() return nil end
        box.input:setText("wool"); IngameMapElement.update(map, 16); box.input.onEnterPressedCallback()
        eq(#WheresMyStuff.hotspots, 0, "no farm, no search")
        g_currentMission.getFarmId = function() return 1 end

        -- the map closed: its box hides with it
        g_time = g_time + 1000
        box.root:update(16)
        eq(box.root.visible, false, "hidden when its map stops updating")
        IngameMapElement.update(map, 16)
        eq(box.root.visible, true, "back with the map")

        -- Ctrl+F on the map, and while walking (quick find: opens the map with the cursor in the box)
        IngameMapElement.registerActionEvents(map)
        fireSearchAction()
        eq(box:getIsCapturing(), true, "Ctrl+F on the map")
        box:releaseInput()
        g_inGameMenu = InGameMenu.new({pageMapOverview = frame})
        PlayerInputComponent.registerGlobalPlayerActionEvents({}, "onFoot")
        eq(calls("PlayerInputComponent.registerGlobalPlayerActionEvents"), 1, "the game's registration still runs")
        g_inputBinding.lastInputMode = GS_INPUT_HELP_MODE_GAMEPAD
        fireSearchAction()
        eq(calls("changeScreen"), 0, "not from a controller")
        g_inputBinding.lastInputMode = GS_INPUT_HELP_MODE_KEYBOARD
        fireSearchAction()
        eq(CHANGED_SCREEN, InGameMenu, "opens the in-game menu"); eq(g_inGameMenu.wentToPage, frame, "on the map page")
        IngameMapElement.update(map, 16)
        eq(box:getIsCapturing(), true, "with the cursor in the search box")

        -- a new box shows the search that is still marked
        WheresMyStuff.runSearch("wool", 1)
        WheresMyStuffMapSearch.delete()
        eq(next(WheresMyStuffMapSearch.boxes), nil, "boxes deleted")
        IngameMapElement.update(map, 16)
        eq(WheresMyStuffMapSearch.boxes[frame]:getText(), "wool", "the last search is shown again")
        eq(WheresMyStuffMapSearch.boxes[frame].countText:getText(), "2 found", "with its count")
    ''')


# ================================================================================================ 4. loading and hooks
HOOKS = ["Gui.update", "Gui.draw", "Gui.mouseEvent", "HelpLineManager.loadMapData", "ShopMenu.update", "ShopMenu.popDetail",
         "ShopMenu.assignMenuButtonInfo", "InGameMenu.assignMenuButtonInfo", "ShopItemsFrame.setDisplayItems",
         "ConstructionScreen.rebuildData", "ConstructionScreen.update", "ConstructionScreen.registerMenuActionEvents",
         "InGameMenuSettingsFrame.update", "InGameMenuSettingsFrame.setupControlsView",
         "PlayerInputComponent.registerGlobalPlayerActionEvents", "IngameMapElement.draw", "IngameMapElement.update",
         "IngameMapElement.mouseEvent", "IngameMapElement.registerActionEvents"]


@test
def test_scripts_load_and_hooks_register():
    """Every script of modDesc.xml loads against the stubs and replaces the game functions it hooks."""
    check([s for s in SCRIPTS if not os.path.exists(os.path.join(ROOT, s))], [], "scripts in modDesc.xml that do not exist")
    check(len(SCRIPTS), 8, "number of scripts in modDesc.xml")
    assert SCRIPTS.index("scripts/Searchables.lua") == 0, "Searchables.lua (the shared core) must load first"
    assert SCRIPTS.index("scripts/WheresMyStuffHotspot.lua") < SCRIPTS.index("scripts/WheresMyStuff.lua")
    env = Env(load=False)
    G = env.lua.globals()
    before = {hook: G[hook.split(".")[0]][hook.split(".")[1]] for hook in HOOKS}
    env.load_scripts()
    check([h for h in HOOKS if G[h.split(".")[0]][h.split(".")[1]] == before[h]], [], "game functions that were not hooked")
    env.run('''
        for _, name in ipairs({"Searchables", "SearchablesSearchBox", "SearchablesShop", "SearchablesConstruction", "SearchablesControls",
            "WheresMyStuffHotspot", "WheresMyStuff", "WheresMyStuffMapSearch"}) do
            eq(type(_G[name]), "table", "global " .. name)
        end
        eq(#LISTENERS, 2, "mod event listeners"); eq(LISTENERS[1], Searchables, "Searchables listens"); eq(LISTENERS[2], WheresMyStuff, "WheresMyStuff listens")
        eq(#LOG, 0, "nothing logged while loading: " .. table.concat(LOG, "; "))
        -- the help chapter is added after the map's own help
        eq(HelpLineManager.loadMapData(HelpLineManager.new()), true, "the game's result is passed on")
        eq(HELP_LOADED, Searchables.HELP_FILENAME, "help file loaded")
    ''')
    # the files the scripts point at are in the mod
    for name in ("Searchables.PROFILES_FILENAME", "Searchables.SEARCH_BOX_FILENAME", "Searchables.HELP_FILENAME",
                 "WheresMyStuffMapSearch.ARROW_ICON", "WheresMyStuffHotspot.ICON"):
        assert os.path.exists(env.lua.eval(name)), "%s points at a file that is not in the mod: %s" % (name, env.lua.eval(name))


@test
def test_lifecycle():
    """loadMap / update / deleteMap: profiles, console commands, specs read one item per frame."""
    env = Env(STORE)
    env.run('''
        for _, listener in ipairs(LISTENERS) do listener:loadMap("map") end
        eq(PROFILES_LOADED, Searchables.PROFILES_FILENAME, "GUI profiles loaded")
        local count = 0
        for name, command in pairs(CONSOLE) do
            count = count + 1
            eq(type(command.target[command.functionName]), "function", "console command " .. name .. " -> " .. command.functionName)
        end
        eq(count, 13, "console commands")
        eq(CONSOLE.searchablesMove.target, Searchables, "searchables commands"); eq(CONSOLE.wmsFind.target, WheresMyStuff, "wms commands")

        -- specs of vehicles and buildings are read in the background, one file per frame
        local withoutSpecs = 0
        for _, storeItem in ipairs(STORE_ITEMS) do
            if storeItem.species ~= StoreSpecies.HANDTOOL and storeItem.specs == nil then withoutSpecs = withoutSpecs + 1 end
        end
        eq(withoutSpecs >= 3, true, "the test shop has items without specs")
        Searchables:update(16)
        eq(calls("loadSpecs"), 1, "one item per frame")
        for _ = 1, 20 do Searchables:update(16) end
        eq(calls("loadSpecs"), withoutSpecs, "every vehicle and building without specs, once (also the ones that can't be read)")
        eq(Searchables.isPreloadDone, true, "then it stops")
        eq(ITEM.cannery.specs ~= nil, true, "specs are there for searching")

        eq(Searchables:consoleDumpUi(), "No screen is open. Open the shop or construction screen first (the console works on top of it).", "searchablesDumpUi without a screen")
        for i = #LISTENERS, 1, -1 do LISTENERS[i]:deleteMap() end
        eq(next(CONSOLE), nil, "console commands removed")
    ''')


@test
def test_dedicated_server():
    """On a dedicated server (no GUI) loading and the lifecycle run without touching menus."""
    env = Env(STORE, WORLD, before="g_dedicatedServer = {}; g_gui = nil", without=["g_cameraManager"])
    env.run('''
        g_cameraManager = nil
        for _, listener in ipairs(LISTENERS) do listener:loadMap("map") end
        eq(PROFILES_LOADED, nil, "no GUI profiles")
        for _ = 1, 5 do Searchables:update(16) end
        eq(calls("loadSpecs"), 0, "no background reading of specs")
        -- console searches still work (without a camera: unsorted)
        print = function() end
        eq(WheresMyStuff:consoleFind("wool"), "2 match(es) for 'wool', marked on the map. Use 'wmsTarget <number>' to navigate to one.", "wmsFind")
        for i = #LISTENERS, 1, -1 do LISTENERS[i]:deleteMap() end
    ''')


@test
def test_loads_when_game_classes_are_missing():
    """A game patch removed or renamed a class: the scripts still load, say so in the log and the rest works."""
    cases = {
        "Gui": None, "HelpLineManager": None, "InGameMenu": None, "PlayerInputComponent": None,
        "ShopMenu": "Shop menu not found", "ShopItemsFrame": "Shop menu not found", "ShopCategoriesFrame": "Shop menu not found",
        "ConstructionScreen": "Construction screen not found", "InGameMenuSettingsFrame": "Controls settings page not found",
        "IngameMapElement": "In-game map not found",
    }
    for name, warning in cases.items():
        env = Env(STORE, WORLD, without=[name])
        try:
            if warning is not None:
                assert env.lua.globals().logged(warning), "no warning in the log"
            warnings = [line for line in env.lua.eval("LOG").values() if line.startswith("warning")]
            check(len(warnings), 0 if warning is None else 1, "warnings in the log")
            env.run('''
                for _, listener in ipairs(LISTENERS) do listener:loadMap("map") end
                Searchables:update(16)
                eq(#WheresMyStuff.runSearch("wool", 1), 2, "the map search itself still works")
                eq(Searchables.matchesStoreItem(ITEM.fendt724, Searchables.parseQuery("fendt hp>200")), true, "the shop search logic too")
                for i = #LISTENERS, 1, -1 do LISTENERS[i]:deleteMap() end
            ''')
        except Exception as ex:
            raise AssertionError("without %s: %s" % (name, ex))
    # all of them at once
    env = Env(without=list(cases))
    check(len([line for line in env.lua.eval("LOG").values() if line.startswith("warning")]), 4, "warnings without any GUI class")
    # a settings page class without the list functions (the page was rebuilt): same warning, no hook
    env = Env(before="InGameMenuSettingsFrame.populateCellForItemInSection = nil")
    assert env.lua.globals().logged("Controls settings page not found"), "no warning for a settings page without list functions"


# ------------------------------------------------------------------------------------------------ run
def main():
    wanted = [a.lower() for a in sys.argv[1:]]
    tests = [t for t in TESTS if not wanted or any(w in t.__name__.lower() for w in wanted)]
    failed = 0
    for func in tests:
        try:
            func()
            print("ok    %s" % func.__name__)
        except Exception as ex:   # a failed check, a Lua error in the mod or in a test
            failed += 1
            print("FAIL  %s: %s" % (func.__name__, str(ex).strip().replace("\n", "\n      ")))
    shutil.rmtree(TMP, ignore_errors=True)
    print("%d tests: %d passed, %d failed (Lua %s, %s)" % (len(tests), len(tests) - failed, failed,
                                                         lua51.LuaRuntime().eval("_VERSION").split()[-1], ROOT))
    return 1 if failed or not tests else 0


if __name__ == "__main__":
    sys.exit(main())
