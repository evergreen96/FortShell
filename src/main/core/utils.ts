import fs from "fs";
import path from "path";

export function resolveRealPath(filePath: string): string {
  const resolved = path.resolve(filePath);
  try {
    return fs.realpathSync(resolved);
  } catch {
    return resolved;
  }
}
