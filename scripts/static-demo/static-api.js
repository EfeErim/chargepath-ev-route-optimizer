"use strict";

// Answers the demo's /api/* requests in the browser by running the ChargePath Python package in
// Pyodide. Only the bundled synthetic fixture is available; nothing is sent to a planning server.
(() => {
  const PYODIDE_INDEX_URL = "https://cdn.jsdelivr.net/pyodide/v__PYODIDE_VERSION__/full/";
  const SOURCE_DIR = "/home/pyodide/chargepath-src";
  const FIXTURE_PATH = "/home/pyodide/synthetic_corridor.json";
  let planner = null;

  async function start() {
    const pyodide = await loadPyodide({indexURL: PYODIDE_INDEX_URL});
    const [source, fixture] = await Promise.all([
      fetch("chargepath-src.zip"),
      fetch("synthetic_corridor.json"),
    ]);
    if (!source.ok || !fixture.ok) throw new Error("Could not load the in-browser planner files");
    pyodide.unpackArchive(await source.arrayBuffer(), "zip", {extractDir: SOURCE_DIR});
    pyodide.FS.writeFile(FIXTURE_PATH, new Uint8Array(await fixture.arrayBuffer()));
    pyodide.runPython(
      `import sys\nsys.path.insert(0, ${JSON.stringify(SOURCE_DIR)})\n` +
        `from chargepath.demo import browser\nbrowser.start(${JSON.stringify(FIXTURE_PATH)})`,
    );
    return pyodide.pyimport("chargepath.demo.browser");
  }

  window.chargepathApi = {
    async fetch(path, init = {}) {
      planner ??= start();
      const browser = await planner;
      const answer = JSON.parse(browser.handle(init.method || "GET", path, init.body ?? null));
      return new Response(JSON.stringify(answer.payload), {
        status: answer.status,
        headers: {"Content-Type": "application/json"},
      });
    },
  };
})();
