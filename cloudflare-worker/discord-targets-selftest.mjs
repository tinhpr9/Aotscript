import fs from "node:fs/promises";
import vm from "node:vm";

const source = await fs.readFile(new URL("./worker.js", import.meta.url), "utf8");

let handleUpdateCalledWith = null;

const context = vm.createContext({
  console,
  handleUpdate: async (update, env) => {
    handleUpdateCalledWith = update;
  }
});

// Extract handleDiscordInteraction
const funcMatch = source.match(/async function handleDiscordInteraction.*?}\s*async function handleDiscordComponent/s);
if (!funcMatch) throw new Error("Could not find handleDiscordInteraction");
let codeToRun = funcMatch[0].replace(/async function handleDiscordComponent.*/, "");
vm.runInContext(codeToRun, context);

async function runTests() {
  const env = {
    DISCORD_ADMIN_USER_ID: "12345",
    TELEGRAM_ADMIN_USER_ID: "67890"
  };

  const handle = async (interaction) => {
    handleUpdateCalledWith = null;
    await vm.runInContext(`handleDiscordInteraction(${JSON.stringify(interaction)}, ${JSON.stringify(env)})`, context);
    return handleUpdateCalledWith;
  };

  // 1. Auth bypass failure
  const t1 = await handle({
    user: { id: "99999" },
    data: { name: "aot" }
  });
  if (t1 !== null) throw new Error("test 1 fail - should block unauthorized");

  // 2. Auth success -> /aot to marmot
  const t2 = await handle({
    member: { user: { id: "12345" } },
    application_id: "app1",
    token: "tok1",
    data: {
      name: "aot",
      options: [{ name: "input", value: "to marmot reboot" }]
    }
  });
  if (!t2) throw new Error("test 2 fail - authorized should pass");
  if (t2.message.from.id !== "67890") throw new Error("test 2 fail - should spoof telegram admin id");
  if (t2.message.chat.id !== "discord:app1:tok1") throw new Error("test 2 fail - chat id mismatch");
  if (t2.message.text !== "/to marmot reboot") throw new Error("test 2 fail - text mismatch: " + t2.message.text);

  // 3. Fallback to command name
  const t3 = await handle({
    user: { id: "12345" },
    application_id: "app1",
    token: "tok2",
    data: {
      name: "status"
    }
  });
  if (t3.message.text !== "/status") throw new Error("test 3 fail - missing slash fallback");

  console.log("AOT_DISCORD_ROUTING_SELFTEST=OK");
}

runTests().catch(e => { console.error(e); process.exit(1); });
