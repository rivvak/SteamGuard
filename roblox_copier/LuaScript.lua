local Mode = "Normal"
local myCookie = ""
local GroupID = nil
local UserID = nil

for i,v in pairs(workspace:GetDescendants()) do if v:IsA("PackageLink") then v:Destroy() end end

local MarketplaceService = game:GetService("MarketplaceService")

local function SendPOST(ids: {any}, cookie: string?, port: string?)
	game:GetService("HttpService"):PostAsync("http://127.0.0.1:6969/", game:GetService("HttpService"):JSONEncode({["ids"]=ids, ["cookie"]=cookie and cookie or nil, ["groupID"] = GroupID and GroupID or nil}))
end

local Modes = {
	Normal = "Begins copying all animations with no filter whatsoever."
}
game:GetService("HttpService").HttpEnabled = true

local function PollForResponse(port): {any}
	local response

	while not response and task.wait(4) do
		response = game:GetService("HttpService"):JSONDecode(game:GetService("HttpService"):GetAsync("http://127.0.0.1:6969/"))
	end

	return response
end

local function ReturnUUID(): {any}
	return tostring(game:GetService("HttpService"):GenerateGUID())
end

local function Spoof(Table)
	local ids = {}

	for index,v in Table do
		local anim = v

		if type(v) == "number" or type(v) == "string" then
			anim = {AnimationId = tostring(v), Name = index}
		elseif anim.ClassName then
			if not anim:IsA("Animation") then
				continue
			end
		end

		if not anim or tonumber(anim.AnimationId:match("%d+")) == nil or string.len(anim.AnimationId:match("%d+")) <= 6 then continue end

		local foundAnimInTable = false

		for _,x in ids do
			if x == anim.AnimationId:match("%d+") then
				foundAnimInTable = true
			end
		end

		if foundAnimInTable == true then continue end

		if (GroupID and MarketplaceService:GetProductInfo(anim.AnimationId:match("%d+"), Enum.InfoType.Asset).Creator.CreatorTargetId == GroupID) or (UserID and MarketplaceService:GetProductInfo(anim.AnimationId:match("%d+"), Enum.InfoType.Asset).Creator.CreatorTargetId == UserID) then continue end

		if Mode == "Coming Soon" then
			ids[index] = anim.AnimationId:match("%d+")
		else
			ids[anim.Name..ReturnUUID()] = anim.AnimationId:match("%d+")
		end
	end

	return ids
end

local function GenerateIDList()
	if Mode ~= "Normal" then
		return {}
	end
	
	return Spoof(game:GetDescendants())
end


local idsToGet = GenerateIDList()

SendPOST(idsToGet, myCookie, "6969", GroupID)
local newIDList = PollForResponse("6969")

for oldID,newID in newIDList do
	for _,v in game:GetDescendants() do
		if v:IsA("Animation") and string.find(v.AnimationId, tonumber(oldID)) then
			v.AnimationId = "rbxassetid://"..tostring(newID)
		end
	end
end
