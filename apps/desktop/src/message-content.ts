type StoredMessage = Record<string, unknown>;

export function renderMessageContent(content: unknown): string {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map(renderMessageContent).filter(Boolean).join("\n");
  if (typeof content !== "object") return String(content);

  const message = content as StoredMessage;
  if (message.type === "image" || message.type === "image_url") return "[Image attachment]";
  if (message.type === "text" && typeof message.text === "string") return message.text;

  if (Object.hasOwn(message, "content")) {
    const nested = renderMessageContent(message.content);
    if (nested) return nested;
  }

  if (Array.isArray(message.tool_calls)) {
    return message.tool_calls.map((call) => {
      if (!call || typeof call !== "object") return "Tool call";
      const entry = call as StoredMessage;
      const fn = entry.function && typeof entry.function === "object" ? entry.function as StoredMessage : undefined;
      const name = fn?.name ?? entry.name;
      return `Tool call: ${typeof name === "string" ? name : "unknown"}`;
    }).join("\n");
  }

  return JSON.stringify(message, null, 2) ?? "";
}
