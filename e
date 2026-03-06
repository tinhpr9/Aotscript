-- ts file was generated at discord.gg/25ms

local fenv = getfenv()
local _5 = loadstring(game:HttpGet('https://github.com/dawid-scripts/Fluent/releases/latest/download/main.lua'))()
local _9 = loadstring(game:HttpGet('https://raw.githubusercontent.com/dawid-scripts/Fluent/master/Addons/SaveManager.lua'))()
local _13 = loadstring(game:HttpGet('https://raw.githubusercontent.com/dawid-scripts/Fluent/master/Addons/InterfaceManager.lua'))()
local _call15 = game:GetService('Players')

game:GetService('TweenService')
game:GetService('RunService')

local _call21 = game:GetService('ReplicatedStorage')
local _call23 = game:GetService('PhysicsService')
local _LocalPlayer24 = _call15.LocalPlayer
local _Character25 = _LocalPlayer24.Character

_Character25:WaitForChild('HumanoidRootPart')
_Character25:WaitForChild('Humanoid')
_LocalPlayer24.CharacterAdded:Connect(function(_33, _33_2)
    _33:WaitForChild('HumanoidRootPart')
    _33:WaitForChild('Humanoid')

    local _Died40 = _33:WaitForChild('Humanoid').Died
    local _ = fenv.onDeath

    _Died40:Connect(nil)

    local _ = fenv.AUTO_FARM
    local _ = fenv.CURRENT_DELTA_TWEEN
end)
_call23:RegisterCollisionGroup('GhostPlayer')

for _50, _50_2 in pairs(_call23:GetRegisteredCollisionGroups())do
    local _ = _50_2.name

    _call23:CollisionGroupSetCollidable('GhostPlayer', _50_2.name, false)
end

task.spawn(function(_57, _57_2)
    for _62, _62_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        local _call70 = _62_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        local _ = _call70.Text == _LocalPlayer24.DisplayName
        local _ = _call70.Text == _LocalPlayer24.Name
    end

    warn('\u{23f3} Waiting for BASE...')
    task.wait(1)

    for _82, _82_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        local _call90 = _82_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        local _ = _call90.Text == _LocalPlayer24.DisplayName
        local _ = _call90.Text == _LocalPlayer24.Name
    end

    warn('\u{23f3} Waiting for BASE...')
    task.wait(1)

    for _102, _102_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        local _call110 = _102_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        local _ = _call110.Text == _LocalPlayer24.DisplayName
        local _ = _call110.Text == _LocalPlayer24.Name
    end

    warn('\u{23f3} Waiting for BASE...')
    task.wait(1)

    for _122, _122_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        local _call130 = _122_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        local _ = _call130.Text == _LocalPlayer24.DisplayName
        local _ = _call130.Text == _LocalPlayer24.Name
    end

    warn('\u{23f3} Waiting for BASE...')
    task.wait(1)

    for _142, _142_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        local _call150 = _142_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        local _ = _call150.Text == _LocalPlayer24.DisplayName
        local _ = _call150.Text == _LocalPlayer24.Name
    end

    warn('\u{23f3} Waiting for BASE...')
    task.wait(1)

    for _162, _162_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        local _call170 = _162_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        local _ = _call170.Text == _LocalPlayer24.DisplayName
        local _ = _call170.Text == _LocalPlayer24.Name
    end

    warn('\u{23f3} Waiting for BASE...')
    task.wait(1)

    for _182, _182_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        local _call190 = _182_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        local _ = _call190.Text == _LocalPlayer24.DisplayName
        local _ = _call190.Text == _LocalPlayer24.Name
    end

    warn('\u{23f3} Waiting for BASE...')
    task.wait(1)

    for _202, _202_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        local _call210 = _202_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        local _ = _call210.Text == _LocalPlayer24.DisplayName
        local _ = _call210.Text == _LocalPlayer24.Name
    end

    warn('\u{23f3} Waiting for BASE...')
    task.wait(1)

    for _222, _222_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        local _call230 = _222_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        local _ = _call230.Text == _LocalPlayer24.DisplayName
        local _ = _call230.Text == _LocalPlayer24.Name
    end

    warn('\u{23f3} Waiting for BASE...')
    task.wait(1)

    for _242, _242_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        local _call250 = _242_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        local _ = _call250.Text == _LocalPlayer24.DisplayName
        local _ = _call250.Text == _LocalPlayer24.Name
    end

    warn('\u{23f3} Waiting for BASE...')
    task.wait(1)

    for _262, _262_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        local _call270 = _262_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        local _ = _call270.Text == _LocalPlayer24.DisplayName
        local _ = _call270.Text == _LocalPlayer24.Name
    end

    warn('\u{23f3} Waiting for BASE...')
    task.wait(1)

    for _282, _282_2 in ipairs(workspace:WaitForChild('Bases'):GetChildren())do
        _282_2:FindFirstChild('Title'):FindFirstChild('TitleGui'):FindFirstChild('Frame'):FindFirstChild('PlayerName')
        error('internal 557: <25ms: infinitelooperror>')
    end
end)
task.spawn(function(_294)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    task.wait(1)
    error('internal 557: <25ms: infinitelooperror>')
end)
Vector3.new(147.00874328613, -7, -19.401155471802)
CFrame.new(Vector3.new(147.00874328613, 3.2539694309235, -19.401155471802))
CFrame.new(125.068192, 4.25395036, -138)

