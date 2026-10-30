import assert from "node:assert/strict";
import test from "node:test";
import { openExternalHttps } from "./external-links.js";

test("external links reject local files and OS protocols before dispatch", async () => {
  const opened: string[] = [];
  const open = async (url: string) => { opened.push(url); };
  for (const url of ["file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "ms-settings:privacy", "http://example.com", "not a URL"]) {
    await assert.rejects(openExternalHttps(url, open));
  }
  assert.deepEqual(opened, []);
  await openExternalHttps("https://example.com/docs?q=hello#section", open);
  assert.deepEqual(opened, ["https://example.com/docs?q=hello#section"]);
});

test("external link dispatch errors remain observable to the caller", async () => {
  await assert.rejects(openExternalHttps("https://example.com", async () => { throw new Error("browser launch failed"); }), /browser launch failed/);
});
