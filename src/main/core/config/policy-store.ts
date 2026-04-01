import crypto from "crypto";
import fs from "fs";
import path from "path";
import { resolveRealPath } from "../utils";

type StoredPolicyFile = {
  version: number;
  workspaceRoot: string;
  protected: string[];
  updatedAt: string;
};

function getDefaultPolicyStoreDir(): string {
  const { app } = require("electron") as typeof import("electron");
  return path.join(app.getPath("userData"), "policies");
}

export function getPolicyStoreDir(policyStoreDir?: string): string {
  return policyStoreDir ?? getDefaultPolicyStoreDir();
}

export function getWorkspacePolicyId(workspaceRoot: string): string {
  return crypto
    .createHash("sha256")
    .update(resolveRealPath(workspaceRoot))
    .digest("hex");
}

export function getWorkspacePolicyPath(
  workspaceRoot: string,
  policyStoreDir?: string
): string {
  return path.join(
    getPolicyStoreDir(policyStoreDir),
    "workspaces",
    `${getWorkspacePolicyId(workspaceRoot)}.json`
  );
}

export function getLegacyPolicyPath(workspaceRoot: string): string {
  return path.join(resolveRealPath(workspaceRoot), ".fortshell", "policy.json");
}

function isRelativeToRoot(rootPath: string, candidatePath: string): boolean {
  const relative = path.relative(rootPath, candidatePath);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function toStoredPath(workspaceRoot: string, protectedPath: string): string {
  const rootPath = resolveRealPath(workspaceRoot);
  const normalizedPath = resolveRealPath(protectedPath);

  if (!isRelativeToRoot(rootPath, normalizedPath)) {
    return normalizedPath;
  }

  const relativePath = path.relative(rootPath, normalizedPath);
  return relativePath === "" ? "." : relativePath.replace(/\\/g, "/");
}

function fromStoredPath(workspaceRoot: string, storedPath: string): string {
  if (path.isAbsolute(storedPath)) {
    return resolveRealPath(storedPath);
  }

  const rootPath = resolveRealPath(workspaceRoot);
  return storedPath === "."
    ? rootPath
    : resolveRealPath(path.join(rootPath, storedPath));
}

function parseStoredPolicy(raw: string, sourcePath: string): StoredPolicyFile | null {
  try {
    const data = JSON.parse(raw);
    if (!Array.isArray(data.protected)) {
      return null;
    }

    return {
      version: Number(data.version) || 1,
      workspaceRoot: typeof data.workspaceRoot === "string" ? data.workspaceRoot : "",
      protected: data.protected.filter((entry: unknown): entry is string => typeof entry === "string"),
      updatedAt: typeof data.updatedAt === "string" ? data.updatedAt : "",
    };
  } catch (err) {
    console.warn(`[policy] Failed to load policy from ${sourcePath}:`, err);
    return null;
  }
}

function removeLegacyPolicyFile(workspaceRoot: string): void {
  const legacyPath = getLegacyPolicyPath(workspaceRoot);
  if (!fs.existsSync(legacyPath)) return;

  try {
    fs.rmSync(legacyPath, { force: true });
  } catch (err) {
    console.warn(`[policy] Failed to remove legacy policy file ${legacyPath}:`, err);
    return;
  }

  const legacyDir = path.dirname(legacyPath);
  try {
    if (fs.existsSync(legacyDir) && fs.readdirSync(legacyDir).length === 0) {
      fs.rmdirSync(legacyDir);
    }
  } catch {}
}

export function saveWorkspacePolicy(
  workspaceRoot: string,
  protectedPaths: Iterable<string>,
  policyStoreDir?: string
): void {
  const filePath = getWorkspacePolicyPath(workspaceRoot, policyStoreDir);
  const dirPath = path.dirname(filePath);
  const normalizedRoot = resolveRealPath(workspaceRoot);
  const protectedEntries = Array.from(new Set(
    Array.from(protectedPaths, (entry) => toStoredPath(normalizedRoot, entry))
  )).sort();

  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }

  const data: StoredPolicyFile = {
    version: 2,
    workspaceRoot: normalizedRoot,
    protected: protectedEntries,
    updatedAt: new Date().toISOString(),
  };

  fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf-8");
  removeLegacyPolicyFile(normalizedRoot);
}

export function loadWorkspacePolicy(
  workspaceRoot: string,
  policyStoreDir?: string
): Set<string> {
  const normalizedRoot = resolveRealPath(workspaceRoot);
  const filePath = getWorkspacePolicyPath(normalizedRoot, policyStoreDir);

  if (fs.existsSync(filePath)) {
    const data = parseStoredPolicy(fs.readFileSync(filePath, "utf-8"), filePath);
    removeLegacyPolicyFile(normalizedRoot);
    if (!data) return new Set();
    return new Set(data.protected.map((entry) => fromStoredPath(normalizedRoot, entry)));
  }

  const legacyPath = getLegacyPolicyPath(normalizedRoot);
  if (!fs.existsSync(legacyPath)) {
    return new Set();
  }

  const data = parseStoredPolicy(fs.readFileSync(legacyPath, "utf-8"), legacyPath);
  if (!data) return new Set();

  const protectedPaths = new Set(
    data.protected.map((entry) => fromStoredPath(normalizedRoot, entry))
  );
  saveWorkspacePolicy(normalizedRoot, protectedPaths, policyStoreDir);
  return protectedPaths;
}