fenv.AUTO_FARM_RUNNING = false
fenv.startAutoFarm = function()
    task.spawn(function(_306)
        fenv.AUTO_FARM_RUNNING = true

        for _309, _309_2 in ipairs(_33:GetDescendants())do
            _309_2:IsA('BasePart')

            _309_2.CollisionGroup = 'Default'
        end

        fenv.AUTO_FARM_RUNNING = false
    end)
end

game:GetService('GuiService')
game:GetService('VirtualInputManager')
game:GetService('UserInputService')
task.spawn(function(_320, _320_2, _320_3, _320_4)
    task.wait(0.2)
    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function(_323, _323_2, _323_3, _323_4, _323_5)
    task.wait(0.08)
    error('internal 557: <25ms: infinitelooperror>')
end)
_call21:WaitForChild('Shared'):WaitForChild('Remotes'):WaitForChild('Networking'):WaitForChild('RF/PlotAction')
task.spawn(function()
    task.wait(0.25)

    local _ = fenv.AUTO_MONEY

    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function(_338, _338_2, _338_3)
    task.wait(0.5)
    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function()
    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function()
    task.wait(0.8)
    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function(_347, _347_2, _347_3)
    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function(_350, _350_2, _350_3)
    task.wait(0.6)
    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function(_353, _353_2, _353_3, _353_4, _353_5)
    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function(_356, _356_2, _356_3, _356_4)
    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function(_359, _359_2, _359_3, _359_4)
    task.wait(0.12)
    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function(_362, _362_2, _362_3, _362_4)
    error('internal 557: <25ms: infinitelooperror>')
end)
Vector3.new(147.00874328613, -5, -19.401155471802)
game:GetService('TweenService')
task.spawn(function()
    error('internal 557: <25ms: infinitelooperror>')
end)

local _call371 = game:GetService('RunService')

_call371.Heartbeat:Connect(function(_375, _375_2, _375_3) end)
task.spawn(function(_378, _378_2, _378_3, _378_4)
    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function(_381, _381_2, _381_3, _381_4)
    task.wait(0.08)
    error('internal 557: <25ms: infinitelooperror>')
end)
task.spawn(function()
    task.wait(1)
    error('internal 557: <25ms: infinitelooperror>')
end)
Vector3.new(95.5, -20.58, 0)
Vector3.new(1000, 6, 336)
Vector3.new(4, 100, 70)

local _ = CFrame.new(0, 16, -139.999985) * CFrame.fromMatrix(Vector3.zero, Vector3.new(0, 0, 1), Vector3.new(-1, 0, 0), Vector3.new(0, -1, 0))
local _ = CFrame.new(0, 16, 160) * CFrame.fromMatrix(Vector3.zero, Vector3.new(0, 0, 1), Vector3.new(-1, 0, 0), Vector3.new(0, -1, 0))

Vector3.new(90.049987792969, 6, 20)
CFrame.new(-3, -3.1, -139.38501)
CFrame.Angles(1.5707963267948966, 1.5707963267948966, 0)

