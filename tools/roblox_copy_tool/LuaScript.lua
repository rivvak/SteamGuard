-- Rivvak Roblox Copy Helper Studio snippet
-- Runs only against the local helper at 127.0.0.1:6969.
-- Use only in experiences/assets you own or are explicitly authorized to edit.

local HttpService = game:GetService("HttpService")
local MarketplaceService = game:GetService("MarketplaceService")
local PORT = "6969"
local BASE_URL = "http://127.0.0.1:" .. PORT .. "/"

pcall(function()
    HttpService.HttpEnabled = true
end)

for _, value in pairs(workspace:GetDescendants()) do
    if value:IsA("PackageLink") then
        value:Destroy()
    end
end

local function guid()
    return tostring(HttpService:GenerateGUID(false))
end

local function collectAnimations()
    local ids = {}
    local seen = {}
    for _, item in pairs(game:GetDescendants()) do
        if item:IsA("Animation") then
            local id = tostring(item.AnimationId or ""):match("%d+")
            if id and #id > 6 and not seen[id] then
                seen[id] = true
                ids[(item.Name or "Animation") .. "_" .. guid()] = id
            end
        end
    end
    return ids
end

local ids = collectAnimations()
print("Rivvak Roblox Copy Helper: sending " .. tostring(#(function(t) local n={} for _ in pairs(t) do table.insert(n, true) end return n end)(ids)) .. " unique animation id(s).")

HttpService:PostAsync(BASE_URL, HttpService:JSONEncode({ ids = ids }), Enum.HttpContentType.ApplicationJson)

local response
while not response do
    task.wait(2)
    local ok, body = pcall(function()
        return HttpService:GetAsync(BASE_URL)
    end)
    if ok and body and body ~= "null" and body ~= "" then
        response = HttpService:JSONDecode(body)
    end
end

for oldId, newId in pairs(response) do
    for _, item in pairs(game:GetDescendants()) do
        if item:IsA("Animation") and string.find(tostring(item.AnimationId), tostring(oldId), 1, true) then
            item.AnimationId = "rbxassetid://" .. tostring(newId)
        end
    end
end

print("Rivvak Roblox Copy Helper: mapping applied. Check the helper export folder for details.")
