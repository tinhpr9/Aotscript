task.wait(15)
local P=game.Players.LocalPlayer
local VIM=game:GetService("VirtualInputManager")
local GS=game:GetService("GuiService")
local Gui=P:WaitForChild("PlayerGui")
local pid=game.PlaceId

local function cg(p,d,oY)
        local t=Gui
        for n in p:gmatch("[^%.]+") do t=t:WaitForChild(n,10) if not t then warn("❌ "..p) task.wait(d or 1) return end end
        local s=t.Parent while s and not s:IsA("ScrollingFrame") do s=s.Parent end
        if s and s:IsA("ScrollingFrame") then
                task.wait(0.1)
                local tT,tB,sT,sB=t.AbsolutePosition.Y,t.AbsolutePosition.Y+t.AbsoluteSize.Y,s.AbsolutePosition.Y,s.AbsolutePosition.Y+s.AbsoluteSize.Y
                local cY,mY=s.CanvasPosition.Y,math.max(0,s.AbsoluteCanvasSize.Y-s.AbsoluteSize.Y)
                if tT<sT then s.CanvasPosition=Vector2.new(s.CanvasPosition.X,math.clamp(cY-(sT-tT)-10,0,mY))
                elseif tB>sB then s.CanvasPosition=Vector2.new(s.CanvasPosition.X,math.clamp(cY+(tB-sB)+10,0,mY)) end
                task.wait(0.3)
        end
        local x,y=t.AbsolutePosition.X+t.AbsoluteSize.X/2,t.AbsolutePosition.Y+t.AbsoluteSize.Y/2+GS:GetGuiInset().Y+(oY or 0)
        VIM:SendMouseButtonEvent(x,y,0,true,game,1) task.wait(0.02) VIM:SendMouseButtonEvent(x,y,0,false,game,1)
        pcall(function() for _,c in pairs(getconnections(t.MouseButton1Click)) do pcall(function() c:Fire() end) end end)
        pcall(function() for _,c in pairs(getconnections(t.Activated)) do pcall(function() c:Fire() end) end end)
        task.wait(d or 1)
end

local function cp(part,d)
        pcall(function() fireclickdetector(part:FindFirstChildWhichIsA("ClickDetector",true)) end)
        pcall(function() fireproximityprompt(part:FindFirstChildWhichIsA("ProximityPrompt",true)) end)
        pcall(function() firetouchinterest(P.Character.HumanoidRootPart,part,0) task.wait(0.1) firetouchinterest(P.Character.HumanoidRootPart,part,1) end)
        pcall(function() local p=workspace.CurrentCamera:WorldToScreenPoint(part.Position) VIM:SendMouseButtonEvent(p.X,p.Y,0,true,game,1) task.wait(0.02) VIM:SendMouseButtonEvent(p.X,p.Y,0,false,game,1) end)
        task.wait(d or 1)
end

local function wfc(...) local t=Gui for _,n in ipairs({...}) do t=t:WaitForChild(n,10) if not t then return nil end end return t end

if pid==13379208636 then cg("Interface.Title_Screen.Slots.A.Select_A",10) cg("Interface.Title_Screen.Buttons.Play",3) cg("Fullscreen.Lobbies.Main.Lobby",5) end
task.wait(10)
cg("Interface.Topbar.Main.Categories.Equipment",2)

local lvl=tonumber(wfc("Interface","Gear_Up","HUD","Level","Title").Text:match("%d+")) or 0
local rk=(wfc("Interface","Equipment","Categories","Upgrades","Main","Title").Text:match("%[(%a)") or ""):upper()
local fw=({E=0,D=1,C=2,B=3})[rk] or 3
print("📊 Lvl:",lvl,"Rank:",rk,"Fw:",fw)

if lvl==100 and pid==14916516914 then
        cg("Interface.Equipment.Categories.Prestige.Main.Title",2)
        cg("Interface.Equipment.Prestige.B_Prestige.Title",2)
        cg("Interface.Warning.Prompt.Main.Yes.Title",60)
        cp(workspace.Objects.Blackout.Memories["Luck Boost"],5)
        cg("Interface.Memories_Buttons.M_Confirm.Title",5)
        local c=workspace.Objects.Blackout.Memories:GetChildren()
        cp(c[math.random(#c)],1)
        cg("Interface.Memories_Buttons.M_Confirm.Title",5)
        print("🍯 Prestige!")
else
        for _,s in ipairs({"ODM_Speed","ODM_Range","ODM_Gas","ODM_Damage","ODM_Crit_Damage","ODM_Crit_Chance","ODM_Control","Blade_Durability"}) do
                cg("Interface.Equipment.Stats.Blades."..s,0.8) cg("Interface.Equipment.Stat.Upgrade",1)
        end
        cg("Interface.Topbar.Main.Categories.Gear_Up.Title",2,10)
        cg("Interface.Topbar.Main.Categories.Gear_Up.Title",2,10)
        cg("Interface.Gear_Up.Mission.Main",1)
        cg("Interface.Missions.Prompt.Main.Selection.Missions",1)
        cg("Interface.Missions.Missions.Main.Maps.Maps.Outskirts_Missions",1)
        for i=1,fw do cg("Interface.Missions.Missions.Main.Info.Main.Difficulty.Forward_Missions",1) end
        cg("Interface.Missions.Missions.Main.Info.Main.Buttons.Creation_Missions",2)
        cg("Interface.Missions.Info.Main.Info.Modifiers.Modifiers_Buttons",1)
        local mb="Interface.Missions.Info.Main.Info.Modifiers.Options."
        for _,m in ipairs({"Nightmare","Injury Prone","Fog","Chronic Injuries","Oddball","No Talents","No Skills","No Perks"}) do
                cg(mb..m..".Selected",1)
        end
        cg("Interface.Missions.Info.Main.Info.Modifiers.Modifiers_Buttons",2)
        cg("Interface.Missions.Info.Main.Info.Main.Info_Buttons.Begin",2)
        print("🍯 Mission!")
end