local _call425 = Instance.new('ScreenGui')

_call425.Name = 'EventTimerHUD'
_call425.ResetOnSpawn = false
_call425.Enabled = false
_call425.Parent = _LocalPlayer24:WaitForChild('PlayerGui')

local _call427 = Instance.new('Frame')

_call427.Parent = _call425
_call427.Size = UDim2.fromScale(0.2, 0.07)
_call427.Position = UDim2.fromScale(0.78, 0.75)
_call427.BackgroundTransparency = 1

task.spawn(function()
    task.wait(0.2)
    error('internal 557: <25ms: infinitelooperror>')
end)
Vector3.new(1, 1, 1)

fenv.ToggleWeaponScale = function(_437, _437_2, _437_3, _437_4, _437_5) end
fenv.SetWeaponScale = function(_438, _438_2, _438_3, _438_4, _438_5, _438_6, _438_7) end
fenv.SetLinkedScale = function(_439) end
fenv.ResetWeaponScale = function(_440, _440_2, _440_3, _440_4, _440_5) end
fenv.ToggleWeaponInvisible = function(_441, _441_2, _441_3) end

_call371.RenderStepped:Connect(function(_445) end)
_LocalPlayer24.CharacterAdded:Connect(function() end)

local _ = workspace.CurrentCamera
local _ = _LocalPlayer24.CameraMinZoomDistance
local _ = _LocalPlayer24.CameraMaxZoomDistance
local _ = _LocalPlayer24.DevCameraOcclusionMode

_call23:CreateCollisionGroup('HitboxGhost')
_call23:CollisionGroupSetCollidable('HitboxGhost', 'Default', false)

for _459, _459_2 in ipairs(_call15:GetPlayers())do
    local _ = _459_2 == _LocalPlayer24
    local _ = _459_2.Character

    _459_2.Character:WaitForChild('HumanoidRootPart', 5):FindFirstChild('ScaledTag')
    _459_2.CharacterAdded:Connect(function(_470) end)
end

_call15.PlayerAdded:Connect(function(_474) end)
game:GetService('UserInputService').JumpRequest:Connect(function(_480, _480_2, _480_3) end)
game:GetService('VirtualUser')
_call15.LocalPlayer.Idled:Connect(function(_487, _487_2, _487_3, _487_4) end)

local _call493 = _5:CreateWindow({
    SubTitle = 'Escape tsunami for brainrots\u{1f30a}v7.4',
    Title = 'YT OsakaTP2 |',
    MinimizeKey = Enum.KeyCode.LeftAlt,
    Theme = 'Dark',
    Acrylic = true,
    TabWidth = 160,
    Size = UDim2.fromOffset(520, 380),
})
local _call495 = _call493:AddTab({
    Title = 'Main',
    Icon = 'home',
})
local _call497 = _call493:AddTab({
    Title = 'Farm setting',
    Icon = 'cog',
})
local _call499 = _call493:AddTab({
    Title = 'farm Auto',
    Icon = 'box',
})
local _call501 = _call493:AddTab({
    Title = 'Event',
    Icon = 'timer',
})
local _call503 = _call493:AddTab({
    Title = 'Fire and Ice',
    Icon = 'box',
})
local _call505 = _call493:AddTab({
    Title = 'Misc',
    Icon = 'user',
})

_call495:AddParagraph({
    Title = '\u{1f34c}Notification',
    Content = 'Follow my channel Youtube OsakaTP2, for new script updates Enjoy!!',
})
_call495:AddParagraph({
    Title = 'New Update 7.4',
    Content = '\u{2b50}Add Auto fire Ice add carry limit 3 02/03/2026',
})
_call495:AddSection('Mian')

local _call513 = _call495:AddToggle('FlyJump', {
    Title = 'Fly Jump',
    Default = false,
})

