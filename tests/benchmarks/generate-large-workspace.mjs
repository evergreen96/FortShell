import { generateLargeWorkspace } from "./lib/large-repo-benchmark.mjs";

if (import.meta.url === `file://${process.argv[1]}`) {
  const [, , rootDir, sizeLabel] = process.argv;

  if (!rootDir || !sizeLabel) {
    console.error("Usage: node tests/benchmarks/generate-large-workspace.mjs <rootDir> <5k|20k|50k>");
    process.exit(1);
  }

  generateLargeWorkspace(rootDir, sizeLabel)
    .then((summary) => {
      console.log(JSON.stringify(summary));
    })
    .catch((error) => {
      console.error(error);
      process.exit(1);
    });
}
