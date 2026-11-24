// Finds a free TCP port on 127.0.0.1, so `npm run dev` still starts when 8000 or 5173 is taken.
import net from "node:net";

function isFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.listen(port, "127.0.0.1", () => server.close(() => resolve(true)));
  });
}

/** The first free port from `preferred` upwards. */
export async function freePort(preferred, tries = 50) {
  for (let port = preferred; port < preferred + tries; port++) {
    if (await isFree(port)) return port;
  }
  throw new Error(`No free port between ${preferred} and ${preferred + tries - 1}.`);
}
