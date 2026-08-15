const fs = require('fs');
const file = 'cloudflare-worker/worker.js';
let content = fs.readFileSync(file, 'utf8');

// 1. Add ctx to fetch
content = content.replace(/async fetch\(request, env\) \{/, 'async fetch(request, env, ctx) {');

// 2. Add discord route
const discordRoute = `
      if (request.method === "POST" && url.pathname === "/discord") {
        const signature = request.headers.get("x-signature-ed25519");
        const timestamp = request.headers.get("x-signature-timestamp");
        const body = await request.text();

        if (!signature || !timestamp || !env.DISCORD_PUBLIC_KEY) {
          return new Response("Unauthorized", { status: 401 });
        }

        const isValid = await verifyDiscordSignature(signature, timestamp, body, env.DISCORD_PUBLIC_KEY);
        if (!isValid) {
          return new Response("Invalid signature", { status: 401 });
        }

        const interaction = JSON.parse(body);
        if (interaction.type === 1) {
          return json({ type: 1 });
        }

        if (interaction.type === 2) {
          ctx.waitUntil(handleDiscordInteraction(interaction, env));
          return json({ type: 5 }); // DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
        }

        if (interaction.type === 3) {
          ctx.waitUntil(handleDiscordComponent(interaction, env));
          return json({ type: 6 }); // DEFERRED_UPDATE_MESSAGE
        }

        return json({ error: "unknown interaction type" }, 400);
      }
`;
content = content.replace(/if \(request.method === "POST" && url.pathname === "\/telegram"\) \{/, discordRoute + '\n      if (request.method === "POST" && url.pathname === "/telegram") {');

// 3. Update sendMessage
const originalSendMessage = `async function sendMessage(chatId, env, textValue, replyMarkup) {
  return telegram(env, "sendMessage", {
    chat_id: chatId,
    text: textValue,
    reply_markup: replyMarkup,
  });
}`;
const newSendMessage = `function convertToDiscordComponents(replyMarkup) {
  if (!replyMarkup || !replyMarkup.inline_keyboard) return undefined;
  const components = [];
  for (const row of replyMarkup.inline_keyboard) {
    const actionRow = { type: 1, components: [] };
    for (const btn of row) {
      actionRow.components.push({
        type: 2,
        label: String(btn.text || "Button").substring(0, 80),
        style: 1,
        custom_id: String(btn.callback_data || "btn").substring(0, 100)
      });
    }
    components.push(actionRow);
  }
  return components;
}

async function sendMessage(chatId, env, textValue, replyMarkup) {
  if (String(chatId).startsWith("discord:")) {
    const [, appId, token] = String(chatId).split(":");
    let content = String(textValue || "");
    if (content.length > 2000) content = content.substring(0, 1997) + "...";
    const payload = { content };
    const comps = convertToDiscordComponents(replyMarkup);
    if (comps && comps.length > 0) payload.components = comps;
    const discordUrl = \`https://discord.com/api/v10/webhooks/\${appId}/\${token}\`;
    await fetch(discordUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return;
  }
  return telegram(env, "sendMessage", {
    chat_id: chatId,
    text: textValue,
    reply_markup: replyMarkup,
  });
}`;
content = content.replace(originalSendMessage, newSendMessage);

// 4. Update editMessage
const originalEditMessage = `async function editMessage(chatId, messageId, env, textValue, replyMarkup) {
  return telegram(env, "editMessageText", {
    chat_id: chatId,
    message_id: messageId,
    text: textValue,
    reply_markup: replyMarkup,
  });
}`;
const newEditMessage = `async function editMessage(chatId, messageId, env, textValue, replyMarkup) {
  if (String(chatId).startsWith("discord:")) {
    const [, appId, token] = String(chatId).split(":");
    let content = String(textValue || "");
    if (content.length > 2000) content = content.substring(0, 1997) + "...";
    const payload = { content };
    const comps = convertToDiscordComponents(replyMarkup);
    if (comps && comps.length > 0) payload.components = comps;
    // For Discord, we just edit the original response
    const discordUrl = \`https://discord.com/api/v10/webhooks/\${appId}/\${token}/messages/@original\`;
    await fetch(discordUrl, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return;
  }
  return telegram(env, "editMessageText", {
    chat_id: chatId,
    message_id: messageId,
    text: textValue,
    reply_markup: replyMarkup,
  });
}`;
content = content.replace(originalEditMessage, newEditMessage);

// 5. Update answerCallback
const originalAnswerCallback = `async function answerCallback(callbackQueryId, env, textValue, showAlert = false) {
  return telegram(env, "answerCallbackQuery", {
    callback_query_id: callbackQueryId,
    ...(textValue ? { text: textValue, show_alert: showAlert } : {}),
  });
}`;
const newAnswerCallback = `async function answerCallback(callbackQueryId, env, textValue, showAlert = false) {
  if (String(callbackQueryId).startsWith("discord:")) {
    if (!textValue) return;
    const [, appId, token] = String(callbackQueryId).split(":");
    let content = String(textValue || "");
    if (content.length > 2000) content = content.substring(0, 1997) + "...";
    const discordUrl = \`https://discord.com/api/v10/webhooks/\${appId}/\${token}\`;
    await fetch(discordUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content })
    });
    return;
  }
  return telegram(env, "answerCallbackQuery", {
    callback_query_id: callbackQueryId,
    ...(textValue ? { text: textValue, show_alert: showAlert } : {}),
  });
}`;
content = content.replace(originalAnswerCallback, newAnswerCallback);

// 6. Append Discord functions at the end of the file
const discordFunctions = `
// === DISCORD INTEGRATION ===
function hexToUint8Array(hex) {
  return new Uint8Array(hex.match(/.{1,2}/g).map(byte => parseInt(byte, 16)));
}

async function verifyDiscordSignature(signature, timestamp, body, publicKeyHex) {
  try {
    const sig = hexToUint8Array(signature);
    const keyBytes = hexToUint8Array(publicKeyHex);
    const data = new TextEncoder().encode(timestamp + body);
    try {
      const key = await crypto.subtle.importKey("raw", keyBytes, "Ed25519", false, ["verify"]);
      return await crypto.subtle.verify("Ed25519", key, sig, data);
    } catch (e) {
      const key = await crypto.subtle.importKey("raw", keyBytes, {name: "NODE-ED25519", namedCurve: "NODE-ED25519"}, false, ["verify"]);
      return await crypto.subtle.verify("NODE-ED25519", key, sig, data);
    }
  } catch(e) {
    return false;
  }
}

async function handleDiscordInteraction(interaction, env) {
  if (String(interaction.member?.user?.id || interaction.user?.id) !== String(env.DISCORD_ADMIN_USER_ID)) {
    return;
  }

  let input = "";
  if (interaction.data?.name === "aot") {
    input = interaction.data?.options?.find(o => o.name === "input")?.value || "";
    input = input.trim();
    if (input && !input.startsWith("/")) input = \`/\${input}\`;
  } else {
    input = \`/\${interaction.data?.name}\`;
    if (interaction.data?.options) {
      for (const opt of interaction.data?.options) {
        input += \` \${opt.value}\`;
      }
    }
  }

  const update = {
    message: {
      from: { id: env.TELEGRAM_ADMIN_USER_ID },
      chat: { id: \`discord:\${interaction.application_id}:\${interaction.token}\` },
      text: input
    }
  };

  await handleUpdate(update, env);
}

async function handleDiscordComponent(interaction, env) {
  if (String(interaction.member?.user?.id || interaction.user?.id) !== String(env.DISCORD_ADMIN_USER_ID)) {
    return;
  }
  const customId = interaction.data?.custom_id;
  const update = {
    callback_query: {
      id: \`discord:\${interaction.application_id}:\${interaction.token}\`,
      from: { id: env.TELEGRAM_ADMIN_USER_ID },
      message: {
        chat: { id: \`discord:\${interaction.application_id}:\${interaction.token}\` },
        message_id: "@original"
      },
      data: customId
    }
  };
  await handleUpdate(update, env);
}
`;
content += discordFunctions;

fs.writeFileSync(file, content);
console.log("worker.js patched");
