import { readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const generatedRoot = path.resolve("src/lib/generated");

async function normalizeDirectory(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  await Promise.all(
    entries.map(async (entry) => {
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        await normalizeDirectory(entryPath);
        return;
      }
      if (!entry.name.endsWith(".ts")) {
        return;
      }
      const source = await readFile(entryPath, "utf8");
      await writeFile(entryPath, `${source.trimEnd()}\n`, "utf8");
    })
  );
}

await normalizeDirectory(generatedRoot);
