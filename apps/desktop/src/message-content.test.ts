import assert from "node:assert/strict";
import test from "node:test";
import { renderMessageContent } from "./message-content.js";

test("renders decoded user API messages as their text", () => {
  assert.equal(renderMessageContent({ role: "user", content: "Run the probe." }), "Run the probe.");
});

test("renders decoded assistant tool calls without stringifying the envelope", () => {
  assert.equal(renderMessageContent({
    role: "assistant",
    content: null,
    tool_calls: [{ type: "function", function: { name: "run_process", arguments: "{}" } }]
  }), "Tool call: run_process");
});

test("renders structured text and image content parts", () => {
  assert.equal(renderMessageContent([
    { type: "text", text: "Inspect this image." },
    { type: "image_url", image_url: { url: "data:image/png;base64,..." } }
  ]), "Inspect this image.\n[Image attachment]");
});

test("keeps unknown structured content readable and null content blank", () => {
  assert.equal(renderMessageContent({ type: "future_part", value: 7 }), '{\n  "type": "future_part",\n  "value": 7\n}');
  assert.equal(renderMessageContent(null), "");
});
