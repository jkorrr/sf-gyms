import { copyFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const maplibreDist = join(appRoot, "node_modules", "maplibre-gl", "dist");
const publicDir = join(appRoot, "public");

await mkdir(publicDir, { recursive: true });
await Promise.all([
  copyFile(join(maplibreDist, "maplibre-gl-worker.mjs"), join(publicDir, "maplibre-gl-worker.mjs")),
  copyFile(join(maplibreDist, "maplibre-gl-shared.mjs"), join(publicDir, "maplibre-gl-shared.mjs")),
]);
