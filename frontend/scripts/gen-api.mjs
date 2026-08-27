/**
 Regenerate the typed API client from the FastAPI OpenAPI schema.
 1) python dumps openapi.json (no server needed — app.openapi())
 2) openapi-typescript compiles it to src/api/schema.d.ts (committed;
    regenerate when backend contracts change — same-commit Zod twin rule).
*/
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..");

const dump = spawnSync("uv", ["run", "python", "frontend/scripts/dump_openapi.py"], {
  cwd: repoRoot,
  stdio: "inherit",
});
if (dump.status !== 0) process.exit(dump.status ?? 1);

const codegen = spawnSync(
  "npx",
  ["openapi-typescript", path.join(here, "openapi.json"), "-o", path.join(here, "..", "src/api/schema.d.ts")],
  { cwd: here, stdio: "inherit", shell: process.platform === "win32" },
);
process.exit(codegen.status ?? 0);
