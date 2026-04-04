import fs from "node:fs/promises";
import path from "node:path";

export const SIZE_MAP = {
  "5k": 5000,
  "20k": 20000,
  "50k": 50000,
};

const BENCHMARK_EXTRAS = [
  ".env",
  "secrets/api.key",
  "config/private/app.json",
  "README.md",
];

function buildRelativePaths(targetCount) {
  const paths = [];
  let i = 0;

  while (paths.length < targetCount) {
    const bucket = String(Math.floor(i / 200)).padStart(3, "0");
    const leaf = String(i).padStart(5, "0");

    paths.push(`packages/pkg-${bucket}/src/file-${leaf}.ts`);
    if (paths.length < targetCount) {
      paths.push(`packages/pkg-${bucket}/config/config-${leaf}.json`);
    }
    if (paths.length < targetCount) {
      paths.push(`services/svc-${bucket}/src/worker-${leaf}.ts`);
    }
    i += 1;
  }

  return paths.slice(0, targetCount);
}

async function writeWorkspaceFiles(rootDir, relativePaths) {
  const batchSize = 128;

  for (let index = 0; index < relativePaths.length; index += batchSize) {
    const batch = relativePaths.slice(index, index + batchSize);
    await Promise.all(
      batch.map(async (relativePath) => {
        const fullPath = path.join(rootDir, relativePath);
        await fs.mkdir(path.dirname(fullPath), { recursive: true });

        const content = relativePath.endsWith(".json")
          ? "{\"ok\":true}\n"
          : relativePath.endsWith(".md")
            ? "# Fixture\n"
            : "export const value = 1;\n";

        await fs.writeFile(fullPath, content, "utf8");
      })
    );
  }
}

export async function generateLargeWorkspace(rootDir, sizeLabel) {
  const targetCount = SIZE_MAP[sizeLabel];
  if (!targetCount) {
    throw new Error(`Unsupported size label: ${sizeLabel}`);
  }

  const entries = buildRelativePaths(targetCount - BENCHMARK_EXTRAS.length);
  const allPaths = [...entries, ...BENCHMARK_EXTRAS];

  await fs.rm(rootDir, { recursive: true, force: true });
  await fs.mkdir(rootDir, { recursive: true });
  await writeWorkspaceFiles(rootDir, allPaths);

  return {
    rootDir,
    fileCount: allPaths.length,
    sizeLabel,
  };
}

export function createBenchmarkReportLines({
  sizeLabel,
  fileCount,
  fixtureGenerationMs,
  searchResults,
  compileResults,
}) {
  return [
    `fixture=${sizeLabel} files=${fileCount} generationMs=${fixtureGenerationMs}`,
    ...searchResults.map(
      (entry) => `${entry.label} durationMs=${entry.durationMs} count=${entry.count}`
    ),
    ...compileResults.map(
      (entry) => `${entry.label} durationMs=${entry.durationMs} count=${entry.count}`
    ),
  ];
}
