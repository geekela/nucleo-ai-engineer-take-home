import net from "node:net";
import { afterEach, expect, test } from "vitest";
import { freePort } from "./ports.mjs";

const open = [];

function listen(port) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => {
      open.push(server);
      resolve(server.address().port);
    });
  });
}

afterEach(async () => {
  await Promise.all(open.splice(0).map((s) => new Promise((r) => s.close(r))));
});

test("keeps the preferred port when it is free", async () => {
  const port = await listen(0); // the OS picks a free port; release it and ask for it back
  await new Promise((r) => open.pop().close(r));
  expect(await freePort(port)).toBe(port);
});

test("moves to the next free port when the preferred one is taken", async () => {
  const busy = await listen(0);
  const chosen = await freePort(busy);
  expect(chosen).toBeGreaterThan(busy);
  expect(await listen(chosen)).toBe(chosen); // and it really is free
});