_call513:OnChanged(function(_516, _516_2, _516_3, _516_4, _516_5) end)
_call495:AddToggle('instant_prompt', {
    Callback = function(_519, _519_2) end,
    Title = 'Instant Pick',
    Default = false,
})
_call495:AddToggle('unlockcam', {
    Callback = function(_522, _522_2, _522_3, _522_4, _522_5) end,
    Title = 'Unlock Zoom',
    Default = true,
})
_call495:AddToggle('event_timer', {
    Callback = function(_525, _525_2, _525_3) end,
    Title = 'Show Event Timer',
    Default = false,
})
_call495:AddToggle('vip_wall_remove', {
    Callback = function(_528) end,
    Title = 'VIP Walls',
    Default = false,
})
_call495:AddToggle('AntiAFKToggle', {
    Callback = function(_531, _531_2, _531_3, _531_4, _531_5) end,
    Title = 'Anti AFK',
    Default = false,
})
_call497:AddSection('Custom Speed')
_call497:AddParagraph({
    Title = 'Read me',
    Content = 'Recommended speed: 500 or 800 Using a higher speed than this may cause your character to die',
})

fenv.Display = false

_call497:AddParagraph({
    Title = 'Current Speed',
    Content = '500',
})
_call497:AddSlider('custom_speed_slider', {
    Min = 100,
    Title = 'Speed Value',
    Max = 2000,
    Default = 500,
    Callback = function(_540, _540_2, _540_3, _540_4, _540_5, _540_6) end,
    Rounding = 50,
})
_call497:AddToggle('custom_speed_toggle', {
    Callback = function(_543) end,
    Title = 'Enable Custom Speed',
    Default = false,
})
_call497:AddSection('FARM BRAINROT LUCKY BLOCK')
_call497:AddDropdown('brainrot_rarity', {
    Title = 'Brainrot Rarity',
    Values = {
        [1] = 'Common',
        [2] = 'Uncommon',
        [3] = 'Rare',
        [4] = 'Epic',
        [5] = 'Legendary',
        [6] = 'Mythical',
        [7] = 'Cosmic',
        [8] = 'Secret',
        [9] = 'Celestial',
        [10] = 'Divine',
        [11] = 'Infinity',
    },
    Multi = true,
    Callback = function(_548, _548_2, _548_3) end,
    Default = {},
})
_call497:AddDropdown('lucky_rarity', {
    Title = 'Lucky Block Rarity',
    Values = {
        [1] = 'Common',
        [2] = 'Uncommon',
        [3] = 'Rare',
        [4] = 'Epic',
        [5] = 'Legendary',
        [6] = 'Mythical',
        [7] = 'Cosmic',
        [8] = 'Secret',
        [9] = 'Celestial',
        [10] = 'Divine',
        [11] = 'Admin',
        [12] = 'Infinity',
        [13] = 'FireAndIce',
        [14] = 'Doom',
        [15] = 'Volcanic',
        [16] = 'UFO',
        [17] = 'Gamer',
        [18] = 'Candy',
        [19] = 'Arcade',
        [20] = 'Sugar Rush',
    },
    Multi = true,
    Callback = function(_551, _551_2) end,
    Default = {},
})
_call497:AddDropdown('mutation_filter_dd', {
    Title = 'Lucky Block Mutations',
    Default = {},
    Values = {
        [1] = 'Fire',
        [2] = 'Ice',
        [3] = 'Blood',
        [4] = 'Doom',
        [5] = 'Electric',
        [6] = 'Candy',
        [7] = 'Gamer',
        [8] = 'Money',
        [9] = 'UFO',
        [10] = 'Admin',
        [11] = 'Hacker',
        [12] = 'Lucky',
        [13] = 'Radioactive',
    },
    Multi = true,
    Callback = function(_554, _554_2, _554_3, _554_4) end,
    Description = 'This function works independently from the Lucky Block Rarity filter',
})
_call497:AddDropdown('farm_mode', {
    Callback = function(_557, _557_2, _557_3, _557_4) end,
    Default = 'All 2 in 1',
    Title = 'Farm Mode',
    Values = {
        [1] = 'All 2 in 1',
        [2] = 'Lucky Block Only',
        [3] = 'Brainrot Only',
    },
})
_call497:AddDropdown('carry_limit', {
    Callback = function(_560, _560_2, _560_3, _560_4, _560_5) end,
    Default = '2',
    Title = 'Carry Limit',
    Values = {
        [1] = '1',
        [2] = '2',
        [3] = '3',
        [4] = '4',
        [5] = '5',
        [6] = '6',
    },
})
_call499:AddToggle('fast_farm_wall', {
    Callback = function(_563, _563_2) end,
    Default = false,
    Title = 'Wall hack',
    Description = '\u{2b50}Safe zone for long farming beta fast farm',
})
_call499:AddSection('AUTO FARM')
_call499:AddToggle('auto_farm_new', {
    Callback = function(_568, _568_2) end,
    Default = false,
    Title = 'Auto Farm',
    Description = 'Farm Rarity your setting farm',
})
_call499:AddInput('trial_target_count_input', {
    Placeholder = 'Enter number (1-20)',
    Title = 'Trial Claim At',
    Numeric = true,
    Callback = function(_571, _571_2, _571_3, _571_4, _571_5) end,
    Default = '15',
})
_call499:AddToggle('auto_trial_tower', {
    Callback = function(_574, _574_2, _574_3) end,
    Default = false,
    Title = 'Auto Trial Tower',
    Description = '\u{1f525}you can use wallhack + unlock zoom working',
})
_call499:AddButton({
    Title = '\u{1f3e0} GO SAFEZONE',
    Callback = function() end,
})
_call499:AddButton({
    Title = '\u{1f3f0} Go To Tower',
    Callback = function(_580) end,
})
_call501:AddSection('Auto run farm coin')
_call501:AddToggle('event_loop_toggle', {
    Callback = function(_585) end,
    Default = false,
    Title = 'GO AND BACK LOOP',
    Description = '\u{2b50}This function will stop working when it encounters the target brianrot you cant off auto farm',
})
_call501:AddSection('\u{1f4f1} Delta Candy/Token')
_call501:AddDropdown('delta_mode', {
    Callback = function(_590, _590_2) end,
    Default = 'Love + Candy',
    Title = '\u{1f4f1} Delta Mode',
    Values = {
        [1] = 'Love + Candy',
        [2] = 'Doom Coin + Ticket',
    },
})
_call501:AddToggle('delta_full_auto', {
    Callback = function(_593, _593_2, _593_3, _593_4, _593_5) end,
    Default = false,
    Title = 'Delta AUTO',
    Description = 'Run & Collect',
})
_call501:AddSection('DOOM Event')
_call501:AddToggle('AutoDoomCoin', {
    Callback = function(_598) end,
    Title = 'Auto Doom Coin',
    Default = false,
})
_call501:AddToggle('AutoDoomTicket', {
    Callback = function(_601, _601_2, _601_3) end,
    Title = 'Auto Doom Ticket',
    Default = false,
})
_call501:AddToggle('AutoButtonDoom', {
    Callback = function(_604) end,
    Title = 'Auto button Doom',
    Default = false,
})
_call501:AddSection('\u{1f36c}Candy Event')
_call501:AddToggle('auto_heart_candy', {
    Callback = function(_609, _609_2, _609_3) end,
    Title = 'Auto HEART Candy',
    Default = false,
})
_call501:AddToggle('auto_love_token', {
    Callback = function(_612, _612_2, _612_3, _612_4, _612_5, _612_6) end,
    Title = 'Auto LOVE Token',
    Default = false,
})
_call501:AddSection('\u{1f47e}Gamer Event')

local _call616 = _call501:AddToggle('AutoGamerCoin', {
    Title = 'Auto Coin and ticket',
    Default = false,
})

_call616:OnChanged(function(_619) end)
_call501:AddSection('\u{1f4b0} Money Event')
_call501:AddToggle('money_event', {
    Callback = function(_624, _624_2, _624_3) end,
    Title = '\u{1f4b0} Auto Money Event',
    Default = false,
})
_call501:AddToggle('money_spin', {
    Callback = function(_627, _627_2, _627_3, _627_4, _627_5, _627_6) end,
    Title = '\u{1f4b0} Money Spin',
    Default = false,
})
_call501:AddToggle('auto_mission_money', {
    Callback = function(_630, _630_2) end,
    Title = '\u{1f4b0} Mission Money',
    Default = false,
})
_call501:AddSection('\u{1f6f8} Event UFO')
_call501:AddToggle('ufo_coin', {
    Callback = function(_635, _635_2, _635_3, _635_4, _635_5) end,
    Title = '\u{1f6f8} UFO COIN',
    Default = false,
})
_call5