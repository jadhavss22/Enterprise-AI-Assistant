export type ChatEvent = { event: "started" | "token" | "citations" | "completed" | "error"; data: Record<string, unknown> };

// Streaming UTF-8 decoder; handles split frames, CRLF, comments and multiple data lines.
export async function consumeChatStream(response: Response, onEvent: (value: ChatEvent) => void) {
  if (!response.body || !response.headers.get("content-type")?.includes("text/event-stream")) throw new Error("The server did not return a chat stream.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "", total = 0, terminal = false;
  function dispatch(raw: string) {
    const lines = raw.split("\n");
    const name = lines.find(line => line.startsWith("event:"))?.slice(6).trim();
    const text = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
    if (!text) return;
    if (!["started", "token", "citations", "completed", "error"].includes(name ?? "")) throw new Error("Unexpected chat event.");
    const data: unknown = JSON.parse(text);
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Invalid chat event.");
    if (terminal) throw new Error("Chat stream continued after completion.");
    onEvent({ event: name as ChatEvent["event"], data: data as Record<string, unknown> });
    if (name === "completed" || name === "error") terminal = true;
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) { buffer += decoder.decode(); break; }
      total += value.byteLength;
      if (total > 1000000) throw new Error("Chat stream exceeded its size limit.");
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        dispatch(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2);
      }
      if (buffer.length > 64000) throw new Error("Chat event exceeded its size limit.");
    }
    if (buffer.trim()) throw new Error("Chat stream ended in an incomplete event.");
    if (!terminal) throw new Error("Connection ended before the answer completed. Reload the conversation to check its status.");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
