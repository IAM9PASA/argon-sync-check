-- SERVICES -------------------------

local HttpService = game:GetService("HttpService")

-- VARIABLES -------------------------

-- Studio half of tools/sync_check: reads what disk has from the local server, sends back Studio's
-- scripts, the scripts Studio has that disk doesn't, and the Studio content Argon would delete
local Config = {
	Url = "http://127.0.0.1:34873/",
	ChunkBytes = 700000, -- roblox refuses posts over 1 MB, so script sources go in pieces
}

local toolbar = plugin:CreateToolbar("Sync Check")
local button = toolbar:CreateButton("Sync Check", "Compare Studio with disk before connecting Argon", "rbxassetid://4458901886")
button.ClickableWhenViewportHidden = true

-- FUNCTIONS -------------------------

local function resolve(path: string, sep: string): Instance?
	local parts = string.split(path, sep)
	local ok, current = pcall(game.GetService, game, parts[1])
	if not ok then
		current = game:FindFirstChild(parts[1])
	end
	for i = 2, #parts do
		if not current then
			return
		end
		current = current:FindFirstChild(parts[i])
	end
	return current
end

local function check()
	local ok, response = pcall(HttpService.GetAsync, HttpService, Config.Url)
	if not ok then
		warn("[sync_check] server not running, start it with: python tools/sync_check/sync_check.py")
		return
	end
	local data = HttpService:JSONDecode(response)
	local sep, index = data.sep, data.index

	local report = { new_scripts = {}, removals = {}, missing = {} }
	local chunk, chunk_bytes = {}, 0
	local function send_chunk()
		if chunk_bytes == 0 then
			return
		end
		HttpService:PostAsync(Config.Url .. "part", HttpService:JSONEncode(chunk))
		chunk, chunk_bytes = {}, 0
	end
	for path, entry in index do
		local kind, keep = entry[1], entry[2]
		local instance = resolve(path, sep)
		if not instance then
			local parent_path = path:match("^(.*)" .. sep)
			if parent_path and resolve(parent_path, sep) then
				table.insert(report.missing, path)
			end
			continue
		end
		if kind == "opaque" then
			continue
		end

		if kind == "script" and instance:IsA("LuaSourceContainer") then
			local source = (instance :: any).Source
			if chunk_bytes + #source > Config.ChunkBytes then
				send_chunk()
			end
			chunk[path] = source
			chunk_bytes += #source + #path
		end
		if keep then
			continue
		end
		for _, child in instance:GetChildren() do
			local child_path = path .. sep .. child.Name
			if index[child_path] then
				continue
			end
			if child:IsA("LuaSourceContainer") then
				report.new_scripts[child_path] = {
					class = child.ClassName,
					source = (child :: any).Source,
					children = #child:GetChildren() > 0,
				}
			else
				table.insert(report.removals, { child_path, child.ClassName })
			end
		end
	end
	table.sort(report.missing)
	send_chunk()

	local posted, result = pcall(HttpService.PostAsync, HttpService, Config.Url, HttpService:JSONEncode(report))
	if not posted then
		warn("[sync_check] couldn't send the report: " .. tostring(result))
		return
	end
	if result:find("CLEAN") then
		print("[sync_check]\n" .. result)
	else
		warn("[sync_check]\n" .. result)
	end
end

-- MAIN -------------------------

button.Click:Connect(function()
	button:SetActive(false)
	check()
end)
